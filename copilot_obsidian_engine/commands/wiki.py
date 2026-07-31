from pathlib import Path

from ..errors import PreconditionError, UsageError
from ..models import CommandRequest, CommandResponse
from ..wiki_workflows import apply_wiki, doctor, plan_wiki


def handle(request: CommandRequest) -> CommandResponse:
    action = request.arguments.get("wiki_command")
    if isinstance(action, str):
        vault = Path(request.vault)
        if action == "doctor":
            return CommandResponse.success("wiki.doctor", data=doctor(vault))
        if action in {"init", "adopt"}:
            apply = request.arguments.get("apply") is True
            approved_hash = request.arguments.get("approved_hash")
            has_approval = isinstance(approved_hash, str)
            if apply != has_approval:
                raise PreconditionError(
                    "Wiki apply requires both --apply and --approved-hash.",
                    details={"workflow": action},
                )
            if apply:
                return CommandResponse.success(
                    f"wiki.{action}",
                    data=apply_wiki(vault, action, approved_hash),
                )
            return CommandResponse.success(
                f"wiki.{action}",
                data=plan_wiki(vault, action).to_dict(),
            )

    if not isinstance(request.arguments.get("request"), str):
        raise UsageError("Legacy wiki routing requires --request.")
    return CommandResponse.failure(
        request.command,
        code="not_implemented",
        message="Wiki planning is not implemented yet.",
        status="not_implemented",
        details={"vault": request.vault},
    )
