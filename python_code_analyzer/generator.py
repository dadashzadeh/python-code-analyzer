"""Walk a directory and build a :class:`TreeNode` model."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

from .analyzer import CodeAnalyzer
from .models import AnalysisOptions, DisplayFilter, TreeNode
from .project_analyzer import ProjectDeadCodeAnalyzer

logger = logging.getLogger(__name__)


class TreeGenerator:
    """Build a :class:`TreeNode` tree, optionally analysing Python files."""

    def __init__(
        self,
        *,
        exclude_dirs: frozenset[str],
        exclude_extensions: frozenset[str],
        analysis_opts: AnalysisOptions,
        display_filter: DisplayFilter,
    ) -> None:
        self._exclude_dirs = exclude_dirs
        self._exclude_extensions = exclude_extensions
        self._analysis_opts = analysis_opts
        self._display_filter = display_filter

    def generate(self, root: Union[str, Path]) -> TreeNode:
        """Return the fully populated tree rooted at *root*."""
        tree = self._build_node(Path(root).resolve())
        
        # Run cross-file dead code analysis if enabled
        if self._analysis_opts.include_dead_code and self._analysis_opts.cross_file_analysis:
            analyzer = ProjectDeadCodeAnalyzer(
                strict=self._analysis_opts.strict_dead_code
            )
            analyzer.analyze(tree)
        
        return tree

    def _build_node(self, path: Path) -> TreeNode:
        """Recursively build a :class:`TreeNode` for *path*."""
        if path.is_dir():
            return self._build_directory(path)
        return self._build_file(path)

    def _build_directory(self, path: Path) -> TreeNode:
        """Build a directory node and recurse into its children."""
        node = TreeNode(name=path.name, path=path, is_dir=True)
        try:
            entries = sorted(
                path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except PermissionError:
            node.error = "Permission Denied"
            return node
        except OSError as exc:
            logger.error("Cannot list %s: %s", path, exc)
            node.error = "Access Error"
            return node

        for entry in entries:
            if self._should_include(entry):
                node.children.append(self._build_node(entry))
        return node

    def _build_file(self, path: Path) -> TreeNode:
        """Build a file node and analyse it when appropriate."""
        node = TreeNode(name=path.name, path=path, is_dir=False)
        if path.suffix == ".py" and self._display_filter.any_enabled:
            try:
                analyzer = CodeAnalyzer(path)
                node.elements = analyzer.get_code_elements(self._analysis_opts)

                if self._analysis_opts.include_markers:
                    node.comment_markers = analyzer.get_comment_markers()

                if self._analysis_opts.include_dead_code:
                    # ✅ تغییر: پاس دادن strict
                    node.dead_code = analyzer.get_dead_code(
                        strict=self._analysis_opts.strict_dead_code
                    )

                if self._analysis_opts.include_dependencies:
                    node.dependencies = analyzer.get_dependencies()

                if self._analysis_opts.include_call_graph:
                    node.module_calls = analyzer.get_module_level_calls()

            except Exception as exc:
                logger.error("Error analysing %s: %s", path, exc)
                node.error = "Analysis Error"
        return node



    def _should_include(self, path: Path) -> bool:
        """Return *True* when *path* should appear in the tree."""
        if path.is_dir():
            return path.name not in self._exclude_dirs
        return path.suffix not in self._exclude_extensions
