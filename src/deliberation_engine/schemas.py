"""Structured agent outputs and the per-round metrics.

The models carry no docstrings or field descriptions on purpose: Pydantic would put them into the
JSON schema sent to the model, and all prompt text lives in `prompts/`. Field order is generation
order, e.g. a challenge's risk comes before its severity.
"""

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["minor", "major", "blocking"]
Action = Literal["defended", "revised", "conceded"]
Closure = Literal[
    "defense_accepted", "concession_accepted", "revision_adequate", "converted_to_open_question"
]
Termination = Literal["converged", "stalled", "max_rounds"]
SECTIONS = ("scope", "assumptions", "key_decisions", "success_criteria", "open_questions")
SEVERITY_WEIGHTS = {"minor": 1, "major": 3, "blocking": 5}


class Confidence(BaseModel):
    rationale: str
    score: int = Field(ge=0, le=100)


class Item(BaseModel):
    id: str
    text: str


class KeyDecision(BaseModel):
    id: str
    decision: str
    status: Literal["agreed", "rejected"]
    reason: str


class OpenQuestion(BaseModel):
    id: str
    question: str
    default: str


class Proposal(BaseModel):
    in_scope: list[Item]
    out_of_scope: list[Item]
    assumptions: list[Item]
    key_decisions: list[KeyDecision]
    success_criteria: list[Item] = Field(min_length=1)
    open_questions: list[OpenQuestion]

    def ids(self) -> list[str]:
        sections = (self.in_scope, self.out_of_scope, self.assumptions)
        sections += (self.key_decisions, self.success_criteria, self.open_questions)
        return [item.id for section in sections for item in section]


class Reply(BaseModel):
    challenge_id: str
    action: Action
    reason: str


class ProposerTurn(BaseModel):
    replies: list[Reply]
    proposal: Proposal
    confidence: Confidence


class Challenge(BaseModel):
    id: str
    target: str
    risk: str
    severity: Severity


class Resolution(BaseModel):
    challenge_id: str
    reason: str
    closed_by: Closure


class CriticReview(BaseModel):
    resolved: list[Resolution]
    challenges: list[Challenge]
    justification: str
    satisfied: bool
    confidence: Confidence


class Rationale(BaseModel):
    decision_id: str
    rationale: str


class Summary(BaseModel):
    rationales: list[Rationale]
    overview: str


class RoundMetrics(BaseModel):
    round: int
    disagreement: int
    delta: int | None
    proposer_confidence: int
    critic_confidence: int
    confidence_gap: int
    resolved: int
    defended: int
    revised: int
    conceded: int
