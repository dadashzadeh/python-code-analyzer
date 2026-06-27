"""Python source analysis built on top of :mod:`ast`."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Union, Optional
import sys

from .constants import DOCSTRING_EXCERPT_LENGTH
from .models import (
    AnalysisOptions, 
    CodeElement, 
    TypeHintInfo, 
    ExceptionInfo, 
    CallInfo,
    CommentMarker,
    ComplexityInfo,
    DeadCodeInfo,
    DependencyInfo,
)

logger = logging.getLogger(__name__)

_FuncNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]

_STDLIB_MODULES = frozenset(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else frozenset({
    'abc', 'ast', 'asyncio', 'collections', 'contextlib', 'copy', 'dataclasses',
    'datetime', 'enum', 'functools', 'hashlib', 'io', 'itertools', 'json',
    'logging', 'math', 'os', 'pathlib', 'pickle', 're', 'shutil', 'socket',
    'sqlite3', 'string', 'subprocess', 'sys', 'tempfile', 'threading', 'time',
    'typing', 'unittest', 'urllib', 'uuid', 'warnings', 'xml', 'zipfile',
})

# Regex for TODO/FIXME markers
_MARKER_PATTERN = re.compile(
    r'#\s*(TODO|FIXME|HACK|NOTE|XXX|BUG|OPTIMIZE)(?:\(([^)]+)\))?[:\s]*(.*)$',
    re.IGNORECASE
)


class CodeAnalyzer:
    """Parse a single Python source file and expose its structure.

    The analyzer uses :mod:`ast` for reliable extraction. When parsing fails
    and signatures were requested, a conservative regex fallback is used.
    """

    def __init__(self, file_path: Union[str, Path]) -> None:
        """Read *file_path* into memory (errors are logged, not raised)."""
        self.file_path = Path(file_path)
        self.source: str = ""
        self._source_lines: list[str] = []
        self._current_function: Optional[str] = None  # For call graph tracking
        self._load_source()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_code_elements(self, opts: AnalysisOptions) -> list[CodeElement]:
        """Return a hierarchical list of :class:`CodeElement` objects."""
        if not self.source:
            return []
        try:
            tree = ast.parse(self.source, filename=str(self.file_path))
        except SyntaxError as exc:
            logger.error("Syntax error in %s: %s", self.file_path, exc)
            return self._fallback_extract() if opts.include_signatures else []
        return self._traverse(tree, opts)

    def get_comment_markers(self) -> list[CommentMarker]:
        """Extract TODO/FIXME/HACK/NOTE markers from source comments."""
        markers: list[CommentMarker] = []
        for lineno, line in enumerate(self._source_lines, start=1):
            match = _MARKER_PATTERN.search(line)
            if match:
                kind = match.group(1).upper()
                author = match.group(2)  # May be None
                text = match.group(3).strip()
                markers.append(CommentMarker(
                    kind=kind,
                    text=text,
                    lineno=lineno,
                    author=author,
                ))
        return markers

    # ------------------------------------------------------------------
    # Complexity Analysis
    # ------------------------------------------------------------------

    def _calculate_complexity(self, node: _FuncNode) -> ComplexityInfo:
        """Calculate cyclomatic complexity for a function/method."""
        complexity = 1  # Base complexity
        cognitive = 0
        nesting_depth = 0
        
        for child in ast.walk(node):
            # Decision points that increase cyclomatic complexity
            if isinstance(child, (ast.If, ast.While, ast.For)):
                complexity += 1
                cognitive += 1 + nesting_depth
            elif isinstance(child, ast.ExceptHandler):
                complexity += 1
                cognitive += 1 + nesting_depth
            elif isinstance(child, (ast.And, ast.Or)):
                complexity += 1
                cognitive += 1
            elif isinstance(child, ast.comprehension):
                complexity += 1
                cognitive += 1 + nesting_depth
            elif isinstance(child, ast.Assert):
                complexity += 1
            elif isinstance(child, ast.IfExp):  # Ternary operator
                complexity += 1
                cognitive += 1
            
            # Track nesting for cognitive complexity
            if isinstance(child, (ast.If, ast.While, ast.For, ast.With, ast.Try)):
                nesting_depth += 1
        
        # Calculate lines of code
        if hasattr(node, 'end_lineno') and node.end_lineno:
            loc = node.end_lineno - node.lineno + 1
        else:
            loc = len([n for n in ast.walk(node)])
        
        # Determine risk level
        if complexity <= 5:
            risk = "low"
        elif complexity <= 10:
            risk = "medium"
        elif complexity <= 20:
            risk = "high"
        else:
            risk = "very_high"
        
        return ComplexityInfo(
            cyclomatic=complexity,
            cognitive=cognitive,
            lines_of_code=loc,
            risk_level=risk,
        )

    # ------------------------------------------------------------------
    # Dead Code Detection
    # ------------------------------------------------------------------

    def get_dead_code(self, strict: bool = False) -> list[DeadCodeInfo]:
        """Detect potentially unused code in the file.
        
        Args:
            strict: If True, also report public functions/classes as potentially unused.
                    Default is False (conservative mode).
        """
        if not self.source:
            return []
    
        try:
            tree = ast.parse(self.source, filename=str(self.file_path))
        except SyntaxError:
            return []
    
        dead_code: list[DeadCodeInfo] = []
    
        # ========== Phase 1: Collect all definitions ==========
        definitions: dict[str, dict] = {}  # name -> {kind, lineno, context, node}
        
        # Track class structure
        class_methods: dict[str, set[str]] = {}  # class_name -> set of method names
        class_attributes: dict[str, set[str]] = {}  # class_name -> set of attribute names
        
        # Track __all__ exports
        exported_names: set[str] = set()
        
        # Track abstract methods and base class methods
        abstract_methods: set[str] = set()
        inherited_methods: set[str] = set()  # Methods that override parent methods
        
        # Common patterns that should not be flagged
        SPECIAL_NAMES = {
            # Entry points
            'main', 'run', 'start', 'execute', 'setup', 'teardown',
            # Test frameworks
            'setUp', 'tearDown', 'setUpClass', 'tearDownClass',
            'setUpModule', 'tearDownModule', 'pytest_configure',
            'pytest_runtest_setup', 'pytest_collection_modifyitems',
            # Web frameworks
            'app', 'application', 'wsgi', 'asgi', 'create_app',
            'get_wsgi_application', 'get_asgi_application',
            # Celery
            'celery', 'task',
            # CLI
            'cli', 'command',
            # Factory patterns
            'factory', 'create', 'build', 'make',
        }
        
        # Common decorator patterns that indicate external usage
        EXTERNAL_DECORATORS = {
            'property', 'staticmethod', 'classmethod',
            'abstractmethod', 'abstractproperty',
            'app.route', 'router.get', 'router.post', 'router.put', 'router.delete',
            'click.command', 'click.group', 'click.option',
            'pytest.fixture', 'pytest.mark',
            'celery.task', 'shared_task',
            'login_required', 'permission_required',
            'cached_property', 'lru_cache', 'cache',
            'dataclass', 'dataclasses.dataclass',
            'validator', 'root_validator',  # Pydantic
            'field_validator', 'model_validator',
            'register', 'receiver',  # Django signals
            'admin.register',
        }
    
        # ========== Phase 2: First pass - collect structure ==========
        for node in ast.walk(tree):
            # Collect __all__
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    exported_names.add(elt.value)
    
        # ========== Phase 3: Detailed collection ==========
        for node in ast.iter_child_nodes(tree):
            
            # === Module-level functions ===
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = self._get_decorator_names(node)
                has_external_decorator = bool(decorators & EXTERNAL_DECORATORS)
                
                definitions[node.name] = {
                    'kind': 'function',
                    'lineno': node.lineno,
                    'context': 'module',
                    'is_private': node.name.startswith('_') and not node.name.startswith('__'),
                    'is_dunder': node.name.startswith('__') and node.name.endswith('__'),
                    'has_external_decorator': has_external_decorator,
                    'decorators': decorators,
                }
            
            # === Classes ===
            elif isinstance(node, ast.ClassDef):
                decorators = self._get_decorator_names(node)
                has_external_decorator = bool(decorators & EXTERNAL_DECORATORS)
                is_abstract = self._is_abstract_class(node)
                has_bases = len(node.bases) > 0
                
                definitions[node.name] = {
                    'kind': 'class',
                    'lineno': node.lineno,
                    'context': 'module',
                    'is_private': node.name.startswith('_') and not node.name.startswith('__'),
                    'is_dunder': False,
                    'has_external_decorator': has_external_decorator,
                    'is_abstract': is_abstract,
                    'has_bases': has_bases,
                    'decorators': decorators,
                }
                
                # Collect class methods and attributes
                class_methods[node.name] = set()
                class_attributes[node.name] = set()
                
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        class_methods[node.name].add(item.name)
                        method_decorators = self._get_decorator_names(item)
                        
                        # Check for abstractmethod
                        if 'abstractmethod' in method_decorators or 'abc.abstractmethod' in method_decorators:
                            abstract_methods.add(item.name)
                        
                        # Track method definitions
                        method_key = f"{node.name}.{item.name}"
                        definitions[method_key] = {
                            'kind': 'method',
                            'lineno': item.lineno,
                            'context': f'class:{node.name}',
                            'is_private': item.name.startswith('_') and not item.name.startswith('__'),
                            'is_dunder': item.name.startswith('__') and item.name.endswith('__'),
                            'has_external_decorator': bool(method_decorators & EXTERNAL_DECORATORS),
                            'decorators': method_decorators,
                            'class_is_abstract': is_abstract,
                            'class_has_bases': has_bases,
                        }
                    
                    elif isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                class_attributes[node.name].add(target.id)
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        class_attributes[node.name].add(item.target.id)
            
            # === Imports ===
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split('.')[0]
                    definitions[name] = {
                        'kind': 'import',
                        'lineno': node.lineno,
                        'context': 'module',
                        'is_private': False,
                        'is_dunder': False,
                        'full_module': alias.name,
                    }
            
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    name = alias.asname or alias.name
                    definitions[name] = {
                        'kind': 'import',
                        'lineno': node.lineno,
                        'context': 'module',
                        'is_private': False,
                        'is_dunder': False,
                        'full_module': f"{node.module}.{alias.name}" if node.module else alias.name,
                    }
            
            # === Module-level variables ===
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        # Skip __all__, __version__, etc.
                        if target.id.startswith('__') and target.id.endswith('__'):
                            continue
                        definitions[target.id] = {
                            'kind': 'variable',
                            'lineno': node.lineno,
                            'context': 'module',
                            'is_private': target.id.startswith('_'),
                            'is_dunder': False,
                        }
            
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                if not (name.startswith('__') and name.endswith('__')):
                    definitions[name] = {
                        'kind': 'variable',
                        'lineno': node.lineno,
                        'context': 'module',
                        'is_private': name.startswith('_'),
                        'is_dunder': False,
                    }
    
        # ========== Phase 4: Collect all usages ==========
        usages: set[str] = set()
        
        for node in ast.walk(tree):
            # Name references (variables, functions, classes)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                usages.add(node.id)
            
            # Attribute access
            elif isinstance(node, ast.Attribute):
                usages.add(node.attr)
                # Also track the base object
                if isinstance(node.value, ast.Name):
                    usages.add(node.value.id)
            
            # Function/method calls
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    usages.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    usages.add(node.func.attr)
                    if isinstance(node.func.value, ast.Name):
                        usages.add(node.func.value.id)
            
            # Decorators
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for decorator in node.decorator_list:
                    self._collect_decorator_usages(decorator, usages)
            
            # Type annotations
            elif isinstance(node, ast.AnnAssign) and node.annotation:
                self._collect_type_usages(node.annotation, usages)
            elif isinstance(node, ast.arg) and node.annotation:
                self._collect_type_usages(node.annotation, usages)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
                self._collect_type_usages(node.returns, usages)
            
            # Base classes
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    self._collect_type_usages(base, usages)
                for keyword in node.keywords:
                    if keyword.value:
                        self._collect_type_usages(keyword.value, usages)
            
            # String annotations (forward references)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Could be a forward reference like "TreeNode"
                if node.value.isidentifier():
                    usages.add(node.value)
    
        # ========== Phase 5: Determine dead code ==========
        for name, info in definitions.items():
            kind = info['kind']
            lineno = info['lineno']
            context = info['context']
            
            # Skip if used
            # For methods, check both full name and short name
            if '.' in name:
                short_name = name.split('.')[-1]
                if name in usages or short_name in usages:
                    continue
            else:
                if name in usages:
                    continue
                
            # === Skip rules ===
            
            # 1. Dunder methods/variables (always skip)
            if info.get('is_dunder', False):
                continue
            
            # 2. Exported in __all__
            base_name = name.split('.')[-1] if '.' in name else name
            if base_name in exported_names:
                continue
            
            # 3. Special entry point names
            if base_name in SPECIAL_NAMES:
                continue
            
            # 4. Has external decorator (routes, fixtures, etc.)
            if info.get('has_external_decorator', False):
                continue
            
            # 5. Abstract methods
            if base_name in abstract_methods:
                continue
            
            # 6. Methods in abstract classes or classes with inheritance
            if kind == 'method':
                if info.get('class_is_abstract', False):
                    continue
                # Protected methods in classes with inheritance are likely for override
                if info.get('class_has_bases', False) and info.get('is_private', False):
                    continue
                
            # === Determine confidence and whether to report ===
            
            confidence = "low"
            reason = ""
            should_report = False
            
            if kind == "import":
                confidence = "high"
                reason = "Imported but never used"
                should_report = True
            
            elif kind == "variable":
                if info.get('is_private', False):
                    confidence = "high"
                    reason = "Private variable never referenced"
                    should_report = True
                else:
                    confidence = "medium"
                    reason = "Variable defined but not used internally"
                    should_report = strict  # Only in strict mode
            
            elif kind == "function":
                if info.get('is_private', False):
                    confidence = "high"
                    reason = "Private function never called"
                    should_report = True
                else:
                    confidence = "low"
                    reason = "Public function - may be used externally"
                    should_report = strict  # Only in strict mode
            
            elif kind == "class":
                if info.get('is_private', False):
                    confidence = "high"
                    reason = "Private class never instantiated"
                    should_report = True
                else:
                    confidence = "low"
                    reason = "Public class - may be used externally"
                    should_report = strict  # Only in strict mode
            
            elif kind == "method":
                if info.get('is_private', False):
                    confidence = "medium"
                    reason = "Protected method not called within class"
                    should_report = True
                else:
                    confidence = "low"
                    reason = "Public method - may be called externally"
                    should_report = strict
            
            if should_report:
                # For methods, report just the method name, not Class.method
                report_name = name.split('.')[-1] if '.' in name else name
                dead_code.append(DeadCodeInfo(
                    name=report_name,
                    kind=kind if kind != "method" else "function",  # Normalize method to function
                    lineno=lineno,
                    reason=reason,
                    confidence=confidence,
                ))
    
        return dead_code
    
    
    def _get_decorator_names(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]) -> set[str]:
        """Extract decorator names from a node."""
        names: set[str] = set()
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                names.add(dec.id)
            elif isinstance(dec, ast.Attribute):
                try:
                    names.add(ast.unparse(dec))
                except:
                    names.add(dec.attr)
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    names.add(dec.func.id)
                elif isinstance(dec.func, ast.Attribute):
                    try:
                        names.add(ast.unparse(dec.func))
                    except:
                        names.add(dec.func.attr)
        return names
    
    
    def _collect_decorator_usages(self, decorator: ast.expr, usages: set[str]) -> None:
        """Collect all names used in a decorator."""
        for node in ast.walk(decorator):
            if isinstance(node, ast.Name):
                usages.add(node.id)
            elif isinstance(node, ast.Attribute):
                usages.add(node.attr)
    

    def _is_abstract_class(self, node: ast.ClassDef) -> bool:
        """Check if a class is abstract (inherits from ABC or has abstract methods)."""
        # Check base classes for ABC
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in ('ABC', 'ABCMeta'):
                return True
            if isinstance(base, ast.Attribute) and base.attr in ('ABC', 'ABCMeta'):
                return True

        # Check for @abstractmethod decorators on any method
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in item.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id == 'abstractmethod':
                        return True
                    if isinstance(dec, ast.Attribute) and dec.attr == 'abstractmethod':
                        return True

        return False


    def _collect_type_usages(self, node: ast.expr, usages: set[str]) -> None:
        """Recursively collect type annotation usages."""
        if isinstance(node, ast.Name):
            usages.add(node.id)
        elif isinstance(node, ast.Attribute):
            usages.add(node.attr)
        elif isinstance(node, ast.Subscript):
            self._collect_type_usages(node.value, usages)
            if isinstance(node.slice, ast.Tuple):
                for elt in node.slice.elts:
                    self._collect_type_usages(elt, usages)
            else:
                self._collect_type_usages(node.slice, usages)
        elif isinstance(node, ast.BinOp):  # Union types with |
            self._collect_type_usages(node.left, usages)
            self._collect_type_usages(node.right, usages)
        elif isinstance(node, ast.Constant):
            # String annotations like "TreeNode"
            if isinstance(node.value, str):
                usages.add(node.value)


    def get_module_level_calls(self) -> list[CallInfo]:
        """Extract function/method calls at module level (not inside functions/classes)."""
        if not self.source:
            return []

        try:
            tree = ast.parse(self.source, filename=str(self.file_path))
        except SyntaxError:
            return []

        calls: list[CallInfo] = []

        # Only look at top-level statements
        for node in tree.body:
            # Standalone expression: load_dotenv()
            if isinstance(node, ast.Expr):
                self._collect_calls_from_node(node.value, calls)

            # Assignment: API_KEY = os.getenv("API_KEY")
            elif isinstance(node, ast.Assign):
                self._collect_calls_from_node(node.value, calls)

            # Annotated assignment: x: str = func()
            elif isinstance(node, ast.AnnAssign) and node.value:
                self._collect_calls_from_node(node.value, calls)

        return calls

    def _collect_calls_from_node(self, node: ast.AST, calls: list[CallInfo]) -> None:
        """Recursively collect all calls from an AST node."""
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_info = self._parse_call(child, "<module>")
                if call_info:
                    calls.append(call_info)


    # ------------------------------------------------------------------
    # Dependency Analysis
    # ------------------------------------------------------------------

    def get_dependencies(self) -> list[DependencyInfo]:
        """Extract module dependencies from imports."""
        if not self.source:
            return []
        
        try:
            tree = ast.parse(self.source, filename=str(self.file_path))
        except SyntaxError:
            return []
        
        dependencies: list[DependencyInfo] = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split('.')[0]
                    dependencies.append(DependencyInfo(
                        module=alias.name,
                        is_stdlib=module in _STDLIB_MODULES,
                        is_local=self._is_local_module(alias.name),
                        imported_names=[alias.asname or alias.name],
                        lineno=node.lineno,
                    ))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module_root = node.module.split('.')[0]
                    names = [alias.name for alias in node.names]
                    dependencies.append(DependencyInfo(
                        module=node.module,
                        is_stdlib=module_root in _STDLIB_MODULES,
                        is_local=self._is_local_module(node.module),
                        imported_names=names,
                        lineno=node.lineno,
                    ))
        
        return dependencies

    def _is_local_module(self, module_name: str) -> bool:
        """Check if a module is a local project module."""
        if module_name.startswith('.'):
            return True
        # Check if module exists in same directory
        module_path = self.file_path.parent / f"{module_name.split('.')[0]}.py"
        package_path = self.file_path.parent / module_name.split('.')[0] / "__init__.py"
        return module_path.exists() or package_path.exists()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_source(self) -> None:
        """Attempt to read the source file; silently record failures."""
        try:
            self.source = self.file_path.read_text(encoding="utf-8")
            self._source_lines = self.source.splitlines()
        except (PermissionError, UnicodeDecodeError, OSError) as exc:
            logger.error("Cannot read %s: %s", self.file_path, exc)

    # ------------------------------------------------------------------
    # AST traversal
    # ------------------------------------------------------------------

    def _traverse(
        self,
        node: ast.AST,
        opts: AnalysisOptions,
        in_function: bool = False,
    ) -> list[CodeElement]:
        """Collect :class:`CodeElement` objects from *node*'s children."""
        elements: list[CodeElement] = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                elements.append(self._make_function_element(child, opts))
            elif isinstance(child, ast.ClassDef):
                elements.append(self._make_class_element(child, opts))
            elif isinstance(child, ast.Import):
                elements.extend(self._make_import_elements(child))
            elif isinstance(child, ast.ImportFrom):
                elements.extend(self._make_import_from_elements(child))
            elif (
                opts.include_variables
                and not in_function
                and isinstance(child, (ast.Assign, ast.AnnAssign))
            ):
                elements.extend(self._make_variable_elements(child))
            elif isinstance(child, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                elements.extend(self._traverse(child, opts, in_function))
        return elements

    def _make_function_element(self, node: _FuncNode, opts: AnalysisOptions) -> CodeElement:
        """Build a ``kind="function"`` element."""
        sig = self._function_signature(node) if opts.include_signatures else node.name
        doc = self._docstring(node, opts.full_docstrings) if opts.include_docstrings else None
        decorators = self._decorators(node) if opts.include_decorators else []
        
        # Extract type hints
        type_hints = None
        if opts.include_type_hints:
            type_hints = self._extract_type_hints(node)
        
        # Extract exceptions
        exceptions: list[ExceptionInfo] = []
        if opts.include_exceptions:
            exceptions = self._extract_exceptions(node)
        
        # Extract calls (call graph)
        calls: list[CallInfo] = []
        if opts.include_call_graph:
            self._current_function = node.name
            calls = self._extract_calls(node)
            self._current_function = None

        complexity = None
        if opts.include_complexity:
            complexity = self._calculate_complexity(node)

        return CodeElement(
            kind="function",
            name=node.name,
            sig=sig,
            doc=doc,
            lineno=node.lineno,
            decorators=decorators,
            children=self._traverse(node, opts, in_function=True),
            type_hints=type_hints,
            exceptions=exceptions,
            calls=calls,
            complexity=complexity,
        )

    def _make_class_element(self, node: ast.ClassDef, opts: AnalysisOptions) -> CodeElement:
        """Build a ``kind="class"`` element."""
        sig = self._class_signature(node) if opts.include_signatures else node.name
        doc = self._docstring(node, opts.full_docstrings) if opts.include_docstrings else None
        decorators = self._decorators(node) if opts.include_decorators else []
        
        # Extract exceptions from class body
        exceptions: list[ExceptionInfo] = []
        if opts.include_exceptions:
            exceptions = self._extract_exceptions(node)
        
        return CodeElement(
            kind="class",
            name=node.name,
            sig=sig,
            doc=doc,
            lineno=node.lineno,
            decorators=decorators,
            children=self._traverse(node, opts, in_function=False),
            exceptions=exceptions,
        )

    @staticmethod
    def _make_import_elements(node: ast.Import) -> list[CodeElement]:
        """Build elements for a plain ``import`` statement."""
        return [
            CodeElement(
                kind="import",
                name=alias.name,
                sig=f"import: {alias.name}",
                lineno=node.lineno,
            )
            for alias in node.names
        ]

    @staticmethod
    def _make_import_from_elements(node: ast.ImportFrom) -> list[CodeElement]:
        """Build elements for a ``from … import`` statement."""
        module = node.module or ""
        return [
            CodeElement(
                kind="import",
                name=alias.name,
                sig=f"from: {module}.{alias.name}",
                lineno=node.lineno,
            )
            for alias in node.names
        ]

    @staticmethod
    def _make_variable_elements(
        node: Union[ast.Assign, ast.AnnAssign],
    ) -> list[CodeElement]:
        """Build ``kind="variable"`` elements for an assignment node."""
        results: list[CodeElement] = []
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                try:
                    annotation = ast.unparse(node.annotation)
                    sig = f"{node.target.id}: {annotation}"
                except Exception:
                    sig = node.target.id
                results.append(
                    CodeElement(
                        kind="variable",
                        name=node.target.id,
                        sig=sig,
                        lineno=node.lineno,
                    )
                )
            return results

        targets: list[ast.expr] = []
        for target in node.targets:
            if isinstance(target, (ast.Tuple, ast.List)):
                targets.extend(target.elts)
            else:
                targets.append(target)
        for target in targets:
            if isinstance(target, ast.Name):
                results.append(
                    CodeElement(
                        kind="variable",
                        name=target.id,
                        sig=target.id,
                        lineno=node.lineno,
                    )
                )
        return results

    # ------------------------------------------------------------------
    # Type Hints Extraction
    # ------------------------------------------------------------------

    def _extract_type_hints(self, node: _FuncNode) -> TypeHintInfo:
        """Extract type hint information from a function/method."""
        params: dict[str, str] = {}
        return_type: Optional[str] = None
        
        # Extract parameter type hints
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.annotation:
                try:
                    params[arg.arg] = ast.unparse(arg.annotation)
                except Exception:
                    params[arg.arg] = "?"
        
        # *args
        if node.args.vararg and node.args.vararg.annotation:
            try:
                params[f"*{node.args.vararg.arg}"] = ast.unparse(node.args.vararg.annotation)
            except Exception:
                pass
        
        # **kwargs
        if node.args.kwarg and node.args.kwarg.annotation:
            try:
                params[f"**{node.args.kwarg.arg}"] = ast.unparse(node.args.kwarg.annotation)
            except Exception:
                pass
        
        # Return type
        if node.returns:
            try:
                return_type = ast.unparse(node.returns)
            except Exception:
                return_type = "?"
        
        return TypeHintInfo(params=params, return_type=return_type)

    # ------------------------------------------------------------------
    # Exception Extraction
    # ------------------------------------------------------------------

    def _extract_exceptions(self, node: ast.AST) -> list[ExceptionInfo]:
        """Extract all raised and caught exceptions from a node."""
        exceptions: list[ExceptionInfo] = []
        
        for child in ast.walk(node):
            # Handle 'raise' statements
            if isinstance(child, ast.Raise):
                if child.exc:
                    exc_name = self._get_exception_name(child.exc)
                    if exc_name:
                        message = None
                        # Try to extract message from exception constructor
                        if isinstance(child.exc, ast.Call) and child.exc.args:
                            try:
                                message = ast.unparse(child.exc.args[0])
                            except Exception:
                                pass
                        exceptions.append(ExceptionInfo(
                            name=exc_name,
                            lineno=child.lineno,
                            context="raise",
                            message=message,
                        ))
            
            # Handle 'except' clauses
            elif isinstance(child, ast.ExceptHandler):
                if child.type:
                    # Handle multiple exceptions: except (A, B, C)
                    if isinstance(child.type, ast.Tuple):
                        for exc_type in child.type.elts:
                            exc_name = self._get_exception_name(exc_type)
                            if exc_name:
                                exceptions.append(ExceptionInfo(
                                    name=exc_name,
                                    lineno=child.lineno,
                                    context="except",
                                ))
                    else:
                        exc_name = self._get_exception_name(child.type)
                        if exc_name:
                            exceptions.append(ExceptionInfo(
                                name=exc_name,
                                lineno=child.lineno,
                                context="except",
                            ))
                else:
                    # Bare except:
                    exceptions.append(ExceptionInfo(
                        name="BaseException",
                        lineno=child.lineno,
                        context="except",
                    ))
        
        return exceptions

    @staticmethod
    def _get_exception_name(node: ast.expr) -> Optional[str]:
        """Extract the exception class name from an AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            try:
                return ast.unparse(node)
            except Exception:
                return node.attr
        elif isinstance(node, ast.Call):
            return CodeAnalyzer._get_exception_name(node.func)
        return None

    # ------------------------------------------------------------------
    # Call Graph Extraction
    # ------------------------------------------------------------------

    def _extract_calls(self, node: ast.AST) -> list[CallInfo]:
        """Extract all function/method calls from a node."""
        calls: list[CallInfo] = []
        caller = self._current_function or "<module>"
        
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_info = self._parse_call(child, caller)
                if call_info:
                    calls.append(call_info)
        
        return calls

    def _parse_call(self, node: ast.Call, caller: str) -> Optional[CallInfo]:
        """Parse a Call node into CallInfo."""
        if isinstance(node.func, ast.Name):
            # Simple function call: func()
            return CallInfo(
                caller=caller,
                callee=node.func.id,
                lineno=node.lineno,
                is_method=False,
            )
        elif isinstance(node.func, ast.Attribute):
            # Method call: obj.method() or module.func()
            receiver = None
            if isinstance(node.func.value, ast.Name):
                receiver = node.func.value.id
            elif isinstance(node.func.value, ast.Attribute):
                try:
                    receiver = ast.unparse(node.func.value)
                except Exception:
                    receiver = "?"
            elif isinstance(node.func.value, ast.Call):
                # Chained call: get_obj().method()
                receiver = "<call>"
            
            return CallInfo(
                caller=caller,
                callee=node.func.attr,
                lineno=node.lineno,
                is_method=True,
                receiver=receiver,
            )
        return None

    # ------------------------------------------------------------------
    # Signature / decorator / docstring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _function_signature(node: _FuncNode) -> str:
        """Return a clean, single-line function signature."""
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        try:
            args_str = ast.unparse(node.args)
            return_part = (
                f" -> {ast.unparse(node.returns)}"
                if getattr(node, "returns", None)
                else ""
            )
            return f"{prefix}def {node.name}({args_str}){return_part}"
        except Exception:
            args_str = ", ".join(arg.arg for arg in node.args.args)
            return f"{prefix}def {node.name}({args_str})"

    @staticmethod
    def _class_signature(node: ast.ClassDef) -> str:
        """Return a clean, single-line class signature with base classes."""
        try:
            bases = [ast.unparse(base) for base in node.bases]
            bases_part = f"({', '.join(bases)})" if bases else ""
            return f"class {node.name}{bases_part}"
        except Exception:
            return f"class {node.name}"

    @staticmethod
    def _decorators(node: Union[_FuncNode, ast.ClassDef]) -> list[str]:
        """Return decorator strings such as ``"@property"``."""
        decorators: list[str] = []
        for dec in node.decorator_list:
            try:
                decorators.append("@" + ast.unparse(dec))
            except Exception:
                continue
        return decorators

    @staticmethod
    def _docstring(
        node: Union[_FuncNode, ast.ClassDef],
        full: bool,
    ) -> "str | None":
        """Extract the docstring of *node*, optionally truncated."""
        raw = ast.get_docstring(node)
        if raw is None:
            return None
        if full:
            return " ".join(line.strip() for line in raw.splitlines() if line.strip())
        first_line = raw.splitlines()[0].strip()
        if len(first_line) > DOCSTRING_EXCERPT_LENGTH:
            return first_line[: DOCSTRING_EXCERPT_LENGTH - 1] + "…"
        return first_line

    # ------------------------------------------------------------------
    # Regex fallback
    # ------------------------------------------------------------------

    def _fallback_extract(self) -> list[CodeElement]:
        """Extract a flat element list via regex when AST parsing fails."""
        results: list[CodeElement] = []
        func_re = re.compile(
            r"^(async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(->\s*[\w\[\], |]+)?\s*:"
        )
        class_re = re.compile(r"^class\s+(\w+)\s*(\([^)]*\))?\s*:")
        for lineno, raw_line in enumerate(self._source_lines, start=1):
            line = raw_line.strip()
            func_match = func_re.match(line)
            class_match = class_re.match(line)
            if func_match:
                results.append(
                    CodeElement(
                        kind="function",
                        name=func_match.group(2),
                        sig=line.rstrip(":"),
                        lineno=lineno,
                    )
                )
            elif class_match:
                results.append(
                    CodeElement(
                        kind="class",
                        name=class_match.group(1),
                        sig=line.rstrip(":"),
                        lineno=lineno,
                    )
                )
        return results
