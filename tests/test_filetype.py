"""Tests for file-type detection and the analyzer gate built on it."""

from pathlib import Path

from stegray.filetype import _mime_from_extension, _mime_from_magic, detect_file_type
from stegray.runner import plan_analyzers

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_detects_png_and_jpeg_tags() -> None:
    """Fixtures are classified with both their format and kind tags."""
    assert detect_file_type(FIXTURES / "zsteg_lsb.png").tags == {"png", "image"}
    assert detect_file_type(FIXTURES / "steghide.jpg").tags == {"jpeg", "image"}
    assert detect_file_type(FIXTURES / "sample.pdf").tags == {"pdf"}


def test_magic_sniff_ignores_the_extension(tmp_path: Path) -> None:
    """The stdlib fallback reads content, so a renamed file is still recognised."""
    disguised = tmp_path / "notes.txt"
    disguised.write_bytes((FIXTURES / "steghide.jpg").read_bytes())
    assert _mime_from_magic(disguised) == "image/jpeg"

    wav = tmp_path / "a.bin"
    wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    assert _mime_from_magic(wav) == "audio/x-wav"
    assert _mime_from_magic(tmp_path / "missing") == ""


def test_extension_fallback() -> None:
    """Unknown content falls back to the extension table."""
    assert _mime_from_extension(Path("x.JPG")) == "image/jpeg"
    assert _mime_from_extension(Path("x.unknown")) == ""


def test_undetectable_file_is_other(tmp_path: Path) -> None:
    """An empty file degrades to kind 'other' with no tags instead of raising."""
    empty = tmp_path / "empty"
    empty.touch()
    file_type = detect_file_type(empty)
    assert file_type.kind == "other"
    assert not file_type.tags


def test_plan_gates_by_type_and_depth() -> None:
    """PNG-only tools are skipped on a JPEG, and outguess waits for --deep."""
    plan = plan_analyzers(FIXTURES / "steghide.jpg", deep=False)
    assert plan.skipped["zsteg"] == "file type"
    assert plan.skipped["pcrt"] == "file type"
    assert plan.skipped["outguess"] == "needs --deep"
    assert "pdfinfo" in plan.skipped

    deep = plan_analyzers(FIXTURES / "steghide.jpg", deep=True)
    assert deep.skipped.get("outguess") != "needs --deep"


def test_plan_honours_only_and_skip() -> None:
    """--only / --skip narrow the selection without listing the rest as skipped."""
    plan = plan_analyzers(FIXTURES / "zsteg_lsb.png", deep=False, only=frozenset({"pcrt"}))
    assert [cls.name for cls in plan.selected] == ["pcrt"]
    assert not plan.skipped

    plan = plan_analyzers(FIXTURES / "zsteg_lsb.png", deep=False, skip=frozenset({"pcrt"}))
    assert "pcrt" not in [cls.name for cls in plan.selected]
