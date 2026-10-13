"""Foremost Analyzer for stegray."""

from pathlib import Path

from .base_analyzer import SubprocessAnalyzer

FOREMOST_ERROR_THRESHOLD = 60


class ForemostAnalyzer(SubprocessAnalyzer):
    """Analyzer for foremost."""

    name = "foremost"
    has_archive = True
    display_order = 60

    def __init__(self, input_img: Path, output_dir: Path) -> None:
        """Initialize the foremost analyzer."""
        super().__init__(input_img, output_dir)
        self.cmd = ["foremost", "-o", str(self.get_extracted_dir()), "-i", self.img]

    def collect_extracted(self, extracted_dir: Path | None = None) -> list[str]:
        """Discard the folder when foremost carved nothing (it always writes audit.txt)."""
        directory = extracted_dir or self.get_extracted_dir()
        audit = directory / "audit.txt"
        if audit.is_file() and sum(1 for _ in directory.rglob("*")) == 1:
            audit.unlink()
        return super().collect_extracted(extracted_dir)

    def is_error(self, returncode: int, stdout: str, stderr: str, *, zip_exist: bool) -> bool:
        """Check if the result is an error."""
        _ = stdout, returncode, zip_exist
        return len(stderr) > FOREMOST_ERROR_THRESHOLD

    def process_output(self, stdout: str, stderr: str) -> str | list[str] | dict[str, str]:
        """Process the stdout/stderr."""
        _ = stdout
        if "Processing" in stderr and "|*|" in stderr:
            return stderr.strip().replace("\n", "")
        return []
