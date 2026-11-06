# Deliberation Engine System Design Document

## Requirements & Scope

- The user can clearly follow and inspect the agent communication.
- Multi-round agent communication: At least 2 rounds.
- Structured final output in the form of a decision document.
- Graceful termination: The system must have a defined, intentional termination condition, not a hardcoded round limit alone. The CRITIC must be capable of signaling that deliberation is complete.
- Configurable input: The system description and feature request should be injectable via an environment variable or CLI.
- Prompt transparency: Agent prompts should be clearly separated from the orchestration logic for easier developer review.
- Confidence or disagreement scoring: Have each agent score its own confidence in the final agreed scope, or produce a disagreement delta between their positions at each round. This surfaces tension explicitly rather than burying it in prose.

### Out-of-Scope

- Graphical user interface, beyond the simple CLI.

## Assumptions

- This is a general purpose (not domain specialized) deliberation engine, capable of working on any system and feature request, that can be described in free text within reasonable string limits.

## Architecture

All code is contained in a single Python package `deliberation_engine`. The package follows [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html) principles.

### Interface

This is a CLI tool. The engine starts with `deliberation-engine start [-s/--system "<plain text description of the target system for the request>"] [-r/--request "<feature request in plain text>"]`. The deliberation process starts immediately if the system description and feature request are provided. Otherwise, the user is first prompted to provide the system description and feature request in plain text when the command is called. Those parameters can also be provided via env variables: `DE_SYSTEM_DESCRIPTION` and `DE_FEATURE_REQUEST` (e.g., loaded from `.env`).

The conversation flow is clearly formatted so the user can clearly see what each agent is saying. Each message has visible metadata (timestamp, agent name, agent-specific color marks surrounding the message to clearly distinguish it from the rest of the conversation). Internal agent steps like tool calls and reasoning are hidden by default, but can be expanded if the user clicks on a summary line of those internal steps (can be collapsed back afterwards, when the user clicks again on them). The thinking process and active work is clearly indicated and animated so the user knows that the process is active. Each CRITIC message clearly shows its satisfaction signal (satisfied / not satisfied).

The final result is clearly shown to the user with distinguishable formatting and colors. It states a short summary of the decision document and the path to the produced full decision document. It also states the number of tokens used, total cost, and total processing time (also, each metric broken down by agent).

### Agents

We use Pydantic AI for agent orchestration. We use OpenRouter as LLM provider, in order to switch easily between different families of models. The base model for each agent is provided as an env variable for easy configuration.

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

Each turn, the PROPOSER returns a structured output containing:

- **Replies:** for each CRITIC challenge, whether it was defended, revised, or conceded, and why (empty in the first turn).
- **Full current proposal:** scope (in/out), assumptions, key decisions (agreed and rejected), success criteria (at least one), and open questions.

#### Agent 2: CRITIC

Reviews the PROPOSER's latest proposal and generates targeted challenges. These may be questions about missing constraints, objections to assumptions, or identification of scope risks. The CRITIC's goal is to make the proposal more robust - not to reject it entirely.

- Challenges must be specific - generic pushback like "this is too vague" is not sufficient.
- Must signal when it is satisfied and ready to conclude, to avoid infinite loops.
- Cannot declare satisfaction in round 1 - a minimum of 2 rounds of back-and-forth is required.

In a single LLM call, the CRITIC checks the latest proposal against the following criteria:

- **Scope Completeness:** The scope (in/out) clearly satisfies all aspects of the feature request, given the system description (i.e., no scope items are missing).
- **Scope Soundness:** Each scope item (in/out) is clearly relevant to the feature request. There are no irrelevant scope items.
- **Scope Quality:** All scope items are concrete and well defined. No tautology.
- **Assumption Completeness:** Assumptions address all identified gaps, derived from the feature request, given the system description.
- **Assumption Soundness:** There are no irrelevant assumptions or verified claims that are wrongly marked as assumptions.
- **Assumption Quality:** All assumptions are concrete and well defined. No tautology.
- **Success Criteria Completeness:** All reasonable conditions that could decide if the feature implementation is successful are listed. Question to ask: "If all given success criteria are met, could the implementation still be considered unsuccessful due to some obvious, but unlisted condition?".
- **Success Criteria Soundness:** Each condition item is implicitly or explicitly required for the feature request, given the provided system description. Question to ask: "If this condition is NOT met, could the implementation still be considered objectively successful?".
- **Success Criteria Quality:** All success criteria are concrete, measurable, and well defined. No tautology. No vagueness.
- **Open Question Completeness:** Each identified ambiguity that requires external input is covered by a specific open question.
- **Open Question Soundness:** No noted open question can be answered by the provided context. Each open question can only be decided by external information that is currently missing.
- **Open Question Quality:** All open questions are concrete and well defined. No tautology. A human can understand clearly what additional information or decision needs to be provided.
- **Decision Log Alignment:** The CRITIC agrees with each key decision - verifying what was agreed and rejected.
- **Challenge Coverage:** All of the CRITIC's previous challenges have been addressed by the PROPOSER.

The CRITIC returns a structured output containing:

- **Challenges:** a list of significant, specific challenges, each referencing the proposal item it targets.
- **Satisfied:** an explicit boolean signal that the CRITIC is satisfied and ready to conclude, with a short justification.

The output must be consistent: `satisfied` is `true` if and only if the challenge list is empty. This is enforced by a Pydantic AI output validator, which raises `ModelRetry` on inconsistent output.

#### Agent 3: SUMMARIZER

Produces the final decision document from the full conversation in `chat.json`. Since the conversation may not fit into a single LLM call and no information may be missed, it uses Map-Reduce:

1. **Split:** The conversation is split at message boundaries into chunks of at most N tokens. Consecutive chunks overlap by K messages, so no information is lost at cut points. N and K are configurable via env variables.
2. **Map:** Each chunk is turned into a partial decision document (same schema, without overview) by a separate LLM call. All map calls run in parallel.
3. **Reduce:** Neighbouring partial documents are merged pairwise and in parallel, preserving chronological order: later states supersede earlier ones, and duplicates from the overlaps are merged. This repeats until a single document remains.
4. **Finalize:** The merged document is reconciled with the PROPOSER's last proposal, which is authoritative for the final scope, assumptions, success criteria, and open questions, and an overview is added.

Pydantic AI has no built-in map-reduce helper, so the map and reduce steps are plain `agent.run` calls combined with `asyncio.gather`.

IMPORTANT: The SUMMARIZER does NOT change or omit any item of the PROPOSER's final proposal. It adds the decision log history (what was agreed, rejected, and why), the challenge status of each assumption, and the overview.

### Flow

1. Obtain system description and feature request as free text from the user (if not already provided).
2. The PROPOSER produces the initial proposal.
3. The CRITIC reviews the latest proposal and returns its challenges and its explicit satisfaction signal.
4. The PROPOSER replies to the challenges and restates the updated proposal. Steps 3 and 4 repeat until one of the stopping criteria is met:

    - **Minimum 2 Rounds:** The CRITIC must not signal satisfaction in the first round. This is a hard rule, checked by deterministic logic in the CRITIC's output validator. If the CRITIC signals satisfaction in the first round, the validator raises `ModelRetry` with an instruction emphasizing it must raise at least one challenge. This is retried 5 times. If after the 5th try the CRITIC still signals satisfaction, the process stops with an error.
    - **CRITIC Satisfied:** From the second round onward, the discussion is over as soon as the CRITIC explicitly signals satisfaction.
    - **Max Turns:** The conversation does not exceed **30** PROPOSER-CRITIC conversation turns. If the limit is reached, the conversation finishes with a clear WARNING "Limit of 30 Conversation Turns Reached". The SUMMARIZER is still invoked and the final decision file is produced, but it should include a warning that the deliberation process finished after reaching the maximum number of turns. This is a hard condition, enforced by deterministic logic.

5. Once the conversation finishes, the SUMMARIZER produces the **decision document** as equivalent Markdown and JSON files. The final document contains the following sections:

    - **Overview:** short executive summary of the deliberation conclusion, including how it ended (the CRITIC's satisfaction justification or the max turns warning)
    - **Scope:** what the feature will and will not include
    - **Assumptions:** what was assumed and whether it was challenged or accepted
    - **Decision Log:** each key decision made by the agents - what was agreed and what was rejected
    - **Success Criteria:** what conditions would indicate the feature is implemented successfully
    - **Open Questions:** what remains unresolved and requires human input

### Technical Constraints

- Prompts are explicitly separated from code for easier developer access. All prompts are stored in a separate module `prompts/`. No prompts live inside the rest of the codebase.
- Feature request and system description text is limited to 10,000 characters and may NOT contain any kind of special formatting like markdown, HTML, XML, etc. Such special formatting will be cleaned before ingestion in the LLM context.
- Agent responses can be at most 4,000 tokens.
- PROPOSER and CRITIC turns are strictly sequential. Only the SUMMARIZER's map and reduce calls run in parallel.
- Each session produces a set of files:
  - Final decision document (`.md`, `.json`)
  - Full conversation history (`chat.json`) including internal agent steps and answers.
