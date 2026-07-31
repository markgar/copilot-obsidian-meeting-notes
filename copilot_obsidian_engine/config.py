from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigurationError
from .models import JsonValue

CONFIG_MARKER = ".copilot-obsidian.json"


@dataclass(frozen=True)
class EngineConfig:
    marker_path: str | None
    values: dict[str, JsonValue] = field(default_factory=dict)


def load_config(vault: Path) -> EngineConfig:
    """Load an optional JSON object from the vault marker."""

    marker = vault / CONFIG_MARKER
    if marker.is_symlink():
        raise ConfigurationError(
            "Config marker may not be a symbolic link.",
            details={"path": str(marker)},
        )
    if not marker.exists():
        return EngineConfig(marker_path=None)
    if not marker.is_file():
        raise ConfigurationError(
            "Config marker is not a file.",
            details={"path": str(marker)},
        )

    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(
            "Config marker could not be read.",
            details={"path": str(marker), "reason": str(error)},
        ) from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            "Config marker is not valid JSON.",
            details={
                "path": str(marker),
                "line": error.lineno,
                "column": error.colno,
            },
        ) from error

    if not isinstance(raw, dict):
        raise ConfigurationError(
            "Config marker must contain a JSON object.",
            details={"path": str(marker)},
        )
    return EngineConfig(marker_path=str(marker), values=raw)
