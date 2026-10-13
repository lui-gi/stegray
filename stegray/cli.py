"""Command-line interface for stegray."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .analyzers.base_analyzer import SubprocessAnalyzer
from .analyzers.registry import discover_analyzers, tool_order
from .report import Reporter
from .runner import (
    RESULTS_FILE,
    OutputDirError,
    plan_analyzers,
    prepare_output_dir,
    run_analyzers,
    write_results,
)


def _tool_set(value: str) -> frozenset[str]:
    """Parse a comma-separated analyzer list, rejecting unknown names."""
    names = frozenset(name.strip() for name in value.split(",") if name.strip())
    unknown = names - set(tool_order())
    if unknown:
        msg = f"unknown analyzer(s): {', '.join(sorted(unknown))}"
        raise argparse.ArgumentTypeError(msg)
    return names


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="stegray",
        description="Run common steganalysis tools against a file and report what they find.",
    )
    parser.add_argument("file", nargs="?", type=Path, help="file to analyze")
    parser.add_argument("-p", "--password", help="password for the tools that take one")
    parser.add_argument(
        "-d",
        "--deep",
        action="store_true",
        help="also run the slow analyzers (outguess)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="DIR",
        help=f"where to keep extracted files and {RESULTS_FILE} (default: ./<file>.stegray)",
    )
    parser.add_argument(
        "--only",
        type=_tool_set,
        default=frozenset(),
        metavar="TOOLS",
        help="comma-separated analyzers to run, instead of all",
    )
    parser.add_argument(
        "--skip",
        type=_tool_set,
        default=frozenset(),
        metavar="TOOLS",
        help="comma-separated analyzers to leave out",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=SubprocessAnalyzer.timeout,
        metavar="SECONDS",
        help="time limit per tool invocation (default: %(default)s)",
    )
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    parser.add_argument(
        "--full",
        action="store_true",
        help="never shorten long tool output on a terminal",
    )
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="list the analyzers and whether their tools are installed, then exit",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def list_tools() -> None:
    """Print every analyzer with its file types and install status."""
    for cls in discover_analyzers():
        missing = cls.missing_tools()
        status = f"missing: {', '.join(missing)}" if missing else "ok"
        accepts = ", ".join(sorted(cls.accepts)) or "any file"
        flags = "".join(
            f" [{flag}]"
            for flag, on in (("password", cls.needs_password), ("deep", cls.deep_only))
            if on
        )
        print(f"{cls.name:<10} {status:<24} {accepts}{flags}")


def analyze(args: argparse.Namespace) -> int:
    """Analyze ``args.file`` and print the report."""
    path: Path = args.file
    if not path.is_file():
        print(f"stegray: {path}: not a file", file=sys.stderr)
        return 2
    output_dir: Path = args.output or Path(f"{path.name}.stegray")
    SubprocessAnalyzer.timeout = args.timeout

    plan = plan_analyzers(path, deep=args.deep, only=args.only, skip=args.skip)
    try:
        prepare_output_dir(output_dir, tool_order())
    except OutputDirError as exc:
        print(f"stegray: {exc}; choose another one with --output", file=sys.stderr)
        return 2

    reporter = Reporter(
        sys.stdout,
        color=False if args.no_color else None,
        full=args.full,
        name_width=max((len(cls.name) for cls in plan.selected), default=0),
        tools=tool_order(),
    )
    if not args.json:
        reporter.header(
            f"stegray {__version__}",
            {"file": str(path), "type": plan.file_type.mime, "output": f"{output_dir}/"},
        )

    results: dict[str, Any] = {}
    for name, result in run_analyzers(path, output_dir, plan.selected, args.password):
        results[name] = result
        if not args.json:
            reporter.section(name, result)

    summary = {
        "file": str(path),
        "mime": plan.file_type.mime,
        "output_dir": str(output_dir),
        "results": results,
        "skipped": plan.skipped,
    }
    write_results(output_dir, summary)
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        reporter.summary(plan.skipped)
    return 0


def main(argv: list[str] | None = None) -> None:
    """Entry point of the ``stegray`` command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_tools:
        list_tools()
        return
    if args.file is None:
        parser.error("the following arguments are required: file")
    try:
        sys.exit(analyze(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # Reader went away (e.g. `stegray f | head`); nothing left to report.
        sys.stderr.close()
        sys.exit(1)
