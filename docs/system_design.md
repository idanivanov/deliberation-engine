# Deliberation Engine System Design Document

## Requirements & Scope

### Out-of-Scope

- Graphical user interface, beyond the simple CLI.

## Assumptions

## Architecture

All code is contained in a single Python package `deliberation_engine`. The package follows [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html) principles.

### Interface

This is a CLI tool. The engine starts with `deliberation-engine start [-s/--system "<plain text description of the target system for the request>"] [-r/--request "<feature request in plain text>"]`. The deliberation process starts immediately if the system description and feature request are provided. Otherwise, the user is first prompted to provide the system description and feature request in plain text when the command is called. Those parameters can also be provided via env variables: `DE_SYSTEM_DESCRIPTION` and `DE_FEATURE_REQUEST` (e.g., loaded from `.env`).

The conversation flow is clearly formatted so the user can clearly see what each agent is saying. Each message has visible metadata (timestamp, agent name, agent-specific color marks surrounding the message to clearly distingquish it from the rest of the conversation). Internal agent steps like tool calls and reasoning are hidden by default, but can be expanded if the user clicks on a summary line of those internal steps (can be collapsed back afterwards, when the user clicks again on them). The thinking process and active work is clearly indicated and animated so the user knows that the process is active.

The present contents of the **scratchpad** are always visible to the user on a dedicated panel next to the conversation. The contents displayed are always up-to-date with the file.

The final result is clearly shown to the user with distunguishable formatting and colors. It states a short summary of the decision document and the path to the produced full decision document. It also states the number of tokens used, total cost, and total processing time (also, each metric broken down by agent).

### Agents

We use Pydantic AI for agent orchestration. We use OpenRouter as LLM provider, in order to switch easily between different families of models. The base model for each agent is provided as an env variable for easy configuration.

#### Agent 1: PROPOSER

Receives the original feature request and produces an initial structured interpretation: a proposed scope, a set of assumptions, and at least one success criterion. In subsequent rounds, the PROPOSER responds to the CRITIC's challenges by either defending, revising, or conceding points.

- Must surface implicit assumptions in the request, not just restate it.
- Must propose something concrete enough to be challenged.
- Cannot simply agree with the CRITIC on everything - it must maintain position until given a convincing reason to change it.

The context of the **PROPOSER** agent contains the following information:

- Agent system instructions
- Last 3 full messeges from the conversation (we sacrifice old messages to avoid context explosion).
- The full contents of the current version of the scratchpad.
- The plain feature request and system description

#### Agent 2: CRITIC

Reviews the PROPOSER's output and generates targeted challenges. These may be questions about missing constraints, objections to assumptions, or identification of scope risks. The CRITIC's goal is to make the proposal more robust - not to reject it entirely.

- Challenges must be specific - generic pushback like "this is too vague" is not sufficient.
- Must signal when it is satisfied and ready to conclude, to avoid infinite loops.
- Cannot declare satisfaction in round 1 - a minimum of 2 rounds of back-and-forth is required.

The context of the **CRITIC** agent contains the following information:

- Agent system instructions
- Last 3 full messeges from the conversation (we sacrifice old messages to avoid context explosion).
- The full contents of the current version of the scratchpad.
- The plain feature request and system description

The CRITIC checks for the following conditions when reviewing the PROPOSER's out at each turn:

- **Scope Completeness:** The scope (in/out) clearly satisfies all aspects of the feature request, given the system description (i.e., no scope items are missing).
- **Scope Soundness:** Each scope item (in/out) is clearly relevant to the feature request. There are no irrelevant scope items.
- **Scope Quality:** All scope items are concrete and well defined.  No tautology.
- **Assumtion Completess:** Assumptions address all identified gaps, derived from the feature request, given the system description.
- **Assumption Soundness:** There are no irrelevant assumptions or verified claims that are wrongly marked as assumtions.
- **Assumption Quality:** All assumptions are concrete and well defined. No tautology.
- **Success Criteria Completeness:** All reasonable conditions that could decide if the feature implementation is successful are listed. Question to ask: "If all given success criteria are met, could the implementation still be considered unsucessful due to some obvious, but unlisted condition?".
- **Success Criteria Soundness:** Each condition item is implicitly or explicitly required for the feature request, given the provided system description. Question to ask: "If this condition is NOT met, could the implementation still be considered objectivelly successful?".
- **Success Criteria Quality:** All sucess criteria are concrete, measurable, and well defined. No tautology. No vagueness.
- **Open Question Completeness:** Each identified key decision that requires external input is covered by a specific open question.
- **Open Question Soundness:** No noted open question can be answered by the provided context. Each open question can only be decided by external information that is currently missing.
- **Open Question Quality:** All open questions are concrete and well defined. No tautology. A human can understand clearly what additional information or decision needs to be provided.
- **Decision Log Alignment:** The CRITIC agrees with each key decision in the decision log - verifying what was agreed and rejected.
- **Challenge Coverage:** All CRITIC challenges, identified gaps, and clarification requests have been addressed by the PROPOSER.

The CRITIC agent runs separate LLM calls for each of these non-deterministic checks, so that each criteria can be processed more reliably without context contamination. It then summarizes the results from each check in a final answer to the PROPOSER by writing in the scratchpad and answering in the chat. The summarization step of the CRITIC decides which comments to include or ignore. If it decides that some comments are not significant, it can skip them. If no comments are judged as significant, the CRITIC is satisfied with the PROPOSER's output.

#### Agent 3: SUMMARIZER

Produces the final decision document.

The context of the **SUMMARIZER** agent contains the following information:

- Agent system instructions
- The full contents of the current version of the scratchpad.
- The plain feature request and system description

#### Scratchpad

All agents have shared access to a **scratchpad** - a text file that each agent can read and write to using a dedicated tool. This file is used to keep the current state of the scope, assumptions, key decisions, open questions, clarifications, challenges, etc. For example, if a scope item has been wrongly included and successfully challenged, it should be promptly removed from the scratchpad. Likewise, when a challenge is adressed succesfully, the chalenge is no longer needed in the scratchpad and should also be removed. By the end, the CRITIC should be able to decide if the process is complete, when it is satisfied with the contents of the scratchpad. Each agent who writes to the scratchpad has the responsibility to keep its contents within the prescribed limits.

### Flow

The deliberation engine process flow consists of the following steps:

1. Obtain system description and feature request as free text from the user (if not already provided).
2. The PROPOSER agent produces the inital tructured interpretation based on the provided input containing:

    - scope
    - assumptions
    - key decisions
    - success criteria
    - open questions

3. The CRITIC reviews the PROPOSER's outputs, challenges its scope, assumptions, decisions, and open questions, searches for gaps, and asks for clarifications. This is done by adding notes to the scratchpad and writing in the chat. The CRITIC does not edit the PROPOSER's items directly, but only comments on them.
4. The conversation between the CRITIC and the PROPOSER (steps 2 and 3) continues until the CRITIC is satisfied with the state of the scratchpad or a fixed number of turns is reached, according to the stopping criteria:

- **Minimum 2 Rounds:** The CRITIC must not agree on the first round. It can declare satisfaction only starting from the second round onward. This is a hard rule and deterministic logic checks if it is followed. If the agent declares satisfaction on the first round, it is re-invoked with an instruction emphasizing it must raise at least one challenge in the first round.
- **Has Comments:** The CRITIC has comments that need to be addressed by the PROPOSER. If the CRITIC produces no comments at a particular turn, this means it is satisfied with the PROPOSER's outputs and the discussion is over.
- **Max Turns:** The conversation does not exceed **30** PROPOSER-CRITIC conversation turns. If the limit is reached, the conversation finishes with a clear WARNING "Limit of 30 Conversation Turns Reached". This is a hard condition, enforced by deterministic logic.

5. Once the conversation finishes, the SUMARIZER agent takes the scratchpad and produces a well formatted **decision document** as equivalent Markdown and JSON files. The final document contains the following sections:

    - **Overview:** short executive summary of the deliberation conclusion
    - **Scope:** what the feature will and will not include
    - **Assumptions:** what was assumed and whether it was challenged or accepted
    - **Decision Log:** each key decision made by the agents - what was agreed and what was rejected
    - **Success Criteria:** what conditions would indicate the feature is imlemented successfully
    - **Open Questions:** what remains unresolved and requires human input

### Technical Details

- Promts are explicitly separated from code for easier developer access. All prompts are stored in a separate module `prompts/`. No prompts live inside the rest of the codebase.
- Feature request and system description text is limited to 10,000 characters and may NOT contain any kind of special formatting like markdown, HTML, XML, etc. Such special formatting will be cleaned before ingestions in the LLM context.
- Scratchpad size must not exceed 50,000 characters. If an agent tries to make the file longer by writing to it, a hard rule in the system prevents it and asks the agent to reduce the length in order to fit into the limits. The agent has at most 5 consecutive attempts to do that, otherwise its write action will be canceled and the agent will have to explain what it tried to do in the conversation, after failing to write in the scratchpad.
- Agent responses in the chat can be at most 2,000 tokens.
- At any point in time there is **only one active agent**. This is the simplest way we avoid concurrence conflicts when reading and editing the scratchpad.
- Each session produces a set of files:
  - Final decision document (`.md`, `.json`)
  - Scrarchpad varsion history (`scratchpad/scratchpad_vXXX_agent_YYYY.txt`, where `XXX` is the consecutive version number - 001, 002, etc. - and Y is the agent name - `PROPOSER`, `CRITIC`)
  - Full conversation history (`chat.json`)
