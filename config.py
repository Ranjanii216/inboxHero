"""Environment-driven model and path configuration. No secrets in code."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# --- Model provider (set in .env; see .env.example) -------------------------
# "none" runs on deterministic rules and grounded templates only.
PROVIDER = os.getenv("INBOXHERO_PROVIDER", "none").strip().lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()

# Seconds between model calls. ~15 req/min free tiers need about 4s.
CALL_GAP = float(os.getenv("INBOXHERO_CALL_GAP", "4.0"))

# --- Paths (flat layout) -----------------------------------------------------
INBOX_PATH = ROOT / os.getenv("INBOXHERO_INBOX", "inbox.json")
OUTBOX_DIR = ROOT / "outbox"
TRACE_PATH = ROOT / "trace.jsonl"
PREFS_PATH = ROOT / "prefs.json"
DECISIONS_PATH = ROOT / "decisions.json"
DASHBOARD_HTML = ROOT / "dashboard.html"
DASHBOARD_JSON = ROOT / "dashboard.json"

# --- Mailbox owner -----------------------------------------------------------
OWNER_EMAIL = os.getenv("INBOXHERO_OWNER", "sam@paperjet.io").strip()
OWNER_NAME = os.getenv("INBOXHERO_OWNER_NAME", "Sam").strip()


def ensure_outbox() -> Path:
    """Create outbox/ on first use and return its path."""
    OUTBOX_DIR.mkdir(exist_ok=True)
    return OUTBOX_DIR


def model_label() -> str:
    """Provider and model actually configured, for the manifest's `model` field."""
    if PROVIDER == "openai":
        return f"{OPENAI_MODEL or 'unset'} (openai)"
    if PROVIDER == "gemini":
        return f"{GEMINI_MODEL or 'unset'} (gemini)"
    if PROVIDER == "ollama":
        return f"{OLLAMA_MODEL} via Ollama at {OLLAMA_HOST}"
    return "none (deterministic rules and grounded templates)"
