"""Config loader: file absent, valid YAML, nested phraseforge, error tolerance."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workflow_ai.config import Config, PhraseforgeConfig, load_config


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Missing file
# ---------------------------------------------------------------------------


def test_missing_file_returns_empty_config(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg == Config()


def test_empty_config_has_none_fields():
    cfg = Config()
    assert cfg.backend is None
    assert cfg.model is None
    assert cfg.api_base_url is None
    assert cfg.api_key is None
    assert cfg.retries is None
    assert cfg.out is None
    assert cfg.verbose is None
    assert cfg.log_file is None
    assert cfg.phraseforge == PhraseforgeConfig()


# ---------------------------------------------------------------------------
# Top-level fields
# ---------------------------------------------------------------------------


def test_loads_backend(tmp_path):
    cfg = load_config(_write(tmp_path, {"backend": "pi"}))
    assert cfg.backend == "pi"


def test_loads_model(tmp_path):
    cfg = load_config(_write(tmp_path, {"model": "gemma2:9b"}))
    assert cfg.model == "gemma2:9b"


def test_loads_api_base_url(tmp_path):
    cfg = load_config(_write(tmp_path, {"api_base_url": "http://localhost:11434"}))
    assert cfg.api_base_url == "http://localhost:11434"


def test_loads_api_key(tmp_path):
    cfg = load_config(_write(tmp_path, {"api_key": "ollama"}))
    assert cfg.api_key == "ollama"


def test_loads_retries(tmp_path):
    cfg = load_config(_write(tmp_path, {"retries": 5}))
    assert cfg.retries == 5


def test_loads_out(tmp_path):
    cfg = load_config(_write(tmp_path, {"out": "runs/ci"}))
    assert cfg.out == "runs/ci"


def test_loads_verbose_true(tmp_path):
    cfg = load_config(_write(tmp_path, {"verbose": True}))
    assert cfg.verbose is True


def test_loads_log_file(tmp_path):
    cfg = load_config(_write(tmp_path, {"log_file": "/tmp/run.log"}))
    assert cfg.log_file == "/tmp/run.log"


# ---------------------------------------------------------------------------
# phraseforge section
# ---------------------------------------------------------------------------


def test_loads_phraseforge_level(tmp_path):
    cfg = load_config(_write(tmp_path, {"phraseforge": {"level": "b2"}}))
    assert cfg.phraseforge.level == "b2"


def test_loads_phraseforge_translation_lang(tmp_path):
    cfg = load_config(_write(tmp_path, {"phraseforge": {"translation_lang": "eng"}}))
    assert cfg.phraseforge.translation_lang == "eng"


def test_loads_phraseforge_cwd(tmp_path):
    cfg = load_config(_write(tmp_path, {"phraseforge": {"cwd": "/my/project"}}))
    assert cfg.phraseforge.cwd == "/my/project"


def test_phraseforge_partial_leaves_others_none(tmp_path):
    cfg = load_config(_write(tmp_path, {"phraseforge": {"level": "a1"}}))
    assert cfg.phraseforge.level == "a1"
    assert cfg.phraseforge.translation_lang is None
    assert cfg.phraseforge.cwd is None


def test_full_config(tmp_path):
    data = {
        "backend": "codex",
        "model": "o4-mini",
        "api_base_url": None,
        "api_key": None,
        "retries": 2,
        "out": "runs/out",
        "verbose": True,
        "log_file": "run.log",
        "phraseforge": {
            "level": "c1",
            "translation_lang": "deu",
            "cwd": "/lessons",
        },
    }
    cfg = load_config(_write(tmp_path, data))
    assert cfg.backend == "codex"
    assert cfg.model == "o4-mini"
    assert cfg.retries == 2
    assert cfg.verbose is True
    assert cfg.phraseforge.level == "c1"
    assert cfg.phraseforge.translation_lang == "deu"


# ---------------------------------------------------------------------------
# Error tolerance
# ---------------------------------------------------------------------------


def test_invalid_yaml_returns_empty_config_with_warning(tmp_path, capsys):
    bad = tmp_path / "config.yaml"
    bad.write_text("backend: [unclosed", encoding="utf-8")
    cfg = load_config(bad)
    assert cfg == Config()
    assert "warning" in capsys.readouterr().err.lower()


def test_non_mapping_yaml_returns_empty_config_with_warning(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg == Config()
    assert "warning" in capsys.readouterr().err.lower()


def test_unknown_keys_are_ignored(tmp_path):
    cfg = load_config(_write(tmp_path, {"backend": "claude", "unknown_key": "value"}))
    assert cfg.backend == "claude"


def test_unknown_phraseforge_keys_are_ignored(tmp_path):
    cfg = load_config(_write(tmp_path, {"phraseforge": {"level": "a2", "future_option": 42}}))
    assert cfg.phraseforge.level == "a2"
