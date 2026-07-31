from pathlib import Path

from ..errors import PreconditionError, UsageError
from ..models import CommandRequest, CommandResponse
from ..save_workflows import apply_save, plan_save


def handle(request: CommandRequest) -> CommandResponse:
    changes = request.arguments.get("changes")
    if not isinstance(changes, str):
        raise UsageError("Save requires a --changes request JSON path.")
    apply = request.arguments.get("apply") is True
    approved_hash = request.arguments.get("approved_hash")
    has_approval = isinstance(approved_hash, str)
    if apply != has_approval:
        raise PreconditionError(
            "Save apply requires both --apply and --approved-hash; dry-run accepts neither."
        )

    vault = Path(request.vault)
    if apply:
        data = apply_save(vault, changes, approved_hash)
    else:
        data = plan_save(vault, changes).to_dict()
    return CommandResponse.success("save", data=data)
