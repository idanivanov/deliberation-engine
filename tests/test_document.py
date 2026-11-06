"""Deterministic parts: document assembly, metrics, Markdown, and input loading."""

import pytest
from conftest import INPUTS, challenge, item, review, turn

from deliberation_engine.cli import build_parser, load_inputs
from deliberation_engine.config import Settings, clean
from deliberation_engine.document import assemble, render_markdown
from deliberation_engine.orchestrator import round_metrics
from deliberation_engine.schemas import KeyDecision, OpenQuestion, Rationale, Summary

DECISION = KeyDecision(id="D1", decision="Alert after 90 days", status="agreed", reason="C1")
QUESTION = OpenQuestion(id="Q1", question="Which threshold?", default="90 days")

# Round 1: C1 and C2 raised. Round 2: C2 conceded and closed, C3 raised.
# Round 3: C1 revised, C3 defended and closed, C4 raised in the final round.
PROPOSALS = [
    turn(score=60),
    turn(("C1", "defended"), ("C2", "conceded"), score=70),
    turn(
        ("C1", "revised"),
        ("C3", "defended"),
        score=85,
        assumptions=[item("A1", "final A1"), item("A2"), item("A3")],
        key_decisions=[DECISION],
        open_questions=[QUESTION],
    ),
]
REVIEWS = [
    review(challenge("C1"), challenge("C2", target="A2"), score=40),
    review(
        challenge("C1"), challenge("C3", target="A2"), resolved=(("C2", "concession_accepted"),)
    ),
    review(
        challenge("C1", "minor"),
        challenge("C4", "minor", target="A3"),
        resolved=(("C3", "defense_accepted"),),
        satisfied=True,
        score=80,
    ),
]


def build_document():
    metrics = []
    for rnd, (p, r) in enumerate(zip(PROPOSALS, REVIEWS, strict=True), start=1):
        metrics.append(round_metrics(rnd, p, r, metrics[-1] if metrics else None))
    summary = Summary(rationales=[Rationale(decision_id="D1", rationale="C1 showed")], overview="o")
    return assemble(
        inputs=INPUTS,
        models={"proposer": "p", "critic": "c", "summarizer": "s"},
        proposals=PROPOSALS,
        reviews=REVIEWS,
        metrics=metrics,
        termination="converged",
        summary=summary,
    )


def test_final_proposal_is_copied_verbatim():
    doc, final = build_document(), PROPOSALS[-1].proposal
    assert (doc.in_scope, doc.out_of_scope) == (final.in_scope, final.out_of_scope)
    assert (doc.success_criteria, doc.open_questions) == (final.success_criteria, [QUESTION])
    assert [a.text for a in doc.assumptions] == ["final A1", "text of A2", "text of A3"]
    assert [(d.decision, d.rationale) for d in doc.decision_log] == [
        (DECISION.decision, "C1 showed")
    ]


def test_assumption_status_comes_from_the_challenges_and_the_last_reply():
    a1, a2, a3 = build_document().assumptions
    assert (a1.status, a1.challenges, a1.last_reply) == ("challenged", ["C1"], "revised")
    assert (a2.status, a2.challenges, a2.last_reply) == ("challenged", ["C2", "C3"], "defended")
    assert (a3.status, a3.challenges, a3.last_reply) == ("challenged", ["C4"], None)


def test_unresolved_disagreements_carry_the_latest_reply():
    first, second = build_document().unresolved_disagreements
    assert (first.challenge.id, first.reply.action, first.reply_round) == ("C1", "revised", 3)
    assert (second.challenge.id, second.reply, second.reply_round) == ("C4", None, None)


def test_round_metrics():
    m1, m2, m3 = build_document().metrics
    assert (m1.disagreement, m1.delta, m1.confidence_gap) == (6, None, 20)
    assert (m2.disagreement, m2.delta, m2.resolved, m2.defended, m2.conceded) == (6, 0, 1, 1, 1)
    assert (m3.disagreement, m3.delta, m3.confidence_gap, m3.revised) == (2, -4, 5, 1)


def test_markdown_has_every_section():
    markdown = render_markdown(build_document())
    for heading in (
        "Overview",
        "Scope",
        "Assumptions",
        "Decision Log",
        "Success Criteria",
        "Open Questions",
        "Unresolved Disagreements",
        "Deliberation Metrics",
    ):
        assert f"## {heading}\n" in markdown
    assert "Converged after 3 rounds" in markdown
    assert "Raised in the final round, no reply." in markdown
    assert "Default until answered: 90 days" in markdown


def test_inputs_come_from_arguments_files_or_environment(tmp_path):
    request_file = tmp_path / "request.txt"
    request_file.write_text("From a file.\x07")
    args = build_parser().parse_args(["--request-file", str(request_file)])
    inputs = load_inputs(args, Settings(system_description="From the environment."))
    assert (inputs.system_description, inputs.feature_request) == (
        "From the environment.",
        "From a file.",
    )
    with pytest.raises(ValueError, match="feature request"):
        load_inputs(build_parser().parse_args(["-s", "A system."]), Settings(feature_request=None))


def test_clean_strips_control_characters_and_enforces_the_cap():
    assert clean(" a\x00b\tc\r\nd\x1b ", "input") == "ab\tc\nd"
    with pytest.raises(ValueError, match="limit"):
        clean("x" * 10_001, "input")
    with pytest.raises(ValueError, match="empty"):
        clean("\x00 ", "input")
