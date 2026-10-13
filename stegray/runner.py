"""Run the analyzers against a file and gather their results."""

import json
import shutil
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analyzers.base_analyzer import SubprocessAnalyzer
from .analyzers.registry import discover_analyzers
from .filetype import FileType, detect_file_type

RESULTS_FILE = "results.json"


class OutputDirError(Exception):
    """The output directory cannot be used safely."""


@dataclass
class Plan:
    """Which analyzers run on a file, and why the others do not."""

    file_type: FileType
    selected: list[type[SubprocessAnalyzer]] = field(default_factory=list)
    # Analyzer name -> reason it is not run.
    skipped: dict[str, str] = field(default_factory=dict)


def plan_analyzers(
    path: Path,
    *,
    deep: bool,
    only: frozenset[str] = frozenset(),
    skip: frozenset[str] = frozenset(),
) -> Plan:
    """Select the analyzers for ``path`` from its file type and the user's filters."""
    plan = Plan(file_type=detect_file_type(path))
    tags = plan.file_type.tags
    for cls in discover_analyzers():
        if (only and cls.name not in only) or cls.name in skip:
            continue
        if cls.accepts and not (cls.accepts & tags):
            plan.skipped[cls.name] = "file type"
        elif cls.deep_only and not deep:
            plan.skipped[cls.name] = "needs --deep"
        elif missing := cls.missing_tools():
            plan.skipped[cls.name] = f"not installed: {', '.join(missing)}"
        else:
            plan.selected.append(cls)
    return plan


def prepare_output_dir(output_dir: Path, tools: list[str]) -> None:
    """Create the output directory, clearing extractions left by a previous run.

    A non-empty directory is only reused when it holds a ``results.json`` from an
    earlier run, so pointing ``--output`` at an unrelated folder never deletes
    anything in it.
    """
    if output_dir.exists():
        if not output_dir.is_dir():
            msg = f"{output_dir} exists and is not a directory"
            raise OutputDirError(msg)
        if any(output_dir.iterdir()) and not (output_dir / RESULTS_FILE).is_file():
            msg = f"{output_dir} is not empty and was not created by stegray"
            raise OutputDirError(msg)
        for tool in tools:
            stale = output_dir / tool
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale)
    output_dir.mkdir(parents=True, exist_ok=True)


def run_analyzers(
    path: Path,
    output_dir: Path,
    analyzers: list[type[SubprocessAnalyzer]],
    password: str | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Run the analyzers concurrently, yielding ``(name, result)`` in display order."""
    if not analyzers:
        return
    with ThreadPoolExecutor(max_workers=len(analyzers)) as pool:
        futures = [
            (cls.name, pool.submit(cls.execute, path, output_dir, password)) for cls in analyzers
        ]
        for name, future in futures:
            yield name, future.result()


def write_results(output_dir: Path, summary: dict[str, Any]) -> None:
    """Save the run summary next to the extracted files."""
    with (output_dir / RESULTS_FILE).open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
