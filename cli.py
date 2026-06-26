"""Command-line entry point with extended analysis options."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from .constants import DEFAULT_EXCLUDE_DIRS, DEFAULT_EXCLUDE_EXTENSIONS
from .generator import TreeGenerator
from .models import AnalysisOptions, DisplayFilter
from .renderers import HtmlRenderer, MarkdownRenderer, TextRenderer

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def build_parser() -> argparse.ArgumentParser:
    """Construct and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="directory_tree",
        description="Generate an annotated directory tree with Python analysis.",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Root directory to analyse (default: current directory).",
    )

    vis = parser.add_argument_group("element visibility")
    vis.add_argument("-f", "--functions", action="store_true", help="Show functions.")
    vis.add_argument("-c", "--classes", action="store_true", help="Show classes.")
    vis.add_argument("-i", "--imports", action="store_true", help="Show imports.")
    vis.add_argument("-V", "--variables", action="store_true", help="Show variables.")

    detail = parser.add_argument_group("detail level")
    detail.add_argument("-s", "--signatures", action="store_true",
                        help="Show full signatures (implies -f and -c).")
    detail.add_argument("-d", "--docstrings", action="store_true",
                        help="Show first-line docstrings.")
    detail.add_argument("--full-docstrings", action="store_true",
                        help="Show complete docstrings (implies -d).")
    detail.add_argument("--decorators", action="store_true",
                        help="Show decorators on functions and classes.")
    detail.add_argument("-n", "--line-numbers", action="store_true",
                        help="Show source line numbers.")

    # === NEW: Extended analysis options ===
    analysis = parser.add_argument_group("extended analysis")
    analysis.add_argument("-t", "--type-hints", action="store_true",
                          help="Extract and display type hint information.")
    analysis.add_argument("-e", "--exceptions", action="store_true",
                          help="Extract raised and caught exceptions.")
    analysis.add_argument("-g", "--call-graph", action="store_true",
                          help="Build call graph (function calls).")
    analysis.add_argument("-m", "--markers", action="store_true",
                          help="Extract TODO/FIXME/HACK/NOTE comments.")
    analysis.add_argument("-x", "--complexity", action="store_true",    
                          help="Calculate cyclomatic complexity.")
    analysis.add_argument("-D", "--dead-code", action="store_true",     
                          help="Detect potentially unused code.")
    analysis.add_argument("--strict-dead-code", action="store_true",
                      help="Also report public unused functions/classes.")
    analysis.add_argument("--cross-file", action="store_true",
                      help="Analyze dead code across entire project (not just per-file).")
    analysis.add_argument("--deps", "--dependencies", action="store_true",
                          dest="dependencies",
                          help="Analyze module dependencies.")
    analysis.add_argument("-A", "--all-analysis", action="store_true",
                          help="Enable all extended analysis features.")

    out = parser.add_argument_group("output files (optional)")
    out.add_argument("--markdown", metavar="FILE",
                     help="Also write a Markdown tree to FILE.")
    out.add_argument("--html", metavar="FILE",
                     help="Also write an HTML (Mermaid) tree to FILE.")
    out.add_argument("--json", metavar="FILE",
                     help="Export analysis data as JSON to FILE.")

    excl = parser.add_argument_group("exclusions")
    excl.add_argument("--exclude-dirs", nargs="+", metavar="DIR",
                      help="Additional directory names to exclude.")
    excl.add_argument("--exclude-ext", nargs="+", metavar="EXT",
                      help="Additional file extensions to exclude.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse arguments, build the tree, and render the requested outputs."""
    parser = build_parser()
    args = parser.parse_args(argv)

    target = Path(args.directory)
    if not target.is_dir():
        parser.error(f"'{target}' is not a valid directory.")

    exclude_dirs = DEFAULT_EXCLUDE_DIRS
    if args.exclude_dirs:
        exclude_dirs = exclude_dirs | frozenset(args.exclude_dirs)

    exclude_extensions = DEFAULT_EXCLUDE_EXTENSIONS
    if args.exclude_ext:
        normalised = {e if e.startswith(".") else f".{e}" for e in args.exclude_ext}
        exclude_extensions = exclude_extensions | frozenset(normalised)

    # Handle --all-analysis flag
    all_analysis = args.all_analysis
    
    show_functions = args.functions or args.signatures
    show_classes = args.classes or args.signatures
    show_docstrings = args.docstrings or args.full_docstrings
    show_type_hints = args.type_hints or all_analysis
    show_exceptions = args.exceptions or all_analysis
    show_call_graph = args.call_graph or all_analysis
    show_markers = args.markers or all_analysis
    show_complexity = args.complexity or all_analysis
    show_dead_code = args.dead_code or all_analysis
    show_dependencies = args.dependencies or all_analysis

    analysis_opts = AnalysisOptions(
        include_signatures=args.signatures,
        include_docstrings=show_docstrings,
        full_docstrings=args.full_docstrings,
        include_decorators=args.decorators,
        include_variables=args.variables,
        include_line_numbers=args.line_numbers,
        include_type_hints=show_type_hints,
        include_exceptions=show_exceptions,
        include_call_graph=show_call_graph,
        include_markers=show_markers,
        include_complexity=show_complexity,     
        include_dead_code=show_dead_code,
        strict_dead_code=getattr(args, 'strict_dead_code', False),
        cross_file_analysis=getattr(args, 'cross_file', False),
        include_dependencies=show_dependencies, 
        
    )
    display_filter = DisplayFilter(
        show_functions=show_functions,
        show_classes=show_classes,
        show_imports=args.imports,
        show_variables=args.variables,
        show_type_hints=show_type_hints,
        show_exceptions=show_exceptions,
        show_call_graph=show_call_graph,
        show_markers=show_markers,
        show_complexity=show_complexity,     
        show_dead_code=show_dead_code,       
        show_dependencies=show_dependencies, 
    )

    generator = TreeGenerator(
        exclude_dirs=exclude_dirs,
        exclude_extensions=exclude_extensions,
        analysis_opts=analysis_opts,
        display_filter=display_filter,
    )
    root_node = generator.generate(target)

    wrote_file = False
    if args.markdown:
        content = MarkdownRenderer(display_filter, analysis_opts).render(root_node)
        Path(args.markdown).write_text(content, encoding="utf-8")
        print(f"Markdown written to {args.markdown}")
        wrote_file = True
    if args.html:
        content = HtmlRenderer(display_filter, analysis_opts).render(root_node)
        Path(args.html).write_text(content, encoding="utf-8")
        print(f"HTML written to {args.html}")
        wrote_file = True

    if not wrote_file:
        text = TextRenderer(display_filter, analysis_opts).render(root_node)
        sys.stdout.write(text + "\n")
