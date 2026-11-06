"""The decision document: deterministic assembly from the agents' outputs, and Markdown rendering.

Scope, assumptions, key decisions, success criteria and open questions are copied verbatim from
the final proposal. Assumption and decision statuses, unresolved disagreements and metrics are
computed here.
The SUMMARIZER only contributes the overview and the decision rationales.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .config import Inputs
from .schemas import (
    Action,
    Challenge,
    Confidence,
    CriticReview,
    Item,
    KeyDecision,
    OpenQuestion,
    ProposerTurn,
    Reply,
    RoundMetrics,
    Summary,
    Termination,
)


class Header(BaseModel):
    feature_request: str
    system_description: str
    models: dict[str, str]
    rounds: int
    termination: Termination
    timestamp: datetime


class AssumptionStatus(Item):
    status: Literal["accepted", "challenged"]
    challenges: list[str]
    last_reply: Action | None  # None if never challenged, or not replied to yet


class LoggedDecision(KeyDecision):
    status: Literal["agreed", "disputed"]  # disputed if a challenge on it is open at the end
    challenges: list[str]
    rationale: str


class Disagreement(BaseModel):
    challenge: Challenge
    reply: Reply | None  # the PROPOSER's latest reply; None if raised in the final round
    reply_round: int | None


class DecisionDocument(BaseModel):
    header: Header
    overview: str
    in_scope: list[Item]
    out_of_scope: list[Item]
    assumptions: list[AssumptionStatus]
    decision_log: list[LoggedDecision]
    success_criteria: list[Item]
    open_questions: list[OpenQuestion]
    unresolved_disagreements: list[Disagreement]
    final_confidence: dict[str, Confidence]
    metrics: list[RoundMetrics]


def assemble(
    *,
    inputs: Inputs,
    models: dict[str, str],
    proposals: list[ProposerTurn],
    reviews: list[CriticReview],
    metrics: list[RoundMetrics],
    termination: Termination,
    summary: Summary,
) -> DecisionDocument:
    final = proposals[-1].proposal
    replies = [(rnd, r) for rnd, turn in enumerate(proposals, start=1) for r in turn.replies]
    latest_reply = {r.challenge_id: (rnd, r) for rnd, r in replies}  # later rounds win
    rationales = {r.decision_id: r.rationale for r in summary.rationales}
    still_open = {c.id for c in reviews[-1].challenges}

    def challenges_on(item_id: str) -> list[str]:
        return list(
            dict.fromkeys(c.id for r in reviews for c in r.challenges if c.target == item_id)
        )

    def assumption(item: Item) -> AssumptionStatus:
        ids = challenges_on(item.id)
        answers = [r.action for _, r in replies if r.challenge_id in ids]
        return AssumptionStatus(
            **item.model_dump(),
            status="challenged" if ids else "accepted",
            challenges=ids,
            last_reply=answers[-1] if answers else None,
        )

    def decision(d: KeyDecision) -> LoggedDecision:
        ids = challenges_on(d.id)
        return LoggedDecision(
            **d.model_dump(),
            status="disputed" if still_open.intersection(ids) else "agreed",
            challenges=ids,
            rationale=rationales.get(d.id, ""),
        )

    def disagreement(challenge: Challenge) -> Disagreement:
        rnd, reply = latest_reply.get(challenge.id, (None, None))
        return Disagreement(challenge=challenge, reply=reply, reply_round=rnd)

    return DecisionDocument(
        header=Header(
            feature_request=inputs.feature_request,
            system_description=inputs.system_description,
            models=models,
            rounds=len(reviews),
            termination=termination,
            timestamp=datetime.now().astimezone(),
        ),
        overview=summary.overview,
        in_scope=final.in_scope,
        out_of_scope=final.out_of_scope,
        assumptions=[assumption(a) for a in final.assumptions],
        decision_log=[decision(d) for d in final.key_decisions],
        success_criteria=final.success_criteria,
        open_questions=final.open_questions,
        unresolved_disagreements=[disagreement(c) for c in reviews[-1].challenges],
        final_confidence={"proposer": proposals[-1].confidence, "critic": reviews[-1].confidence},
        metrics=metrics,
    )


def termination_label(termination: Termination, rounds: int) -> str:
    return {
        "converged": f"Converged after {rounds} rounds",
        "stalled": f"WARNING: Deliberation Stalled after {rounds} rounds",
        "max_rounds": f"WARNING: Limit of {rounds} Rounds Reached",
    }[termination]


def format_delta(delta: int | None) -> str:
    return "–" if delta is None else f"{delta:+d}"


def assumption_label(a: AssumptionStatus) -> str:
    if a.status == "accepted":
        return "accepted (never challenged)"
    reply = f"last reply: {a.last_reply}" if a.last_reply else "raised in the final round, no reply"
    return f"challenged by {', '.join(a.challenges)}; {reply}"


def decision_label(d: LoggedDecision) -> str:
    if not d.challenges:
        return "agreed (never challenged)"
    return f"{d.status}, challenged by {', '.join(d.challenges)}"


def render_markdown(doc: DecisionDocument) -> str:
    h = doc.header

    def bullets(lines: list[str]) -> list[str]:
        return [*(lines or ["_None._"]), ""]

    def items(entries: list[Item]) -> list[str]:
        return bullets([f"- **{i.id}** {i.text}" for i in entries])

    def quote(text: str) -> str:
        return "\n".join(f"> {line}" for line in text.splitlines())

    def unresolved(d: Disagreement) -> str:
        c = d.challenge
        lines = [f"- **{c.id}** ({c.severity}, on {c.target}) {c.risk}"]
        if d.reply:
            lines.append(
                f"  - PROPOSER, round {d.reply_round} ({d.reply.action}): {d.reply.reason}"
            )
        else:
            lines.append("  - Raised in the final round, no reply.")
        return "\n".join(lines)

    confidence = doc.final_confidence
    lines = [
        "# Decision Document",
        "",
        f"- **Termination:** {termination_label(h.termination, h.rounds)}",
        "- **Models:** " + ", ".join(f"{a.upper()} `{m}`" for a, m in h.models.items()),
        f"- **Generated:** {h.timestamp:%Y-%m-%d %H:%M %Z}",
        "",
        "**Feature request**",
        "",
        quote(h.feature_request),
        "",
        "**System description**",
        "",
        quote(h.system_description),
        "",
        "## Overview",
        "",
        doc.overview,
        "",
        "## Scope",
        "",
        "### In scope",
        "",
        *items(doc.in_scope),
        "### Out of scope",
        "",
        *items(doc.out_of_scope),
        "## Assumptions",
        "",
        *bullets([f"- **{a.id}** {a.text} — _{assumption_label(a)}_" for a in doc.assumptions]),
        "## Decision Log",
        "",
        *bullets(
            [
                f"- **{d.id}** {d.decision} — _{decision_label(d)}_\n  - Rejected: {d.rejected}"
                f"\n  - Reason: {d.reason}\n  - Rationale: {d.rationale}"
                for d in doc.decision_log
            ]
        ),
        "## Success Criteria",
        "",
        *items(doc.success_criteria),
        "## Open Questions",
        "",
        *bullets(
            [
                f"- **{q.id}** {q.question}\n  - Default until answered: {q.default}"
                for q in doc.open_questions
            ]
        ),
        "## Unresolved Disagreements",
        "",
        *bullets([unresolved(d) for d in doc.unresolved_disagreements]),
        "## Deliberation Metrics",
        "",
        *[
            f"- **{a.upper()} confidence:** {c.score} — {c.rationale}"
            for a, c in confidence.items()
        ],
        "",
        "| Round | Disagreement | Δ | PROPOSER conf. | CRITIC conf. | Gap | Resolved "
        "| Defended | Revised | Conceded |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {m.round} | {m.disagreement} | {format_delta(m.delta)} | {m.proposer_confidence} "
            f"| {m.critic_confidence} | {m.confidence_gap} | {m.resolved} | {m.defended} "
            f"| {m.revised} | {m.conceded} |"
            for m in doc.metrics
        ],
    ]
    return "\n".join(lines) + "\n"
