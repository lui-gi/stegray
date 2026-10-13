"""Tests for analyzer auto-discovery."""

from stegray.analyzers.registry import discover_analyzers, get_analyzers, tool_order

EXPECTED = {
    "binwalk", "exiftool", "file", "foremost", "identify", "jpseek", "jsteg", "openstego",
    "outguess", "pcrt", "pdfid", "pdfinfo", "pngcheck", "steghide", "strings", "zsteg",
}  # fmt: skip


def test_all_analyzers_are_discovered() -> None:
    """Every analyzer module registers itself, and nothing else does."""
    assert set(tool_order()) == EXPECTED


def test_display_order_is_stable() -> None:
    """Analyzers come back sorted by display order."""
    orders = [cls.display_order for cls in discover_analyzers()]
    assert orders == sorted(orders)
    assert tool_order()[0] == "file"


def test_deep_and_type_gates() -> None:
    """deep_only tools need deep=True; typed tools need a matching tag."""
    jpeg = frozenset({"jpeg", "image"})
    assert "outguess" not in [c.name for c in get_analyzers(deep=False, tags=jpeg)]
    names = [c.name for c in get_analyzers(deep=True, tags=jpeg)]
    assert "outguess" in names
    assert "zsteg" not in names
    assert "strings" in names
