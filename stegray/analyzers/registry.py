"""Analyzer auto-discovery.

Importing every module in this package triggers ``SubprocessAnalyzer``
subclass registration, so adding an analyzer only requires creating its file
(and installing its tool).
"""

import importlib
import pkgutil

from .base_analyzer import REGISTRY, SubprocessAnalyzer

_SKIPPED_MODULES = frozenset({"base_analyzer", "registry"})
_PACKAGE = __package__ or "stegray.analyzers"


def discover_analyzers() -> list[type[SubprocessAnalyzer]]:
    """Import all analyzer modules and return registered analyzers in display order."""
    package = importlib.import_module(_PACKAGE)
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in _SKIPPED_MODULES:
            continue
        importlib.import_module(f"{_PACKAGE}.{module_info.name}")

    names = [cls.name for cls in REGISTRY]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        msg = f"Duplicate analyzer names registered: {sorted(duplicates)}"
        raise RuntimeError(msg)

    return sorted(REGISTRY, key=lambda cls: (cls.display_order, cls.name))


def get_analyzers(
    *,
    deep: bool,
    tags: frozenset[str] | None = None,
) -> list[type[SubprocessAnalyzer]]:
    """Return the analyzers to run for a file.

    ``deep`` includes the ``deep_only`` analyzers. ``tags`` are the detected
    file-type tags (see :mod:`stegray.filetype`); when provided, an analyzer
    is skipped unless it is file-agnostic (empty ``accepts``) or its ``accepts``
    intersects ``tags``. ``tags=None`` disables the gate.
    """
    result: list[type[SubprocessAnalyzer]] = []
    for cls in discover_analyzers():
        if cls.deep_only and not deep:
            continue
        if tags is not None and cls.accepts and not (cls.accepts & tags):
            continue
        result.append(cls)
    return result


def tool_order() -> list[str]:
    """Ordered analyzer names for the report."""
    return [cls.name for cls in discover_analyzers()]
