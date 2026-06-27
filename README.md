# 🌳 Directory Tree Analyzer

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A powerful Python tool that generates **annotated directory trees** with deep code analysis capabilities. Walk through your project structure while extracting valuable insights about your Python code — including complexity metrics, dead code detection, dependency analysis, type hints, call graphs, and more.

## ✨ Features

### 📁 Directory Tree Generation

- Beautiful box-drawing tree visualization
- Multiple output formats: **Plain Text**, **Markdown**, and **HTML** (Mermaid) [[9]]
- Smart exclusion of common directories (`__pycache__`, `.git`, `node_modules`, `.venv`, etc.) [[7]]
- Customizable directory and file extension filters

### 🔍 Python Code Analysis

- **Functions & Classes** — Signatures, docstrings, decorators [[11]]
- **Imports** — Detailed import tracking [[11]]
- **Variables** — Module and class-level variable detection [[11]]
- **Type Hints** — Comprehensive type annotation extraction [[10]]
- **Exceptions** — Track raised and caught exceptions [[10]]
- **Call Graph** — Visualize function/method call relationships [[10]]
- **Comment Markers** — Find `TODO`, `FIXME`, `HACK`, `NOTE`, `XXX`, `BUG`, `OPTIMIZE` [[11]]

### 📊 Advanced Metrics

- **Cyclomatic Complexity** with risk levels 🟢🟡🟠🔴 [[11]]
- **Cognitive Complexity** for readability
- **Lines of Code** tracking [[10]]

### 💀 Dead Code Detection

- **Per-file** and **cross-file (project-wide)** analysis [[1]]
- Smart heuristics: respects `__all__`, dunder methods, abstract methods, decorators (`@app.route`, `@pytest.fixture`), and entry points like `main`/`run`/`setup` [[1]]
- Confidence levels (high / medium / low)

### 📦 Dependency Analysis

- Classify imports as **stdlib**, **third-party**, or **local** [[11]]

---

## 🚀 Installation

### From source

```bash
git clone https://github.com/dadashzadeh/python-code-analyzer.git
cd python-code-analyzer
pip install -e .
```

**Requirements:** Python 3.9+ — uses only the standard library (no runtime dependencies!) [[11]]

---

## 📖 Usage

### 🔹 1) As a CLI tool

#### Basic

```bash
python -m python_code_analyzer /path/to/project
```

#### Show functions, classes, and imports

```bash
python -m python_code_analyzer . -f -c -i
```

#### Full analysis (everything!)

```bash
python -m python_code_analyzer . -A
```

#### Export to Markdown / HTML

```bash
python -m python_code_analyzer . -A --markdown report.md
python -m python_code_analyzer . -A --html report.html
```

---

### 🔹 2) Programmatic Usage (Without CLI) 🐍

You can use Directory Tree Analyzer directly from your Python code without invoking the CLI. This is useful for integration into other tools, IDEs, or custom scripts.

#### Example 1: Basic Tree Generation

```python
from pathlib import Path
from python_code_analyzer import TreeGenerator, AnalysisOptions, DisplayFilter
from python_code_analyzer.renderers import TextRenderer

# Configure what to extract from code
analysis_opts = AnalysisOptions(
    include_signatures=True,
    include_docstrings=True,
    include_line_numbers=True,
)

# Configure what to display in the rendered output
display_filter = DisplayFilter(
    show_functions=True,
    show_classes=True,
    show_imports=True,
)

# Create the generator
generator = TreeGenerator(
    exclude_dirs=frozenset({"__pycache__", ".git", "venv"}),
    exclude_extensions=frozenset({".pyc", ".pyo"}),
    analysis_opts=analysis_opts,
    display_filter=display_filter,
)

# Walk a directory and build the tree
root_node = generator.generate(Path("./my_project"))

# Render to plain text
text_output = TextRenderer(display_filter, analysis_opts).render(root_node)
print(text_output)
```

#### Example 2: Full Analysis with All Features

```python
from pathlib import Path
from python_code_analyzer import TreeGenerator, AnalysisOptions, DisplayFilter
from python_code_analyzer.renderers import MarkdownRenderer

# Enable every analysis feature
analysis_opts = AnalysisOptions(
    include_signatures=True,
    include_docstrings=True,
    include_decorators=True,
    include_variables=True,
    include_line_numbers=True,
    include_type_hints=True,
    include_exceptions=True,
    include_call_graph=True,
    include_markers=True,
    include_complexity=True,
    include_dead_code=True,
    include_dependencies=True,
    strict_dead_code=False,
    cross_file_analysis=True,  # project-wide dead code detection
)

display_filter = DisplayFilter(
    show_functions=True,
    show_classes=True,
    show_imports=True,
    show_variables=True,
    show_type_hints=True,
    show_exceptions=True,
    show_call_graph=True,
    show_markers=True,
    show_complexity=True,
    show_dead_code=True,
    show_dependencies=True,
)

generator = TreeGenerator(
    exclude_dirs=frozenset({"__pycache__", ".git", "venv", "node_modules"}),
    exclude_extensions=frozenset({".pyc", ".pyo", ".pyd"}),
    analysis_opts=analysis_opts,
    display_filter=display_filter,
)

root = generator.generate(Path("./my_project"))

# Save as Markdown
markdown = MarkdownRenderer(display_filter, analysis_opts).render(root)
Path("analysis_report.md").write_text(markdown, encoding="utf-8")
print("✅ Analysis report saved to analysis_report.md")
```

#### Example 3: Analyzing a Single Python File

```python
from pathlib import Path
from python_code_analyzer.analyzer import CodeAnalyzer
from python_code_analyzer.models import AnalysisOptions

analyzer = CodeAnalyzer("my_module.py")

opts = AnalysisOptions(
    include_signatures=True,
    include_docstrings=True,
    include_complexity=True,
    include_type_hints=True,
)

elements = analyzer.get_code_elements(opts)

for element in elements:
    print(f"[{element.kind}] {element.sig}  (Line {element.lineno})")
    if element.doc:
        print(f"   📝 {element.doc}")
    if element.complexity:
        print(f"   📊 Cyclomatic Complexity: {element.complexity.cyclomatic} "
              f"[{element.complexity.risk_level}]")
```

#### Example 4: Detecting Dead Code

```python
from python_code_analyzer.analyzer import CodeAnalyzer

analyzer = CodeAnalyzer("my_module.py")
dead_items = analyzer.get_dead_code(strict=False)

for item in dead_items:
    print(f"💀 [{item.kind}] '{item.name}' at line {item.lineno}")
    print(f"   Reason: {item.reason}")
    print(f"   Confidence: {item.confidence}")
```

#### Example 5: Extracting TODO/FIXME Markers

```python
from python_code_analyzer.analyzer import CodeAnalyzer

analyzer = CodeAnalyzer("my_module.py")
markers = analyzer.get_comment_markers()

for marker in markers:
    author = f" [@{marker.author}]" if marker.author else ""
    print(f"📌 {marker.kind}{author} (L{marker.lineno}): {marker.text}")
```

#### Example 6: Cross-File Dead Code Analysis

```python
from pathlib import Path
from python_code_analyzer import TreeGenerator, AnalysisOptions, DisplayFilter

opts = AnalysisOptions(
    include_dead_code=True,
    cross_file_analysis=True,  # 🔑 key option
    strict_dead_code=True,
)

flt = DisplayFilter(show_dead_code=True)

gen = TreeGenerator(
    exclude_dirs=frozenset({"__pycache__", ".git"}),
    exclude_extensions=frozenset({".pyc"}),
    analysis_opts=opts,
    display_filter=flt,
)

root = gen.generate(Path("./my_project"))


def walk_tree(node):
    """Recursively visit every node and print dead code findings."""
    if node.dead_code:
        print(f"\n📄 {node.path}")
        for dc in node.dead_code:
            print(f"  💀 {dc.kind} '{dc.name}' (L{dc.lineno}) — {dc.reason}")
    for child in node.children:
        walk_tree(child)

walk_tree(root)
```

#### Example 6: Issue Reporter

```
from pathlib import Path
from python_code_analyzer.models import AnalysisOptions, DisplayFilter
from python_code_analyzer.issue_reporter import IssueReporter, IssueReporterConfig

# Define analysis options
analysis_opts = AnalysisOptions(
    include_signatures=True,
    include_docstrings=True,
    include_decorators=True,
    include_variables=True,
    include_line_numbers=True,
    include_type_hints=True,
    include_exceptions=True,
    include_call_graph=True,
    include_markers=True,
    include_complexity=True,
    include_dead_code=True,
    include_dependencies=True,
    strict_dead_code=False,
    cross_file_analysis=True,  # Enable project-wide dead code detection
)

display_filter = DisplayFilter(
    show_functions=True,
    show_classes=True,
    show_imports=True,
    show_variables=True,
    show_type_hints=True,
    show_exceptions=True,
    show_call_graph=True,
    show_markers=True,
    show_complexity=True,
    show_dead_code=True,
    show_dependencies=True,
)

# Create config from options
config = IssueReporterConfig.from_analysis_options(analysis_opts, display_filter)
# Or create config directly
config = IssueReporterConfig(
    max_line_length=99,
    max_function_complexity=10,
    check_dead_code=True,
    cross_file_analysis=True,
    strict_dead_code=False,
    check_documentation=True,
    require_docstrings=True,
)


# Analyze project
reporter = IssueReporter(config)
report = reporter.analyze_directory(Path("./python-code-analyzer"))
# Print report
print(reporter.format_report(report, format_type="text"))
```

---

## 🎛️ Command-Line Options

### Element Visibility

| Flag | Description |
|------|-------------|
| `-f`, `--functions` | Show functions |
| `-c`, `--classes` | Show classes |
| `-i`, `--imports` | Show imports |
| `-V`, `--variables` | Show variables |

### Detail Level

| Flag | Description |
|------|-------------|
| `-s`, `--signatures` | Show full signatures (implies `-f` and `-c`) |
| `-d`, `--docstrings` | Show first-line docstrings |
| `--full-docstrings` | Show complete docstrings |
| `--decorators` | Show decorators |
| `-n`, `--line-numbers` | Show source line numbers |

### Extended Analysis

| Flag | Description |
|------|-------------|
| `-t`, `--type-hints` | Extract type hint info [[6]] |
| `-e`, `--exceptions` | Extract raised/caught exceptions |
| `-g`, `--call-graph` | Build call graph |
| `-m`, `--markers` | Extract TODO/FIXME/HACK comments |
| `-x`, `--complexity` | Calculate cyclomatic complexity |
| `-D`, `--dead-code` | Detect unused code |
| `--strict-dead-code` | Also flag public unused functions/classes |
| `--cross-file` | Project-wide dead code analysis |
| `--deps` | Analyze module dependencies |
| `-A`, `--all-analysis` | Enable all extended features |

### Output Files

| Flag | Description |
|------|-------------|
| `--markdown FILE` | Write Markdown to FILE |
| `--html FILE` | Write HTML (Mermaid) to FILE |
| `--json FILE` | Export JSON to FILE |

---

## 🏗️ Project Structure

```
python-code-analyzer/                 ← Repository root
├── python_code_analyzer/             ← Python package
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── constants.py
│   ├── models.py
│   ├── analyzer.py
│   ├── project_analyzer.py
│   ├── generator.py
│   ├── issue_reporter.py
│   └── renderers/
│       ├── __init__.py
│       ├── base.py
│       ├── text.py
│       ├── markdown.py
│       └── html.py
├── setup.py
├── pyproject.toml
├── LICENSE
└── README.md
```

---

## 🧠 How It Works

1. **Tree Generation** (`generator.py`) — Recursively walks the directory, applying exclusion rules [[8]]
2. **Per-file Analysis** (`analyzer.py`) — Parses each `.py` file's AST [[11]]
3. **Cross-File Analysis** (`project_analyzer.py`) — Two-phase: collection → analysis [[1]]
4. **Rendering** — Output via Text / Markdown / HTML renderer

---

## 💡 Example Output

```
my_project
┣ src
┃ ┣ main.py
┃ ┃ ┣ def process_data(data: List[int]) -> Dict[str, int] 🟢
┃ ┃ ┃ ┣ 📊 Complexity: CC=3 | Cognitive=2 [Simple]
┃ ┃ ┃ ┣ 🔷 Types: params: data: List[int] | returns: Dict[str, int]
┃ ┃ ┃ ┗ 📞 Calls: validate(), transform()
┃ ┃ ┣ 📌 TODO (L42): Add error handling
┃ ┃ ┗ 💀 Dead Code (2): os (L3), sys (L4)
┗ tests
  ┗ test_main.py
```

---

## 🧪 Running Tests

```bash
pip install -e ".[dev]"
pytest
```

With coverage:

```bash
pytest --cov=python-code-analyzer --cov-report=html
```

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Run tests & lint (`pytest && ruff check python-code-analyzer/`)
4. Submit a Pull Request

---

## 📄 License

MIT License — see [LICENSE](LICENSE).

---
