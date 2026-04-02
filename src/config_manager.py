"""Configuration manager — loads/saves JSON config."""

import json
import os
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default_config.json"
USER_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "user_config.json"


def load_config() -> dict:
    """Load user config if exists, otherwise default."""
    path = USER_CONFIG_PATH if USER_CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    """Save config to user_config.json."""
    with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


def get_download_dir(cfg: dict) -> Path:
    """Return absolute download directory path, create if needed."""
    d = Path(cfg["workers"]["download_dir"])
    if not d.is_absolute():
        d = Path(__file__).resolve().parent.parent / d
    d.mkdir(parents=True, exist_ok=True)
    return d
