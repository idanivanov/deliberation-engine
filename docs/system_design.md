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

- **Agent 1 - PROPOSER:** Interprets the request and proposes scope and assumptions.
- **Agent 2 - CRITIC:** Challenges assumptions, probes gaps, signals competion.
- **Agent 3 - SUMMARIZER:** Collects all information from the provided input and full conversation between the PROPOSER and CRITIC agents.

All agents have shared access to a **scratchpad** - a text file that each agent can read and write to. This file is used to keep the current state of the scope, assumptions, open questions, clarifications, challenges, etc. For example, if a scope item has been wrongly included and successfully challenged, it should be promptly removed from the scratchpad. Likewise, when a challenge is adressed succesfully, the chalenge is no longer needed in the scratchpad and should also be removed. By the end, the CRITIC should be able to decide if the process is complete, when it is satisfied with the contents of the scratchpad. Each agent who writes to the scratchpad has the responsibility to keep its contents within the prescribed limits.

The context of the **PROPOSER** and **CRITIC** agents contains the following information:

- Agent system instructions
- Last 3 full messeges from the conversation (we sacrifice old messages to avoid context explosion).
- The full contents of the current version of the scratchpad.
- The plain feature request and system description

The context of the **SUMMARIZER** agent contains the following information:

- Agent system instructions
- The full contents of the current version of the scratchpad.
- The plain feature request and system description

### Flow

The deliberation engine process flow consists of the following steps:

1. Obtain system description and feature request as free text from the user (if not already provided).
2. The PROPOSER agent produces the first draft of the scope, assumptions, and open questions based on the provided input and presents them to the chat.
3. The CRITIC reviews the PROPOSER's outputs, challenges its assumptions, searches for gaps, and asks for clarifications. This is done by adding notes to the scratchpad and writing in the chat. The CRITIC can also add open questions to the scratchpad.
4. The conversation between the CRITIC and the PROPOSER (steps 2 and 3) continues until the CRITIC is satisfied with the state of the scratchpad or a fixed number of turns is reached. STOPPING CRITERIA:

    - **Scope Completeness:** The scope (in/out) clearly satisfies all aspects of the feature request, given the system description (i.e., no scope items are missing).
    - **Scope Soundness:** Each scope item (in/out) is clearly relevant to the feature request. There are no irrelevant scope items.
    - **Scope Quality:** All scope items are concrete and well defined.  No tautology.
    - **Assumtion Completess:** Assumptions address all identified gaps, derived from the feature request, given the system description.
    - **Assumption Soundness:** There are no irrelevant assumptions or verified claims that are wrongly marked as assumtions.
    - **Assumption Quality:** All assumptions are concrete and well defined. No tautology.
    - **Open Question Completeness:** Each identified key decision that requires external input is covered by a specific open question.
    - **Open Question Soundness:** No noted open question can be answered by the provided context. Each open question can only be decided by external information that is currently missing.
    - **Open Question Quality:** All open questions are concrete and well defined. No tautology. A human can understand clearly what additional information or decision needs to be provided.
    - **Challenge Coverage:** All CRITIC challenges, identified gaps, and clarification requests have been addressed by the PROPOSER.
    - **Max Turns:** The conversation does not exceed **30** PROPOSER-CRITIC conversation turns. If the limit is reached, the conversation finishes with a clear WARNING "Limit of 30 Conversation Turns Reached". This is a hard condition, enforced by deterministic logic.

5. Once the conversation finishes, the SUMARIZER agent takes the scratchpad and produces a well formatted **decision document** as equivalent Markdown (`.md`) and PDF files. The final document contains the following sections:

    - **Overview:** short executive summary of the deliberation conclusion
    - **Scope:** what the feature will and will not include
    - **Assumptions:** what was assumed and whether it was challenged or accepted
    - **Open Questions:** what remains unresolved and requires human input

### Technical Details

- Promts are explicitly separated from code for easier developer access. All prompts are stored in a separate module `prompts/`. No prompts live inside the rest of the codebase.
- Feature request and system description text is limited to 10,000 characters and may NOT contain any kind of special formatting like markdown, HTML, XML, etc. Such special formatting will be cleaned before ingestions in the LLM context.
- Scratchpad size must not exceed 50,000 characters. If an agent tries to make the file longer by writing to it, a hard rule in the system prevents it and asks the agent to reduce the length in order to fit into the limits. The agent has at most 5 consecutive attempts to do that, otherwise its write action will be canceled and the agent will have to explain what it tried to do in the conversation, after failing to write in the scratchpad.
- Agent responses in the chat can be at most 2,000 tokens.
- At any point in time there is **only one active agent**. This is the simplest way we avoid concurrence conflicts when reading and editing the scratchpad.
