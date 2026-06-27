"""Markdown (nested bullet list) tree renderer."""

from __future__ import annotations

from ..models import CodeElement, TreeNode
from .base import BaseRenderer

_INDENT = "  "
_ICONS = {
    "function": "🔧",
    "class": "🧩",
    "import": "📦",
    "variable": "🔣",
}


class MarkdownRenderer(BaseRenderer):
    """Render a :class:`TreeNode` as a nested Markdown list."""

    def render(self, root: TreeNode) -> str:
        """Return a Markdown document describing the tree."""
        lines = [f"# 📁 {root.name}", ""]
        lines.extend(self._render_children(root, level=0))
        return "\n".join(lines) + "\n"

    def _render_children(self, node: TreeNode, level: int) -> list[str]:
        """Render the children of a directory node."""
        out: list[str] = []
        if node.error:
            out.append(f"{_INDENT * level}- ⚠️ _{node.error}_")
            return out
        for child in node.children:
            out.extend(self._render_node(child, level))
        return out

    def _render_node(self, node: TreeNode, level: int) -> list[str]:
        """Render a single file/directory node."""
        indent = _INDENT * level
        out: list[str] = []
        if node.is_dir:
            out.append(f"{indent}- 📁 **{node.name}/**")
            out.extend(self._render_children(node, level + 1))
        else:
            out.append(f"{indent}- 📄 `{node.name}`")
            if node.error:
                out.append(f"{_INDENT * (level + 1)}- ⚠️ _{node.error}_")
            out.extend(
                self._render_elements(
                    self._visible_elements(node.elements), level + 1
                )
            )
        return out

    def _render_elements(self, elements: list[CodeElement], level: int) -> list[str]:
        """Render code elements recursively as Markdown list items."""
        indent = _INDENT * level
        out: list[str] = []
        for element in elements:
            icon = _ICONS.get(element.kind, "•")
            sig = self._decorated_sig(element).replace("`", "ʼ")
            line = f"{indent}- {icon} `{sig}`{self._lineno_suffix(element)}"
            if element.doc:
                line += f" — _{element.doc}_"
            out.append(line)
            out.extend(
                self._render_elements(
                    self._visible_elements(element.children), level + 1
                )
            )
        return out
