# Deliberation Engine Decision Log

## Implementation Approach

I decided to build the system using spec-driven development. I worked initially on the system design document to clarify all requirements, assumptions, architecture, etc. I then used Claude Code to implement the initial version of the Python package. Finally, I iterated reviewing, testing, and debugged the solution in order to improve it within the given time.

The alternative would have been to write the code myself. I decided not to do that, considering the given time restriction.

## Agentic Framework

We choose Pydantic AI as a modern and well maintained agentic framework. It integrates well with Pydantic for structured outputs and OpenRouter for connecting to different model families. It also provides many common agentic patterns out-of-the-box, which reduces boilerplate code.

LangChain/LangGraph, AutoGen, Semantic Kernel, CrewAI are all good alternatives, but considering the simplicity of the agents, the framework choice was mostly due to personal preference.

## State and Context Management

The state of the system is the conversation history between the PROPOSER and the CRITIC agents. The chat contains all information that is used for finally producing the decision document. This approach is simple to implement, but has significant drawbacks:

- A lot of information is unnecessarily repeated.
- A long conversation history can fill up the whole context window of an LLM, which is why we are truncating it.

We have chosen this option for simplicity of implementation. Here are some other approaches and improvements we can use in future:

- **Shared State:** The agents can share a scratchpad file that both have read/write access to. This way, any changes in the state can be applied to the file, which would reduce the necessity to repeat information in the conversation history. This approach is a bit more complex to implement than just the simple conversation flow.
- **History Compaction:** Long conversation histories can be compacted so that only the last view messages are kept verbatim in context and older messages are summarized using an additional LLM call. The compaction step adds more cost and latency, but prevents any context being lost due to long conversation histories.
- **Map-Reduce:** Ideally, the SUMMARIZATION agent should go through the full conversation history and all other relevant information in order to produce the final result (in addition to the deterministic orchestrator logic). However, the conversation history might be too long to fit in the agent's context window. We can use the Map-Reduce paradigm to process the context in (overlapping) chunks and then combine the intermediate results into a final summary that takes into consideration all relevant information. This approach required more LLM calls and additional scaffolding.

## Termination Logic

The termination is handled by the CRITIC agent and safeguarded by deterministic rules (stalled, min/max rounds). The CRITIC decides if the process should terminate by signaling with an explicit boolean value `satisfied`, indicating that it has no more significant challenges to the PROPOSER's outputs. The logic itself is described in detail in the system design document.

I made multiple tradeoffs when fine-tuning the termination logic:

- Should minor severity challenges be a reason for the conversation to continue? They might cause the conversation to run for too long. I decided to keep them optional, so if there are no major or blocking challenges, only minor challenges would not cause the conversation to continue (i.e., the CRITIC is satisfied with leaving minor challenges unresolved).
- Getting stuck into an unproductive loop: If the number of disagreements does not change for a while, we assume the agents are stuck, so we stop the conversation. The trade-off is that we might miss some productive conversation that is improving on a single item.
- How disagreeable should the PROPOSER and CRITIC be? This is difficult to balance and is mostly controlled through the prompts of the two agents. At the current state, the agents usually converge within 3-4 rounds. If we want more rounds, we can edit the prompts to be more "disagreeable".

## Prompt Design

The prompts have been designed around templates shared among the agents (inputs, conversation history, etc.).

Changes to the prompts have been applied after iterating on different inputs and seeing the context log (using `--debug`) and final results. The main problems were how to balance the "disagreeableness" between the agents.

## Misc

- Renamed `system context` to `system description` to avoid naming conflicts with `system prompt` and `LLM context`.
- All agents use different model families (GPT, Claude, Gemini) in order to make the interaction richer. THis way we avoid the tendency of models from the same family to be more likely to agree with each other's outputs.
- The CRITIC agent currently judges everything within one LLM call. That is unreliable. A better approach is to separate the judgement on scope, assumptions, etc., between separate specialized LLM calls and then combine the results by one final CRITIC LLM call. This increases the cost and latency, but makes the CRITIC more reliable.
- Inputs are assumed to be plain text. Markdown, HTML, XML formatting in those fields could lead to unexpected results.
- Confidence is currently calculated on the whole response. It would be more meaningful to calculate it on an individual proposal/challenge level.
- All model choices and prompts have been "vibe" tested. We haven't run any quantitative evaluations on different versions in order to know for sure which models and prompts would work better. For future development, we need to prepare a golden dataset for evaluations and run experiments to improve the agents - choose best models, adapt prompts, improve scaffolding.
