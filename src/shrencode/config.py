"""Defaults from config.toml.

Keys are long option names, for example:

    encoder = "svt-av1"
    quality = "compact"
    output-dir = "E:/Archive"

    [crf.x265]
    1080p = 21
"""

from __future__ import annotations

import argparse
import os
import tomllib
from pathlib import Path

from . import plan


def config_path() -> Path:
    if os.environ.get("SHRENCODE_CONFIG"):
        return Path(os.environ["SHRENCODE_CONFIG"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "shrencode" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "shrencode" / "config.toml"


class ConfigError(ValueError):
    pass


def load(parser: argparse.ArgumentParser, path: Path | None = None) -> dict:
    """Validated defaults for `parser`, keyed by dest. Applies [crf.*] tables directly."""
    path = path or config_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from None

    for encoder, table in (data.pop("crf", None) or {}).items():
        if encoder not in plan.CRF_BASE or not isinstance(table, dict):
            raise ConfigError(f"{path}: unknown [crf.{encoder}]")
        for rclass, value in table.items():
            if rclass not in plan.CRF_BASE[encoder] or not isinstance(value, int):
                raise ConfigError(f"{path}: bad crf.{encoder}.{rclass}")
            plan.CRF_BASE[encoder][rclass] = value

    actions = {a.dest: a for a in parser._actions}
    defaults = {}
    for key, value in data.items():
        dest = key.replace("-", "_")
        action = actions.get(dest)
        if action is None or dest in ("inputs", "help"):
            raise ConfigError(f"{path}: unknown option {key}")
        if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            if not isinstance(value, bool):
                raise ConfigError(f"{path}: {key} must be true or false")
            defaults[dest] = value  # keep-mtime = false means the same as --no-keep-mtime
            continue
        if action.type is not None:
            try:
                value = action.type(str(value))
            except (ValueError, argparse.ArgumentTypeError) as e:
                raise ConfigError(f"{path}: {key}: {e}") from None
        if action.choices is not None and value not in action.choices:
            raise ConfigError(f"{path}: {key} must be one of {', '.join(map(str, action.choices))}")
        defaults[dest] = value
    return defaults
