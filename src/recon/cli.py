"""Unified ``recon`` command-line entry point."""

from __future__ import annotations

import argparse
import sys

from recon import data as data_module
from recon import reconcile as reconcile_module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("data", help="Generate synthetic ledger + external feed.")
    sub.add_parser("reconcile", help="Reconcile and emit the audit report.")
    sub.add_parser("all", help="Generate then reconcile.")

    args, rest = parser.parse_known_args(argv)

    if args.command in ("data", "all"):
        data_module.main(rest if args.command == "data" else [])
    if args.command in ("reconcile", "all"):
        reconcile_module.main(rest if args.command == "reconcile" else [])
    return 0


if __name__ == "__main__":
    sys.exit(main())
