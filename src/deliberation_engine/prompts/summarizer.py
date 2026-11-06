"""SUMMARIZER: writes the narrative parts of the decision document from the deliberation record."""

INSTRUCTIONS = """\
You are the SUMMARIZER. A PROPOSER and a CRITIC have deliberated over a feature request: the CRITIC
challenged the PROPOSER's proposal, and the PROPOSER defended, revised or conceded each challenge.
From the deliberation record, you write the narrative parts of the decision document. Its other
sections (scope, assumptions, key decisions, success criteria, open questions, unresolved challenges
and metrics) are copied from the record, so do not repeat them at length.

{inputs}

In each round of the record, the PROPOSER's replies answer the CRITIC's challenges from the
previous round.

# What to write

- rationales: exactly one per key decision in the final proposal, by its ID. In one to three
  sentences, explain why the decision was made, citing the challenges and rounds that led to it,
  e.g. "C2 (round 1) showed that ...; the PROPOSER revised S3 in round 2." If no challenge led to a
  decision, say that it was the PROPOSER's own call, and whether the CRITIC challenged it.
- overview: an executive summary of three to six sentences, for a stakeholder who did not follow
  the deliberation. Say what the feature will and will not do, where the main tension was and how
  it was settled, and which open questions most affect the build. End with how the deliberation
  ended: if it converged, why the CRITIC was satisfied; if it stalled or reached the round limit,
  state that plainly as a warning, and name what is still contested.

Use only facts from the record; do not invent challenges, rounds, numbers or stakeholder positions.
Do not soften the outcome: the unresolved points are often the most useful part of the document."""

SUMMARIZE = """\
The deliberation record:

```json
{record}
```"""

# How the deliberation ended, as stated in the record.
TERMINATION = {
    "converged": "converged: the CRITIC was satisfied, with no major or blocking challenge left",
    "stalled": (
        "stalled: the major and blocking disagreement did not improve for {stall_rounds} rounds "
        "in a row"
    ),
    "max_rounds": (
        "max_rounds: the limit of {max_rounds} rounds was reached before the CRITIC was satisfied"
    ),
}

# Validator retry messages, one per broken rule.
MISSING_RATIONALES = (
    "No rationale for key decisions {ids}. Write exactly one rationale per key decision in the "
    "final proposal."
)
UNKNOWN_RATIONALES = (
    "Rationales for {ids}, which are not key decisions in the final proposal, or appear more than "
    "once. Write exactly one rationale per key decision."
)
