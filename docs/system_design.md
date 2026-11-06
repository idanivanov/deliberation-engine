# Deliberation Engine System Design Document

## Requirements & Scope

- **Multi-round agent communication:** The two agents exchange messages across at least 2 rounds before producing a final output. Each round's exchange is visible (printed and logged), so the user can clearly follow and inspect the deliberation.
- **Structured final output:** A decision document containing at minimum the agreed scope (what the feature will and will not include), the surfaced assumptions (and whether each was challenged or accepted), and the open questions that remain unresolved and require human input.
- **Graceful termination:** The system must have a defined, intentional termination condition, not a hardcoded round limit alone. The CRITIC must be capable of signaling that deliberation is complete. The termination strategy is documented and justified.
- **Configurable input:** The system description and feature request are injectable via CLI argument, file, or environment variable, so the system can be run against different inputs without code changes.
- **Prompt transparency:** Agent prompts are clearly separated from the orchestration logic for easier developer review.
- **Confidence or disagreement scoring:** Each agent scores its own confidence in the final agreed scope, and a disagreement delta between their positions is produced each round. This surfaces tension explicitly rather than burying it in prose.
- **CLI:** Runnable with a single command.
- **Simplicity:** A small, focused solution with reasonable token usage is preferred over an elaborate one. Framework choices are justified.

### Out-of-Scope

- Graphical user interface, beyond the simple CLI.
- Sophisticated agent context management (history compaction, map-reduce, etc.).
- Persistence beyond the session's output files (no database), API server, or multi-user support.
- Agent tools or retrieval: the agents only see the provided system description and feature request.
- Human input during the deliberation: open questions are collected in the decision document instead.
- Input sanitization beyond a length cap: the inputs are trusted plain text.

## Assumptions

- This is a general purpose (not domain specialized) deliberation engine, capable of working on any system and feature request, that can be described in free text within reasonable string limits.

## Architecture

All code is contained in a single, flat Python package `deliberation_engine` with clearly separated concerns. The orchestrator reports progress through a plain `on_event` callback that the CLI renders, so the deliberation loop does not depend on the terminal output and can be tested on its own. All LLM calls are sequential; there is no parallelism anywhere.

### Interface

This is a CLI tool, started with a single command: `uv run deliberation-engine [-s/--system "<plain text description of the target system for the request>"] [-r/--request "<feature request in plain text>"]`. Alternatively, each input can be read from a file (`--system-file`, `--request-file`) or provided via env variables: `DE_SYSTEM_DESCRIPTION` and `DE_FEATURE_REQUEST` (e.g., loaded from `.env`). The deliberation process starts immediately if the system description and feature request are provided. Otherwise, it raises an error about the missing inputs.

The conversation flow is clearly formatted so the user can clearly see what each agent is saying. Each message has visible metadata (timestamp, agent name, agent-specific color marks surrounding the message to clearly distinguish it from the rest of the conversation). Internal agent steps (validator retries and model reasoning, when the model returns it) are hidden by default and shown with `-v/--verbose`. They are always recorded in `chat.json`. The thinking process and active work is clearly indicated and animated so the user knows that the process is active. Each CRITIC message clearly shows its satisfaction signal (satisfied / not satisfied). Each PROPOSER and CRITIC message shows the agent's confidence. After each round, a single line shows the round's disagreement, disagreement delta, confidence gap, the number of challenges resolved, and the PROPOSER's reply breakdown (defended / revised / conceded).

The final result is clearly shown to the user with distinguishable formatting and colors. It states the termination reason, a short summary of the decision document, and the path to the produced full decision document. It also states the number of tokens used, total cost (if reported by OpenRouter), and total processing time (also, each metric broken down by agent).

### Agents

We use Pydantic AI for agent orchestration, because its typed structured outputs and output validators (raising `ModelRetry`) are how the termination and consistency rules are enforced. We use OpenRouter as LLM provider, in order to switch easily between different families of models. The base model for each agent is provided as an env variable for easy configuration. By default, the PROPOSER and the CRITIC use different model families, since a model tends to agree with its own reasoning.

#### Conversation State & Context Management

For simplicity, the conversation history is the only state shared between the PROPOSER and the CRITIC.

- Each agent keeps its own Pydantic AI message history: its own replies are assistant messages, and the other agent's latest reply is passed as the next user prompt. The history returned by each run is passed back as `message_history` to the agent's next run.
- The feature request and system description are part of each agent's instructions, so they are always in context.
- The other agent's output is passed as JSON text, so item and challenge IDs are unambiguous. Each user prompt also states the current round and the round limits.
- The history is not compacted. It is bounded by the round limit and the response token cap: typical runs finish in 3–6 rounds with messages of 2–4k tokens, so the history stays well under 50k tokens, and even the theoretical worst case at the caps (24 messages × 8k tokens) fits in a 200k context window. If a smaller-context model is ever needed, a Pydantic AI history processor that keeps only the last two rounds is a few-line fallback: since the PROPOSER restates its full proposal and the CRITIC re-raises every unresolved challenge each turn, nothing older is required.
- The orchestrator separately records the full conversation, including retries, in `chat.json`.

#### Agent 1: PROPOSER

Receives the original feature request and produces an initial structured interpretation: a proposed scope, a set of assumptions, and at least one success criterion. In subsequent rounds, the PROPOSER responds to each of the CRITIC's challenges by either defending, revising, or conceding it.

- Must surface implicit assumptions in the request, not just restate it.
- Must propose something concrete enough to be challenged.
- Cannot simply agree with the CRITIC on everything - it must maintain position until given a convincing reason to change it.
- Concedes or revises only when a challenge shows a concrete failure scenario, a contradiction with the feature request or system description, or a missing constraint. Otherwise, it defends its position with a counter-argument. A revision must change the proposal, not just adopt the CRITIC's wording.
- When a challenge hinges on information that is in neither the system description nor the feature request (e.g., what "right person" means), the PROPOSER resolves it by revising the proposal with a specific open question plus a stated default that holds until the question is answered. This counts as a revision. Open questions must not be used to dodge decisions the provided context allows the PROPOSER to make.
- When it concedes or revises, it records the outcome as a key decision (what was agreed or rejected, and why), so dropped or changed items stay visible in the decision log.

Each turn, the PROPOSER returns a structured output containing:

- **Replies:** for each CRITIC challenge (by ID), whether it was defended, revised, or conceded, and why (empty in the first turn).
- **Full current proposal:** scope (in/out), assumptions, key decisions (agreed and rejected, each with a one-sentence reason), success criteria (at least one), and open questions. Each item has a stable ID (e.g., S1, A1, D1, SC1, Q1) that is kept across revisions.
- **Confidence:** its confidence (0–100) in the current proposal, with a one-sentence rationale.

The output must be consistent: every challenge in the latest CRITIC review has exactly one reply, replies reference only existing challenge IDs, item IDs are unique, and at least one success criterion exists. This is enforced by the schema and a Pydantic AI output validator, which raises `ModelRetry` otherwise, so a challenge can never be silently ignored.

#### Agent 2: CRITIC

Reviews the PROPOSER's latest proposal and generates targeted challenges. These may be questions about missing constraints, objections to assumptions, or identification of scope risks. The CRITIC's goal is to make the proposal more robust - not to reject it entirely.

- Challenges must be specific - generic pushback like "this is too vague" is not sufficient.
- Must signal when it is satisfied and ready to conclude, to avoid infinite loops.
- Cannot declare satisfaction in round 1 - a minimum of 2 rounds of back-and-forth is required.
- Accepts a well-formed open question with a stated default as resolving a challenge about facts outside the provided context, but rejects open questions that defer decisions the context allows the PROPOSER to make.

In a single LLM call, the CRITIC checks each section of the latest proposal (scope, assumptions, success criteria, open questions) on three dimensions:

- **Completeness:** Nothing is missing. The scope (in/out) covers all aspects of the feature request, given the system description. Assumptions address all identified gaps. Success criteria cover all reasonable conditions for success ("If all given success criteria are met, could the implementation still be considered unsuccessful due to some obvious, but unlisted condition?"). Each ambiguity that requires external input is covered by a specific open question.
- **Soundness:** Nothing is irrelevant. Each scope item is relevant to the feature request. No verified claims are wrongly marked as assumptions. Each success criterion is implicitly or explicitly required ("If this condition is NOT met, could the implementation still be considered objectively successful?"). No open question can be answered by the provided context.
- **Quality:** All items are concrete and well defined. No tautology, no vagueness. Success criteria are measurable. For each open question, a human can clearly understand what information or decision needs to be provided.

It also checks:

- **Decision Log Alignment:** The CRITIC agrees with each key decision - verifying what was agreed and rejected.
- **Challenge Coverage:** Each of the PROPOSER's replies actually addresses its challenge (the orchestrator guarantees that a reply exists). After a defense, the CRITIC either accepts it (resolving the challenge), escalates with a new concrete failure scenario, or asks for the point to become an open question. It does not repeat the same challenge.

The CRITIC returns a structured output containing:

- **Challenges:** a list of significant, specific challenges. Each has a stable ID (e.g., C1), the ID of the proposal item it targets (or the section, for missing items), a severity (minor, major, blocking), the concrete risk or failure scenario, and a question or suggested resolution. Unresolved challenges are re-raised with the same ID. At most 5 new challenges are raised per round, most severe first.
- **Resolved:** the IDs of previously open challenges closed in this round, each with how it closed: defense accepted, revision adequate, or converted to an open question.
- **Satisfied:** an explicit boolean signal that the CRITIC is satisfied and ready to conclude, with a short justification.
- **Confidence:** its confidence (0–100) in the current proposal, with a one-sentence rationale.

Severity follows a fixed rubric, stored in `prompts/`: blocking - the feature cannot be implemented or would clearly fail as proposed; major - a gap likely to lead to a wrong implementation; minor - an improvement that does not change what gets built. Since minor challenges never block convergence, the CRITIC cannot hold up a sound proposal over details.

The output must be consistent:

- `satisfied` is `true` if and only if no blocking or major challenges remain (minor ones may remain as reservations).
- In round 1, `satisfied` is `false` and at least one major or blocking challenge is raised.
- Every challenge targets an existing proposal item ID or section.
- Every challenge open in the previous round appears either re-raised or in the resolved list, so no challenge disappears silently.

This is enforced by a Pydantic AI output validator, which raises `ModelRetry` on inconsistent output.

#### Confidence & Disagreement Scoring

After each round, the orchestrator computes the following metrics deterministically from the agents' structured outputs, with no extra LLM calls:

- **Disagreement:** the sum of the severity weights of the CRITIC's challenges in that round (minor = 1, major = 3, blocking = 5). Since the CRITIC re-raises every unresolved challenge, this covers all open disagreement.
- **Disagreement Delta:** the change in disagreement since the previous round.
- **Confidence Gap:** the absolute difference between the PROPOSER's and the CRITIC's confidence.
- **Reply Breakdown:** the number of challenges the PROPOSER defended, revised, and conceded in that round. This shows whether the PROPOSER holds its positions or simply gives in.
- **Resolved:** the number of challenges the CRITIC closed in that round.

Confidence is self-reported, and LLM self-assessments are poorly calibrated. To keep scores comparable across turns and models, both agents use the same rubric, stored in `prompts/` (90–100: implementable as-is, 70–89: minor gaps, 40–69: major gaps, below 40: fundamentally unclear). The confidence scores are informational only and never decide termination. Each agent's last stated confidence is its confidence in the final agreed scope.

#### Agent 3: SUMMARIZER

Writes the narrative parts of the decision document in a single LLM call: the overview, and a short rationale for each key decision that references the challenges and round that led to it.

Its input is a condensed transcript that the orchestrator builds from the structured outputs: the inputs; then, per round, the CRITIC's challenges and resolved list and the PROPOSER's replies; then the final proposal, the termination reason, and the CRITIC's final justification. The restated proposals are the only redundancy in the full conversation, so keeping only the final one loses nothing.

IMPORTANT: The orchestrator copies the scope, assumptions, key decisions, success criteria, and open questions verbatim from the PROPOSER's final proposal into the decision document, and computes the assumption status, the unresolved disagreements, and the metrics deterministically. The SUMMARIZER cannot change or omit any of them; it only adds the overview and the decision rationales.

### Flow

A **round** is one PROPOSER turn followed by one CRITIC review.

1. Load the system description and feature request from CLI arguments, files, or env variables. Enforce the length cap and strip control characters.
2. The PROPOSER produces the initial proposal.
3. The CRITIC reviews the latest proposal and returns its challenges, its resolved list, and its explicit satisfaction signal. Each CRITIC prompt states the current round and the minimum and maximum number of rounds.
4. The PROPOSER replies to the challenges and restates the updated proposal. Steps 3 and 4 repeat until one of the stopping criteria is met. The criteria are checked after each CRITIC review, so the final proposal has always been reviewed:

    - **Minimum 2 Rounds:** In round 1, the CRITIC must not signal satisfaction and must raise at least one major or blocking challenge. This is a hard rule, checked by deterministic logic in the CRITIC's output validator. If violated, the validator raises `ModelRetry` with an instruction stating exactly this. This is retried 3 times. If the CRITIC still violates the rule after the 3rd try, the process stops with an error.
    - **Converged:** From the second round onward, the discussion is over as soon as the CRITIC explicitly signals satisfaction, i.e., no blocking or major challenges remain.
    - **Stalled:** The lowest disagreement so far has not improved for **3** consecutive rounds, e.g., because the agents are deadlocked on the same challenges. The conversation finishes with a clear WARNING "Deliberation Stalled".
    - **Max Rounds:** The conversation does not exceed **12** rounds. If the limit is reached, the conversation finishes with a clear WARNING "Limit of 12 Rounds Reached". This is a hard safety net, enforced by deterministic logic.

    Both stalled and max rounds thresholds are configurable via env variables. In all cases, except on error, the decision document is produced, including the termination reason (converged, stalled, or max rounds). On error, the run exits non-zero and `chat.json` is preserved.

    Rationale: Converged is the intended way to finish. An empty challenge list as the condition for being done would rarely be met, so runs would drift to the round limit. The severity threshold defines "done" as "no major gaps left". The open-question rule is what lets genuinely ambiguous points converge instead of stalling: a disagreement that depends on information neither agent has is turned into a question for humans, which is exactly what the decision document is for. Stalled and Max Rounds only catch edge cases: the stall rule tolerates temporary setbacks (e.g., new challenges after a revision) and only fires on a sustained lack of progress, and the round limit is a last-resort safety net. In both cases, the remaining tension is surfaced instead of hidden.

5. Once the conversation finishes, the orchestrator assembles the **decision document** and the SUMMARIZER adds the narrative parts. The document is saved as JSON, and the Markdown is rendered deterministically from the JSON (no second LLM call). The document contains the following sections:

    - **Header:** the feature request, the system description, the models used, the number of rounds, the termination reason, and the timestamp. Filled in by the orchestrator.
    - **Overview:** short executive summary of the deliberation conclusion, including how it ended (the termination reason, with the CRITIC's satisfaction justification or the warning). Written by the SUMMARIZER.
    - **Scope:** what the feature will and will not include. Copied verbatim from the final proposal.
    - **Assumptions:** what was assumed and whether it was challenged or accepted. Copied verbatim from the final proposal; the status is computed by the orchestrator: accepted (never challenged), or challenged with the PROPOSER's last reply to the challenge (defended / revised / conceded).
    - **Decision Log:** each key decision made by the agents - what was agreed and what was rejected. Copied verbatim from the final proposal, each with the SUMMARIZER's rationale.
    - **Success Criteria:** what conditions would indicate the feature is implemented successfully. Copied verbatim from the final proposal.
    - **Open Questions:** what remains unresolved and requires human input. Copied verbatim from the final proposal.
    - **Unresolved Disagreements:** the challenges still open at termination (minor reservations if converged), with the PROPOSER's latest reply to each, or "raised in the final round, no reply". The orchestrator fills this section in from the last CRITIC review.
    - **Deliberation Metrics:** each agent's final confidence and a per-round table of the scoring metrics. The orchestrator fills this section in from `chat.json`, not the SUMMARIZER, so the numbers cannot be misreported.

### Technical Constraints

- Prompts are explicitly separated from code for easier developer access. All prompts are stored in a separate module `prompts/`. No prompts live inside the rest of the codebase, including the `ModelRetry` messages sent back to the agents.
- Feature request and system description text is limited to 10,000 characters each. Control characters are stripped; there is no other cleaning. In the prompts, the inputs are wrapped in clear delimiters and the agents are instructed to treat them as data, not as instructions.
- Agent responses are capped at 8,000 tokens by default, configurable via an env variable. A full restated proposal with replies in JSON can exceed 4,000 tokens, and truncated JSON fails every retry identically. For reasoning models, some providers count reasoning tokens against this cap.
- All LLM calls are strictly sequential.
- Each session produces a set of files in `runs/<timestamp>/`:
  - Final decision document (`.md`, `.json`)
  - Full conversation history (`chat.json`) including internal agent steps and answers. It is written after every turn, so a failed or interrupted run still leaves a trace.
  - Rendered console output (`transcript.txt`).

### Testing

The termination paths (round-1 retry, converged, stalled, max rounds), both output validators, the metrics, and the deterministic document assembly (verbatim copy, assumption status, unresolved disagreements) are covered by tests that script the agent outputs with Pydantic AI's `FunctionModel`, without API calls.
