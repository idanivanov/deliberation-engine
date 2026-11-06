"""The output validators' consistency rules."""

from conftest import challenge, item, proposal, review, turn

from deliberation_engine import prompts
from deliberation_engine.agents import (
    CriticDeps,
    ProposerDeps,
    check_critic,
    check_proposer,
    check_summary,
)
from deliberation_engine.schemas import Rationale, Summary

ITEM_IDS = set(proposal().ids())


def test_valid_outputs_pass():
    assert check_proposer(turn(("C1", "defended")), ProposerDeps(["C1"])) == []
    first_review = review(challenge("C1"), challenge("C2", "minor", target="success_criteria"))
    assert check_critic(first_review, CriticDeps(1, [], set(), ITEM_IDS)) == []


def test_proposer_replies_to_every_challenge_exactly_once():
    replies = turn(("C1", "defended"), ("C1", "revised"), ("C9", "conceded"))
    assert check_proposer(replies, ProposerDeps(["C1", "C2"])) == [
        prompts.proposer.MISSING_REPLIES.format(ids="C2"),
        prompts.proposer.UNKNOWN_REPLIES.format(ids="C9"),
        prompts.proposer.DUPLICATE_REPLIES.format(ids="C1"),
    ]


def test_proposer_item_ids_are_unique():
    duplicate = turn(assumptions=[item("A1"), item("S1")])
    assert check_proposer(duplicate, ProposerDeps([])) == [
        prompts.proposer.DUPLICATE_IDS.format(ids="S1")
    ]


def test_critic_is_not_satisfied_in_round_one_and_raises_a_major_challenge():
    deps = CriticDeps(1, [], set(), ITEM_IDS)
    assert check_critic(review(challenge("C1"), satisfied=True), deps) == [prompts.critic.ROUND_ONE]
    assert check_critic(review(challenge("C1", "minor")), deps) == [prompts.critic.ROUND_ONE]


def test_critic_is_satisfied_exactly_when_no_major_challenge_remains():
    deps = CriticDeps(2, ["C1"], {"C1"}, ITEM_IDS)
    assert check_critic(review(challenge("C1"), satisfied=True), deps) == [
        prompts.critic.SATISFIED_WITH_SERIOUS.format(ids="C1")
    ]
    assert check_critic(review(challenge("C1", "minor")), deps) == [
        prompts.critic.UNSATISFIED_WITHOUT_SERIOUS
    ]
    assert check_critic(review(challenge("C1", "minor"), satisfied=True), deps) == []


def test_critic_challenges_cannot_vanish_reappear_or_miss_their_target():
    deps = CriticDeps(3, ["C1", "C2", "C3"], {"C0", "C1", "C2", "C3"}, ITEM_IDS)
    output = review(
        challenge("C1"),
        challenge("C0"),
        challenge("C4", target="A9"),
        resolved=(("C1", "revision_adequate"), ("C7", "defense_accepted")),
    )
    msg = prompts.critic
    assert check_critic(output, deps) == [
        msg.DUPLICATE_IDS.format(ids="C1"),
        msg.UNKNOWN_TARGETS.format(ids="C4 on A9"),
        msg.VANISHED.format(ids="C2, C3"),
        msg.UNKNOWN_RESOLVED.format(ids="C7"),
        msg.REUSED_IDS.format(ids="C0"),
    ]


def test_critic_raises_at_most_five_new_challenges():
    output = review(*(challenge(f"C{i}") for i in range(1, 7)))
    assert check_critic(output, CriticDeps(1, [], set(), ITEM_IDS)) == [
        prompts.critic.TOO_MANY_NEW.format(ids="C1, C2, C3, C4, C5, C6", limit=5)
    ]


def test_summary_has_one_rationale_per_key_decision():
    rationales = [Rationale(decision_id=d, rationale="why") for d in ("D1", "D1", "D9")]
    summary = Summary(rationales=rationales, overview="o")
    assert check_summary(summary, ["D1", "D2"]) == [
        prompts.summarizer.MISSING_RATIONALES.format(ids="D2"),
        prompts.summarizer.UNKNOWN_RATIONALES.format(ids="D9, D1"),
    ]
