"""Issue detection and reporting for Python projects.

This module provides comprehensive issue detection independent of the
main tree output, focusing on code quality, style, and maintainability.
"""

from __future__ import annotations

import ast
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Union

from .models import (
    AnalysisOptions,
    DisplayFilter,
    Issue,
    IssueCategory,
    IssueLocation,
    IssueReport,
    IssueSeverity,
    TreeNode,
)
from .project_analyzer import ProjectDeadCodeAnalyzer

logger = logging.getLogger(__name__)


# =============================================================================
# Issue Codes Reference
# =============================================================================
#
# Style (S):
#   S001 - Line too long (> 79/99/120 chars)
#   S002 - Missing blank lines
#   S005 - Missing newline at end of file
#   S006 - Multiple statements on one line
#
# Complexity (C):
#   C001 - Function too complex (cyclomatic > threshold)
#   C002 - Function too long (lines > threshold)
#   C003 - Too many arguments (> 5)
#   C004 - Too many local variables (> 15)
#   C005 - Too many branches (> 12)
#   C006 - Too many return statements (> 6)
#   C007 - Deeply nested code (> 4 levels)
#
# Dead Code (D):
#   D001 - Unused import
#   D002 - Unused variable
#   D003 - Unused function
#   D004 - Unused class
#   D005 - Unreachable code
#   D006 - Redundant pass statement
#
# Type Hints (T):
#   T001 - Missing return type annotation
#   T002 - Missing parameter type annotation
#   T003 - Inconsistent type annotations
#   T004 - Any type used explicitly
#
# Documentation (DOC):
#   DOC001 - Missing module docstring
#   DOC002 - Missing function docstring
#   DOC003 - Missing class docstring
#   DOC004 - Incomplete docstring (missing params/returns)
#
# Maintainability (M):
#   M001 - Too many methods in class (> 20)
#   M002 - Class too long (> 500 lines)
#   M003 - File too long (> 1000 lines)
#   M004 - Too many imports (> 15)
#   M005 - Circular import potential
#   M006 - Global variable usage
#
# Markers (MK):
#   MK001 - TODO marker found
#   MK002 - FIXME marker found (higher priority)
#   MK003 - BUG marker found (critical)
#   MK004 - HACK marker found
#   MK005 - XXX marker found
#
# =============================================================================


@dataclass(frozen=True)
class IssueReporterConfig:
    """Configuration for issue detection thresholds.

    Attributes:
        max_line_length:         Maximum allowed line length.
        max_function_complexity: Maximum cyclomatic complexity.
        max_function_length:     Maximum lines per function.
        max_arguments:           Maximum function arguments.
        max_local_variables:     Maximum local variables per function.
        max_branches:            Maximum branches per function.
        max_returns:             Maximum return statements.
        max_nesting_depth:       Maximum nesting depth.
        max_methods_per_class:   Maximum methods per class.
        max_class_length:        Maximum lines per class.
        max_file_length:         Maximum lines per file.
        max_imports:             Maximum imports per file.
        require_docstrings:      Require docstrings for public API.
        require_type_hints:      Require type hints for functions.
        check_style:             Enable style checks.
        check_complexity:        Enable complexity checks.
        check_dead_code:         Enable dead code detection.
        check_type_hints:        Enable type hint checks.
        check_documentation:     Enable documentation checks.
        check_maintainability:   Enable maintainability checks.
        check_markers:           Enable marker extraction.
        strict_dead_code:        Report public unused code as well.
        cross_file_analysis:     Enable project-wide dead code detection.
    """

    # Thresholds
    max_line_length: int = 99
    max_function_complexity: int = 10
    max_function_length: int = 50
    max_arguments: int = 5
    max_local_variables: int = 15
    max_branches: int = 12
    max_returns: int = 6
    max_nesting_depth: int = 4
    max_methods_per_class: int = 20
    max_class_length: int = 500
    max_file_length: int = 1000
    max_imports: int = 15

    # Requirements
    require_docstrings: bool = True
    require_type_hints: bool = False

    # Category toggles
    check_style: bool = True
    check_complexity: bool = True
    check_dead_code: bool = True
    check_type_hints: bool = True
    check_documentation: bool = True
    check_maintainability: bool = True
    check_markers: bool = True

    # Dead code options
    strict_dead_code: bool = False
    cross_file_analysis: bool = True

    @classmethod
    def from_analysis_options(
        cls,
        opts: AnalysisOptions,
        display: DisplayFilter,
    ) -> "IssueReporterConfig":
        """Create config from AnalysisOptions and DisplayFilter."""
        return cls(
            check_style=True,
            check_complexity=display.show_complexity and opts.include_complexity,
            check_dead_code=display.show_dead_code and opts.include_dead_code,
            check_type_hints=display.show_type_hints and opts.include_type_hints,
            check_documentation=opts.include_docstrings,
            check_maintainability=True,
            check_markers=display.show_markers and opts.include_markers,
            strict_dead_code=opts.strict_dead_code,
            cross_file_analysis=opts.cross_file_analysis,
            require_docstrings=opts.include_docstrings,
            require_type_hints=opts.include_type_hints,
        )


class IssueReporter:
    """Detect and report code issues across a project.

    This class provides comprehensive issue detection that is completely
    independent of the tree rendering output. Results can be exported
    to various formats (text, JSON, SARIF, etc.).

    Example:
        >>> config = IssueReporterConfig(max_line_length=120)
        >>> reporter = IssueReporter(config)
        >>> report = reporter.analyze_directory(Path("./src"))
        >>> print(reporter.format_report(report))

    For cross-file dead code analysis:
        >>> analysis_opts = AnalysisOptions(
        ...     include_dead_code=True,
        ...     cross_file_analysis=True,
        ... )
        >>> display_filter = DisplayFilter(show_dead_code=True)
        >>> config = IssueReporterConfig.from_analysis_options(
        ...     analysis_opts, display_filter
        ... )
        >>> reporter = IssueReporter(config)
        >>> report = reporter.analyze_directory(Path("./src"))
    """

    # Marker pattern for TODO/FIXME detection
    _MARKER_PATTERN = re.compile(
        r"#\s*(TODO|FIXME|HACK|NOTE|XXX|BUG|OPTIMIZE)"
        r"(?:\(([^)]+)\))?[:\s]*(.*)$",
        re.IGNORECASE,
    )

    # Dead code kind to issue code mapping
    _DEAD_CODE_MAP = {
        "import": "D001",
        "variable": "D002",
        "function": "D003",
        "class": "D004",
    }

    def __init__(self, config: Optional[IssueReporterConfig] = None) -> None:
        """Initialize the reporter with configuration.

        Args:
            config: Configuration for issue detection. Uses defaults if None.
        """
        self._config = config or IssueReporterConfig()
        self._checkers: list[Callable[[Path, str, ast.AST], list[Issue]]] = []
        self._cross_file_dead_code: dict[Path, list[Issue]] = {}
        self._register_checkers()

    def _register_checkers(self) -> None:
        """Register all enabled issue checkers."""
        cfg = self._config

        if cfg.check_style:
            self._checkers.append(self._check_style_issues)
        if cfg.check_complexity:
            self._checkers.append(self._check_complexity_issues)
        if cfg.check_type_hints:
            self._checkers.append(self._check_type_hint_issues)
        if cfg.check_documentation:
            self._checkers.append(self._check_documentation_issues)
        if cfg.check_maintainability:
            self._checkers.append(self._check_maintainability_issues)
        if cfg.check_markers:
            self._checkers.append(self._check_marker_issues)

        # Note: Dead code is handled separately for cross-file analysis

    # =========================================================================
    # Public API
    # =========================================================================

    def analyze_directory(
        self,
        root: Union[str, Path],
        exclude_dirs: Optional[frozenset[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ) -> IssueReport:
        """Analyze all Python files in a directory.

        Args:
            root:             Root directory to analyze.
            exclude_dirs:     Directory names to exclude.
            exclude_patterns: Glob patterns to exclude.

        Returns:
            IssueReport containing all detected issues.
        """
        root_path = Path(root).resolve()
        start_time = time.time()

        if exclude_dirs is None:
            exclude_dirs = frozenset({
                "__pycache__", ".git", ".venv", "venv",
                "node_modules", ".tox", "build", "dist",
            })

        report = IssueReport(project_path=root_path)

        # Phase 1: Run cross-file dead code analysis if enabled
        if self._config.check_dead_code and self._config.cross_file_analysis:
            self._run_cross_file_analysis(root_path, exclude_dirs)

        # Phase 2: Analyze each file
        for py_file in self._iter_python_files(root_path, exclude_dirs):
            file_issues = self._analyze_file_with_cross_file_context(py_file)
            report.issues.extend(file_issues)
            report.files_analyzed += 1

        report.analysis_time = time.time() - start_time
        return report

    def _run_cross_file_analysis(
        self,
        root_path: Path,
        exclude_dirs: frozenset[str],
    ) -> None:
        """Run cross-file dead code analysis and cache results."""
        logger.info("Running cross-file dead code analysis...")
        
        analyzer = ProjectDeadCodeAnalyzer(strict=self._config.strict_dead_code)
        
        # Build a minimal tree structure for the analyzer
        from .models import TreeNode
        root_node = self._build_minimal_tree(root_path, exclude_dirs)
        
        # Run analysis
        analyzer.analyze(root_node)
        
        # Cache results by file path
        self._cross_file_dead_code.clear()
        self._collect_dead_code_from_tree(root_node)
        
        logger.info(
            "Cross-file analysis complete: found %d files with dead code",
            len(self._cross_file_dead_code),
        )

    def _build_minimal_tree(
        self,
        path: Path,
        exclude_dirs: frozenset[str],
    ) -> "TreeNode":
        """Build a minimal TreeNode structure for dead code analysis."""
        from .models import TreeNode
        
        if path.is_dir():
            node = TreeNode(name=path.name, path=path, is_dir=True)
            try:
                for entry in sorted(path.iterdir()):
                    if entry.is_dir() and entry.name in exclude_dirs:
                        continue
                    if entry.is_file() and entry.suffix != ".py":
                        continue
                    child = self._build_minimal_tree(entry, exclude_dirs)
                    node.children.append(child)
            except PermissionError:
                pass
            return node
        else:
            return TreeNode(name=path.name, path=path, is_dir=False)

    def _collect_dead_code_from_tree(self, node: "TreeNode") -> None:
        """Collect dead code issues from analyzed tree nodes."""
        if node.is_dir:
            for child in node.children:
                self._collect_dead_code_from_tree(child)
        elif node.dead_code:
            file_path = node.path.resolve()
            issues = []
            for dc in node.dead_code:
                code = self._DEAD_CODE_MAP.get(dc.kind, "D001")
                severity = (
                    IssueSeverity.WARNING
                    if dc.confidence == "high"
                    else IssueSeverity.INFO
                )
                issues.append(Issue(
                    code=code,
                    message=f"Unused {dc.kind}: '{dc.name}' - {dc.reason}",
                    severity=severity,
                    category=IssueCategory.DEAD_CODE,
                    location=IssueLocation(file_path=file_path, lineno=dc.lineno),
                    suggestion=f"Remove unused {dc.kind} or add to __all__",
                ))
            self._cross_file_dead_code[file_path] = issues

    def _analyze_file_with_cross_file_context(
        self,
        file_path: Path,
    ) -> list[Issue]:
        """Analyze a file using cross-file dead code results."""
        path = file_path.resolve()
        issues: list[Issue] = []

        # Add cross-file dead code issues
        if path in self._cross_file_dead_code:
            issues.extend(self._cross_file_dead_code[path])

        # Run other checkers
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return issues

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            issues.append(Issue(
                code="E999",
                message=f"Syntax error: {exc.msg}",
                severity=IssueSeverity.ERROR,
                category=IssueCategory.STYLE,
                location=IssueLocation(
                    file_path=path,
                    lineno=exc.lineno or 1,
                    col_offset=exc.offset,
                ),
            ))
            return issues

        # Run all registered checkers (excluding dead code)
        for checker in self._checkers:
            try:
                checker_issues = checker(path, source, tree)
                issues.extend(checker_issues)
            except Exception as exc:
                logger.error(
                    "Checker %s failed on %s: %s",
                    checker.__name__,
                    path,
                    exc,
                )

        return issues

    def analyze_file(self, file_path: Union[str, Path]) -> list[Issue]:
        """Analyze a single Python file for issues.

        Note: For cross-file dead code analysis, use analyze_directory instead.

        Args:
            file_path: Path to the Python file.

        Returns:
            List of detected issues.
        """
        path = Path(file_path).resolve()
        issues: list[Issue] = []

        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return issues

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            issues.append(Issue(
                code="E999",
                message=f"Syntax error: {exc.msg}",
                severity=IssueSeverity.ERROR,
                category=IssueCategory.STYLE,
                location=IssueLocation(
                    file_path=path,
                    lineno=exc.lineno or 1,
                    col_offset=exc.offset,
                ),
            ))
            return issues

        # Run all registered checkers
        for checker in self._checkers:
            try:
                checker_issues = checker(path, source, tree)
                issues.extend(checker_issues)
            except Exception as exc:
                logger.error(
                    "Checker %s failed on %s: %s",
                    checker.__name__,
                    path,
                    exc,
                )

        # Single-file dead code analysis (fallback when not using cross-file)
        if self._config.check_dead_code and not self._config.cross_file_analysis:
            issues.extend(self._check_dead_code_single_file(path, source, tree))

        return issues

    def analyze_tree_node(self, node: TreeNode) -> IssueReport:
        """Analyze issues from an existing TreeNode structure.

        This allows integration with the existing tree generator
        without re-parsing files. Uses pre-computed dead_code from
        cross-file analysis.

        Args:
            node: Root TreeNode from TreeGenerator.

        Returns:
            IssueReport with detected issues.
        """
        report = IssueReport(project_path=node.path)
        start_time = time.time()
        self._analyze_node_recursive(node, report)
        report.analysis_time = time.time() - start_time
        return report

    def _analyze_node_recursive(
        self,
        node: TreeNode,
        report: IssueReport,
    ) -> None:
        """Recursively analyze TreeNode and children."""
        if node.is_dir:
            for child in node.children:
                self._analyze_node_recursive(child, report)
        elif node.path.suffix == ".py":
            # Use pre-computed dead_code from cross-file analysis
            if self._config.check_dead_code and node.dead_code:
                for dc in node.dead_code:
                    code = self._DEAD_CODE_MAP.get(dc.kind, "D001")
                    severity = (
                        IssueSeverity.WARNING
                        if dc.confidence == "high"
                        else IssueSeverity.INFO
                    )
                    report.issues.append(Issue(
                        code=code,
                        message=f"Unused {dc.kind}: '{dc.name}' - {dc.reason}",
                        severity=severity,
                        category=IssueCategory.DEAD_CODE,
                        location=IssueLocation(
                            file_path=node.path,
                            lineno=dc.lineno,
                        ),
                        suggestion=f"Remove unused {dc.kind} or add to __all__",
                    ))

            # Run other file-level checks
            other_issues = self.analyze_file(node.path)
            # Filter out dead code to avoid duplicates
            other_issues = [
                i for i in other_issues
                if i.category != IssueCategory.DEAD_CODE
            ]
            report.issues.extend(other_issues)
            report.files_analyzed += 1

    # =========================================================================
    # Style Checks (S)
    # =========================================================================

    def _check_style_issues(
        self,
        path: Path,
        source: str,
        tree: ast.AST,
    ) -> list[Issue]:
        """Check for PEP 8 style issues."""
        issues: list[Issue] = []
        lines = source.splitlines(keepends=True)
        max_len = self._config.max_line_length

        for lineno, line in enumerate(lines, start=1):
            line_stripped = line.rstrip("\n\r")

            # S001: Line too long
            if len(line_stripped) > max_len:
                issues.append(Issue(
                    code="S001",
                    message=f"Line too long ({len(line_stripped)} > {max_len})",
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.STYLE,
                    location=IssueLocation(
                        file_path=path,
                        lineno=lineno,
                        col_offset=max_len,
                    ),
                    suggestion=f"Break line to stay under {max_len} characters",
                    context=(
                        line_stripped[:80] + "..."
                        if len(line_stripped) > 80
                        else line_stripped
                    ),
                ))

            # S006: Multiple statements on one line
            if ";" in line_stripped and not line_stripped.strip().startswith("#"):
                try:
                    compile(line_stripped, "<string>", "exec")
                    if line_stripped.count(";") > 0:
                        issues.append(Issue(
                            code="S006",
                            message="Multiple statements on one line",
                            severity=IssueSeverity.INFO,
                            category=IssueCategory.STYLE,
                            location=IssueLocation(file_path=path, lineno=lineno),
                            suggestion="Put each statement on its own line",
                        ))
                except SyntaxError:
                    pass

        # S005: Missing newline at end of file
        if source and not source.endswith("\n"):
            issues.append(Issue(
                code="S005",
                message="Missing newline at end of file",
                severity=IssueSeverity.INFO,
                category=IssueCategory.STYLE,
                location=IssueLocation(file_path=path, lineno=len(lines)),
                suggestion="Add a newline at the end of the file",
            ))

        return issues

    # =========================================================================
    # Complexity Checks (C)
    # =========================================================================

    def _check_complexity_issues(
        self,
        path: Path,
        source: str,
        tree: ast.AST,
    ) -> list[Issue]:
        """Check for complexity-related issues."""
        issues: list[Issue] = []
        cfg = self._config

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                issues.extend(
                    self._check_function_complexity(path, node, cfg)
                )
            elif isinstance(node, ast.ClassDef):
                issues.extend(
                    self._check_class_complexity(path, node, cfg)
                )

        return issues

    def _check_function_complexity(
        self,
        path: Path,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
        cfg: IssueReporterConfig,
    ) -> list[Issue]:
        """Check complexity metrics for a function."""
        issues: list[Issue] = []

        # Calculate cyclomatic complexity
        complexity = self._calculate_cyclomatic_complexity(node)
        if complexity > cfg.max_function_complexity:
            issues.append(Issue(
                code="C001",
                message=(
                    f"Function '{node.name}' is too complex "
                    f"(complexity={complexity}, max={cfg.max_function_complexity})"
                ),
                severity=IssueSeverity.WARNING,
                category=IssueCategory.COMPLEXITY,
                location=IssueLocation(file_path=path, lineno=node.lineno),
                suggestion="Consider breaking this function into smaller parts",
            ))

        # C002: Function too long
        if hasattr(node, "end_lineno") and node.end_lineno:
            func_length = node.end_lineno - node.lineno + 1
            if func_length > cfg.max_function_length:
                issues.append(Issue(
                    code="C002",
                    message=(
                        f"Function '{node.name}' is too long "
                        f"({func_length} lines, max={cfg.max_function_length})"
                    ),
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.COMPLEXITY,
                    location=IssueLocation(file_path=path, lineno=node.lineno),
                    suggestion="Extract parts into helper functions",
                ))

        # C003: Too many arguments
        total_args = (
            len(node.args.args)
            + len(node.args.posonlyargs)
            + len(node.args.kwonlyargs)
        )
        if node.args.vararg:
            total_args += 1
        if node.args.kwarg:
            total_args += 1

        if total_args > cfg.max_arguments:
            issues.append(Issue(
                code="C003",
                message=(
                    f"Function '{node.name}' has too many arguments "
                    f"({total_args}, max={cfg.max_arguments})"
                ),
                severity=IssueSeverity.WARNING,
                category=IssueCategory.COMPLEXITY,
                location=IssueLocation(file_path=path, lineno=node.lineno),
                suggestion="Consider using a configuration object or dataclass",
            ))

        # C004: Too many local variables
        local_vars = self._count_local_variables(node)
        if local_vars > cfg.max_local_variables:
            issues.append(Issue(
                code="C004",
                message=(
                    f"Function '{node.name}' has too many local variables "
                    f"({local_vars}, max={cfg.max_local_variables})"
                ),
                severity=IssueSeverity.INFO,
                category=IssueCategory.COMPLEXITY,
                location=IssueLocation(file_path=path, lineno=node.lineno),
                suggestion="Consider extracting related variables into a class",
            ))

        # C005: Too many branches
        branches = self._count_branches(node)
        if branches > cfg.max_branches:
            issues.append(Issue(
                code="C005",
                message=(
                    f"Function '{node.name}' has too many branches "
                    f"({branches}, max={cfg.max_branches})"
                ),
                severity=IssueSeverity.WARNING,
                category=IssueCategory.COMPLEXITY,
                location=IssueLocation(file_path=path, lineno=node.lineno),
                suggestion="Consider using early returns or extracting conditions",
            ))

        # C006: Too many return statements
        returns = self._count_returns(node)
        if returns > cfg.max_returns:
            issues.append(Issue(
                code="C006",
                message=(
                    f"Function '{node.name}' has too many return statements "
                    f"({returns}, max={cfg.max_returns})"
                ),
                severity=IssueSeverity.INFO,
                category=IssueCategory.COMPLEXITY,
                location=IssueLocation(file_path=path, lineno=node.lineno),
            ))

        # C007: Deeply nested code
        max_depth = self._calculate_max_nesting(node)
        if max_depth > cfg.max_nesting_depth:
            issues.append(Issue(
                code="C007",
                message=(
                    f"Function '{node.name}' has deeply nested code "
                    f"(depth={max_depth}, max={cfg.max_nesting_depth})"
                ),
                severity=IssueSeverity.WARNING,
                category=IssueCategory.COMPLEXITY,
                location=IssueLocation(file_path=path, lineno=node.lineno),
                suggestion="Consider using guard clauses or extracting nested logic",
            ))

        return issues

    def _check_class_complexity(
        self,
        path: Path,
        node: ast.ClassDef,
        cfg: IssueReporterConfig,
    ) -> list[Issue]:
        """Check complexity metrics for a class."""
        issues: list[Issue] = []

        # Count methods
        methods = [
            n for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        if len(methods) > cfg.max_methods_per_class:
            issues.append(Issue(
                code="M001",
                message=(
                    f"Class '{node.name}' has too many methods "
                    f"({len(methods)}, max={cfg.max_methods_per_class})"
                ),
                severity=IssueSeverity.WARNING,
                category=IssueCategory.MAINTAINABILITY,
                location=IssueLocation(file_path=path, lineno=node.lineno),
                suggestion="Consider splitting into multiple classes",
            ))

        # Class length
        if hasattr(node, "end_lineno") and node.end_lineno:
            class_length = node.end_lineno - node.lineno + 1
            if class_length > cfg.max_class_length:
                issues.append(Issue(
                    code="M002",
                    message=(
                        f"Class '{node.name}' is too long "
                        f"({class_length} lines, max={cfg.max_class_length})"
                    ),
                    severity=IssueSeverity.WARNING,
                    category=IssueCategory.MAINTAINABILITY,
                    location=IssueLocation(file_path=path, lineno=node.lineno),
                ))

        return issues

    def _calculate_cyclomatic_complexity(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> int:
        """Calculate McCabe's cyclomatic complexity."""
        complexity = 1

        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                complexity += 1
            elif isinstance(child, ast.ExceptHandler):
                complexity += 1
            elif isinstance(child, (ast.And, ast.Or)):
                complexity += 1
            elif isinstance(child, ast.comprehension):
                complexity += 1
            elif isinstance(child, ast.Assert):
                complexity += 1
            elif isinstance(child, ast.IfExp):
                complexity += 1

        return complexity

    def _count_local_variables(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> int:
        """Count local variable assignments in a function."""
        names: set[str] = set()

        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(child, ast.AnnAssign):
                if isinstance(child.target, ast.Name):
                    names.add(child.target.id)

        return len(names)

    def _count_branches(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> int:
        """Count branching statements."""
        count = 0
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.AsyncFor)):
                count += 1
            elif isinstance(child, ast.ExceptHandler):
                count += 1
        return count

    def _count_returns(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> int:
        """Count return statements."""
        return sum(
            1 for child in ast.walk(node) if isinstance(child, ast.Return)
        )

    def _calculate_max_nesting(
        self,
        node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    ) -> int:
        """Calculate maximum nesting depth."""

        def get_depth(n: ast.AST, current: int = 0) -> int:
            max_depth = current
            for child in ast.iter_child_nodes(n):
                if isinstance(child, (
                    ast.If, ast.For, ast.While, ast.With,
                    ast.Try, ast.AsyncFor, ast.AsyncWith
                )):
                    child_depth = get_depth(child, current + 1)
                    max_depth = max(max_depth, child_depth)
                else:
                    child_depth = get_depth(child, current)
                    max_depth = max(max_depth, child_depth)
            return max_depth

        return get_depth(node)

    # =========================================================================
    # Dead Code Checks (D) - Single File Fallback
    # =========================================================================

    def _check_dead_code_single_file(
        self,
        path: Path,
        source: str,
        tree: ast.AST,
    ) -> list[Issue]:
        """Check for dead/unused code using single-file analysis.

        This is a fallback when cross_file_analysis is disabled.
        """
        from .analyzer import CodeAnalyzer

        issues: list[Issue] = []

        try:
            analyzer = CodeAnalyzer(path)
            dead_code_list = analyzer.get_dead_code(
                strict=self._config.strict_dead_code
            )

            for dc in dead_code_list:
                code = self._DEAD_CODE_MAP.get(dc.kind, "D001")
                severity = (
                    IssueSeverity.WARNING
                    if dc.confidence == "high"
                    else IssueSeverity.INFO
                )
                issues.append(Issue(
                    code=code,
                    message=f"Unused {dc.kind}: '{dc.name}' - {dc.reason}",
                    severity=severity,
                    category=IssueCategory.DEAD_CODE,
                    location=IssueLocation(file_path=path, lineno=dc.lineno),
                    suggestion=f"Remove unused {dc.kind} or add to __all__",
                ))
        except Exception as exc:
            logger.warning("Dead code analysis failed for %s: %s", path, exc)

        return issues

    # =========================================================================
    # Type Hint Checks (T)
    # =========================================================================

    def _check_type_hint_issues(
        self,
        path: Path,
        source: str,
        tree: ast.AST,
    ) -> list[Issue]:
        """Check for type hint issues."""
        issues: list[Issue] = []

        if not self._config.require_type_hints:
            return issues

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip private and dunder methods
                if node.name.startswith("_"):
                    continue

                # T001: Missing return type
                if node.returns is None and node.name != "__init__":
                    issues.append(Issue(
                        code="T001",
                        message=(
                            f"Function '{node.name}' missing "
                            "return type annotation"
                        ),
                        severity=IssueSeverity.INFO,
                        category=IssueCategory.TYPE_HINT,
                        location=IssueLocation(file_path=path, lineno=node.lineno),
                        suggestion="Add return type annotation",
                    ))

                # T002: Missing parameter types
                for arg in node.args.args:
                    if arg.arg in ("self", "cls"):
                        continue
                    if arg.annotation is None:
                        issues.append(Issue(
                            code="T002",
                            message=(
                                f"Parameter '{arg.arg}' in function "
                                f"'{node.name}' missing type annotation"
                            ),
                            severity=IssueSeverity.INFO,
                            category=IssueCategory.TYPE_HINT,
                            location=IssueLocation(
                                file_path=path,
                                lineno=node.lineno,
                            ),
                        ))

        return issues

    # =========================================================================
    # Documentation Checks (DOC)
    # =========================================================================

    def _check_documentation_issues(
        self, path: Path, source: str, tree: ast.AST
    ) -> list[Issue]:
        """Check for documentation issues."""
        issues: list[Issue] = []

        # DOC001: Missing module docstring
        if not ast.get_docstring(tree):
            issues.append(Issue(
                code="DOC001",
                message="Missing module docstring",
                severity=IssueSeverity.INFO,
                category=IssueCategory.DOCUMENTATION,
                location=IssueLocation(file_path=path, lineno=1),
                suggestion="Add a module-level docstring describing the module's purpose",
            ))

        for node in ast.walk(tree):
            # DOC002: Missing function docstring
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith('_'):  # Public functions only
                    if not ast.get_docstring(node):
                        issues.append(Issue(
                            code="DOC002",
                            message=f"Missing docstring for function '{node.name}'",
                            severity=IssueSeverity.INFO,
                            category=IssueCategory.DOCUMENTATION,
                            location=IssueLocation(file_path=path, lineno=node.lineno),
                            suggestion="Add a docstring describing the function's purpose",
                        ))
                    else:
                        # DOC004: Incomplete docstring
                        issues.extend(self._check_docstring_completeness(
                            path, node, ast.get_docstring(node) or ""
                        ))

            # DOC003: Missing class docstring
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith('_'):
                    if not ast.get_docstring(node):
                        issues.append(Issue(
                            code="DOC003",
                            message=f"Missing docstring for class '{node.name}'",
                            severity=IssueSeverity.INFO,
                            category=IssueCategory.DOCUMENTATION,
                            location=IssueLocation(file_path=path, lineno=node.lineno),
                            suggestion="Add a docstring describing the class's purpose",
                        ))

        return issues

    def _check_docstring_completeness(
        self, path: Path, node: ast.FunctionDef | ast.AsyncFunctionDef, docstring: str
    ) -> list[Issue]:
        """Check if docstring documents all parameters and return value."""
        issues: list[Issue] = []
        docstring_lower = docstring.lower()

        # Get parameter names (excluding self, cls)
        params = [
            arg.arg for arg in node.args.args
            if arg.arg not in ('self', 'cls')
        ]

        # Check for missing parameter documentation
        missing_params = [p for p in params if p not in docstring]
        has_return = node.returns is not None
        missing_return = has_return and 'return' not in docstring_lower

        if missing_params or missing_return:
            missing_parts = []
            if missing_params:
                missing_parts.append(f"parameters ({', '.join(missing_params)})")
            if missing_return:
                missing_parts.append("return value")

            issues.append(Issue(
                code="DOC004",
                message=f"Incomplete docstring for '{node.name}': missing documentation for {', '.join(missing_parts)}",
                severity=IssueSeverity.INFO,
                category=IssueCategory.DOCUMENTATION,
                location=IssueLocation(file_path=path, lineno=node.lineno),
                suggestion="Document all parameters and return value in docstring",
            ))

        return issues


    # =========================================================================
    # Maintainability Checks (M)
    # =========================================================================

    def _check_maintainability_issues(
        self,
        path: Path,
        source: str,
        tree: ast.AST,
    ) -> list[Issue]:
        """Check for maintainability issues."""
        issues: list[Issue] = []
        lines = source.splitlines()
        cfg = self._config

        # M003: File too long
        if len(lines) > cfg.max_file_length:
            issues.append(Issue(
                code="M003",
                message=(
                    f"File is too long ({len(lines)} lines, "
                    f"max={cfg.max_file_length})"
                ),
                severity=IssueSeverity.WARNING,
                category=IssueCategory.MAINTAINABILITY,
                location=IssueLocation(file_path=path, lineno=1),
                suggestion="Consider splitting into multiple modules",
            ))

        # M004: Too many imports
        import_count = sum(
            1 for node in ast.iter_child_nodes(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        if import_count > cfg.max_imports:
            issues.append(Issue(
                code="M004",
                message=f"Too many imports ({import_count}, max={cfg.max_imports})",
                severity=IssueSeverity.INFO,
                category=IssueCategory.MAINTAINABILITY,
                location=IssueLocation(file_path=path, lineno=1),
                suggestion="Consider consolidating imports or splitting the module",
            ))

        # M006: Global variable usage
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                for name in node.names:
                    issues.append(Issue(
                        code="M006",
                        message=f"Use of global variable '{name}'",
                        severity=IssueSeverity.WARNING,
                        category=IssueCategory.MAINTAINABILITY,
                        location=IssueLocation(file_path=path, lineno=node.lineno),
                        suggestion="Consider passing as parameter or class attribute",
                    ))

        return issues

    # =========================================================================
    # Marker Checks (MK)
    # =========================================================================

    def _check_marker_issues(
        self,
        path: Path,
        source: str,
        tree: ast.AST,
    ) -> list[Issue]:
        """Extract TODO/FIXME/BUG markers as issues."""
        issues: list[Issue] = []

        marker_config = {
            "TODO": ("MK001", IssueSeverity.INFO),
            "FIXME": ("MK002", IssueSeverity.WARNING),
            "BUG": ("MK003", IssueSeverity.ERROR),
            "HACK": ("MK004", IssueSeverity.WARNING),
            "XXX": ("MK005", IssueSeverity.WARNING),
            "NOTE": ("MK001", IssueSeverity.HINT),
            "OPTIMIZE": ("MK001", IssueSeverity.INFO),
        }

        for lineno, line in enumerate(source.splitlines(), start=1):
            match = self._MARKER_PATTERN.search(line)
            if match:
                kind = match.group(1).upper()
                author = match.group(2)
                text = match.group(3).strip()

                code, severity = marker_config.get(
                    kind, ("MK001", IssueSeverity.INFO)
                )

                author_str = f" ({author})" if author else ""
                issues.append(Issue(
                    code=code,
                    message=f"{kind}{author_str}: {text}",
                    severity=severity,
                    category=IssueCategory.MARKER,
                    location=IssueLocation(file_path=path, lineno=lineno),
                ))

        return issues

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _iter_python_files(
        self,
        root: Path,
        exclude_dirs: frozenset[str],
    ) -> Iterator[Path]:
        """Yield all Python files under root, excluding specified directories."""
        for entry in root.iterdir():
            if entry.is_dir():
                if entry.name not in exclude_dirs:
                    yield from self._iter_python_files(entry, exclude_dirs)
            elif entry.suffix == ".py":
                yield entry

    # =========================================================================
    # Report Formatting
    # =========================================================================

    def format_report(
        self,
        report: IssueReport,
        format_type: str = "text",
    ) -> str:
        """Format the report as text, JSON, or other formats.

        Args:
            report: The issue report to format.
            format_type: Output format ('text', 'json', 'sarif', 'markdown').

        Returns:
            Formatted report string.
        """
        if format_type == "json":
            return self._format_json(report)
        elif format_type == "markdown":
            return self._format_markdown(report)
        elif format_type == "sarif":
            return self._format_sarif(report)
        return self._format_text(report)

    def _format_text(self, report: IssueReport) -> str:
        """Format report as plain text."""
        lines: list[str] = []

        lines.append("=" * 70)
        lines.append("CODE ISSUE REPORT")
        lines.append("=" * 70)
        lines.append(f"Project: {report.project_path}")
        lines.append(f"Files analyzed: {report.files_analyzed}")
        lines.append(f"Analysis time: {report.analysis_time:.2f}s")
        lines.append(f"Total issues: {len(report.issues)}")
        lines.append(f"  Errors: {report.error_count}")
        lines.append(f"  Warnings: {report.warning_count}")
        lines.append("")

        # Group by file
        for file_path, file_issues in sorted(report.issues_by_file.items()):
            try:
                rel_path = file_path.relative_to(report.project_path)
            except ValueError:
                rel_path = file_path
            lines.append("-" * 70)
            lines.append(f"📄 {rel_path} ({len(file_issues)} issues)")
            lines.append("-" * 70)

            for issue in sorted(file_issues, key=lambda i: i.location.lineno):
                lines.append(str(issue))
                if issue.suggestion:
                    lines.append(f"   💡 {issue.suggestion}")
            lines.append("")

        # Summary by category
        lines.append("=" * 70)
        lines.append("SUMMARY BY CATEGORY")
        lines.append("=" * 70)

        for category, cat_issues in report.issues_by_category.items():
            lines.append(f"  {category.value}: {len(cat_issues)}")

        return "\n".join(lines)

    def _format_markdown(self, report: IssueReport) -> str:
        """Format report as Markdown."""
        lines: list[str] = []

        lines.append("# Code Issue Report\n")
        lines.append(f"**Project:** `{report.project_path}`  ")
        lines.append(f"**Files analyzed:** {report.files_analyzed}  ")
        lines.append(f"**Analysis time:** {report.analysis_time:.2f}s  ")
        lines.append(f"**Total issues:** {len(report.issues)}  \n")

        # Summary table
        lines.append("## Summary\n")
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        lines.append(f"| ❌ Error | {report.error_count} |")
        lines.append(f"| ⚠️ Warning | {report.warning_count} |")
        lines.append("")

        # Issues by file
        lines.append("## Issues by File\n")

        for file_path, file_issues in sorted(report.issues_by_file.items()):
            try:
                rel_path = file_path.relative_to(report.project_path)
            except ValueError:
                rel_path = file_path
            lines.append(f"### 📄 `{rel_path}`\n")
            lines.append("| Line | Code | Message |")
            lines.append("|------|------|---------|")

            for issue in sorted(file_issues, key=lambda i: i.location.lineno):
                sev_icon = {
                    IssueSeverity.ERROR: "❌",
                    IssueSeverity.WARNING: "⚠️",
                    IssueSeverity.INFO: "ℹ️",
                    IssueSeverity.HINT: "💡",
                }.get(issue.severity, "•")
                lines.append(
                    f"| {issue.location.lineno} | "
                    f"{sev_icon} `{issue.code}` | "
                    f"{issue.message} |"
                )
            lines.append("")

        return "\n".join(lines)

    def _format_json(self, report: IssueReport) -> str:
        """Format report as JSON."""
        import json

        data = {
            "project_path": str(report.project_path),
            "files_analyzed": report.files_analyzed,
            "analysis_time": report.analysis_time,
            "total_issues": len(report.issues),
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "severity": issue.severity.value,
                    "category": issue.category.value,
                    "file": str(issue.location.file_path),
                    "line": issue.location.lineno,
                    "column": issue.location.col_offset,
                    "suggestion": issue.suggestion,
                }
                for issue in report.issues
            ],
        }

        return json.dumps(data, indent=2, ensure_ascii=False)

    def _format_sarif(self, report: IssueReport) -> str:
        """Format report as SARIF (Static Analysis Results Interchange Format)."""
        import json

        sarif = {
            "$schema": (
                "https://raw.githubusercontent.com/oasis-tcs/"
                "sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
            ),
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "python-code-analyzer",
                            "version": "1.0.0",
                            "informationUri": (
                                "https://github.com/dadashzadeh/python-code-analyzer"
                            ),
                        }
                    },
                    "results": [
                        {
                            "ruleId": issue.code,
                            "level": {
                                IssueSeverity.ERROR: "error",
                                IssueSeverity.WARNING: "warning",
                                IssueSeverity.INFO: "note",
                                IssueSeverity.HINT: "note",
                            }.get(issue.severity, "note"),
                            "message": {"text": issue.message},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": str(issue.location.file_path),
                                        },
                                        "region": {
                                            "startLine": issue.location.lineno,
                                        },
                                    }
                                }
                            ],
                        }
                        for issue in report.issues
                    ],
                }
            ],
        }

        return json.dumps(sarif, indent=2)
