"""Every piece of text the agents see: instructions, user prompts and validator retry messages.

- `shared`: the input framing, the confidence rubric, the round header and the retry wrapper.
- `proposer`, `critic`, `summarizer`: one module per agent.

Templates use `str.format` placeholders, filled in by `agents.py` and `orchestrator.py`.
"""

from . import critic, proposer, shared, summarizer

__all__ = ["critic", "proposer", "shared", "summarizer"]
