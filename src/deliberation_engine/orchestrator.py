"""The deliberation loop: PROPOSER/CRITIC rounds, stopping criteria, metrics and the chat log.

A round is one PROPOSER turn followed by one CRITIC review. The stopping criteria are checked after
every review, so the final proposal has always been reviewed:
- converged: the CRITIC is satisfied (its validator rules this out in round 1);
- stalled: the lowest major/blocking disagreement so far has not improved for `stall_rounds` rounds;
- max_rounds: the hard safety net.
Progress is reported through `on_event`, so the loop has no terminal dependency.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryFeedbackPart,
    ThinkingPart,
    ToolReturnPart,
)

from . import prompts
from .agents import Agents, CriticDeps, ProposerDeps
from .config import Inputs, Settings
from .document import DecisionDocument, assemble, render_markdown
from .schemas import (
    SEVERITY_WEIGHTS,
    Challenge,
    CriticReview,
    ProposerTurn,
    RoundMetrics,
    Summary,
    Termination,
)

CHAT_FILE, DOCUMENT_JSON, DOCUMENT_MD = "chat.json", "decision.json", "decision.md"


class Usage(BaseModel):
    requests: int
    input_tokens: int
    output_tokens: int
    cost: float | None  # as reported by OpenRouter
    seconds: float


class Turn(BaseModel):
    agent: str
    round: int
    timestamp: datetime  # when the output arrived
    usage: Usage
    output: ProposerTurn | CriticReview | Summary | None
    error: str | None = None
    messages: list[ModelMessage]  # the whole exchange, including retries and reasoning


class ChatLog(BaseModel):
    inputs: Inputs
    models: dict[str, str]
    max_rounds: int
    stall_rounds: int
    turns: list[Turn] = []


@dataclass
class TurnStarted:
    agent: str
    round: int


@dataclass
class TurnFinished:
    turn: Turn


@dataclass
class RoundFinished:
    metrics: RoundMetrics


type Event = TurnStarted | TurnFinished | RoundFinished


def disagreement(challenges: list[Challenge], *, serious_only: bool = False) -> int:
    return sum(
        SEVERITY_WEIGHTS[c.severity]
        for c in challenges
        if not (serious_only and c.severity == "minor")
    )


def round_metrics(
    rnd: int, turn: ProposerTurn, review: CriticReview, previous: RoundMetrics | None
) -> RoundMetrics:
    actions = [r.action for r in turn.replies]
    value = disagreement(review.challenges)
    return RoundMetrics(
        round=rnd,
        disagreement=value,
        delta=None if previous is None else value - previous.disagreement,
        proposer_confidence=turn.confidence.score,
        critic_confidence=review.confidence.score,
        confidence_gap=abs(turn.confidence.score - review.confidence.score),
        resolved=len(review.resolved),
        defended=actions.count("defended"),
        revised=actions.count("revised"),
        conceded=actions.count("conceded"),
    )


def internal_steps(turn: Turn) -> list[tuple[str, str]]:
    """The validator retries and the model reasoning of a turn, as (kind, text) pairs."""
    steps = []
    for message in turn.messages:
        for part in message.parts:
            if isinstance(part, ThinkingPart) and part.content:
                steps.append(("reasoning", part.content))
            elif isinstance(part, ToolReturnPart) and part.outcome == "retried":
                steps.append(("retry", part.model_response_str()))
            elif isinstance(part, RetryFeedbackPart):
                steps.append(("retry", part.model_response()))
    return steps


def _usage(messages: list[ModelMessage], seconds: float) -> Usage:
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    costs = [c for r in responses if (c := (r.provider_details or {}).get("cost")) is not None]
    return Usage(
        requests=len(responses),
        input_tokens=sum(r.usage.input_tokens for r in responses),
        output_tokens=sum(r.usage.output_tokens for r in responses),
        cost=sum(costs) if costs else None,
        seconds=round(seconds, 2),
    )


def _without_confidence(output: BaseModel) -> str:
    # Each agent's confidence stays hidden from the other, so the two scores are not anchored.
    return output.model_dump_json(indent=2, exclude={"confidence"})


class Deliberation:
    def __init__(
        self,
        agents: Agents,
        inputs: Inputs,
        settings: Settings,
        run_dir: Path,
        on_event: Callable[[Event], None],
    ):
        self.agents, self.inputs, self.settings = agents, inputs, settings
        self.run_dir, self.on_event = run_dir, on_event
        models = {a.name: str(a.model) for a in (agents.proposer, agents.critic, agents.summarizer)}
        self.log = ChatLog(
            inputs=inputs,
            models=models,
            max_rounds=settings.max_rounds,
            stall_rounds=settings.stall_rounds,
        )

    async def run(self) -> DecisionDocument:
        proposals: list[ProposerTurn] = []
        reviews: list[CriticReview] = []
        metrics: list[RoundMetrics] = []
        proposer_history: list[ModelMessage] = []
        critic_history: list[ModelMessage] = []
        best, stuck = None, 0
        termination: Termination = "max_rounds"

        for rnd in range(1, self.settings.max_rounds + 1):
            header = prompts.shared.ROUND.format(round=rnd, max_rounds=self.settings.max_rounds)
            open_ids = [c.id for c in reviews[-1].challenges] if reviews else []

            if reviews:
                prompt = prompts.proposer.NEXT_TURN.format(
                    round_header=header,
                    review=_without_confidence(reviews[-1]),
                    challenge_ids=", ".join(open_ids),
                )
            else:
                prompt = prompts.proposer.FIRST_TURN.format(round_header=header)
            deps = ProposerDeps(open_ids)
            turn, proposer_history = await self._run(
                self.agents.proposer, rnd, prompt, proposer_history, deps
            )
            proposals.append(turn)

            if reviews:
                prompt = prompts.critic.NEXT_REVIEW.format(
                    round_header=header,
                    turn=_without_confidence(turn),
                    open_ids=", ".join(open_ids),
                )
            else:
                prompt = prompts.critic.FIRST_REVIEW.format(
                    round_header=header, turn=_without_confidence(turn)
                )
            used_ids = {c.id for r in reviews for c in r.challenges}
            deps = CriticDeps(rnd, open_ids, used_ids, set(turn.proposal.ids()))
            review, critic_history = await self._run(
                self.agents.critic, rnd, prompt, critic_history, deps
            )
            reviews.append(review)

            metrics.append(round_metrics(rnd, turn, review, metrics[-1] if metrics else None))
            self.on_event(RoundFinished(metrics[-1]))

            if review.satisfied:
                termination = "converged"
                break
            serious = disagreement(review.challenges, serious_only=True)
            best, stuck = (serious, 0) if best is None or serious < best else (best, stuck + 1)
            if stuck >= self.settings.stall_rounds:
                termination = "stalled"
                break

        summary = await self._summarize(proposals, reviews, termination)
        document = assemble(
            inputs=self.inputs,
            models=self.log.models,
            proposals=proposals,
            reviews=reviews,
            metrics=metrics,
            termination=termination,
            summary=summary,
        )
        (self.run_dir / DOCUMENT_JSON).write_text(document.model_dump_json(indent=2))
        (self.run_dir / DOCUMENT_MD).write_text(render_markdown(document))
        return document

    async def _summarize(
        self, proposals: list[ProposerTurn], reviews: list[CriticReview], termination: Termination
    ) -> Summary:
        final = proposals[-1].proposal
        record = {
            "rounds": [
                {
                    "round": rnd,
                    "proposer_replies": [r.model_dump() for r in turn.replies],
                    "critic_resolved": [r.model_dump() for r in review.resolved],
                    "critic_open_challenges": [c.model_dump() for c in review.challenges],
                }
                for rnd, (turn, review) in enumerate(zip(proposals, reviews, strict=True), start=1)
            ],
            "final_proposal": final.model_dump(),
            "termination": prompts.summarizer.TERMINATION[termination].format(
                stall_rounds=self.settings.stall_rounds, max_rounds=self.settings.max_rounds
            ),
            "critic_final_justification": reviews[-1].justification,
        }
        prompt = prompts.summarizer.SUMMARIZE.format(record=json.dumps(record, indent=2))
        decision_ids = [d.id for d in final.key_decisions]
        summary, _ = await self._run(self.agents.summarizer, len(reviews), prompt, [], decision_ids)
        return summary

    async def _run[D, O](
        self, agent: Agent[D, O], rnd: int, prompt: str, history: list[ModelMessage], deps: D
    ) -> tuple[O, list[ModelMessage]]:
        name = agent.name or "agent"
        self.on_event(TurnStarted(name, rnd))
        start = perf_counter()
        with capture_run_messages() as messages:
            try:
                result = await agent.run(prompt, message_history=history, deps=deps)
            except Exception as error:
                # Record the failed attempts too, so the log shows why the run stopped.
                failed = messages[len(history) :]
                self._record(name, rnd, failed, start, error=repr(error))
                raise
        turn = self._record(name, rnd, result.new_messages(), start, output=result.output)
        self.on_event(TurnFinished(turn))
        return result.output, result.all_messages()

    def _record(
        self,
        agent: str,
        rnd: int,
        messages: list[ModelMessage],
        start: float,
        *,
        output: ProposerTurn | CriticReview | Summary | None = None,
        error: str | None = None,
    ) -> Turn:
        turn = Turn(
            agent=agent,
            round=rnd,
            timestamp=datetime.now().astimezone(),
            usage=_usage(messages, perf_counter() - start),
            output=output,
            error=error,
            messages=messages,
        )
        self.log.turns.append(turn)
        (self.run_dir / CHAT_FILE).write_text(self.log.model_dump_json(indent=2))
        return turn

    @property
    def turns(self) -> list[Turn]:
        return self.log.turns
