"""Cross-file dead code analysis for entire projects."""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import DeadCodeInfo, TreeNode

logger = logging.getLogger(__name__)


@dataclass
class DefinitionInfo:
    """Information about where a symbol is defined."""
    file_path: Path
    lineno: int
    kind: str  # 'function', 'class', 'variable', 'import'
    is_private: bool
    is_dunder: bool
    is_exported: bool  # in __all__
    context: str  # 'module' or 'class:ClassName'


class ProjectDeadCodeAnalyzer:
    """Analyze dead code across an entire project (cross-file analysis).
    
    This performs two passes:
    1. Collection: Gather all definitions and usages from all Python files
    2. Analysis: Determine which definitions are never used anywhere
    """

    # Names that should never be flagged as dead code
    SPECIAL_NAMES = frozenset({
        'main', 'run', 'start', 'setup', 'teardown', 'execute',
        'setUp', 'tearDown', 'setUpClass', 'tearDownClass',
        'app', 'application', 'wsgi', 'asgi', 'create_app',
        'cli', 'celery', 'pytest_configure',
    })

    def __init__(self, strict: bool = False):
        """Initialize the analyzer.
        
        Args:
            strict: If True, also report public functions/classes as potentially unused.
        """
        self._strict = strict
        
        # name -> list of DefinitionInfo (same name can be defined in multiple files)
        self._definitions: dict[str, list[DefinitionInfo]] = {}
        
        # Set of all names used across the entire project
        self._global_usages: set[str] = set()
        
        # file_path -> set of names exported via __all__
        self._exports: dict[Path, set[str]] = {}
        
        # Track which files are part of the project (for local import detection)
        self._project_files: set[Path] = set()
        self._project_modules: set[str] = set()

    def analyze(self, root_node: TreeNode) -> None:
        """Run cross-file dead code analysis on the entire tree.
        
        This modifies the tree in-place, updating dead_code on each file node.
        """
        logger.info("Starting cross-file dead code analysis...")
        
        # Phase 1: Discover all project files
        self._discover_files(root_node)
        
        # Phase 2: Collect all definitions and usages
        self._collection_phase(root_node)
        
        # Phase 3: Update dead_code on each node
        self._analysis_phase(root_node)
        
        logger.info(
            "Cross-file analysis complete: %d definitions, %d usages tracked",
            sum(len(v) for v in self._definitions.values()),
            len(self._global_usages),
        )

    # ------------------------------------------------------------------
    # Phase 1: Discover files
    # ------------------------------------------------------------------

    def _discover_files(self, node: TreeNode) -> None:
        """Build a set of all Python files in the project."""
        if node.is_dir:
            for child in node.children:
                self._discover_files(child)
        elif node.path.suffix == '.py':
            self._project_files.add(node.path.resolve())
            # Track module name for import resolution
            module_name = node.path.stem
            self._project_modules.add(module_name)

    # ------------------------------------------------------------------
    # Phase 2: Collection
    # ------------------------------------------------------------------

    def _collection_phase(self, node: TreeNode) -> None:
        """Collect definitions and usages from all files."""
        if node.is_dir:
            for child in node.children:
                self._collection_phase(child)
        elif node.path.suffix == '.py':
            self._analyze_file(node.path)

    def _analyze_file(self, path: Path) -> None:
        """Extract definitions and usages from a single file."""
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot analyze %s: %s", path, exc)
            return

        # Get __all__ exports for this file
        exports = self._get_exports(tree)
        self._exports[path] = exports

        # Collect definitions
        self._collect_definitions(tree, path, exports)

        # Collect usages
        self._collect_usages(tree)

    def _get_exports(self, tree: ast.AST) -> set[str]:
        """Extract names from __all__ if present."""
        exports: set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    exports.add(elt.value)
        return exports

    def _collect_definitions(
        self, tree: ast.AST, path: Path, exports: set[str]
    ) -> None:
        """Collect all top-level definitions from a file."""
        for node in ast.iter_child_nodes(tree):
            # Functions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_definition(
                    name=node.name,
                    path=path,
                    lineno=node.lineno,
                    kind='function',
                    exports=exports,
                )

            # Classes
            elif isinstance(node, ast.ClassDef):
                self._add_definition(
                    name=node.name,
                    path=path,
                    lineno=node.lineno,
                    kind='class',
                    exports=exports,
                )
                # Also collect class methods for more accurate analysis
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = f"{node.name}.{item.name}"
                        self._add_definition(
                            name=method_name,
                            path=path,
                            lineno=item.lineno,
                            kind='method',
                            exports=exports,
                            context=f"class:{node.name}",
                        )

            # Module-level variables
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        # Skip dunders
                        if name.startswith('__') and name.endswith('__'):
                            continue
                        self._add_definition(
                            name=name,
                            path=path,
                            lineno=node.lineno,
                            kind='variable',
                            exports=exports,
                        )

            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                if not (name.startswith('__') and name.endswith('__')):
                    self._add_definition(
                        name=name,
                        path=path,
                        lineno=node.lineno,
                        kind='variable',
                        exports=exports,
                    )

            # Imports (for unused import detection)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split('.')[0]
                    self._add_definition(
                        name=name,
                        path=path,
                        lineno=node.lineno,
                        kind='import',
                        exports=exports,
                    )

            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    name = alias.asname or alias.name
                    self._add_definition(
                        name=name,
                        path=path,
                        lineno=node.lineno,
                        kind='import',
                        exports=exports,
                    )

    def _add_definition(
        self,
        name: str,
        path: Path,
        lineno: int,
        kind: str,
        exports: set[str],
        context: str = "module",
    ) -> None:
        """Add a definition to the registry."""
        is_private = name.startswith('_') and not name.startswith('__')
        is_dunder = name.startswith('__') and name.endswith('__')
        is_exported = name in exports or (
            '.' in name and name.split('.')[0] in exports
        )

        info = DefinitionInfo(
            file_path=path.resolve(),
            lineno=lineno,
            kind=kind,
            is_private=is_private,
            is_dunder=is_dunder,
            is_exported=is_exported,
            context=context,
        )

        if name not in self._definitions:
            self._definitions[name] = []
        self._definitions[name].append(info)

    def _collect_usages(self, tree: ast.AST) -> None:
        """Collect all name usages from the AST."""
        for node in ast.walk(tree):
            # Direct name references
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self._global_usages.add(node.id)

            # Attribute access
            elif isinstance(node, ast.Attribute):
                self._global_usages.add(node.attr)
                # Also track the base object
                if isinstance(node.value, ast.Name):
                    self._global_usages.add(node.value.id)

            # Function/method calls
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self._global_usages.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    self._global_usages.add(node.func.attr)
                    if isinstance(node.func.value, ast.Name):
                        self._global_usages.add(node.func.value.id)

            # Type annotations
            elif isinstance(node, ast.AnnAssign) and node.annotation:
                self._collect_type_annotation_usages(node.annotation)
            elif isinstance(node, ast.arg) and node.annotation:
                self._collect_type_annotation_usages(node.annotation)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns:
                    self._collect_type_annotation_usages(node.returns)

            # Base classes
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    self._collect_type_annotation_usages(base)

            # String literals (forward references)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.isidentifier():
                    self._global_usages.add(node.value)

    def _collect_type_annotation_usages(self, node: ast.expr) -> None:
        """Recursively collect type annotation usages."""
        if isinstance(node, ast.Name):
            self._global_usages.add(node.id)
        elif isinstance(node, ast.Attribute):
            self._global_usages.add(node.attr)
            if isinstance(node.value, ast.Name):
                self._global_usages.add(node.value.id)
        elif isinstance(node, ast.Subscript):
            self._collect_type_annotation_usages(node.value)
            if isinstance(node.slice, ast.Tuple):
                for elt in node.slice.elts:
                    self._collect_type_annotation_usages(elt)
            else:
                self._collect_type_annotation_usages(node.slice)
        elif isinstance(node, ast.BinOp):
            self._collect_type_annotation_usages(node.left)
            self._collect_type_annotation_usages(node.right)

    # ------------------------------------------------------------------
    # Phase 3: Analysis
    # ------------------------------------------------------------------

    def _analysis_phase(self, node: TreeNode) -> None:
        """Update dead_code on each file node based on global analysis."""
        if node.is_dir:
            for child in node.children:
                self._analysis_phase(child)
        elif node.path.suffix == '.py':
            node.dead_code = self._get_dead_code_for_file(node.path.resolve())

    def _get_dead_code_for_file(self, file_path: Path) -> list[DeadCodeInfo]:
        """Determine dead code for a specific file."""
        dead_code: list[DeadCodeInfo] = []

        for name, definitions in self._definitions.items():
            # Find definitions in this file
            for defn in definitions:
                if defn.file_path != file_path:
                    continue

                # Check if this name is used ANYWHERE in the project
                base_name = name.split('.')[-1] if '.' in name else name
                
                # For methods, check both Class.method and method
                is_used = name in self._global_usages or base_name in self._global_usages

                if is_used:
                    continue

                # === Skip rules ===

                # 1. Dunder methods/variables
                if defn.is_dunder:
                    continue

                # 2. Exported in __all__
                if defn.is_exported:
                    continue

                # 3. Special names (entry points, etc.)
                if base_name in self.SPECIAL_NAMES:
                    continue

                # === Determine reporting ===
                
                should_report = False
                confidence = "low"
                reason = ""

                if defn.kind == "import":
                    should_report = True
                    confidence = "high"
                    reason = "Imported but never used in project"

                elif defn.kind == "variable":
                    if defn.is_private:
                        should_report = True
                        confidence = "high"
                        reason = "Private variable never referenced in project"
                    else:
                        should_report = self._strict
                        confidence = "medium"
                        reason = "Variable not used in project (may be used externally)"

                elif defn.kind == "function":
                    if defn.is_private:
                        should_report = True
                        confidence = "high"
                        reason = "Private function never called in project"
                    else:
                        should_report = self._strict
                        confidence = "medium"
                        reason = "Public function not called in project"

                elif defn.kind == "class":
                    if defn.is_private:
                        should_report = True
                        confidence = "high"
                        reason = "Private class never used in project"
                    else:
                        should_report = self._strict
                        confidence = "medium"
                        reason = "Public class not used in project"

                elif defn.kind == "method":
                    if defn.is_private:
                        should_report = True
                        confidence = "medium"
                        reason = "Protected method never called in project"
                    else:
                        should_report = self._strict
                        confidence = "low"
                        reason = "Public method not called in project"

                if should_report:
                    dead_code.append(DeadCodeInfo(
                        name=base_name,
                        kind="function" if defn.kind == "method" else defn.kind,
                        lineno=defn.lineno,
                        reason=reason,
                        confidence=confidence,
                    ))

        return dead_code
