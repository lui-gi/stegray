"""Binwalk Analyzer for stegray."""

import functools
import os
import re
import subprocess
from pathlib import Path

from .base_analyzer import SubprocessAnalyzer

_BINWALK_RUST_MAJOR = 3
_TABLE_COLUMNS = 3


@functools.cache
def _is_rust_binwalk() -> bool:
    """Whether the installed binwalk is the v3+ Rust rewrite (different flags)."""
    try:
        probe = subprocess.run(
            ["binwalk", "--version"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # v3 prints "binwalk 3.1.0"; v2 has no --version flag and prints its usage.
    words = probe.stdout.split()
    if probe.returncode != 0 or len(words) != 2:  # noqa: PLR2004
        return False
    major = words[1].split(".")[0]
    return major.isdigit() and int(major) >= _BINWALK_RUST_MAJOR


class BinwalkAnalyzer(SubprocessAnalyzer):
    """Analyzer for binwalk."""

    name = "binwalk"
    has_archive = True
    display_order = 50

    def __init__(self, input_img: Path, output_dir: Path) -> None:
        """Initialize the binwalk analyzer."""
        super().__init__(input_img, output_dir)
        extracted_dir = str(self.get_extracted_dir())
        if _is_rust_binwalk():
            self.cmd = ["binwalk", "--matryoshka", "-e", "-C", extracted_dir, self.img]
        else:
            # Bounded extraction: untrusted input can be a decompression bomb, so
            # cap recursion depth, file count and per-file size.
            self.cmd = [
                "binwalk",
                "--matryoshka",
                "--depth=2",
                "--count=100",
                "--size=10485760",
                "-e",
                "-C",
                extracted_dir,
                self.img,
            ]
            # binwalk refuses to extract as root (e.g. in a container) without this.
            if os.geteuid() == 0:
                self.cmd.append("--run-as=root")
        self.make_folder = False  # Binwalk already create its own folder

    def collect_extracted(self, extracted_dir: Path | None = None) -> list[str]:
        """Drop the symlinks binwalk leaves next to its extraction folders."""
        directory = extracted_dir or self.get_extracted_dir()
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_symlink():
                    path.unlink()
        return super().collect_extracted(extracted_dir)

    def is_error(self, returncode: int, stdout: str, stderr: str, *, zip_exist: bool) -> bool:
        """Check whether binwalk execution failed."""
        _ = returncode, stdout
        return len(stderr) > 0 and not zip_exist

    def process_output(self, stdout: str, stderr: str) -> list[str]:
        """Process the stdout into lines, re-fitting the terminal-wide signature table."""
        _ = stderr
        lines = [line.strip() for line in stdout.split("\n")]
        lines = [line for line in lines if line.strip("-")]
        rows = [re.split(r"\s{2,}", line, maxsplit=_TABLE_COLUMNS - 1) for line in lines]
        table = [row for row in rows if len(row) == _TABLE_COLUMNS]
        widths = (
            [max(len(row[i]) for row in table) for i in range(_TABLE_COLUMNS - 1)] if table else []
        )
        return [
            "  ".join(
                [*(cell.ljust(width) for cell, width in zip(row, widths, strict=False)), row[-1]],
            )
            if len(row) == _TABLE_COLUMNS
            else line
            for line, row in zip(lines, rows, strict=True)
        ]
