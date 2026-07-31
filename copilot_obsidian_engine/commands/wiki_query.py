from pathlib import Path

from ..errors import UsageError
from ..models import CommandRequest, CommandResponse
from ..query_workflows import query_vault


def handle(request: CommandRequest) -> CommandResponse:
    query = request.arguments.get("query")
    limit = request.arguments.get("limit")
    if not isinstance(query, str) or not isinstance(limit, int):
        raise UsageError("Wiki query requires text and an integer limit.")
    return CommandResponse.success(
        "wiki-query",
        data=query_vault(Path(request.vault), query, limit),
    )
