"""Configuration loading.

Two layers:
  * YAML config files in ``config/`` (gates, thresholds, weights) — version-controlled.
  * Secrets from environment / ``.env`` — NEVER stored in YAML or code.

Design goals:
  * Zero hard dependency on pydantic-settings or python-dotenv (graceful fallback),
    so the engine loads in a bare stdlib + PyYAML environment.
  * A single ``load_config()`` returns a typed-ish ``Config`` namespace plus a
    ``Secrets`` object that only reads from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Project root = two levels up from this file (src/rmas/config.py -> repo root).
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


# --------------------------------------------------------------------------- #
# .env handling (tiny parser; python-dotenv used if available)
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a .env file if present. Never overrides existing."""
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    try:  # prefer python-dotenv when installed (handles quoting/escapes)
        from dotenv import load_dotenv as _ld  # type: ignore

        _ld(dotenv_path=str(path), override=False)
        return
    except Exception:
        pass
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


# --------------------------------------------------------------------------- #
# Secrets (read-only view over the environment)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Secrets:
    """All API keys / credentials. Sourced ONLY from the environment.

    Accessing a missing secret returns ``""`` rather than raising, so offline
    mode never crashes. Adapters check ``has(...)`` before going live.
    """

    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    def get(self, key: str, default: str = "") -> str:
        return self.env.get(key, default)

    def has(self, *keys: str) -> bool:
        return all(bool(self.env.get(k)) for k in keys)

    # convenience groups -----------------------------------------------------
    @property
    def reddit_ready(self) -> bool:
        return self.has("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")

    @property
    def alpaca_ready(self) -> bool:
        return self.has("ALPACA_API_KEY", "ALPACA_API_SECRET")

    @property
    def tradier_ready(self) -> bool:
        return self.has("TRADIER_API_KEY")

    @property
    def smtp_ready(self) -> bool:
        return self.has("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")


# --------------------------------------------------------------------------- #
# Config (YAML-backed dotted-access namespace)
# --------------------------------------------------------------------------- #
class Config:
    """Dotted/dict access wrapper over merged YAML config.

    Example::
        cfg = load_config()
        cfg.discovery.gate.min_score        # attribute access
        cfg["discovery"]["gate"]["min_score"]  # dict access
    """

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            val = self._data[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc
        return Config(val) if isinstance(val, dict) else val

    def __getitem__(self, key: str) -> Any:
        val = self._data[key]
        return Config(val) if isinstance(val, dict) else val

    def get(self, key: str, default: Any = None) -> Any:
        val = self._data.get(key, default)
        return Config(val) if isinstance(val, dict) else val

    def to_dict(self) -> dict[str, Any]:
        return self._data

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Config({list(self._data.keys())})"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def load_config(config_dir: Path | None = None) -> Config:
    """Load and merge all YAML config files into one Config namespace."""
    config_dir = config_dir or CONFIG_DIR
    merged: dict[str, Any] = {}
    for name in ("config.yaml", "strategies.yaml", "universe.yaml"):
        merged.update(_read_yaml(config_dir / name))
    return Config(merged)


def is_offline(secrets: Secrets | None = None, cfg: Config | None = None) -> bool:
    """Decide whether adapters should run offline (synthetic/cached) data."""
    env_flag = os.environ.get("RMAS_OFFLINE", "").lower()
    if env_flag in ("1", "true", "yes"):
        return True
    if env_flag in ("0", "false", "no"):
        return False
    if cfg is not None:
        try:
            return bool(cfg.run.get("offline_default", True))
        except Exception:
            pass
    return True


load_dotenv()  # best-effort on import; never raises
