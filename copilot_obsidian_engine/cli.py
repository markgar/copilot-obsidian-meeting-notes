from __future__ import annotations

import argparse
from collections.abc import Sequence

from .commands import HANDLERS
from .config import load_config
from .errors import EngineError, UsageError
from .models import CommandRequest, CommandResponse, JsonValue
from .vault import resolve_vault_path


class EngineArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", required=True, help="Path to the local vault")
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json",),
        default="json",
        help="Output format (default: json)",
    )


def build_parser() -> EngineArgumentParser:
    parser = EngineArgumentParser(prog="python -m copilot_obsidian_engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    wiki = subparsers.add_parser("wiki", help="Plan or initialize wiki work")
    wiki.add_argument(
        "--vault",
        dest="legacy_vault",
        help="Path to the local vault for legacy --request routing",
    )
    wiki.add_argument(
        "--format",
        dest="output_format",
        choices=("json",),
        default="json",
        help="Output format (default: json)",
    )
    wiki.add_argument("--request", help="Path to request JSON")
    wiki_actions = wiki.add_subparsers(dest="wiki_command")

    doctor = wiki_actions.add_parser("doctor", help="Report vault readiness")
    _add_common_arguments(doctor)

    for action in ("init", "adopt"):
        workflow = wiki_actions.add_parser(
            action,
            help=f"Plan or apply wiki {action}",
        )
        _add_common_arguments(workflow)
        workflow.add_argument(
            "--apply",
            action="store_true",
            help="Apply the approved plan",
        )
        workflow.add_argument(
            "--approved-hash",
            help="SHA-256 approval hash from the current plan",
        )

    ingest = subparsers.add_parser("wiki-ingest", help="Prepare candidate notes")
    _add_common_arguments(ingest)
    ingest.add_argument(
        "--source",
        action="append",
        required=True,
        help="Local source path; repeat for multiple sources",
    )
    ingest.add_argument(
        "--output",
        help="Legacy candidate output path; preview is returned inline",
    )
    ingest.add_argument(
        "--apply",
        action="store_true",
        help="Apply the approved ingest plan",
    )
    ingest.add_argument(
        "--approved-hash",
        help="SHA-256 approval hash from the current preview",
    )

    query = subparsers.add_parser("wiki-query", help="Query local vault content")
    _add_common_arguments(query)
    query.add_argument("--query", required=True, help="Query text")
    query.add_argument("--limit", type=int, default=10, help="Maximum result count")

    save = subparsers.add_parser("save", help="Preview or apply a change set")
    _add_common_arguments(save)
    save.add_argument(
        "--changes",
        required=True,
        help="Path to scoped save request JSON",
    )
    mode = save.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview changes")
    mode.add_argument("--apply", action="store_true", help="Apply reviewed changes")
    save.add_argument(
        "--approved-hash",
        help="SHA-256 approval hash from the current dry-run",
    )

    transaction = subparsers.add_parser(
        "transaction",
        help="Inspect, apply, or recover operation bundles",
    )
    transaction_subparsers = transaction.add_subparsers(
        dest="transaction_command",
        required=True,
    )

    inspect = transaction_subparsers.add_parser(
        "inspect",
        help="Validate a bundle and target preconditions",
    )
    _add_common_arguments(inspect)
    inspect.add_argument("--bundle", required=True, help="Path to operation bundle")

    apply = transaction_subparsers.add_parser(
        "apply",
        help="Atomically apply an operation bundle",
    )
    _add_common_arguments(apply)
    apply.add_argument("--bundle", required=True, help="Path to operation bundle")

    recover = transaction_subparsers.add_parser(
        "recover",
        help="Roll back an incomplete transaction journal",
    )
    _add_common_arguments(recover)
    recover.add_argument(
        "--transaction-id",
        required=True,
        help="Transaction id to recover",
    )

    return parser


def dispatch(args: argparse.Namespace) -> CommandResponse:
    if args.command == "wiki":
        wiki_action = getattr(args, "wiki_command", None)
        legacy_vault = getattr(args, "legacy_vault", None)
        if wiki_action is not None and legacy_vault is not None:
            raise UsageError(
                "Specify --vault only after the wiki subcommand."
            )
        vault_value = (
            legacy_vault
            if wiki_action is None
            else getattr(args, "vault", None)
        )
    else:
        vault_value = getattr(args, "vault", None)
    if not isinstance(vault_value, str):
        raise UsageError("--vault is required.")
    vault = resolve_vault_path(vault_value)
    is_doctor = (
        args.command == "wiki"
        and getattr(args, "wiki_command", None) == "doctor"
    )
    config_values = {} if is_doctor else load_config(vault).values
    arguments: dict[str, JsonValue] = {
        key: value
        for key, value in vars(args).items()
        if key
        not in {
            "command",
            "vault",
            "legacy_vault",
            "output_format",
        }
    }
    request = CommandRequest(
        command=args.command,
        vault=str(vault),
        arguments=arguments,
        config=config_values,
    )
    return HANDLERS[args.command](request)


def _response_for_error(command: str, error: EngineError) -> CommandResponse:
    return CommandResponse.failure(
        command,
        code=error.code,
        message=error.message,
        details=error.details,
    )


def main(argv: Sequence[str] | None = None) -> int:
    command = "cli"
    try:
        args = build_parser().parse_args(argv)
        command = args.command
        if args.command == "transaction":
            command = f"transaction.{args.transaction_command}"
        elif args.command == "wiki" and args.wiki_command is not None:
            command = f"wiki.{args.wiki_command}"
        response = dispatch(args)
    except EngineError as error:
        response = _response_for_error(command, error)
        print(response.to_json())
        return error.exit_code

    print(response.to_json())
    if response.ok:
        return 0
    return 3 if response.status == "not_implemented" else 1
