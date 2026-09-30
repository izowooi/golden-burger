"""Runtime paths and secret loading.

Every DB lives on the Mac mini external disk. If it is not mounted we fail closed
instead of silently writing to the internal disk (the old system filled it up).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = Path(os.environ.get("POLYLAB_SECRETS_DIR", Path.home() / ".polylab"))
DEFAULT_RUNTIME_ROOT = Path("/Volumes/t7/polylab")


class StorageUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def core_db(self) -> Path:
        return self.data / "core.db"

    @property
    def books_dir(self) -> Path:
        return self.data / "books"

    @property
    def raw_dir(self) -> Path:
        return self.data / "raw"

    @property
    def strategies_dir(self) -> Path:
        return self.data / "strategies"

    @property
    def research_dir(self) -> Path:
        return self.data / "research"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def state(self) -> Path:
        """Small runtime state files (locks, heartbeats, retro work dirs)."""
        return self.root / "state"

    def strategy_db(self, strategy_id: str) -> Path:
        return self.strategies_dir / f"{strategy_id}.db"

    def books_db(self, year_month: str) -> Path:
        return self.books_dir / f"{year_month}.db"

    def ensure(self) -> "Paths":
        for p in (self.data, self.books_dir, self.raw_dir, self.strategies_dir,
                  self.research_dir, self.logs, self.state):
            p.mkdir(parents=True, exist_ok=True)
        return self


def paths(require_external: bool | None = None) -> Paths:
    """Resolve the runtime root.

    POLYLAB_ROOT overrides the location (tests, local analysis on a copy).
    When the default external root is used, its volume must be mounted.
    """
    override = os.environ.get("POLYLAB_ROOT")
    if override:
        return Paths(Path(override)).ensure()
    root = DEFAULT_RUNTIME_ROOT
    if require_external is None:
        require_external = True
    if require_external and not Path("/Volumes/t7").is_mount():
        raise StorageUnavailable("/Volumes/t7 is not mounted; refusing to use internal disk")
    return Paths(root).ensure()


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def service_env() -> dict[str, str]:
    """Slack / Supabase settings: process env wins over ~/.polylab/services.env."""
    values = load_env_file(SECRETS_DIR / "services.env")
    for key in ("SLACK_WEBHOOK_URL", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID",
                "SUPABASE_URL", "SUPABASE_SECRET_KEY"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


@dataclass(frozen=True)
class AccountCredentials:
    alias: str
    private_key: str
    funder_address: str
    signature_type: int

    def __repr__(self) -> str:  # never leak the key through logs/tracebacks
        return f"AccountCredentials(alias={self.alias!r}, funder=***, signature_type={self.signature_type})"


def account_credentials(alias: str) -> AccountCredentials:
    """Look up POLYBOT_<ALIAS>__POLYMARKET_* from ~/.polylab/accounts.env.

    Legacy jobs without an explicit signature type used POLY_PROXY (1).
    """
    env = load_env_file(SECRETS_DIR / "accounts.env")
    prefix = f"POLYBOT_{alias.upper().replace('-', '_')}__"
    key = env.get(prefix + "POLYMARKET_PRIVATE_KEY")
    funder = env.get(prefix + "POLYMARKET_FUNDER_ADDRESS")
    if not key or not funder:
        raise KeyError(f"no credentials for account alias {alias!r}")
    sig = int(env.get(prefix + "POLYMARKET_SIGNATURE_TYPE", "1"))
    return AccountCredentials(alias=alias, private_key=key, funder_address=funder, signature_type=sig)


def account_aliases() -> list[str]:
    env = load_env_file(SECRETS_DIR / "accounts.env")
    return sorted({k.split("__")[0].removeprefix("POLYBOT_").lower()
                   for k in env if k.endswith("__POLYMARKET_PRIVATE_KEY")})
