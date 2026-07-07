"""Optional user config loaded from ~/.config/workflow-ai/config.yaml.

All fields are optional. Missing file or invalid YAML is non-fatal: a warning
is printed to stderr and an empty Config (all defaults) is returned.

Precedence: CLI flag > config file > hardcoded default.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


DEFAULT_PATH = Path.home() / ".config" / "workflow-ai" / "config.yaml"


class PhraseforgeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    level: str | None = None
    translation_lang: str | None = None
    cwd: str | None = None


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    backend: str | None = None
    model: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    retries: int | None = None
    out: str | None = None
    verbose: bool | None = None
    log_file: str | None = None
    default_headers: dict[str, str] | None = None
    api_version: str | None = None
    azure_endpoint: str | None = None
    copilot_config: str | None = None
    phraseforge: PhraseforgeConfig = PhraseforgeConfig()


def load_config(path: Path | None = None) -> Config:
    """Load config from *path* (default: DEFAULT_PATH).

    Returns an empty Config if the file does not exist.
    Prints a warning to stderr and returns an empty Config on any error.
    """
    p = path if path is not None else DEFAULT_PATH
    if not p.exists():
        return Config()
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config file must be a YAML mapping")
        return Config.model_validate(raw)
    except Exception as exc:
        print(f"workflow-ai: warning: could not load config {p}: {exc}", file=sys.stderr)
        return Config()
