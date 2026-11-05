"""Central configuration, loaded from environment (.env supported)."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Runtime settings read from environment variables.

    Kept intentionally tiny — this is a demo, not production config management.
    """

    def __init__(self) -> None:
        self.llm_provider: str = os.getenv("LLM_PROVIDER", "stub").lower().strip()

        self.anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
        self.anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")

        self.agent_max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "12"))

        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./purchasing.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
