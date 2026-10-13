"""End-to-end tests of the command line against the stego fixtures.

Tests needing an external tool are skipped when it is not installed; run them
inside the container image to cover every analyzer.
"""

import json
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from stegray.analyzers.pcrt import PCRTAnalyzer
from stegray.cli import main
from stegray.png import PNG

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STEGO_PASSWORD = "s3cret"  # noqa: S105 — the passphrase baked into the fixtures


def needs(*tools: str) -> pytest.MarkDecorator:
    """Skip a test unless every tool is on PATH."""
    missing = [tool for tool in tools if shutil.which(tool) is None]
    return pytest.mark.skipif(bool(missing), reason=f"not installed: {', '.join(missing)}")


def run(tmp_path: Path, fixture: str, *args: str) -> dict:
    """Run the CLI on a fixture and return the saved summary."""
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exit_info:
        main([str(FIXTURES / fixture), "-o", str(out), *args])
    assert exit_info.value.code == 0
    return json.loads((out / "results.json").read_text(encoding="utf-8"))


def extracted(tmp_path: Path, result: dict) -> list[Path]:
    """Resolve a result's extracted files, checking they exist and hold data."""
    assert result["status"] == "ok", result
    paths = [tmp_path / "out" / name for name in result["extracted"]]
    assert any(path.stat().st_size > 0 for path in paths)
    return paths


def test_pcrt_reports_trailing_data(tmp_path: Path) -> None:
    """Data appended after IEND is saved, without a bogus 'repaired' copy."""
    summary = run(tmp_path, "polyglot_zip.png", "--only", "pcrt")
    result = summary["results"]["pcrt"]
    assert result["extracted"] == ["pcrt/extra_data.bin"]
    assert (tmp_path / "out" / "pcrt" / "extra_data.bin").read_bytes().startswith(b"PK")
    assert result["note"] == "Extra data found after IEND"


def test_pcrt_recovers_tampered_dimensions(tmp_path: Path) -> None:
    """A PNG whose IHDR size was zeroed is repaired from its CRC, without a database."""
    ihdr_data = struct.pack(">IIBBBBB", 640, 480, 8, 2, 0, 0, 0)
    crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    raw = zlib.compress(b"\x00" * 16)
    idat = (
        struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", zlib.crc32(b"IDAT" + raw))
    )
    iend = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"
    tampered = struct.pack(">IIBBBBB", 0, 0, 8, 2, 0, 0, 0)
    png = tmp_path / "broken.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + tampered + crc + idat + iend,
    )

    result = PCRTAnalyzer.execute(png, tmp_path)

    assert result["status"] == "ok", result
    assert result["extracted"] == ["pcrt/recovered_broken.png"]
    repaired = PNG((tmp_path / "pcrt" / "recovered_broken.png").read_bytes())
    assert repaired.get_pic_info()
    assert (repaired.width, repaired.height) == (640, 480)


def test_valid_png_is_not_reported_as_repaired(tmp_path: Path) -> None:
    """An intact PNG produces no recovered copy."""
    result = run(tmp_path, "zsteg_lsb.png", "--only", "pcrt")["results"]["pcrt"]
    assert result["status"] == "ok"
    assert "extracted" not in result
    assert not (tmp_path / "out" / "pcrt").exists()


def test_json_output_matches_saved_results(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """--json prints exactly what is written to results.json."""
    summary = run(tmp_path, "zsteg_lsb.png", "--only", "pcrt", "--json")
    assert json.loads(capsys.readouterr().out) == summary
    assert summary["mime"] == "image/png"


def test_report_lists_skipped_tools(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """The text report names the analyzers that did not apply to the file."""
    run(tmp_path, "steghide.jpg", "--skip", "binwalk,foremost")
    out = capsys.readouterr().out
    assert "summary" in out
    assert "(file type)" in out
    assert "outguess (needs --deep)" in out
    assert "\033[" not in out  # no colors when not a terminal


def test_rerun_reuses_its_own_output_dir(tmp_path: Path) -> None:
    """A second run into the same directory replaces the previous extraction."""
    run(tmp_path, "polyglot_zip.png", "--only", "pcrt")
    stale = tmp_path / "out" / "pcrt" / "stale.bin"
    stale.write_bytes(b"old")
    run(tmp_path, "polyglot_zip.png", "--only", "pcrt")
    assert not stale.exists()


def test_refuses_unrelated_output_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A non-empty directory stegray did not create is left untouched."""
    out = tmp_path / "out"
    (out / "pcrt").mkdir(parents=True)
    keep = out / "pcrt" / "mine.txt"
    keep.write_text("keep me")
    with pytest.raises(SystemExit) as exit_info:
        main([str(FIXTURES / "polyglot_zip.png"), "-o", str(out)])
    assert exit_info.value.code == 2
    assert keep.read_text() == "keep me"
    assert "not created by stegray" in capsys.readouterr().err


def test_missing_file_and_unknown_tool_fail(tmp_path: Path) -> None:
    """Usage errors exit with status 2."""
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path / "nope.png")])
    assert exit_info.value.code == 2
    with pytest.raises(SystemExit) as exit_info:
        main([str(FIXTURES / "sample.pdf"), "--only", "decomposer"])
    assert exit_info.value.code == 2


@needs("file", "strings")
def test_file_and_strings(tmp_path: Path) -> None:
    """The file-agnostic tools run on any input."""
    results = run(tmp_path, "sample.pdf", "--only", "file,strings")["results"]
    assert "PDF" in results["file"]["output"]
    assert any("%PDF" in line for line in results["strings"]["output"])


@needs("binwalk")
def test_binwalk_extracts_appended_zip(tmp_path: Path) -> None:
    """The ZIP appended to the polyglot PNG is carved out."""
    result = run(tmp_path, "polyglot_zip.png", "--only", "binwalk")["results"]["binwalk"]
    assert any("zip" in line.lower() for line in result["output"])
    assert extracted(tmp_path, result)
    assert max(len(line) for line in result["output"]) < 200


@needs("foremost")
def test_foremost_carves_or_stays_quiet(tmp_path: Path) -> None:
    """Foremost never reports its audit file alone as an extraction."""
    result = run(tmp_path, "polyglot_zip.png", "--only", "foremost")["results"]["foremost"]
    assert result["status"] == "ok", result
    assert result.get("extracted") != ["foremost/audit.txt"]


@needs("exiftool")
def test_exiftool_returns_metadata(tmp_path: Path) -> None:
    """Exiftool output is parsed into key/value pairs."""
    result = run(tmp_path, "steghide.jpg", "--only", "exiftool")["results"]["exiftool"]
    assert result["output"]["File Type"] == "JPEG"


@needs("identify")
def test_identify_describes_image(tmp_path: Path) -> None:
    """Identify reports the image format."""
    result = run(tmp_path, "zsteg_lsb.png", "--only", "identify")["results"]["identify"]
    assert any("PNG" in line for line in result["output"])


@needs("pngcheck")
def test_pngcheck_passes_valid_png(tmp_path: Path) -> None:
    """Pngcheck lists the chunks of a valid PNG."""
    result = run(tmp_path, "zsteg_lsb.png", "--only", "pngcheck")["results"]["pngcheck"]
    assert result["status"] == "ok", result
    assert any("IHDR" in line for line in result["output"])


@needs("zsteg")
def test_zsteg_finds_lsb_text(tmp_path: Path) -> None:
    """Zsteg reports the LSB payload."""
    result = run(tmp_path, "zsteg_lsb.png", "--only", "zsteg")["results"]["zsteg"]
    assert result["status"] == "ok", result
    assert any("text" in line for line in result["output"])


@needs("pdfinfo")
def test_pdfinfo_reads_metadata(tmp_path: Path) -> None:
    """Pdfinfo reports the page count."""
    result = run(tmp_path, "sample.pdf", "--only", "pdfinfo")["results"]["pdfinfo"]
    assert any(line.startswith("Pages:") for line in result["output"])


@needs("pdfid")
def test_pdfid_counts_objects(tmp_path: Path) -> None:
    """Pdfid lists the object counts."""
    result = run(tmp_path, "sample.pdf", "--only", "pdfid")["results"]["pdfid"]
    assert any("/Page" in line for line in result["output"])


@needs("jsteg")
def test_jsteg_reveals_payload(tmp_path: Path) -> None:
    """Jsteg prints the hidden message."""
    result = run(tmp_path, "jsteg.jpg", "--only", "jsteg")["results"]["jsteg"]
    assert result["status"] == "ok", result
    assert result["output"]


@needs("steghide")
def test_steghide_extracts_with_password(tmp_path: Path) -> None:
    """Steghide extracts with the right passphrase and reports no result without it."""
    result = run(tmp_path, "steghide.jpg", "--only", "steghide", "-p", STEGO_PASSWORD)
    assert extracted(tmp_path, result["results"]["steghide"])
    wrong = run(tmp_path, "steghide.jpg", "--only", "steghide", "-p", "wrong")
    assert wrong["results"]["steghide"]["status"] == "error"
    assert not (tmp_path / "out" / "steghide").exists()


@needs("openstego")
def test_openstego_extracts_with_password(tmp_path: Path) -> None:
    """OpenStego extracts once the matching crypt algorithm is tried."""
    result = run(tmp_path, "openstego.png", "--only", "openstego", "-p", STEGO_PASSWORD)
    assert extracted(tmp_path, result["results"]["openstego"])


@needs("jpseek", "expect")
def test_jpseek_extracts_with_password(tmp_path: Path) -> None:
    """Jpseek recovers the jphide payload."""
    result = run(tmp_path, "jphide.jpg", "--only", "jpseek", "-p", STEGO_PASSWORD)
    assert extracted(tmp_path, result["results"]["jpseek"])


@needs("outguess")
def test_outguess_runs_only_in_deep_mode(tmp_path: Path) -> None:
    """Outguess is gated behind --deep and extracts with the passphrase."""
    args = ("--only", "outguess", "-p", STEGO_PASSWORD)
    shallow = run(tmp_path, "outguess.jpg", *args)
    assert shallow["skipped"]["outguess"] == "needs --deep"
    deep = run(tmp_path, "outguess.jpg", *args, "--deep")
    assert extracted(tmp_path, deep["results"]["outguess"])
