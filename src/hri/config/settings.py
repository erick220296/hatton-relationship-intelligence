from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings

CONFIG_DIR = Path(__file__).parent


class Settings(BaseSettings):
    database_url: str = "postgresql://hri:hri@localhost:5432/hri"
    mcp_url: str = "http://localhost:8001/mcp"
    api_url: str = "http://localhost:8000"
    anthropic_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    extract_model: str = "claude-haiku-5-5"
    brief_model: str = "claude-sonnet-5-5"
    verify_model: str = "claude-haiku-5-5"
    llm_kill_switch: bool = False
    vertical: str = "pe"
    # The demo clock. Corpus backfill ends 2026-11-24; "now" is the morning after.
    as_of: datetime = datetime.fromisoformat("2026-11-25T07:00:00+00:00")
    brief_timeout_s: float = 25.0
    context_budget_tokens: int = 6000

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def vertical() -> dict:
    return yaml.safe_load((CONFIG_DIR / "verticals" / f"{settings().vertical}.yaml").read_text())


@lru_cache
def prices() -> dict:
    return yaml.safe_load((CONFIG_DIR / "prices.yaml").read_text())


def cost_usd(model: str, tokens_in: int, tokens_out: int, batch: bool = False) -> float:
    p = prices()["models"].get(model)
    if not p:
        return 0.0
    c = tokens_in / 1e6 * p["input_per_mtok"] + tokens_out / 1e6 * p["output_per_mtok"]
    return c * (prices()["batch_discount"] if batch else 1.0)


def kill_switch() -> bool:
    """Read live so the failure demo can flip it without a restart."""
    flag = Path("/tmp/hri_llm_down")
    return settings().llm_kill_switch or flag.exists() or os.getenv("LLM_KILL_SWITCH") == "1"
