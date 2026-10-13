"""PCRT (PNG Check & Repair Tool) Analyzer for stegray."""

from typing import Any

from stegray.png import PNG

from .base_analyzer import SubprocessAnalyzer


class PCRTAnalyzer(SubprocessAnalyzer):
    """Analyzer for PCRT (PNG Check & Repair Tool)."""

    name = "pcrt"
    has_archive = True
    display_order = 90
    requires = ()  # pure Python, no external tool
    accepts = frozenset({"png"})

    def _write_extracted(self, name: str, data: bytes) -> None:
        """Write a recovered file into the extraction folder."""
        extracted_dir = self.get_extracted_dir()
        extracted_dir.mkdir(parents=True, exist_ok=True)
        with (extracted_dir / name).open("wb") as f:
            f.write(data)

    def get_results(self, password: str | None = None) -> dict[str, Any]:
        """Analyze PNG and attempt repairs."""
        _ = password
        try:
            with self.input_img.open("rb") as f:
                data = f.read()

            png = PNG(data)
            fixed, extra_data = png.repair()

            result: dict[str, Any] = {}

            if png.errors:
                result["status"] = "failed"
                result["error"] = "\n".join(png.errors)
                if png.logs:
                    result["output"] = png.logs
                return result

            result["status"] = "ok"
            result["output"] = png.logs or ["PNG appears valid, no repairs needed"]

            notes: list[str] = []
            # Save repaired PNG if any fixes were made
            if fixed and png.repaired_data:
                self._write_extracted(
                    f"recovered_{self.input_img.stem}.png",
                    bytes(png.repaired_data),
                )
                notes.append("PNG was repaired and saved")

            # Save extra data if found after IEND
            if extra_data:
                self._write_extracted("extra_data.bin", extra_data)
                notes.append("Extra data found after IEND")

            if notes:
                result["note"] = " | ".join(notes)
                result["extracted"] = self.collect_extracted()

        except (ValueError, OSError, RuntimeError, TypeError) as e:
            return {"status": "failed", "error": f"Analysis failed: {e!s}"}
        else:
            return result
