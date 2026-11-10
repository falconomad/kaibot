"""Engine settings: everything comes from environment variables (or one .env file), so the same container runs
unchanged on a laptop, Google Cloud, Oracle or anywhere else. Paper trading only: a live URL is refused."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
DEFAULT_SUPABASE_URL = "https://dosdkdlxejqynbgkjhup.supabase.co"  # public project URL, not a secret
ROOT = Path(__file__).resolve().parents[3]


def load_env_file(path: str | Path | None) -> None:
    """Fill os.environ from a .env file without overriding variables that are already set."""
    if not path or not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    alpaca_key: str
    alpaca_secret: str
    supabase_url: str
    supabase_service_key: str
    fred_key: str
    data_dir: Path
    engine_name: str = "engine"

    @classmethod
    def from_env(cls) -> Settings:
        load_env_file(os.environ.get("KAIBOT_ENV_FILE"))
        os.environ.setdefault("SUPABASE_URL", DEFAULT_SUPABASE_URL)
        for alias, name in (
            ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY"),
            ("ALPACA_API_SECRET", "ALPACA_SECRET"),
        ):
            if os.environ.get(alias) and not os.environ.get(name):
                os.environ[name] = os.environ[alias]
        missing = [
            k
            for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "SUPABASE_SERVICE_KEY", "FRED_API_KEY")
            if not os.environ.get(k)
        ]
        if missing:
            raise SystemExit(f"missing settings: {', '.join(missing)} (put them in the .env file)")
        base = os.environ.get("ALPACA_BASE_URL", PAPER_TRADING_URL).rstrip("/")
        if base != PAPER_TRADING_URL:
            raise SystemExit("Kaibot is paper-only: ALPACA_BASE_URL must be the paper endpoint")
        return cls(
            alpaca_key=os.environ["ALPACA_API_KEY"],
            alpaca_secret=os.environ["ALPACA_SECRET"],
            supabase_url=os.environ["SUPABASE_URL"].rstrip("/"),
            supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"],
            fred_key=os.environ["FRED_API_KEY"],
            data_dir=Path(os.environ.get("KAIBOT_DATA_DIR", ROOT / "data" / "live")),
            engine_name=os.environ.get("KAIBOT_ENGINE_NAME", "engine"),
        )
