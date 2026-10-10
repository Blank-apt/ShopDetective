"""Application configuration: limits, versions, currencies and secret lookup.

Secrets are read from environment variables first, then Streamlit secrets.
A local, git-ignored ``.env`` file is loaded for development convenience.
Secret values are never logged or exported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# SD_DISABLE_DOTENV=1 keeps offline tests from picking up a developer's .env key.
if os.environ.get("SD_DISABLE_DOTENV") != "1":
    try:  # python-dotenv is optional at runtime
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:  # pragma: no cover
        pass

APP_NAME = "ShopDetective"
APP_VERSION = "1.0.0"

# Verified against https://ai.google.dev/gemini-api/docs/models (checked 2026-10-06):
# gemini-3.5-flash is listed as a stable Flash model. Override with GEMINI_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

PROMPT_VERSION = "classify-v1"
ACTION_PROMPT_VERSION = "actions-v1"
SCHEMA_VERSION = "1.0"
REPORT_FORMAT_VERSION = "1.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEMO_DIR = DATA_DIR / "demo"
REVIEW_CASES_DIR = DATA_DIR / "review_cases"
EVALUATION_DIR = DATA_DIR / "evaluation"

# Two-decimal currencies supported. No exchange-rate conversion is performed.
CURRENCIES: dict[str, str] = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
MINOR_UNITS = 2

REVIEWER_ID = "local_reviewer"


# Monetary safety bounds, in minor units. Amounts are parsed exactly with
# Decimal and checked as Python integers *before* any int64 storage, so no
# value or sum can overflow. Rows above the bounds are rejected with a
# validation error; nothing is rounded, clipped or dropped.
MAX_LINE_AMOUNT_MINOR = 10**14  # 1,000,000,000,000.00 per purchase line
MAX_TOTAL_AMOUNT_MINOR = 10**18  # 10,000,000,000,000,000.00 total sales per file
INT64_MAX = 2**63 - 1


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 5 * 1024 * 1024
    max_order_lines: int = 20_000
    max_feedback_messages: int = 2_000
    max_message_chars: int = 2_000
    max_line_amount_minor: int = MAX_LINE_AMOUNT_MINOR
    max_total_amount_minor: int = MAX_TOTAL_AMOUNT_MINOR

    def __post_init__(self) -> None:
        if not 0 < self.max_line_amount_minor <= self.max_total_amount_minor <= INT64_MAX:
            raise ValueError("monetary bounds must satisfy 0 < line bound <= total bound <= int64 max")


@dataclass(frozen=True)
class ProviderSettings:
    model: str = DEFAULT_GEMINI_MODEL
    batch_size: int = 15
    timeout_seconds: float = 60.0
    max_attempts: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 8.0


@dataclass(frozen=True)
class Settings:
    limits: Limits = field(default_factory=Limits)
    provider: ProviderSettings = field(default_factory=ProviderSettings)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_secret(name: str) -> str | None:
    """Return a configuration value from env vars or Streamlit secrets."""
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    try:
        import streamlit as st

        if name in st.secrets:
            secret = str(st.secrets[name]).strip()
            return secret or None
    except Exception:  # no secrets.toml, or not running under Streamlit
        return None
    return None


def load_settings() -> Settings:
    limits = Limits(
        max_file_bytes=_int_env("SD_MAX_FILE_MB", 5) * 1024 * 1024,
        max_order_lines=_int_env("SD_MAX_ORDER_LINES", 20_000),
        max_feedback_messages=_int_env("SD_MAX_FEEDBACK_MESSAGES", 2_000),
        max_message_chars=_int_env("SD_MAX_MESSAGE_CHARS", 2_000),
    )
    provider = ProviderSettings(
        model=get_secret("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL,
        batch_size=min(_int_env("SD_BATCH_SIZE", 15), 50),
        timeout_seconds=_float_env("SD_REQUEST_TIMEOUT_SECONDS", 60.0),
        max_attempts=min(_int_env("SD_MAX_ATTEMPTS", 3), 3),
    )
    return Settings(limits=limits, provider=provider)
