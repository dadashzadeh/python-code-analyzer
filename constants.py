"""Shared constants for the directory tree package."""

from __future__ import annotations

#: Directories ignored unless the caller overrides the default set.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".git", ".hg", "webfonts", ".svn", "node_modules", ".venv", "venv", ".tox", "docs", "downloaded_files"}
)

#: File extensions ignored by default.
DEFAULT_EXCLUDE_EXTENSIONS: frozenset[str] = frozenset({".pyc", ".pyo", ".pyd"})

#: Box-drawing connectors used by the text renderer.
CONNECTOR_LAST = "┗ "
CONNECTOR_MID = "┣ "
INDENT_LAST = "  "
INDENT_MID = "┃ "

#: Maximum length of a truncated single-line docstring excerpt.
DOCSTRING_EXCERPT_LENGTH = 80
