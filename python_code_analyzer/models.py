"""Immutable data structures shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum

@dataclass
class TypeHintInfo:
    """Information about type hints in a code element.
    
    Attributes:
        params:      Dict mapping parameter names to their type annotations.
        return_type: The return type annotation (if any).
        variables:   Dict mapping variable names to their type annotations.
    """
    params: dict[str, str] = field(default_factory=dict)
    return_type: Optional[str] = None
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class ExceptionInfo:
    """Information about an exception raise/handle.
    
    Attributes:
        name:    The exception class name.
        lineno:  Line number where it occurs.
        context: 'raise' or 'except'.
        message: Optional message or variable.
    """
    name: str
    lineno: int
    context: str  # 'raise' or 'except'
    message: Optional[str] = None


@dataclass
class CallInfo:
    """Information about a function/method call.
    
    Attributes:
        caller:      Name of the calling function/method.
        callee:      Name of the called function/method.
        lineno:      Line number of the call.
        is_method:   Whether it's a method call (obj.method()).
        receiver:    The object/class receiving the call (if method).
    """
    caller: str
    callee: str
    lineno: int
    is_method: bool = False
    receiver: Optional[str] = None


@dataclass
class CommentMarker:
    """A TODO, FIXME, or similar marker comment.
    
    Attributes:
        kind:    Type of marker (TODO, FIXME, HACK, NOTE, XXX).
        text:    The comment text.
        lineno:  Line number.
        author:  Optional author if format is "TODO(author):".
    """
    kind: str
    text: str
    lineno: int
    author: Optional[str] = None


@dataclass
class ComplexityInfo:
    """Complexity metrics for a code element.
    
    Attributes:
        cyclomatic:      McCabe's cyclomatic complexity.
        cognitive:       Cognitive complexity score.
        halstead_volume: Halstead volume metric.
        lines_of_code:   Number of lines in the element.
        risk_level:     'low', 'medium', 'high', 'very_high'.
        maintainability: Maintainability index (0-100).
    """
    cyclomatic: int = 1
    cognitive: int = 0
    halstead_volume: Optional[float] = None
    lines_of_code: int = 0
    risk_level: str = "low"  # low (1-5), medium (6-10), high (11-20), very_high (21+)
    maintainability: Optional[float] = None


@dataclass
class DeadCodeInfo:
    """Information about potentially unused code.
    
    Attributes:
        name:       Name of the unused element.
        kind:       Type of element ('function', 'class', 'variable', 'import').
        lineno:     Line number where it's defined.
        reason:     Why it's considered dead code.
        confidence: Confidence level ('high', 'medium', 'low').
    """
    name: str
    kind: str
    lineno: int
    reason: str
    confidence: str = "medium"



@dataclass
class DependencyInfo:
    """Information about a module dependency.
    
    Attributes:
        module:         The imported module name.
        alias:          Import alias (if any).
        is_stdlib:      Whether it's a standard library module.
        is_third_party: Whether it's a third-party package.
        is_local:       Whether it's a local/project module.
        imported_names: Specific names imported from the module.
        lineno:         Line number of the import.
    """
    module: str
    alias: Optional[str] = None
    is_stdlib: bool = False
    is_third_party: bool = False
    is_local: bool = False
    imported_names: list[str] = field(default_factory=list)
    lineno: int = 0

@dataclass
class CodeElement:
    """A single discoverable element inside a Python source file.

    Attributes:
        kind:        One of ``"function"``, ``"class"``, ``"import"`` or
                     ``"variable"``.
        name:        Bare identifier (used for visibility checks).
        sig:         Human-readable signature, import path or declaration.
        doc:         Optional docstring excerpt.
        lineno:      1-based source line number (0 when unknown).
        decorators:  Decorator strings such as ``"@property"``.
        children:    Nested elements (e.g. methods inside a class).
        type_hints:  Type hint information for this element.
        exceptions:  Exceptions raised or caught in this element.
        calls:       Function/method calls made from this element.
    """

    kind: str
    name: str
    sig: str
    doc: Optional[str] = None
    lineno: int = 0
    decorators: list["CodeElement"] = field(default_factory=list)
    children: list["CodeElement"] = field(default_factory=list)
    type_hints: Optional[TypeHintInfo] = None
    exceptions: list[ExceptionInfo] = field(default_factory=list)
    calls: list[CallInfo] = field(default_factory=list)
    complexity: Optional[ComplexityInfo] = None
    is_dead_code: bool = False

    @property
    def is_private(self) -> bool:
        """Return *True* for names like ``_helper`` (but not dunders)."""
        return self.name.startswith("_") and not self.name.startswith("__")


CodeElement.__annotations__["decorators"] = "list[str]"


@dataclass
class TreeNode:
    """A node in the rendered file-system tree.

    Attributes:
        name:            Display name of the file or directory.
        path:            Resolved filesystem path.
        is_dir:          Whether the node is a directory.
        children:        Child nodes (directories/files) for directories.
        elements:        Extracted code elements for analysable files.
        error:           Inline error label (e.g. ``"Permission Denied"``).
        comment_markers: TODO/FIXME markers found in the file.
    """

    name: str
    path: Path
    is_dir: bool
    children: list["TreeNode"] = field(default_factory=list)
    elements: list[CodeElement] = field(default_factory=list)
    error: Optional[str] = None
    comment_markers: list[CommentMarker] = field(default_factory=list)
    dependencies: list[DependencyInfo] = field(default_factory=list)
    dead_code: list[DeadCodeInfo] = field(default_factory=list)
    module_calls: list[CallInfo] = field(default_factory=list)

@dataclass(frozen=True)
class AnalysisOptions:
    """Flags controlling what :class:`CodeAnalyzer` extracts.

    Attributes:
        include_signatures:    Emit full ``def``/``class`` signatures.
        include_docstrings:    Emit docstring excerpts.
        full_docstrings:       Emit the whole docstring (single line).
        include_decorators:    Capture decorators of functions and classes.
        include_variables:     Capture module- and class-level variables.
        include_line_numbers:  Capture source line numbers.
        include_type_hints:    Extract and analyze type hints.
        include_exceptions:    Extract raised/caught exceptions.
        include_call_graph:    Build call graph information.
        include_markers:       Extract TODO/FIXME/HACK/NOTE comments.
    """

    include_signatures: bool = False
    include_docstrings: bool = False
    full_docstrings: bool = False
    include_decorators: bool = False
    include_variables: bool = False
    include_line_numbers: bool = False
    include_type_hints: bool = False
    include_exceptions: bool = False
    include_call_graph: bool = False
    include_markers: bool = False
    include_complexity: bool = False
    include_dead_code: bool = False
    include_dependencies: bool = False
    strict_dead_code: bool = False
    cross_file_analysis: bool = False

@dataclass(frozen=True)
class DisplayFilter:
    """Controls which element *kinds* appear in the rendered output.

    Attributes:
        show_functions:  Render ``"function"`` elements.
        show_classes:    Render ``"class"`` elements.
        show_imports:    Render ``"import"`` elements.
        show_variables:  Render ``"variable"`` elements.
        show_type_hints: Show type hint analysis.
        show_exceptions: Show exception information.
        show_call_graph: Show call relationships.
        show_markers:    Show TODO/FIXME markers.
    """

    show_functions: bool = False
    show_classes: bool = False
    show_imports: bool = False
    show_variables: bool = False
    show_type_hints: bool = False
    show_exceptions: bool = False
    show_call_graph: bool = False
    show_markers: bool = False
    show_complexity: bool = False
    show_dead_code: bool = False
    show_dependencies: bool = False

    @property
    def any_enabled(self) -> bool:
        """Return *True* when at least one kind is visible."""
        return (
            self.show_functions
            or self.show_classes
            or self.show_imports
            or self.show_variables
            or self.show_type_hints
            or self.show_exceptions 
            or self.show_call_graph 
            or self.show_markers
            or self.show_complexity 
            or self.show_dead_code  
            or self.show_dependencies
        )


"""Issue reporting data structures."""

class IssueSeverity(Enum):
    """Severity levels for reported issues."""
    
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"


class IssueCategory(Enum):
    """Categories of code issues."""
    
    STYLE = "style"              # PEP8, formatting
    COMPLEXITY = "complexity"    # Cyclomatic, cognitive complexity
    DEAD_CODE = "dead_code"      # Unused imports, functions, variables
    TYPE_HINT = "type_hint"      # Missing or incorrect type hints
    DOCUMENTATION = "documentation"  # Missing docstrings
    SECURITY = "security"        # Potential security issues
    MAINTAINABILITY = "maintainability"  # Code smells
    MARKER = "marker"            # TODO, FIXME, BUG markers


@dataclass
class IssueLocation:
    """Location information for an issue.
    
    Attributes:
        file_path:   Path to the file containing the issue.
        lineno:      Line number (1-based).
        col_offset:  Column offset (0-based, optional).
        end_lineno:  End line number (optional).
        end_col:     End column offset (optional).
    """
    
    file_path: Path
    lineno: int
    col_offset: Optional[int] = None
    end_lineno: Optional[int] = None
    end_col: Optional[int] = None

    def __str__(self) -> str:
        """Return human-readable location string."""
        loc = f"{self.file_path}:{self.lineno}"
        if self.col_offset is not None:
            loc += f":{self.col_offset}"
        return loc


@dataclass
class Issue:
    """A single code issue/problem.
    
    Attributes:
        code:        Unique issue code (e.g., 'E501', 'C901', 'DC001').
        message:     Human-readable description of the issue.
        severity:    Severity level of the issue.
        category:    Category of the issue.
        location:    Location information.
        suggestion:  Optional fix suggestion.
        context:     Optional code context/snippet.
        rule_url:    Optional URL to rule documentation.
    """
    
    code: str
    message: str
    severity: IssueSeverity
    category: IssueCategory
    location: IssueLocation
    suggestion: Optional[str] = None
    context: Optional[str] = None
    rule_url: Optional[str] = None

    def __str__(self) -> str:
        """Return formatted issue string."""
        severity_icon = {
            IssueSeverity.ERROR: "❌",
            IssueSeverity.WARNING: "⚠️",
            IssueSeverity.INFO: "ℹ️",
            IssueSeverity.HINT: "💡",
        }
        icon = severity_icon.get(self.severity, "•")
        return f"{icon} [{self.code}] {self.location}: {self.message}"


@dataclass
class IssueReport:
    """Aggregated issue report for a project.
    
    Attributes:
        project_path:  Root path of the analyzed project.
        issues:        List of all detected issues.
        files_analyzed: Number of files analyzed.
        analysis_time:  Time taken for analysis (seconds).
    """
    
    project_path: Path
    issues: list[Issue] = field(default_factory=list)
    files_analyzed: int = 0
    analysis_time: float = 0.0

    @property
    def error_count(self) -> int:
        """Return count of error-level issues."""
        return sum(1 for i in self.issues if i.severity == IssueSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        """Return count of warning-level issues."""
        return sum(1 for i in self.issues if i.severity == IssueSeverity.WARNING)

    @property
    def issues_by_category(self) -> dict[IssueCategory, list[Issue]]:
        """Group issues by category."""
        result: dict[IssueCategory, list[Issue]] = {}
        for issue in self.issues:
            result.setdefault(issue.category, []).append(issue)
        return result

    @property
    def issues_by_file(self) -> dict[Path, list[Issue]]:
        """Group issues by file path."""
        result: dict[Path, list[Issue]] = {}
        for issue in self.issues:
            result.setdefault(issue.location.file_path, []).append(issue)
        return result
