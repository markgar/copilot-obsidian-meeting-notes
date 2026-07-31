from pathlib import Path

from ..errors import EngineError, TransactionError, UsageError
from ..models import CommandRequest, CommandResponse
from ..transaction import apply_bundle, inspect_bundle, recover_transaction


def _string_argument(request: CommandRequest, name: str) -> str:
    value = request.arguments.get(name)
    if not isinstance(value, str):
        raise UsageError(f"Transaction argument '{name}' must be a string.")
    return value


def handle(request: CommandRequest) -> CommandResponse:
    action = _string_argument(request, "transaction_command")
    vault = Path(request.vault)
    try:
        if action == "inspect":
            data = inspect_bundle(vault, _string_argument(request, "bundle"))
        elif action == "apply":
            data = apply_bundle(vault, _string_argument(request, "bundle"))
        elif action == "recover":
            data = recover_transaction(
                vault,
                _string_argument(request, "transaction_id"),
            )
        else:
            raise UsageError(
                "Unknown transaction command.",
                details={"transaction_command": action},
            )
    except EngineError:
        raise
    except OSError as error:
        raise TransactionError(
            "Transaction I/O failed.",
            details={"action": action, "reason": str(error)},
        ) from error
    return CommandResponse.success(f"transaction.{action}", data=data)
