from __future__ import annotations

from typing import Any, Mapping


class EngineError(Exception):
    """Expected engine failure that can be returned as structured JSON."""

    code = "engine_error"
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class UsageError(EngineError):
    code = "usage_error"
    exit_code = 2


class VaultPathError(EngineError):
    code = "invalid_vault_path"


class ConfigurationError(EngineError):
    code = "invalid_config"


class ConflictError(EngineError):
    code = "transaction_conflict"
    exit_code = 4


class LockError(EngineError):
    code = "vault_locked"
    exit_code = 5


class PreconditionError(EngineError):
    code = "precondition_failed"
    exit_code = 6


class TransactionError(EngineError):
    code = "transaction_failed"
    exit_code = 7


class RecoveryRequiredError(TransactionError):
    code = "recovery_required"
