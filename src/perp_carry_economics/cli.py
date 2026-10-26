"""Command-line entry point (`pce`); later tasks register their subcommands in COMMANDS."""

import argparse
from collections.abc import Callable

COMMANDS: dict[str, Callable[[argparse.Namespace], None]] = {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pce", description="BTC cash-and-carry backtest.")
    parser.add_subparsers(dest="command")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return
    COMMANDS[args.command](args)
