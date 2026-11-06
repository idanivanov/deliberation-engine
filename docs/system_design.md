# Deliberation Engine System Design Document

## Requirements & Scope

- The user can clearly follow and inspect the agent communication.
- Multi-round agent communication: At least 2 rounds.
- Structured final output in the form of a decision document.
- Graceful termination: The system must have a defined, intentional termination condition, not a hardcoded round limit alone. The CRITIC must be capable of signaling that deliberation is complete. The termination strategy is documented and justified.
- Configurable input: The system description and feature request should be injectable via an environment variable, file, or CLI.
- Prompt transparency: Agent prompts should be clearly separated from the orchestration logic for easier developer review.
- Confidence or disagreement scoring: Have each agent score its own confidence in the final agreed scope, or produce a disagreement delta between their positions at each round. This surfaces tension explicitly rather than burying it in prose.
- Runnable with a single command.
- Simplicity: A small, focused solution with reasonable token usage is preferred over an elaborate one. Framework choices are justified.

### Out-of-Scope

- Graphical user interface, beyond the simple CLI.
- Persistence beyond the session's output files (no database), API server, or multi-user support.
- Agent tools or retrieval: the agents only see the provided system description and feature request.
- Human input during the deliberation: open questions are collected in the decision document instead.

## Assumptions

- This is a general purpose (not domain specialized) deliberation engine, capable of working on any system and feature request, that can be described in free text within reasonable string limits.

## Architecture

All code is contained in a single, flat Python package `deliberation_engine` with clearly separated concerns. The orchestrator emits events that the CLI renders, so the deliberation loop does not depend on the terminal output and can be tested on its own.

### Interface

This is a CLI tool, started with a single command: `uv run deliberation-engine [-s/--system "<plain text description of the target system for the request>"] [-r/--request "<feature request in plain text>"]`. Alternatively, each input can be read from a file (`--system-file`, `--request-file`) or provided via env variables: `DE_SYSTEM_DESCRIPTION` and `DE_FEATURE_REQUEST` (e.g., loaded from `.env`). The deliberation process starts immediately if the system description and feature request are provided. Otherwise, the user is first prompted to provide them in plain text.

The conversation flow is clearly formatted so the user can clearly see what each agent is saying. Each message has visible metadata (timestamp, agent name, agent-specific color marks surrounding the message to clearly distinguish it from the rest of the conversation). Internal agent steps like reasoning are hidden by default and shown with `-v/--verbose`. They are always recorded in `chat.json`. The thinking process and active work is clearly indicated and animated so the user knows that the process is active. Each CRITIC message clearly shows its satisfaction signal (satisfied / not satisfied). Each PROPOSER and CRITIC message shows the agent's confidence. After each round, a single line shows the round's disagreement, disagreement delta, confidence gap, and the PROPOSER's reply breakdown (defended / revised / conceded).

The final result is clearly shown to the user with distinguishable formatting and colors. It states the termination reason, a short summary of the decision document, and the path to the produced full decision document. It also states the number of tokens used, total cost (as reported by OpenRouter), and total processing time (also, each metric broken down by agent).

### Agents

We use Pydantic AI for agent orchestration, because its typed structured outputs and output validators (raising `ModelRetry`) are how the termination and consistency rules are enforced. We use OpenRouter as LLM provider, in order to switch easily between different families of models. The base model for each agent is provided as an env variable for easy configuration. By default, the PROPOSER and the CRITIC use different model families, since a model tends to agree with its own reasoning.

#### Conversation State & Context Management

For simplicity, the conversation history is the only state shared between the PROPOSER and the CRITIC.

- Each agent keeps its own Pydantic AI message history: its own replies are assistant messages, and the other agent's latest reply is passed as the next user prompt. The history returned by each run is passed back as `message_history` to the agent's next run.
- The feature request and system description are part of each agent's instructions, so they are always in context and never summarized away.
- To keep the context bounded, both agents use `SummarizingCompaction` from `pydantic-ai-harness` (attached via `capabilities=[...]`). Once the history exceeds a token threshold, older messages are replaced by an LLM summary and the most recent messages are kept verbatim. The threshold, the number of kept messages, and the summarizer model are configurable via env variables.
- Since the PROPOSER restates its full proposal every turn, the current proposal is always among the recent messages kept verbatim.
- The orchestrator separately records the full, uncompacted conversation in `chat.json`. This is the input of the SUMMARIZER.

#### Agent 1: PROPOSER

Receives the original feature request and produces an initial structured interpretation: a proposed scope, a set of assumptions, and at least one success criterion. In subsequent rounds, the PROPOSER responds to each of the CRITIC's challenges by either defending, revising, or conceding it.

- Must surface implicit assumptions in the request, not just restate it.
- Must propose something concrete enough to be challenged.
- Cannot simply agree with the CRITIC on everything - it must maintain position until given a convincing reason to change it.
- Concedes or revises only when a challenge shows a concrete failure scenario, a contradiction with the feature request or system description, or a missing constraint. Otherwise, it defends its position with a counter-argument. A revision must change the proposal, not just adopt the CRITIC's wording.

Each turn, the PROPOSER returns a structured output containing:

- **Replies:** for each CRITIC challenge (by ID), whether it was defended, revised, or conceded, and why (empty in the first turn).
- **Full current proposal:** scope (in/out), assumptions, key decisions (agreed and rejected), success criteria (at least one), and open questions. Each item has a stable ID (e.g., S1, A1, D1, SC1, Q1) that is kept across revisions.
- **Confidence:** its confidence (0–100) in the current proposal, with a one-sentence rationale.

#### Agent 2: CRITIC

Reviews the PROPOSER's latest proposal and generates targeted challenges. These may be questions about missing constraints, objections to assumptions, or identification of scope risks. The CRITIC's goal is to make the proposal more robust - not to reject it entirely.

- Challenges must be specific - generic pushback like "this is too vague" is not sufficient.
- Must signal when it is satisfied and ready to conclude, to avoid infinite loops.
- Cannot declare satisfaction in round 1 - a minimum of 2 rounds of back-and-forth is required.

In a single LLM call, the CRITIC checks each section of the latest proposal (scope, assumptions, success criteria, open questions) on three dimensions:

- **Completeness:** Nothing is missing. The scope (in/out) covers all aspects of the feature request, given the system description. Assumptions address all identified gaps. Success criteria cover all reasonable conditions for success ("If all given success criteria are met, could the implementation still be considered unsuccessful due to some obvious, but unlisted condition?"). Each ambiguity that requires external input is covered by a specific open question.
- **Soundness:** Nothing is irrelevant. Each scope item is relevant to the feature request. No verified claims are wrongly marked as assumptions. Each success criterion is implicitly or explicitly required ("If this condition is NOT met, could the implementation still be considered objectively successful?"). No open question can be answered by the provided context.
- **Quality:** All items are concrete and well defined. No tautology, no vagueness. Success criteria are measurable. For each open question, a human can clearly understand what information or decision needs to be provided.

It also checks:

- **Decision Log Alignment:** The CRITIC agrees with each key decision - verifying what was agreed and rejected.
- **Challenge Coverage:** All of the CRITIC's previous challenges have been addressed by the PROPOSER. When re-raising a challenge the PROPOSER defended, the CRITIC responds to the defense instead of repeating the challenge.

The CRITIC returns a structured output containing:

- **Challenges:** a list of significant, specific challenges. Each has a stable ID (e.g., C1), the ID of the proposal item it targets (or the section, for missing items), a severity (minor, major, blocking), the concrete risk or failure scenario, and a question or suggested resolution. Unresolved challenges are re-raised with the same ID. At most 5 new challenges are raised per round, most severe first.
- **Satisfied:** an explicit boolean signal that the CRITIC is satisfied and ready to conclude, with a short justification.
- **Confidence:** its confidence (0–100) in the current proposal, with a one-sentence rationale.

The output must be consistent: `satisfied` is `true` if and only if no blocking or major challenges remain (minor ones may remain as reservations), a satisfied CRITIC must state a confidence of at least 70, and every challenge must target an existing proposal item ID or section. This is enforced by a Pydantic AI output validator, which raises `ModelRetry` on inconsistent output.

#### Confidence & Disagreement Scoring

After each round, the orchestrator computes the following metrics deterministically from the agents' structured outputs, with no extra LLM calls:

- **Disagreement:** the sum of the severity weights of the CRITIC's challenges in that round (minor = 1, major = 3, blocking = 5). Since the CRITIC re-raises every unresolved challenge, this covers all open disagreement.
- **Disagreement Delta:** the change in disagreement since the previous round.
- **Confidence Gap:** the absolute difference between the PROPOSER's and the CRITIC's confidence.
- **Reply Breakdown:** the number of challenges the PROPOSER defended, revised, and conceded in that round. This shows whether the PROPOSER holds its positions or simply gives in.

Confidence is self-reported, and LLM self-assessments are poorly calibrated. To keep scores comparable across turns and models, both agents use the same rubric, stored in `prompts/` (90–100: implementable as-is, 70–89: minor gaps, 40–69: major gaps, below 40: fundamentally unclear). The confidence scores are informational only and never decide termination. Each agent's last stated confidence is its confidence in the final agreed scope.

#### Agent 3: SUMMARIZER

Produces the final decision document from the full conversation in `chat.json`. Since the conversation may not fit into a single LLM call and no information may be missed, it uses Map-Reduce:

1. **Split:** The conversation is split at message boundaries into chunks of at most N tokens. Consecutive chunks overlap by K messages, so no information is lost at cut points. N and K are configurable via env variables.
2. **Map:** Each chunk is turned into a partial decision document (same schema, without overview) by a separate LLM call. All map calls run in parallel.
3. **Reduce:** Neighbouring partial documents are merged pairwise and in parallel, preserving chronological order: later states supersede earlier ones, and duplicates from the overlaps are merged. This repeats until a single document remains.
4. **Finalize:** The merged document is reconciled with the PROPOSER's last proposal, which is authoritative for the final scope, assumptions, success criteria, and open questions, and an overview is added.

Pydantic AI has no built-in map-reduce helper, so the map and reduce steps are plain `agent.run` calls combined with `asyncio.gather`.

IMPORTANT: The orchestrator copies the scope, assumptions, success criteria, and open questions verbatim from the PROPOSER's final proposal into the decision document, so the SUMMARIZER cannot change or omit any of them. The SUMMARIZER adds the decision log history (what was agreed, rejected, and why), the challenge status of each assumption, and the overview.

### Flow

A **round** is one PROPOSER turn followed by one CRITIC review.

1. Obtain system description and feature request as free text from the user (if not already provided).
2. The PROPOSER produces the initial proposal.
3. The CRITIC reviews the latest proposal and returns its challenges and its explicit satisfaction signal. Each CRITIC prompt states the current round and the minimum and maximum number of rounds.
4. The PROPOSER replies to the challenges and restates the updated proposal. Steps 3 and 4 repeat until one of the stopping criteria is met. The criteria are checked after each CRITIC review, so the final proposal has always been reviewed:

    - **Minimum 2 Rounds:** The CRITIC must not signal satisfaction in the first round and must raise at least one challenge. This is a hard rule, checked by deterministic logic in the CRITIC's output validator. If violated, the validator raises `ModelRetry` with an instruction emphasizing it must raise at least one challenge. This is retried 5 times. If after the 5th try the CRITIC still signals satisfaction, the process stops with an error.
    - **Converged:** From the second round onward, the discussion is over as soon as the CRITIC explicitly signals satisfaction, i.e., no blocking or major challenges remain.
    - **Stalled:** The disagreement has not decreased for 2 consecutive rounds (checked from round 3). The conversation finishes with a clear WARNING "Deliberation Stalled".
    - **Max Rounds:** The conversation does not exceed **8** rounds. If the limit is reached, the conversation finishes with a clear WARNING "Limit of 8 Rounds Reached". This is a hard safety net, enforced by deterministic logic.

    In all cases, except on error, the SUMMARIZER is invoked and the decision document is produced, including the termination reason (converged, stalled, or max rounds). Rationale: an empty challenge list as the condition for being done would rarely be met, so runs would drift to the round limit. The severity threshold defines "done" as "no major gaps left", and the stall rule ends unproductive loops early, surfacing the remaining tension instead of hiding it.

5. Once the conversation finishes, the SUMMARIZER produces the **decision document** as equivalent Markdown and JSON files. The final document contains the following sections:

    - **Overview:** short executive summary of the deliberation conclusion, including how it ended (the termination reason, with the CRITIC's satisfaction justification or the warning)
    - **Scope:** what the feature will and will not include
    - **Assumptions:** what was assumed and whether it was challenged or accepted
    - **Decision Log:** each key decision made by the agents - what was agreed and what was rejected
    - **Success Criteria:** what conditions would indicate the feature is implemented successfully
    - **Open Questions:** what remains unresolved and requires human input
    - **Unresolved Disagreements:** the challenges still open at termination (minor reservations if converged), with the PROPOSER's latest reply to each. The orchestrator fills this section in from the last CRITIC review.
    - **Deliberation Metrics:** each agent's final confidence and a per-round table of the scoring metrics. The orchestrator fills this section in from `chat.json`, not the SUMMARIZER, so the numbers cannot be misreported.

### Technical Constraints

- Prompts are explicitly separated from code for easier developer access. All prompts are stored in a separate module `prompts/`. No prompts live inside the rest of the codebase, including the `ModelRetry` messages sent back to the agents.
- Feature request and system description text is limited to 10,000 characters and may NOT contain any kind of special formatting like markdown, HTML, XML, etc. Such special formatting will be cleaned before ingestion in the LLM context.
- Agent responses can be at most 4,000 tokens.
- PROPOSER and CRITIC turns are strictly sequential. Only the SUMMARIZER's map and reduce calls run in parallel.
- Each session produces a set of files in `runs/<timestamp>/`:
  - Final decision document (`.md`, `.json`)
  - Full conversation history (`chat.json`) including internal agent steps and answers. It is written after every turn, so a failed or interrupted run still leaves a trace.
  - Rendered console output (`transcript.txt`).

### Testing

The termination paths (round-1 retry, converged, stalled, max rounds), the output validators, and the metrics are covered by tests that script the agent outputs with Pydantic AI's `FunctionModel`, without API calls.
