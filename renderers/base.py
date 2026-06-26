"""Shared rendering logic and the renderer interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AnalysisOptions, CodeElement, DisplayFilter, TreeNode


class BaseRenderer(ABC):
    """Common helpers shared by all concrete renderers."""

    def __init__(self, display_filter: DisplayFilter, analysis_opts: AnalysisOptions) -> None:
        """Store the filter and options used while rendering."""
        self._filter = display_filter
        self._opts = analysis_opts

    @abstractmethod
    def render(self, root: TreeNode) -> str:
        """Render *root* into the renderer's output format."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _is_visible(self, element: CodeElement) -> bool:
        """Return *True* when *element* should be rendered."""
        flt = self._filter
        return (
            (element.kind == "function" and flt.show_functions)
            or (element.kind == "class" and flt.show_classes)
            or (element.kind == "import" and flt.show_imports)
            or (element.kind == "variable" and flt.show_variables)
        )

    def _visible_elements(self, elements: list[CodeElement]) -> list[CodeElement]:
        """Filter *elements* by the active :class:`DisplayFilter`."""
        return [e for e in elements if self._is_visible(e)]

    def _decorated_sig(self, element: CodeElement) -> str:
        """Return the signature prefixed with decorators when enabled."""
        if element.decorators:
            return " ".join(element.decorators) + " " + element.sig
        return element.sig

    def _lineno_suffix(self, element: CodeElement) -> str:
        """Return a ``"(L<n>)"`` suffix when line numbers are enabled."""
        if self._opts.include_line_numbers and element.lineno:
            return f" (L{element.lineno})"
        return ""
