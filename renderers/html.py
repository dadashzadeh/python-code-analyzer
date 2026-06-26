"""Self-contained HTML renderer with Mermaid architecture diagrams."""

from __future__ import annotations

import html
import json
import re
from typing import List, Dict, Set, Any
from collections import defaultdict

from ..models import (
    CodeElement, TreeNode, DependencyInfo, CallInfo,
    ComplexityInfo, DeadCodeInfo, CommentMarker,
)
from .base import BaseRenderer


# ----------------------------------------------------------------------
# Mermaid ID sanitization
# ----------------------------------------------------------------------
_ID_RE = re.compile(r"[^A-Za-z0-9_]")


def _mid(*parts: str) -> str:
    """Build a Mermaid-safe node id from arbitrary path parts."""
    raw = "__".join(p for p in parts if p)
    sid = _ID_RE.sub("_", raw)
    if sid and sid[0].isdigit():
        sid = "n_" + sid
    return sid or "n_root"


def _esc_label(text: str) -> str:
    """Escape a label for inclusion in a Mermaid node `["..."]`."""
    return (
        text.replace("\\", "\\\\")
            .replace('"', "&quot;")
            .replace("\n", "<br/>")
    )


class HtmlRenderer(BaseRenderer):
    """Render a TreeNode as an interactive HTML document with Mermaid diagrams."""

    # ==================================================================
    # Entry point
    # ==================================================================
    def render(self, root: TreeNode) -> str:
        stats           = self._collect_stats(root)
        classes_data    = self._collect_classes(root)
        tree_data       = self._build_tree_json(root)
        dead_code_data  = self._collect_dead_code(root)
        markers_data    = self._collect_markers(root)

        deps_mermaid    = self._build_dependency_mermaid(root)
        flow_mermaid    = self._build_callflow_mermaid(root)

        return self._generate_html(
            title=root.name,
            stats=stats,
            classes_data=classes_data,
            tree_data=tree_data,
            dead_code_data=dead_code_data,
            markers_data=markers_data,
            deps_mermaid=deps_mermaid,
            flow_mermaid=flow_mermaid,
        )

    # ==================================================================
    # STATISTICS
    # ==================================================================
    def _collect_stats(self, root: TreeNode) -> Dict[str, int]:
        stats = dict(files=0, classes=0, functions=0, variables=0,
                     imports=0, lines=0, dead_code=0, todos=0,
                     complexity_high=0)
        self._count_stats(root, stats)
        return stats

    def _count_stats(self, node: TreeNode, stats: Dict[str, int]) -> None:
        if node.is_dir:
            for child in node.children:
                self._count_stats(child, stats)
            return
        if node.path.suffix == ".py":
            stats["files"] += 1
        stats["dead_code"] += len(node.dead_code)
        stats["todos"]     += len(node.comment_markers)
        for elem in node.elements:
            self._count_element(elem, stats)

    def _count_element(self, elem: CodeElement, stats: Dict[str, int]) -> None:
        key = {"class": "classes", "function": "functions",
               "variable": "variables", "import": "imports"}.get(elem.kind)
        if key:
            stats[key] += 1
        if elem.complexity and elem.complexity.cyclomatic > 10:
            stats["complexity_high"] += 1
        for child in elem.children:
            self._count_element(child, stats)

    # ==================================================================
    # CLASSES (kept simple list — class diagram tab unchanged)
    # ==================================================================
    def _collect_classes(self, root: TreeNode) -> List[Dict]:
        out: List[Dict] = []
        self._find_classes(root, out)
        return out

    def _find_classes(self, node: TreeNode, out: List[Dict]) -> None:
        if node.is_dir:
            for child in node.children:
                self._find_classes(child, out)
            return
        for elem in node.elements:
            if elem.kind != "class":
                continue
            info = {
                "name": elem.name, "file": node.name,
                "path": str(node.path), "lineno": elem.lineno,
                "doc": elem.doc or "", "decorators": elem.decorators,
                "bases": self._extract_bases(elem.sig),
                "methods": [], "attributes": [],
                "is_dead": elem.is_dead_code,
            }
            for child in elem.children:
                if child.kind == "function":
                    info["methods"].append({
                        "name": child.name, "sig": child.sig,
                        "lineno": child.lineno,
                        "complexity": (child.complexity.cyclomatic
                                       if child.complexity else 1),
                        "is_private":    child.name.startswith("_"),
                        "is_property":   "@property"     in (child.decorators or []),
                        "is_static":     "@staticmethod" in (child.decorators or []),
                        "is_classmethod":"@classmethod"  in (child.decorators or []),
                    })
                elif child.kind == "variable":
                    info["attributes"].append({
                        "name": child.name,
                        "type": self._extract_type(child.sig),
                    })
            out.append(info)

    @staticmethod
    def _extract_bases(sig: str) -> List[str]:
        if "(" not in sig:
            return []
        try:
            inside = sig[sig.index("(") + 1 : sig.rindex(")")]
            return [b.strip() for b in inside.split(",") if b.strip()]
        except ValueError:
            return []

    @staticmethod
    def _extract_type(sig: str) -> str:
        return sig.split(":", 1)[1].strip() if ":" in sig else ""

    # ==================================================================
    # MERMAID: DEPENDENCIES  (project structure + imports)
    # ==================================================================
    def _build_dependency_mermaid(self, root: TreeNode) -> str:
        """
        Produce a Mermaid `flowchart LR` that mirrors project structure:
          - Each directory becomes a subgraph
          - Each .py file becomes a node showing class/fn counts
          - Local imports become edges
          - Files get classes: pkg / file / hot / dead
        """
        lines: List[str] = [
            "flowchart LR",
            "    %% Project architecture (auto-generated)",
            "    classDef pkg  fill:#eef2ff,stroke:#6366f1,stroke-width:1px,color:#1e293b;",
            "    classDef file fill:#ffffff,stroke:#94a3b8,color:#1e293b;",
            "    classDef hot  fill:#fff7ed,stroke:#f59e0b,color:#7c2d12;",
            "    classDef dead fill:#fef2f2,stroke:#ef4444,color:#7f1d1d;",
        ]

        # Index nodes by stem so we can wire local imports
        file_index: Dict[str, str] = {}   # stem -> mermaid id
        edges: List[str] = []

        def render_node(node: TreeNode, depth: int) -> None:
            indent = "    " * (depth + 1)
            if node.is_dir:
                sub_id = _mid(str(node.path))
                lines.append(f'{indent}subgraph {sub_id}["📁 {_esc_label(node.name)}/"]')
                lines.append(f"{indent}    direction TB")
                for child in node.children:
                    render_node(child, depth + 1)
                lines.append(f"{indent}end")
                lines.append(f"{indent}class {sub_id} pkg;")
            else:
                if node.path.suffix != ".py":
                    return
                file_id = _mid(str(node.path))
                file_index[node.path.stem] = file_id

                # Count direct elements
                cls = sum(1 for e in node.elements if e.kind == "class")
                fns = sum(1 for e in node.elements if e.kind == "function")
                meta_parts = []
                if cls: meta_parts.append(f"{cls} cls")
                if fns: meta_parts.append(f"{fns} fn")
                meta = f"<br/>{' · '.join(meta_parts)}" if meta_parts else ""

                label = f"📄 {_esc_label(node.name)}{meta}"
                lines.append(f'{indent}{file_id}["{label}"]')

                # Classification
                has_dead    = bool(node.dead_code)
                has_complex = any(
                    e.complexity and e.complexity.cyclomatic > 10
                    for e in node.elements
                )
                cls_kind = "dead" if has_dead else ("hot" if has_complex else "file")
                lines.append(f"{indent}class {file_id} {cls_kind};")

                # Collect local import edges (resolved later, after full pass)
                for dep in node.dependencies:
                    if dep.is_local:
                        target_stem = dep.module.split(".")[-1]
                        edges.append(f"__PENDING__::{file_id}::{target_stem}")

        render_node(root, 0)

        # Resolve pending edges now that file_index is populated
        seen_edges: Set[str] = set()
        for pending in edges:
            _, src, target_stem = pending.split("::")
            tgt = file_index.get(target_stem)
            if tgt and tgt != src:
                key = f"{src}->{tgt}"
                if key not in seen_edges:
                    lines.append(f"    {src} --> {tgt}")
                    seen_edges.add(key)

        return "\n".join(lines)

    # ==================================================================
    # MERMAID: CALL FLOW
    # ==================================================================
    def _build_callflow_mermaid(self, root: TreeNode) -> str:
        """
        Produce a Mermaid `flowchart TD` of function/method calls.
          - One subgraph per file
          - Each function/method is a node
          - Calls become edges (only resolved-internal ones drawn)
        """
        lines: List[str] = [
            "flowchart TD",
            "    %% Call graph (auto-generated)",
            "    classDef fn      fill:#ecfeff,stroke:#0891b2,color:#0e7490;",
            "    classDef method  fill:#f5f3ff,stroke:#8b5cf6,color:#5b21b6;",
            "    classDef hot     fill:#fff7ed,stroke:#f59e0b,color:#7c2d12;",
            "    classDef dead    fill:#fef2f2,stroke:#ef4444,color:#7f1d1d;",
            "    classDef module  fill:#f1f5f9,stroke:#475569,color:#0f172a,font-style:italic;",
        ]

        # name -> mermaid id (for resolving call targets by callee name)
        symbol_index: Dict[str, List[str]] = defaultdict(list)
        nodes_emitted: Set[str] = set()
        edges: List[tuple] = []  # (caller_id, callee_name, receiver)

        def emit_function(elem: CodeElement, file_stem: str,
                          parent_class: str | None, indent: str) -> str:
            kind = "method" if parent_class else "fn"
            qualified = (f"{file_stem}.{parent_class}.{elem.name}"
                         if parent_class else f"{file_stem}.{elem.name}")
            nid = _mid(qualified)
            if nid in nodes_emitted:
                return nid
            nodes_emitted.add(nid)

            cc = elem.complexity.cyclomatic if elem.complexity else 1
            badge = f" <br/>CC={cc}" if cc > 1 else ""
            display = (f"{parent_class}.{elem.name}()" if parent_class
                       else f"{elem.name}()")
            label = f"{display}{badge}"
            lines.append(f'{indent}{nid}["{_esc_label(label)}"]')

            if elem.is_dead_code:
                style = "dead"
            elif cc > 10:
                style = "hot"
            else:
                style = kind
            lines.append(f"{indent}class {nid} {style};")

            symbol_index[elem.name].append(nid)
            if parent_class:
                symbol_index[f"{parent_class}.{elem.name}"].append(nid)

            for call in elem.calls:
                edges.append((nid, call.callee, call.receiver))
            return nid

        def walk(node: TreeNode, depth: int) -> None:
            if node.is_dir:
                if not any(self._has_callable(c) for c in node.children):
                    return
                sub_id = _mid("flow", str(node.path))
                indent = "    " * (depth + 1)
                lines.append(f'{indent}subgraph {sub_id}["📁 {_esc_label(node.name)}/"]')
                lines.append(f"{indent}    direction TB")
                for child in node.children:
                    walk(child, depth + 1)
                lines.append(f"{indent}end")
                return

            if node.path.suffix != ".py":
                return
            if not self._has_callable(node):
                return

            file_stem = node.path.stem
            sub_id = _mid("flow_file", str(node.path))
            indent = "    " * (depth + 1)
            inner  = indent + "    "
            lines.append(f'{indent}subgraph {sub_id}["📄 {_esc_label(node.name)}"]')
            lines.append(f"{indent}    direction TB")

            for elem in node.elements:
                if elem.kind == "function":
                    emit_function(elem, file_stem, None, inner)
                elif elem.kind == "class":
                    for child in elem.children:
                        if child.kind == "function":
                            emit_function(child, file_stem, elem.name, inner)

            lines.append(f"{indent}end")

        walk(root, 0)

        # Resolve call edges to internal symbols only (skip stdlib/builtins)
        seen: Set[str] = set()
        for src, callee, receiver in edges:
            candidates = []
            if receiver:
                candidates.extend(symbol_index.get(f"{receiver}.{callee}", []))
            candidates.extend(symbol_index.get(callee, []))
            for tgt in candidates:
                if tgt == src:
                    continue
                key = f"{src}->{tgt}"
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"    {src} --> {tgt}")
                break  # only the first plausible match

        if len(lines) <= 6:
            lines.append('    empty["No internal function calls detected"]')
            lines.append("    class empty module;")
        return "\n".join(lines)

    def _has_callable(self, node: TreeNode) -> bool:
        if node.is_dir:
            return any(self._has_callable(c) for c in node.children)
        if node.path.suffix != ".py":
            return False
        for elem in node.elements:
            if elem.kind == "function":
                return True
            if elem.kind == "class":
                if any(c.kind == "function" for c in elem.children):
                    return True
        return False

    # ==================================================================
    # TREE / DEAD CODE / MARKERS (mostly unchanged)
    # ==================================================================
    def _build_tree_json(self, node: TreeNode) -> Dict:
        result = {
            "name": node.name, "path": str(node.path),
            "is_dir": node.is_dir, "error": node.error,
        }
        if node.is_dir:
            result["children"] = [self._build_tree_json(c) for c in node.children]
        else:
            result["elements"] = [
                self._element_to_dict(e)
                for e in self._visible_elements(node.elements)
            ]
            result["dead_code"] = [
                {"name": d.name, "kind": d.kind, "lineno": d.lineno, "reason": d.reason}
                for d in node.dead_code
            ]
            result["markers"] = [
                {"kind": m.kind, "text": m.text, "lineno": m.lineno}
                for m in node.comment_markers
            ]
            result["dependencies"] = {
                "stdlib":      [d.module for d in node.dependencies if d.is_stdlib],
                "local":       [d.module for d in node.dependencies if d.is_local],
                "third_party": [d.module for d in node.dependencies if d.is_third_party],
            }
        return result

    def _element_to_dict(self, elem: CodeElement) -> Dict:
        out = {
            "kind": elem.kind, "name": elem.name, "sig": elem.sig,
            "doc": elem.doc, "lineno": elem.lineno,
            "decorators": elem.decorators, "is_dead": elem.is_dead_code,
        }
        if elem.complexity:
            out["complexity"] = {
                "cyclomatic": elem.complexity.cyclomatic,
                "cognitive":  elem.complexity.cognitive,
                "risk":       elem.complexity.risk_level,
            }
        if elem.type_hints:
            out["types"] = {"params": elem.type_hints.params,
                            "return": elem.type_hints.return_type}
        if elem.exceptions:
            out["exceptions"] = {
                "raises":  [e.name for e in elem.exceptions if e.context == "raise"],
                "catches": [e.name for e in elem.exceptions if e.context == "except"],
            }
        if elem.calls:
            out["calls"] = [
                f"{c.receiver}.{c.callee}" if c.receiver else c.callee
                for c in elem.calls[:20]
            ]
        if elem.children:
            out["children"] = [
                self._element_to_dict(c)
                for c in self._visible_elements(elem.children)
            ]
        return out

    def _collect_dead_code(self, root: TreeNode) -> List[Dict]:
        out: List[Dict] = []
        self._find_dead_code(root, out)
        return out

    def _find_dead_code(self, node: TreeNode, out: List[Dict]) -> None:
        if node.is_dir:
            for c in node.children:
                self._find_dead_code(c, out)
            return
        for item in node.dead_code:
            out.append({
                "name": item.name, "kind": item.kind, "file": node.name,
                "lineno": item.lineno, "reason": item.reason,
                "confidence": item.confidence,
            })

    def _collect_markers(self, root: TreeNode) -> List[Dict]:
        out: List[Dict] = []
        self._find_markers(root, out)
        return out

    def _find_markers(self, node: TreeNode, out: List[Dict]) -> None:
        if node.is_dir:
            for c in node.children:
                self._find_markers(c, out)
            return
        for m in node.comment_markers:
            out.append({
                "kind": m.kind, "text": m.text, "file": node.name,
                "lineno": m.lineno, "author": m.author,
            })

    # ==================================================================
    # HTML GENERATION
    # ==================================================================
    def _generate_html(self, *, title, stats, classes_data, tree_data,
                       dead_code_data, markers_data,
                       deps_mermaid, flow_mermaid) -> str:
        return f'''<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Code Architecture — {html.escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>{self._get_styles()}</style>
</head>
<body>

<header class="header">
  <div class="header-content">
    <h1>🏗️ {html.escape(title)}</h1>
    <div class="header-controls">
      <button class="theme-toggle" onclick="toggleTheme()">🌓 Theme</button>
      <button class="btn-export" onclick="exportData()">📥 Export JSON</button>
    </div>
  </div>
</header>

<main class="container">
  <div class="stats-bar">
    <div class="stat-card"><div class="stat-value">{stats['files']}</div><div class="stat-label">📄 Files</div></div>
    <div class="stat-card"><div class="stat-value">{stats['classes']}</div><div class="stat-label">🧩 Classes</div></div>
    <div class="stat-card"><div class="stat-value">{stats['functions']}</div><div class="stat-label">⚙️ Functions</div></div>
    <div class="stat-card"><div class="stat-value">{stats['dead_code']}</div><div class="stat-label">💀 Dead Code</div></div>
    <div class="stat-card"><div class="stat-value">{stats['complexity_high']}</div><div class="stat-label">⚠️ High Complexity</div></div>
    <div class="stat-card"><div class="stat-value">{stats['todos']}</div><div class="stat-label">📌 TODOs</div></div>
  </div>

  <div class="tabs-container">
    <div class="tabs">
      <button class="tab-btn active" data-tab="tree">📁 Code Tree</button>
      <button class="tab-btn" data-tab="classes">🧩 Classes</button>
      <button class="tab-btn" data-tab="dependencies">📦 Architecture</button>
      <button class="tab-btn" data-tab="flow">🔄 Call Graph</button>
      <button class="tab-btn" data-tab="deadcode">💀 Dead Code</button>
      <button class="tab-btn" data-tab="markers">📌 Markers</button>
    </div>
  </div>

  <div class="tab-content">

    <!-- ===== Code Tree ===== -->
    <div id="tree" class="tab-panel active">
      <div class="panel-header">
        <h2>📁 Project Structure</h2>
        <div class="tree-controls">
          <input type="text" id="tree-search" placeholder="🔎 Search..." class="search-input">
          <div class="filter-group">
            <label><input type="checkbox" id="filter-functions" checked> Functions</label>
            <label><input type="checkbox" id="filter-classes" checked> Classes</label>
            <label><input type="checkbox" id="filter-variables"> Variables</label>
            <label><input type="checkbox" id="filter-imports"> Imports</label>
            <label><input type="checkbox" id="filter-dead" checked> Dead Code</label>
          </div>
          <button class="btn-sm" onclick="expandAll()">➕ Expand</button>
          <button class="btn-sm" onclick="collapseAll()">➖ Collapse</button>
        </div>
      </div>
      <div id="tree-view" class="tree-view"></div>
    </div>

    <!-- ===== Classes ===== -->
    <div id="classes" class="tab-panel">
      <div class="panel-header">
        <h2>🧩 Classes Overview</h2>
      </div>
      <div id="class-list" class="item-list"></div>
    </div>

    <!-- ===== Dependencies / Architecture (Mermaid) ===== -->
    <div id="dependencies" class="tab-panel">
      <div class="panel-header">
        <h2>📦 Project Architecture</h2>
        <div class="graph-controls">
          <button class="btn-sm" onclick="copyMermaid('deps')">📋 Copy Mermaid</button>
          <button class="btn-sm" onclick="downloadMermaid('deps')">⬇️ Download .mmd</button>
          <button class="btn-sm" onclick="toggleSource('deps')">👁 View Source</button>
        </div>
      </div>
      <p class="hint">
        Hierarchical project layout — directories as subgraphs, files annotated with class/function counts.
        <span class="chip chip-file">file</span>
        <span class="chip chip-hot">high complexity</span>
        <span class="chip chip-dead">contains dead code</span>
      </p>
      <div class="mermaid-wrap">
        <div class="mermaid" id="mermaid-deps">{html.escape(deps_mermaid)}</div>
      </div>
      <pre id="source-deps" class="mermaid-source" hidden></pre>
    </div>

    <!-- ===== Call Flow (Mermaid) ===== -->
    <div id="flow" class="tab-panel">
      <div class="panel-header">
        <h2>🔄 Call Graph</h2>
        <div class="graph-controls">
          <button class="btn-sm" onclick="copyMermaid('flow')">📋 Copy Mermaid</button>
          <button class="btn-sm" onclick="downloadMermaid('flow')">⬇️ Download .mmd</button>
          <button class="btn-sm" onclick="toggleSource('flow')">👁 View Source</button>
        </div>
      </div>
      <p class="hint">
        Functions and methods grouped per file. Edges show internal calls.
        <span class="chip chip-fn">function</span>
        <span class="chip chip-method">method</span>
        <span class="chip chip-hot">CC&gt;10</span>
        <span class="chip chip-dead">unused</span>
      </p>
      <div class="mermaid-wrap">
        <div class="mermaid" id="mermaid-flow">{html.escape(flow_mermaid)}</div>
      </div>
      <pre id="source-flow" class="mermaid-source" hidden></pre>
    </div>

    <!-- ===== Dead Code ===== -->
    <div id="deadcode" class="tab-panel">
      <div class="panel-header">
        <h2>💀 Dead Code</h2>
        <div class="filter-group">
          <label><input type="checkbox" id="dead-high" checked> High</label>
          <label><input type="checkbox" id="dead-medium" checked> Medium</label>
          <label><input type="checkbox" id="dead-low"> Low</label>
        </div>
      </div>
      <div id="dead-code-list" class="item-list"></div>
    </div>

    <!-- ===== Markers ===== -->
    <div id="markers" class="tab-panel">
      <div class="panel-header">
        <h2>📌 Code Markers</h2>
      </div>
      <div id="markers-list" class="item-list"></div>
    </div>

  </div>
</main>

<script>
const TREE_DATA      = {json.dumps(tree_data, ensure_ascii=False)};
const CLASSES_DATA   = {json.dumps(classes_data, ensure_ascii=False)};
const DEAD_CODE_DATA = {json.dumps(dead_code_data, ensure_ascii=False)};
const MARKERS_DATA   = {json.dumps(markers_data, ensure_ascii=False)};
const MERMAID_SRC = {{
  deps: {json.dumps(deps_mermaid, ensure_ascii=False)},
  flow: {json.dumps(flow_mermaid, ensure_ascii=False)}
}};

{self._get_javascript()}
</script>
</body>
</html>'''

    # ==================================================================
    # STYLES
    # ==================================================================
    def _get_styles(self) -> str:
        return '''
:root {
  --primary:#6366f1; --primary-dark:#4f46e5; --secondary:#8b5cf6;
  --success:#10b981; --warning:#f59e0b; --danger:#ef4444;
  --bg-main:#f8fafc; --bg-panel:#fff; --bg-code:#f1f5f9;
  --border:#e2e8f0; --text:#1e293b; --text-muted:#64748b;
  --shadow:0 4px 6px -1px rgb(0 0 0 / .1); --radius:12px; --radius-sm:8px;
}
[data-theme="dark"] {
  --primary:#818cf8; --bg-main:#0f172a; --bg-panel:#1e293b;
  --bg-code:#334155; --border:#334155; --text:#f1f5f9; --text-muted:#94a3b8;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Inter',system-ui,sans-serif; background:var(--bg-main); color:var(--text); line-height:1.6; }
.header { background:linear-gradient(135deg,var(--primary),var(--secondary)); padding:1.5rem 2rem; color:#fff;
          position:sticky; top:0; z-index:100; box-shadow:var(--shadow); }
.header-content { max-width:1800px; margin:0 auto; display:flex; justify-content:space-between; align-items:center; }
.header h1 { font-size:1.5rem; }
.header-controls { display:flex; gap:.5rem; }
.theme-toggle,.btn-export { background:rgba(255,255,255,.2); border:0; padding:.5rem 1rem;
                            border-radius:var(--radius-sm); color:#fff; cursor:pointer; }
.theme-toggle:hover,.btn-export:hover { background:rgba(255,255,255,.3); }
.container { max-width:1800px; margin:0 auto; padding:1.5rem; }
.stats-bar { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:1rem; margin-bottom:1.5rem; }
.stat-card { background:var(--bg-panel); border-radius:var(--radius); padding:1rem; text-align:center;
             border:1px solid var(--border); transition:transform .2s,box-shadow .2s; }
.stat-card:hover { transform:translateY(-2px); box-shadow:var(--shadow); }
.stat-value { font-size:1.75rem; font-weight:700; color:var(--primary); }
.stat-label { font-size:.8rem; color:var(--text-muted); }
.tabs-container { background:var(--bg-panel); border-radius:var(--radius) var(--radius) 0 0;
                  border:1px solid var(--border); border-bottom:0; overflow-x:auto; }
.tabs { display:flex; }
.tab-btn { padding:1rem 1.5rem; border:0; background:transparent; cursor:pointer; font-weight:500;
           color:var(--text-muted); border-bottom:3px solid transparent; transition:all .2s; white-space:nowrap; }
.tab-btn:hover { color:var(--primary); }
.tab-btn.active { color:var(--primary); border-bottom-color:var(--primary); background:var(--bg-code); }
.tab-content { background:var(--bg-panel); border:1px solid var(--border); border-top:0;
               border-radius:0 0 var(--radius) var(--radius); min-height:600px; }
.tab-panel { display:none; padding:1.5rem; }
.tab-panel.active { display:block; }
.panel-header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem;
                margin-bottom:1rem; padding-bottom:1rem; border-bottom:1px solid var(--border); }
.panel-header h2 { font-size:1.25rem; }
.hint { color:var(--text-muted); font-size:.85rem; margin-bottom:1rem; display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; }
.chip { display:inline-block; padding:.1rem .5rem; border-radius:10px; font-size:.7rem; font-weight:600; }
.chip-file   { background:#fff; border:1px solid #94a3b8; color:#1e293b; }
.chip-hot    { background:#fff7ed; border:1px solid #f59e0b; color:#7c2d12; }
.chip-dead   { background:#fef2f2; border:1px solid #ef4444; color:#7f1d1d; }
.chip-fn     { background:#ecfeff; border:1px solid #0891b2; color:#0e7490; }
.chip-method { background:#f5f3ff; border:1px solid #8b5cf6; color:#5b21b6; }
.tree-controls,.graph-controls { display:flex; align-items:center; gap:1rem; flex-wrap:wrap; }
.search-input { padding:.5rem 1rem; border:1px solid var(--border); border-radius:var(--radius-sm);
                background:var(--bg-code); color:var(--text); width:200px; }
.filter-group { display:flex; gap:1rem; flex-wrap:wrap; }
.filter-group label { display:flex; align-items:center; gap:.25rem; font-size:.875rem; color:var(--text-muted); cursor:pointer; }
.btn-sm { padding:.4rem .8rem; border:1px solid var(--border); border-radius:var(--radius-sm);
          background:var(--bg-code); color:var(--text); cursor:pointer; font-size:.8rem; transition:all .2s; }
.btn-sm:hover { background:var(--primary); color:#fff; border-color:var(--primary); }
.tree-view { font-family:'Fira Code',monospace; font-size:.9rem; max-height:70vh; overflow:auto; }
.tree-node { margin-left:1.5rem; }
.tree-item { display:flex; align-items:center; padding:.3rem .5rem; border-radius:var(--radius-sm);
             cursor:pointer; transition:background .2s; }
.tree-item:hover { background:var(--bg-code); }
.tree-toggle { width:20px; text-align:center; color:var(--text-muted); user-select:none; }
.tree-icon { margin-right:.5rem; }
.tree-name { flex:1; }
.tree-badge { font-size:.7rem; padding:.1rem .4rem; border-radius:10px; margin-left:.5rem; }
.badge-dead { background:var(--danger); color:#fff; }
.badge-complex { background:var(--warning); color:#fff; }
.tree-children { display:none; }
.tree-children.expanded { display:block; }
.tree-element { margin-left:2rem; padding:.2rem .5rem; border-left:2px solid var(--border); }
.element-function { border-color:var(--success); }
.element-class { border-color:var(--secondary); }
.element-variable { border-color:var(--warning); }
.element-import { border-color:var(--text-muted); }
.element-sig { font-family:'Fira Code',monospace; font-size:.85rem; }
.element-meta { font-size:.75rem; color:var(--text-muted); margin-top:.2rem; }
.mermaid-wrap { border:1px solid var(--border); border-radius:var(--radius); background:var(--bg-code);
                padding:1rem; overflow:auto; max-height:75vh; }
.mermaid { background:transparent; text-align:center; }
.mermaid-source { background:#0f172a; color:#e2e8f0; padding:1rem; border-radius:var(--radius);
                  font-family:'Fira Code',monospace; font-size:.8rem; overflow:auto; max-height:60vh;
                  margin-top:1rem; white-space:pre-wrap; }
.item-list { display:flex; flex-direction:column; gap:.5rem; max-height:60vh; overflow:auto; }
.list-item { display:flex; align-items:center; padding:.75rem 1rem; background:var(--bg-code);
             border-radius:var(--radius-sm); border-left:4px solid var(--border); transition:all .2s; }
.list-item:hover { transform:translateX(4px); box-shadow:var(--shadow); }
.list-item.high { border-color:var(--danger); }
.list-item.medium { border-color:var(--warning); }
.list-item.low { border-color:var(--text-muted); }
.list-item.todo { border-color:var(--primary); }
.list-item.fixme { border-color:var(--danger); }
.list-item.hack { border-color:var(--warning); }
.list-item.note { border-color:var(--success); }
.item-icon { font-size:1.25rem; margin-right:.75rem; }
.item-content { flex:1; }
.item-title { font-weight:600; }
.item-meta { font-size:.8rem; color:var(--text-muted); }
'''

    # ==================================================================
    # JAVASCRIPT
    # ==================================================================
    def _get_javascript(self) -> str:
        return '''
// ---- Mermaid bootstrap ----
mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
  securityLevel: 'loose'
});

function renderMermaidIn(id) {
  const el = document.getElementById(id);
  if (!el || el.dataset.rendered === '1') return;
  const src = el.textContent;
  el.removeAttribute('data-processed');
  mermaid.render(id + '_svg', src).then(({svg}) => {
    el.innerHTML = svg;
    el.dataset.rendered = '1';
  }).catch(err => {
    el.innerHTML = '<pre style="color:var(--danger)">Mermaid error: ' + err.message + '</pre>';
  });
}

// ---- Theme ----
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  // Re-render mermaid with new theme
  mermaid.initialize({ startOnLoad:false, theme: next === 'dark' ? 'dark' : 'default',
                       flowchart:{useMaxWidth:true,htmlLabels:true,curve:'basis'},
                       securityLevel:'loose' });
  ['mermaid-deps','mermaid-flow'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.textContent = MERMAID_SRC[id.split('-')[1]]; el.removeAttribute('data-rendered'); }
  });
  if (document.querySelector('.tab-btn.active').dataset.tab === 'dependencies') renderMermaidIn('mermaid-deps');
  if (document.querySelector('.tab-btn.active').dataset.tab === 'flow')         renderMermaidIn('mermaid-flow');
}

// ---- Tab switching ----
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'dependencies') renderMermaidIn('mermaid-deps');
    if (btn.dataset.tab === 'flow')         renderMermaidIn('mermaid-flow');
  });
});

// ---- Mermaid actions ----
function copyMermaid(which) {
  navigator.clipboard.writeText(MERMAID_SRC[which]).then(() => {
    alert('Mermaid source copied. Paste into a Markdown ```mermaid block.');
  });
}
function downloadMermaid(which) {
  const blob = new Blob([MERMAID_SRC[which]], { type:'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = which + '.mmd';
  a.click();
}
function toggleSource(which) {
  const el = document.getElementById('source-' + which);
  el.textContent = MERMAID_SRC[which];
  el.hidden = !el.hidden;
}

// ---- Tree view ----
function renderTree() {
  const c = document.getElementById('tree-view');
  c.innerHTML = '';
  c.appendChild(createTreeNode(TREE_DATA, true));
  applyTreeFilters();
}
function createTreeNode(node, expanded) {
  const div = document.createElement('div'); div.className = 'tree-node';
  const item = document.createElement('div'); item.className = 'tree-item';
  if (node.is_dir) {
    const toggle = document.createElement('span'); toggle.className='tree-toggle';
    toggle.textContent = expanded ? '▼' : '▶'; item.appendChild(toggle);
    const icon = document.createElement('span'); icon.className='tree-icon'; icon.textContent='📁'; item.appendChild(icon);
    const name = document.createElement('span'); name.className='tree-name'; name.textContent=node.name; item.appendChild(name);
    const children = document.createElement('div');
    children.className = 'tree-children' + (expanded ? ' expanded' : '');
    (node.children||[]).forEach(c => children.appendChild(createTreeNode(c,false)));
    item.addEventListener('click', e => { e.stopPropagation();
      const ex = children.classList.toggle('expanded'); toggle.textContent = ex?'▼':'▶'; });
    div.appendChild(item); div.appendChild(children);
  } else {
    const icon = document.createElement('span'); icon.className='tree-icon';
    icon.textContent = node.name.endsWith('.py') ? '🐍' : '📄'; item.appendChild(icon);
    const name = document.createElement('span'); name.className='tree-name'; name.textContent=node.name; item.appendChild(name);
    if (node.dead_code && node.dead_code.length) {
      const b = document.createElement('span'); b.className='tree-badge badge-dead';
      b.textContent = '💀 ' + node.dead_code.length; item.appendChild(b);
    }
    div.appendChild(item);
    if (node.elements && node.elements.length) {
      const els = document.createElement('div'); els.className='tree-children expanded';
      node.elements.forEach(e => els.appendChild(createElementNode(e)));
      div.appendChild(els);
    }
  }
  return div;
}
function createElementNode(elem) {
  const div = document.createElement('div');
  div.className = 'tree-element element-' + elem.kind;
  div.dataset.kind = elem.kind;
  div.dataset.dead = elem.is_dead ? 'true' : 'false';
  const sig = document.createElement('div'); sig.className='element-sig';
  const icon = elem.kind==='function'?'⚙️':elem.kind==='class'?'🧩':elem.kind==='variable'?'📦':'📥';
  sig.textContent = icon + ' ' + elem.sig;
  if (elem.is_dead) sig.innerHTML += ' <span class="tree-badge badge-dead">💀</span>';
  if (elem.complexity && elem.complexity.cyclomatic > 10)
    sig.innerHTML += ' <span class="tree-badge badge-complex">CC:'+elem.complexity.cyclomatic+'</span>';
  div.appendChild(sig);
  const meta = document.createElement('div'); meta.className='element-meta';
  const parts = [];
  if (elem.lineno) parts.push('L'+elem.lineno);
  if (elem.types && elem.types.return) parts.push('→ '+elem.types.return);
  if (elem.calls && elem.calls.length) parts.push('calls:'+elem.calls.length);
  meta.textContent = parts.join(' | '); div.appendChild(meta);
  if (elem.children && elem.children.length) {
    const ch = document.createElement('div'); ch.className='tree-children expanded';
    ch.style.marginLeft = '1rem';
    elem.children.forEach(c => ch.appendChild(createElementNode(c)));
    div.appendChild(ch);
  }
  return div;
}
function applyTreeFilters() {
  const f = id => document.getElementById(id).checked;
  const s = document.getElementById('tree-search').value.toLowerCase();
  document.querySelectorAll('.tree-element').forEach(el => {
    const k = el.dataset.kind; const dead = el.dataset.dead === 'true';
    let vis = true;
    if (k==='function' && !f('filter-functions')) vis=false;
    if (k==='class'    && !f('filter-classes'))   vis=false;
    if (k==='variable' && !f('filter-variables')) vis=false;
    if (k==='import'   && !f('filter-imports'))   vis=false;
    if (dead && !f('filter-dead')) vis=false;
    if (s && !el.textContent.toLowerCase().includes(s)) vis=false;
    el.style.display = vis ? 'block' : 'none';
  });
}
function expandAll()   { document.querySelectorAll('.tree-children').forEach(e=>e.classList.add('expanded'));
                         document.querySelectorAll('.tree-toggle').forEach(e=>e.textContent='▼'); }
function collapseAll() { document.querySelectorAll('.tree-children').forEach(e=>e.classList.remove('expanded'));
                         document.querySelectorAll('.tree-toggle').forEach(e=>e.textContent='▶'); }
['filter-functions','filter-classes','filter-variables','filter-imports','filter-dead'].forEach(id =>
  document.getElementById(id).addEventListener('change', applyTreeFilters));
document.getElementById('tree-search').addEventListener('input', applyTreeFilters);

// ---- Classes list ----
function renderClasses() {
  const c = document.getElementById('class-list'); c.innerHTML = '';
  CLASSES_DATA.forEach(cls => {
    const item = document.createElement('div'); item.className='list-item';
    if (cls.is_dead) item.classList.add('high');
    item.innerHTML = `
      <span class="item-icon">🧩</span>
      <div class="item-content">
        <div class="item-title">${cls.name}${cls.bases.length ? ' <small>extends '+cls.bases.join(', ')+'</small>' : ''}</div>
        <div class="item-meta">${cls.file} (L${cls.lineno}) · ${cls.methods.length} methods · ${cls.attributes.length} attrs</div>
        ${cls.doc ? '<div class="item-meta"><em>'+cls.doc+'</em></div>' : ''}
      </div>`;
    c.appendChild(item);
  });
  if (!CLASSES_DATA.length) c.innerHTML = '<p style="color:var(--text-muted)">No classes found.</p>';
}

// ---- Dead code ----
function renderDeadCode() {
  const f = id => document.getElementById(id).checked;
  const c = document.getElementById('dead-code-list'); c.innerHTML = '';
  const kinds = { function:'⚙️', class:'🧩', variable:'📦', import:'📥' };
  DEAD_CODE_DATA
    .filter(d => (d.confidence==='high'&&f('dead-high')) ||
                 (d.confidence==='medium'&&f('dead-medium')) ||
                 (d.confidence==='low'&&f('dead-low')))
    .forEach(d => {
      const item = document.createElement('div');
      item.className = 'list-item ' + d.confidence;
      item.innerHTML = `
        <span class="item-icon">${kinds[d.kind] || '💀'}</span>
        <div class="item-content">
          <div class="item-title">${d.name} <small>(${d.kind})</small></div>
          <div class="item-meta">${d.file} L${d.lineno} · ${d.reason} · confidence: ${d.confidence}</div>
        </div>`;
      c.appendChild(item);
    });
  if (!c.children.length) c.innerHTML = '<p style="color:var(--text-muted)">No dead code at selected confidence levels.</p>';
}
['dead-high','dead-medium','dead-low'].forEach(id =>
  document.getElementById(id).addEventListener('change', renderDeadCode));

// ---- Markers ----
function renderMarkers() {
  const c = document.getElementById('markers-list'); c.innerHTML = '';
  const icons = { TODO:'📌', FIXME:'🔧', HACK:'⚡', NOTE:'📝', XXX:'❌', BUG:'🐛', OPTIMIZE:'🚀' };
  MARKERS_DATA.forEach(m => {
    const item = document.createElement('div');
    item.className = 'list-item ' + m.kind.toLowerCase();
    item.innerHTML = `
      <span class="item-icon">${icons[m.kind] || '📋'}</span>
      <div class="item-content">
        <div class="item-title">${m.kind}${m.author ? ' ('+m.author+')' : ''}: ${m.text}</div>
        <div class="item-meta">${m.file} L${m.lineno}</div>
      </div>`;
    c.appendChild(item);
  });
  if (!MARKERS_DATA.length) c.innerHTML = '<p style="color:var(--text-muted)">No markers found.</p>';
}

// ---- Export ----
function exportData() {
  const data = { tree: TREE_DATA, classes: CLASSES_DATA, dead_code: DEAD_CODE_DATA,
                 markers: MARKERS_DATA, mermaid: MERMAID_SRC };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type:'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'code-analysis.json'; a.click();
}

// ---- Init ----
renderTree();
renderClasses();
renderDeadCode();
renderMarkers();
// First-paint: dependencies tab not active, but pre-render is fast — only render on click.
'''
