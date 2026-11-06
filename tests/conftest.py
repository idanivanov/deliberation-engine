"""Builders for agent outputs, and a deliberation driven by scripted models (no API calls)."""

from pathlib import Path

from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from deliberation_engine.agents import build_agents
from deliberation_engine.config import Inputs, Settings
from deliberation_engine.document import DecisionDocument
from deliberation_engine.orchestrator import Deliberation, Event
from deliberation_engine.schemas import (
    Challenge,
    Confidence,
    CriticReview,
    Item,
    Proposal,
    ProposerTurn,
    Reply,
    Resolution,
    Summary,
)

INPUTS = Inputs("A CRM of government contacts.", "Alert us when a relationship goes cold.")


def item(id: str, text: str | None = None) -> Item:
    return Item(id=id, text=text or f"text of {id}")


def proposal(**sections) -> Proposal:
    default = {
        "in_scope": [item("S1")],
        "out_of_scope": [item("S2")],
        "assumptions": [item("A1"), item("A2")],
        "key_decisions": [],
        "success_criteria": [item("SC1")],
        "open_questions": [],
    }
    return Proposal(**default | sections)


def turn(*replies: tuple[str, str], score: int = 70, **sections) -> ProposerTurn:
    """A PROPOSER turn; each reply is a (challenge ID, action) pair."""
    return ProposerTurn(
        replies=[Reply(challenge_id=c, reason=f"{a} because", action=a) for c, a in replies],
        proposal=proposal(**sections),
        confidence=Confidence(rationale="proposer rationale", score=score),
    )


def challenge(id: str, severity: str = "major", target: str = "A1") -> Challenge:
    return Challenge(
        id=id, target=target, risk=f"risk {id}", suggestion=f"fix {id}", severity=severity
    )


def review(
    *challenges: Challenge,
    resolved: tuple[tuple[str, str], ...] = (),
    satisfied: bool = False,
    score: int = 50,
) -> CriticReview:
    """A CRITIC review; each resolution is a (challenge ID, how it closed) pair."""
    return CriticReview(
        resolved=[Resolution(challenge_id=c, closed_by=how) for c, how in resolved],
        challenges=list(challenges),
        justification="critic justification",
        satisfied=satisfied,
        confidence=Confidence(rationale="critic rationale", score=score),
    )


def scripted(*outputs: BaseModel) -> FunctionModel:
    """A model that returns the given outputs in order, one per request, retries included."""
    queue = list(outputs)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        output = queue.pop(0).model_dump_json()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])

    return FunctionModel(respond)


async def deliberate(
    run_dir: Path,
    proposer: list[ProposerTurn],
    critic: list[CriticReview],
    summary: Summary | None = None,
    **limits: int,
) -> tuple[DecisionDocument, Deliberation, list[Event]]:
    settings = Settings(**limits)
    agents = build_agents(INPUTS, settings)
    events: list[Event] = []
    deliberation = Deliberation(agents, INPUTS, settings, run_dir, events.append)
    summary = summary or Summary(rationales=[], overview="overview")
    with (
        agents.proposer.override(model=scripted(*proposer)),
        agents.critic.override(model=scripted(*critic)),
        agents.summarizer.override(model=scripted(summary)),
    ):
        document = await deliberation.run()
    return document, deliberation, events
