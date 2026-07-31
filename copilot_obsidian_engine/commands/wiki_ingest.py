from pathlib import Path

from ..errors import PreconditionError, UsageError
from ..ingest_workflows import apply_ingest, plan_ingest
from ..models import CommandRequest, CommandResponse


def handle(request: CommandRequest) -> CommandResponse:
    sources = request.arguments.get("source")
    if not isinstance(sources, list) or not all(
        isinstance(source, str) for source in sources
    ):
        raise UsageError("At least one --source path is required.")
    apply = request.arguments.get("apply") is True
    approved_hash = request.arguments.get("approved_hash")
    has_approval = isinstance(approved_hash, str)
    if apply != has_approval:
        raise PreconditionError(
            "Wiki ingest apply requires both --apply and --approved-hash."
        )

    vault = Path(request.vault)
    if apply:
        data = apply_ingest(vault, sources, approved_hash)
    else:
        output = request.arguments.get("output")
        data = plan_ingest(vault, sources).to_dict(
            requested_output=output if isinstance(output, str) else None
        )
    return CommandResponse.success("wiki-ingest", data=data)
