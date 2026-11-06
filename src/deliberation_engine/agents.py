"""The three agents, and the output validators that enforce the consistency rules.

Each check returns the list of broken rules; the agent's output validator sends them all back to
the model in one `ModelRetry`, so a single retry can fix every problem.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openrouter import OpenRouterModelSettings

from . import prompts
from .config import Inputs, Settings
from .schemas import SECTIONS, CriticReview, ProposerTurn, Summary

MIN_ROUNDS = 2
MAX_NEW_CHALLENGES = 5
OUTPUT_RETRIES = 3


@dataclass
class ProposerDeps:
    challenge_ids: list[str]  # the challenges of the latest CRITIC review


@dataclass
class CriticDeps:
    round: int
    open_ids: list[str]  # challenges open after the previous review
    used_ids: set[str]  # every challenge ID raised so far
    item_ids: set[str]  # item IDs of the proposal under review


@dataclass
class Agents:
    proposer: Agent[ProposerDeps, ProposerTurn]
    critic: Agent[CriticDeps, CriticReview]
    summarizer: Agent[list[str], Summary]  # deps: the key decision IDs of the final proposal


def _repeated(ids: list[str]) -> list[str]:
    return [i for i, count in Counter(ids).items() if count > 1]


def _problems(rules: list[tuple[Iterable[str], str]]) -> list[str]:
    """One message per broken rule; a rule is broken when its list of offending IDs is non-empty."""
    return [
        message.format(ids=", ".join(dict.fromkeys(ids)), limit=MAX_NEW_CHALLENGES)
        for ids, message in rules
        if ids
    ]


def check_proposer(turn: ProposerTurn, deps: ProposerDeps) -> list[str]:
    msg, replied = prompts.proposer, [r.challenge_id for r in turn.replies]
    return _problems(
        [
            ([c for c in deps.challenge_ids if c not in replied], msg.MISSING_REPLIES),
            ([c for c in replied if c not in deps.challenge_ids], msg.UNKNOWN_REPLIES),
            (_repeated(replied), msg.DUPLICATE_REPLIES),
            (_repeated(turn.proposal.ids()), msg.DUPLICATE_IDS),
        ]
    )


def _satisfaction(review: CriticReview, rnd: int, serious: list[str]) -> list[str]:
    """Round 1 is never satisfied; afterwards, satisfied exactly when nothing major remains."""
    msg = prompts.critic
    if rnd < MIN_ROUNDS:
        return [msg.ROUND_ONE] if review.satisfied or not serious else []
    if review.satisfied and serious:
        return [msg.SATISFIED_WITH_SERIOUS.format(ids=", ".join(serious))]
    if not review.satisfied and not serious:
        return [msg.UNSATISFIED_WITHOUT_SERIOUS]
    return []


def check_critic(review: CriticReview, deps: CriticDeps) -> list[str]:
    msg = prompts.critic
    raised = [c.id for c in review.challenges]
    resolved = [r.challenge_id for r in review.resolved]
    serious = [c.id for c in review.challenges if c.severity != "minor"]
    new = [i for i in raised if i not in deps.open_ids]
    targets = deps.item_ids | set(SECTIONS)
    off_target = [f"{c.id} on {c.target}" for c in review.challenges if c.target not in targets]
    return _satisfaction(review, deps.round, serious) + _problems(
        [
            (_repeated(raised + resolved), msg.DUPLICATE_IDS),
            (off_target, msg.UNKNOWN_TARGETS),
            ([i for i in deps.open_ids if i not in raised + resolved], msg.VANISHED),
            ([i for i in resolved if i not in deps.open_ids], msg.UNKNOWN_RESOLVED),
            ([i for i in new if i in deps.used_ids], msg.REUSED_IDS),
            (new if len(new) > MAX_NEW_CHALLENGES else [], msg.TOO_MANY_NEW),
        ]
    )


def check_summary(summary: Summary, decision_ids: list[str]) -> list[str]:
    msg, written = prompts.summarizer, [r.decision_id for r in summary.rationales]
    extra = [d for d in written if d not in decision_ids] + _repeated(written)
    return _problems(
        [
            ([d for d in decision_ids if d not in written], msg.MISSING_RATIONALES),
            (extra, msg.UNKNOWN_RATIONALES),
        ]
    )


def _retry_on(problems: list[str]) -> None:
    if problems:
        bullets = "\n".join(f"- {problem}" for problem in problems)
        raise ModelRetry(prompts.shared.RETRY.format(problems=bullets))


def build_agents(inputs: Inputs, settings: Settings) -> Agents:
    context = {
        "inputs": prompts.shared.INPUTS.format(
            system_description=inputs.system_description, feature_request=inputs.feature_request
        ),
        "severity_rubric": prompts.shared.SEVERITY_RUBRIC,
        "confidence_rubric": prompts.shared.CONFIDENCE_RUBRIC,
        "max_new_challenges": MAX_NEW_CHALLENGES,
    }
    model_settings = OpenRouterModelSettings(
        max_tokens=settings.max_tokens, openrouter_usage={"include": True}
    )

    def agent[D, O](
        name: str, model: str, output: type[O], deps: type[D], text: str
    ) -> Agent[D, O]:
        return Agent(
            model,
            name=name,
            output_type=output,
            deps_type=deps,
            instructions=text.format(**context),
            model_settings=model_settings,
            retries=OUTPUT_RETRIES,
            defer_model_check=True,  # resolve the model on first run, so tests can override it
        )

    proposer = agent(
        "proposer",
        settings.proposer_model,
        ProposerTurn,
        ProposerDeps,
        prompts.proposer.INSTRUCTIONS,
    )
    critic = agent(
        "critic", settings.critic_model, CriticReview, CriticDeps, prompts.critic.INSTRUCTIONS
    )
    summarizer = agent(
        "summarizer", settings.summarizer_model, Summary, list[str], prompts.summarizer.INSTRUCTIONS
    )

    @proposer.output_validator
    def _validate_turn(ctx: RunContext[ProposerDeps], output: ProposerTurn) -> ProposerTurn:
        _retry_on(check_proposer(output, ctx.deps))
        return output

    @critic.output_validator
    def _validate_review(ctx: RunContext[CriticDeps], output: CriticReview) -> CriticReview:
        _retry_on(check_critic(output, ctx.deps))
        return output

    @summarizer.output_validator
    def _validate_summary(ctx: RunContext[list[str]], output: Summary) -> Summary:
        _retry_on(check_summary(output, ctx.deps))
        return output

    return Agents(proposer, critic, summarizer)
