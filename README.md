# Deliberation Engine

A multi-agent system that turns a vague feature request into a structured decision document. A **PROPOSER** interprets the request and proposes a scope, assumptions and success criteria. A **CRITIC** challenges the proposal over several rounds until it is satisfied. A **SUMMARIZER** then writes the narrative parts of the decision document: what was agreed, what was rejected, and what remains open for stakeholders.

The design and its rationale are in [docs/system_design.md](docs/system_design.md).

## Setup

Requires Python 3.15, [uv](https://docs.astral.sh/uv/) and an [OpenRouter](https://openrouter.ai/) API key.

```bash
uv sync
cp .env.example .env    # then set OPENROUTER_API_KEY
```

## Run

```bash
uv run deliberation-engine \
  -s "A CRM system used in Operations and Country Engagement." \
  -r "We need a better way to track who is the right person to contact in each country."
```

Each input can come from a CLI argument, a file, or an environment variable (e.g. set in `.env`):

| Input | Argument | File | Environment variable |
| --- | --- | --- | --- |
| System description | `-s`, `--system` | `--system-file` | `DE_SYSTEM_DESCRIPTION` |
| Feature request | `-r`, `--request` | `--request-file` | `DE_FEATURE_REQUEST` |

An argument or file takes precedence over the environment variable. Each input is limited to 10,000 characters. Add `-v`/`--verbose` to also show the validator retries and the model reasoning.

## Output

The console shows each agent message as it arrives, followed by a metrics line after every round and a final summary with the token usage, cost and time per agent. Each run also writes a folder `runs/<timestamp>/`:

| File | Content |
| --- | --- |
| `decision.md` | The decision document: scope, assumptions (challenged or accepted), decision log, success criteria, open questions, unresolved disagreements, and per-round metrics |
| `decision.json` | The same document as JSON |
| `chat.json` | The full conversation, including retries and reasoning; rewritten after every turn, so it survives a failed run |
| `transcript.txt` | The rendered console output |

The run exits with `0` when a decision document was written, `1` on an error and `2` on invalid input.

## How deliberation ends

After each CRITIC review, the first matching condition ends the deliberation:

1. **Converged:** the CRITIC is satisfied, which it may only be once no major or blocking challenge remains, and never in round 1.
2. **Stalled:** the major/blocking disagreement has not improved on its lowest value for 3 rounds (`DE_STALL_ROUNDS`).
3. **Max rounds:** the safety net at 12 rounds (`DE_MAX_ROUNDS`).

A stalled or max-rounds run still produces the decision document, with a warning.

## Configuration

Set in `.env` or the environment; see [.env.example](.env.example).

| Variable | Default |
| --- | --- |
| `OPENROUTER_API_KEY` | required |
| `PROPOSER_AGENT_MODEL` | `openrouter:openai/gpt-6-luna` |
| `CRITIC_AGENT_MODEL` | `openrouter:anthropic/claude-haiku-5.5` |
| `SUMMARIZER_AGENT_MODEL` | `openrouter:google/gemini-3.5-flash-lite` |
| `DE_MAX_ROUNDS` | `12` |
| `DE_STALL_ROUNDS` | `3` |
| `DE_MAX_TOKENS` | `8000` (response cap per agent turn) |

## Project layout

```sh
src/deliberation_engine/
├── prompts/         # every text the agents see: instructions, user prompts, retry messages
├── schemas.py       # structured agent outputs and round metrics
├── agents.py        # the three Pydantic AI agents and their output validators
├── orchestrator.py  # the deliberation loop, stopping criteria, metrics and chat log
├── document.py      # decision document assembly and Markdown rendering
├── config.py        # settings and input cleaning
└── cli.py           # command line and console rendering
```

## Development

```bash
uv run pytest          # tests script the agents with FunctionModel: no API calls
uv run ruff format .
uv run ruff check --fix .
uv run lint-imports    # checks the module layering
```
