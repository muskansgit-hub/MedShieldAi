"""Central configuration. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so python-dotenv isn't a dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

DATA_PATH = Path(os.getenv("MEDSHIELD_DATA", ROOT / "data" / "medfsc_bench.json"))
MODEL_DIR = Path(os.getenv("MEDSHIELD_MODEL_DIR", ROOT / "models"))
RESULTS_DIR = Path(os.getenv("MEDSHIELD_RESULTS", ROOT / "results"))
DB_PATH = Path(os.getenv("MEDSHIELD_DB", ROOT / "medshield_audit.db"))

# Policy thresholds on FSC in [0, 1]
WARN_AT = float(os.getenv("MEDSHIELD_WARN_AT", 0.08))
REWRITE_AT = float(os.getenv("MEDSHIELD_REWRITE_AT", 0.18))
BLOCK_AT = float(os.getenv("MEDSHIELD_BLOCK_AT", 0.35))

# Retrieval threshold for matching a query to a verified benchmark reference
REF_MATCH_MIN = float(os.getenv("MEDSHIELD_REF_MATCH_MIN", 0.48))

# Default provider used by /api/ask when none is given
DEFAULT_PROVIDER = os.getenv("MEDSHIELD_DEFAULT_PROVIDER", "mock")
# Optional LLM judge for incorrectness, e.g. "openai/gpt-4o-mini" (empty = off)
JUDGE = os.getenv("MEDSHIELD_JUDGE", "")

EMERGENCY_NUMBER = os.getenv("MEDSHIELD_EMERGENCY_NUMBER", "112")
CRISIS_LINE = os.getenv("MEDSHIELD_CRISIS_LINE", "Tele-MANAS 14416 (India)")

# ---- deployment / public-demo hardening ----
# When set, the audit log and the /v1 proxy require "Authorization: Bearer <token>".
ADMIN_TOKEN = os.getenv("MEDSHIELD_ADMIN_TOKEN", "")
# Comma-separated provider names visitors may use, e.g. "mock,groq" (empty = all configured)
ALLOWED_PROVIDERS = [p.strip().lower() for p in os.getenv("MEDSHIELD_ALLOWED_PROVIDERS", "").split(",") if p.strip()]
# Max LLM-calling requests per client IP per hour (0 = unlimited). Admin token bypasses it.
RATE_LIMIT_PER_HOUR = int(os.getenv("MEDSHIELD_RATE_LIMIT_PER_HOUR", 0))
