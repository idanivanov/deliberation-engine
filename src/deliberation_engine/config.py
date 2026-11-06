"""Settings from environment variables, and cleaning of the user inputs."""

import unicodedata
from dataclasses import dataclass

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_INPUT_CHARS = 10_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(validate_by_name=True, extra="ignore")

    proposer_model: str = Field("openrouter:openai/gpt-6-luna", alias="PROPOSER_AGENT_MODEL")
    critic_model: str = Field("openrouter:anthropic/claude-haiku-5.5", alias="CRITIC_AGENT_MODEL")
    summarizer_model: str = Field(
        "openrouter:google/gemini-3.5-flash-lite", alias="SUMMARIZER_AGENT_MODEL"
    )
    system_description: str | None = Field(None, alias="DE_SYSTEM_DESCRIPTION")
    feature_request: str | None = Field(None, alias="DE_FEATURE_REQUEST")
    max_rounds: int = Field(12, ge=2, alias="DE_MAX_ROUNDS")
    stall_rounds: int = Field(3, ge=1, alias="DE_STALL_ROUNDS")
    max_tokens: int = Field(8000, ge=1, alias="DE_MAX_TOKENS")


@dataclass(frozen=True)
class Inputs:
    system_description: str
    feature_request: str


def clean(text: str, name: str) -> str:
    """Strip control characters (except newlines and tabs) and enforce the length cap."""
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc").strip()
    if not text:
        raise ValueError(f"The {name} is empty.")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(
            f"The {name} has {len(text):,} characters; the limit is {MAX_INPUT_CHARS:,}."
        )
    return text
