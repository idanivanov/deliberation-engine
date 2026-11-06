"""The termination paths: round-1 retry, converged, stalled, max rounds, and errors."""

import json

import pytest
from conftest import challenge, deliberate, item, review, turn
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import UserPromptPart

from deliberation_engine import prompts
from deliberation_engine.orchestrator import RoundFinished, internal_steps
from deliberation_engine.schemas import KeyDecision, Rationale, Summary


async def test_converges_when_the_critic_is_satisfied(tmp_path):
    decision = KeyDecision(id="D1", decision="Use 90 days", status="agreed", reason="C1")
    final = turn(("C1", "revised"), score=85, key_decisions=[decision])
    document, deliberation, events = await deliberate(
        tmp_path,
        proposer=[turn(), final],
        critic=[
            review(challenge("C1")),
            review(
                challenge("C2", "minor", target="SC1"),
                resolved=(("C1", "revision_adequate"),),
                satisfied=True,
                score=80,
            ),
        ],
        summary=Summary(rationales=[Rationale(decision_id="D1", rationale="why")], overview="o"),
    )

    assert (document.header.termination, document.header.rounds) == ("converged", 2)
    assert [e.metrics.round for e in events if isinstance(e, RoundFinished)] == [1, 2]
    assert [d.rationale for d in document.decision_log] == ["why"]
    for name in ("chat.json", "decision.json", "decision.md"):
        assert (tmp_path / name).exists()
    log = json.loads((tmp_path / "chat.json").read_text())
    assert [t["agent"] for t in log["turns"]] == ["proposer", "critic"] * 2 + ["summarizer"]


async def test_round_one_satisfaction_is_sent_back_as_a_retry(tmp_path):
    document, deliberation, _ = await deliberate(
        tmp_path,
        proposer=[turn(), turn(("C1", "defended"))],
        critic=[
            review(satisfied=True),  # breaks the round-1 rule
            review(challenge("C1")),
            review(resolved=(("C1", "defense_accepted"),), satisfied=True),
        ],
    )

    first_review = deliberation.turns[1]
    [(kind, text)] = internal_steps(first_review)
    assert kind == "retry" and prompts.critic.ROUND_ONE in text
    assert first_review.usage.requests == 2
    assert (document.header.termination, document.header.rounds) == ("converged", 2)


async def test_round_one_violation_after_three_retries_stops_with_an_error(tmp_path):
    with pytest.raises(UnexpectedModelBehavior):
        await deliberate(tmp_path, proposer=[turn()], critic=[review(satisfied=True)] * 4)

    failed = json.loads((tmp_path / "chat.json").read_text())["turns"][-1]
    assert failed["agent"] == "critic" and failed["output"] is None and failed["error"]
    assert failed["usage"]["requests"] == 4
    assert not (tmp_path / "decision.md").exists()


async def test_stalls_when_major_disagreement_stops_improving(tmp_path):
    document, _, _ = await deliberate(
        tmp_path,
        proposer=[turn()] + [turn(("C1", "defended"))] * 3,
        critic=[review(challenge("C1"))] * 4,
    )

    # Round 1 sets the lowest disagreement; rounds 2-4 do not improve on it.
    assert (document.header.termination, document.header.rounds) == ("stalled", 4)
    [disagreement] = document.unresolved_disagreements
    assert (disagreement.reply.action, disagreement.reply_round) == ("defended", 4)


async def test_stall_counts_from_the_lowest_disagreement_not_the_previous_round(tmp_path):
    document, _, _ = await deliberate(
        tmp_path,
        proposer=[
            turn(),
            turn(("C1", "defended")),
            turn(("C1", "revised"), ("C2", "defended")),
            turn(("C2", "defended")),
        ],
        critic=[
            review(challenge("C1")),  # 3: the lowest so far
            review(challenge("C1"), challenge("C2")),  # 6: a setback
            review(challenge("C2"), resolved=(("C1", "revision_adequate"),)),  # 3: back, no gain
            review(challenge("C2")),  # 3: third round without improvement
        ],
    )

    assert (document.header.termination, document.header.rounds) == ("stalled", 4)


async def test_stops_at_the_round_limit_while_still_improving(tmp_path):
    document, _, _ = await deliberate(
        tmp_path,
        proposer=[turn(), turn(("C1", "revised"), ("C2", "defended")), turn(("C1", "revised"))],
        critic=[
            review(challenge("C1", "blocking"), challenge("C2")),  # 8
            review(challenge("C1", "blocking"), resolved=(("C2", "defense_accepted"),)),  # 5
            review(challenge("C1")),  # 3
        ],
        max_rounds=3,
    )

    assert (document.header.termination, document.header.rounds) == ("max_rounds", 3)
    assert [m.delta for m in document.metrics] == [None, -3, -2]


async def test_agents_do_not_see_each_others_confidence(tmp_path):
    _, deliberation, _ = await deliberate(
        tmp_path,
        proposer=[turn(), turn(("C1", "revised"), assumptions=[item("A1", "new")])],
        critic=[
            review(challenge("C1")),
            review(resolved=(("C1", "revision_adequate"),), satisfied=True),
        ],
    )

    for t in deliberation.turns[1:4]:  # every prompt that carries the other agent's output
        prompt = next(p for p in t.messages[0].parts if isinstance(p, UserPromptPart))
        assert '"replies"' in prompt.content or '"challenges"' in prompt.content
        assert '"confidence"' not in prompt.content
