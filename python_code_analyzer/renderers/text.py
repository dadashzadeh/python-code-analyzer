"""Plain-text (box-drawing) tree renderer with extended analysis support."""

from __future__ import annotations

from typing import Iterator

from ..constants import CONNECTOR_LAST, CONNECTOR_MID, INDENT_LAST, INDENT_MID
from ..models import CodeElement, TreeNode, CommentMarker, ComplexityInfo, DependencyInfo, DeadCodeInfo
from .base import BaseRenderer


class TextRenderer(BaseRenderer):
    """Render a :class:`TreeNode` as an indented text tree."""

    def render(self, root: TreeNode) -> str:
        """Return the full tree as a newline-joined string."""
        lines = list(self._render_node(root, prefix="", is_last=True, is_root=True))
        return "\n".join(lines)

    def _render_node(
        self,
        node: TreeNode,
        prefix: str,
        is_last: bool,
        is_root: bool = False,
    ) -> Iterator[str]:
        """Yield the lines for *node* and its descendants."""
        connector = "" if is_root else (CONNECTOR_LAST if is_last else CONNECTOR_MID)
        yield f"{prefix}{connector}{node.name}"

        child_prefix = prefix + (
            "" if is_root else (INDENT_LAST if is_last else INDENT_MID)
        )

        if node.error:
            yield f"{child_prefix}{CONNECTOR_MID}[{node.error}]"
            return

        if node.is_dir:
            count = len(node.children)
            for idx, child in enumerate(node.children):
                yield from self._render_node(child, child_prefix, idx == count - 1)
        else:
            visible = self._visible_elements(node.elements)
            yield from self._render_elements(visible, child_prefix)
            
            if self._filter.show_call_graph and node.module_calls:
                yield from self._render_module_calls(node.module_calls, child_prefix)

            # Render comment markers if enabled
            if self._filter.show_markers and node.comment_markers:
                yield from self._render_markers(node.comment_markers, child_prefix)
            
            # Render dependencies if enabled
            if self._filter.show_dependencies and node.dependencies:
                yield from self._render_dependencies(node.dependencies, child_prefix)
            
            # Render dead code warnings if enabled
            if self._filter.show_dead_code and node.dead_code:
                yield from self._render_dead_code(node.dead_code, child_prefix)

    def _render_elements(
        self,
        elements: list[CodeElement],
        prefix: str,
    ) -> Iterator[str]:
        """Yield the lines for code *elements* (recursively)."""
        count = len(elements)
        for idx, element in enumerate(elements):
            is_last = idx == count - 1
            connector = CONNECTOR_LAST if is_last else CONNECTOR_MID
            yield f"{prefix}{connector}{self._element_text(element)}"

            inner_prefix = prefix + (INDENT_LAST if is_last else INDENT_MID)

            # Render complexity if enabled
            if self._filter.show_complexity and element.complexity:
                yield from self._render_complexity(element.complexity, inner_prefix)

            # Render type hints if enabled
            if self._filter.show_type_hints and element.type_hints:
                yield from self._render_type_hints(element, inner_prefix)

            # Render exceptions if enabled
            if self._filter.show_exceptions and element.exceptions:
                yield from self._render_exceptions(element.exceptions, inner_prefix)

            # Render calls if enabled
            if self._filter.show_call_graph and element.calls:
                yield from self._render_calls(element.calls, inner_prefix)

            # Render dead code indicator for element
            if self._filter.show_dead_code and element.is_dead_code:
                yield f"{inner_prefix}{CONNECTOR_MID}💀 DEAD CODE: Never called/used"

            children = self._visible_elements(element.children)
            if children:
                yield from self._render_elements(children, inner_prefix)

    def _element_text(self, element: CodeElement) -> str:
        """Build the single-line representation of *element*."""
        text = self._decorated_sig(element) + self._lineno_suffix(element)
        if element.doc:
            text += f' - "{element.doc}"'
        # Add complexity badge inline if enabled
        if self._filter.show_complexity and element.complexity:
            cc = element.complexity.cyclomatic
            badge = self._complexity_badge(cc)
            text += f" {badge}"
        return text

    def _render_module_calls(self, calls: list, prefix: str) -> Iterator[str]:
        """Render module-level calls."""
        unique_calls = set()
        for call in calls:
            if call.is_method and call.receiver:
                unique_calls.add(f"{call.receiver}.{call.callee}()")
            else:
                unique_calls.add(f"{call.callee}()")
        
        if unique_calls:
            calls_str = ", ".join(sorted(unique_calls))
            yield f"{prefix}{CONNECTOR_MID}📞 Calls: {calls_str}"
    
    
    def _render_complexity(self, complexity: ComplexityInfo, prefix: str) -> Iterator[str]:
        """Render complexity metrics."""
        cc = complexity.cyclomatic
        cognitive = complexity.cognitive
        halstead = complexity.halstead_volume
        
        parts = [f"CC={cc}"]
        if cognitive:
            parts.append(f"Cognitive={cognitive}")
        if halstead:
            parts.append(f"Halstead={halstead:.1f}")
        
        level = self._complexity_level(cc)
        yield f"{prefix}{CONNECTOR_MID}📊 Complexity: {' | '.join(parts)} [{level}]"

    def _complexity_badge(self, cc: int) -> str:
        """Return a colored badge based on cyclomatic complexity."""
        if cc <= 5:
            return "🟢"  # Simple
        elif cc <= 10:
            return "🟡"  # Moderate
        elif cc <= 20:
            return "🟠"  # Complex
        else:
            return "🔴"  # Very Complex

    def _complexity_level(self, cc: int) -> str:
        """Return complexity level description."""
        if cc <= 5:
            return "Simple"
        elif cc <= 10:
            return "Moderate"
        elif cc <= 20:
            return "Complex"
        else:
            return "Very Complex - Consider refactoring"

    def _render_type_hints(self, element: CodeElement, prefix: str) -> Iterator[str]:
        """Render type hint information."""
        th = element.type_hints
        if not th:
            return
        
        hints = []
        if th.params:
            param_str = ", ".join(f"{k}: {v}" for k, v in th.params.items())
            hints.append(f"params: {param_str}")
        if th.return_type:
            hints.append(f"returns: {th.return_type}")
        
        if hints:
            yield f"{prefix}{CONNECTOR_MID}🔷 Types: {' | '.join(hints)}"

    def _render_exceptions(self, exceptions: list, prefix: str) -> Iterator[str]:
        """Render exception information."""
        raises = [e for e in exceptions if e.context == "raise"]
        catches = [e for e in exceptions if e.context == "except"]
        
        if raises:
            raise_names = ", ".join(set(e.name for e in raises))
            yield f"{prefix}{CONNECTOR_MID}⚠️ Raises: {raise_names}"
        if catches:
            catch_names = ", ".join(set(e.name for e in catches))
            yield f"{prefix}{CONNECTOR_MID}🛡️ Catches: {catch_names}"

    def _render_calls(self, calls: list, prefix: str) -> Iterator[str]:
        """Render call graph information."""
        unique_calls = set()
        for call in calls:
            if call.is_method and call.receiver:
                unique_calls.add(f"{call.receiver}.{call.callee}()")
            else:
                unique_calls.add(f"{call.callee}()")
        
        if unique_calls:
            calls_str = ", ".join(sorted(unique_calls)[:10])
            if len(unique_calls) > 10:
                calls_str += f" (+{len(unique_calls) - 10} more)"
            yield f"{prefix}{CONNECTOR_MID}📞 Calls: {calls_str}"

    def _render_markers(self, markers: list[CommentMarker], prefix: str) -> Iterator[str]:
        """Render TODO/FIXME markers."""
        icons = {
            "TODO": "📌", "FIXME": "🔧", "HACK": "⚡", 
            "NOTE": "📝", "XXX": "❌", "BUG": "🐛", "OPTIMIZE": "🚀"
        }
        for marker in markers:
            icon = icons.get(marker.kind, "📋")
            author_part = f"({marker.author}) " if marker.author else ""
            yield f"{prefix}{CONNECTOR_MID}{icon} {marker.kind} {author_part}(L{marker.lineno}): {marker.text}"

    def _render_dependencies(self, dependencies: list[DependencyInfo], prefix: str) -> Iterator[str]:
        """Render module dependency information."""
        if not dependencies:
            return
        
        # Group by type
        stdlib = [d for d in dependencies if d.is_stdlib]
        third_party = [d for d in dependencies if d.is_third_party]
        local = [d for d in dependencies if d.is_local]
        
        if stdlib:
            modules = ", ".join(sorted(set(d.module for d in stdlib)))
            yield f"{prefix}{CONNECTOR_MID}📚 Stdlib: {modules}"
        if third_party:
            modules = ", ".join(sorted(set(d.module for d in third_party)))
            yield f"{prefix}{CONNECTOR_MID}📦 Third-party: {modules}"
        if local:
            modules = ", ".join(sorted(set(d.module for d in local)))
            yield f"{prefix}{CONNECTOR_MID}🏠 Local: {modules}"

    def _render_dead_code(self, dead_code: list[DeadCodeInfo], prefix: str) -> Iterator[str]:
        """Render dead code warnings grouped by confidence."""
        if not dead_code:
            return

        # Filter by confidence - only show medium and high confidence
        high_conf = [d for d in dead_code if d.confidence == "high"]
        medium_conf = [d for d in dead_code if d.confidence == "medium"]

        # Skip if nothing significant to show
        if not high_conf and not medium_conf:
            return

        significant = high_conf + medium_conf

        # Group by kind
        by_kind: dict[str, list[DeadCodeInfo]] = {}
        for item in significant:
            by_kind.setdefault(item.kind, []).append(item)

        kind_icons = {
            "function": "🔧",
            "class": "🧩", 
            "variable": "📦",
            "import": "📥",
        }

        yield f"{prefix}{CONNECTOR_MID}💀 Dead Code Detected ({len(significant)} items):"

        for kind, items in by_kind.items():
            icon = kind_icons.get(kind, "•")
            names_with_lines = [f"{item.name} (L{item.lineno})" for item in items[:5]]
            yield f"{prefix}{INDENT_MID}  {icon} Unused {kind}s: {', '.join(names_with_lines)}"

            if len(items) > 5:
                yield f"{prefix}{INDENT_MID}     (+{len(items) - 5} more unused {kind}s)"

