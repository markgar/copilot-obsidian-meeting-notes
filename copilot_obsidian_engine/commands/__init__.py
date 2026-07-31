from collections.abc import Callable

from ..models import CommandRequest, CommandResponse
from .save import handle as handle_save
from .transaction import handle as handle_transaction
from .wiki import handle as handle_wiki
from .wiki_ingest import handle as handle_wiki_ingest
from .wiki_query import handle as handle_wiki_query

CommandHandler = Callable[[CommandRequest], CommandResponse]

HANDLERS: dict[str, CommandHandler] = {
    "wiki": handle_wiki,
    "wiki-ingest": handle_wiki_ingest,
    "wiki-query": handle_wiki_query,
    "save": handle_save,
    "transaction": handle_transaction,
}
