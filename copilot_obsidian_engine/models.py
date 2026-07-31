from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TypeAlias

JsonValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)


@dataclass(frozen=True)
class CommandRequest:
    command: str
    vault: str
    arguments: dict[str, JsonValue] = field(default_factory=dict)
    config: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "command": self.command,
            "vault": self.vault,
            "arguments": self.arguments,
            "config": self.config,
        }


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str
    details: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class CommandResponse:
    ok: bool
    command: str
    status: str
    data: dict[str, JsonValue] = field(default_factory=dict)
    error: ErrorPayload | None = None

    @classmethod
    def success(
        cls,
        command: str,
        *,
        data: dict[str, JsonValue] | None = None,
    ) -> "CommandResponse":
        return cls(ok=True, command=command, status="ok", data=data or {})

    @classmethod
    def failure(
        cls,
        command: str,
        *,
        code: str,
        message: str,
        details: dict[str, JsonValue] | None = None,
        status: str = "error",
    ) -> "CommandResponse":
        return cls(
            ok=False,
            command=command,
            status=status,
            error=ErrorPayload(code=code, message=message, details=details or {}),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "ok": self.ok,
            "command": self.command,
            "status": self.status,
            "data": self.data,
        }
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)
