"""Stdlib ast-based best-effort Python caller graph.

Advisory LLM-prompt context, not a precise static analyzer: call resolution
is last-component name matching, deliberately over-inclusive (a call to
`.run()` matches every known function/method named `run`, not just the
right one).
"""
import ast
import os


def _relpath(repo_root, file_path):
    return os.path.relpath(file_path, repo_root).replace(os.sep, "/")


def _module_name(repo_root, file_path):
    rel = _relpath(repo_root, file_path)
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")


def _iter_python_files(repo_root):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _collect_functions(tree, module_name, relpath):
    """Returns {qualname: lineno} for every def in this module."""
    locations = {}

    def visit(node, scope_parts):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join([module_name] + scope_parts + [child.name])
                locations[qualname] = child.lineno
                visit(child, scope_parts + [child.name])
            elif isinstance(child, ast.ClassDef):
                visit(child, scope_parts + [child.name])
            else:
                visit(child, scope_parts)

    visit(tree, [])
    return locations


def _collect_calls(tree, module_name):
    """Returns {enclosing_qualname: [called_name, ...]} - called_name is the
    raw last-component text (e.g. "run" or "helper"), resolved against the
    whole-repo qualname index by the caller."""
    calls = {}

    def visit(node, scope_parts):
        enclosing = ".".join([module_name] + scope_parts) if scope_parts else None
        for child in ast.walk(node) if False else ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, scope_parts + [child.name])
            elif isinstance(child, ast.ClassDef):
                visit(child, scope_parts + [child.name])
            elif isinstance(child, ast.Call) and enclosing:
                name = None
                if isinstance(child.func, ast.Name):
                    name = child.func.id
                elif isinstance(child.func, ast.Attribute):
                    name = child.func.attr
                if name:
                    calls.setdefault(enclosing, []).append(name)
                visit(child, scope_parts)
            else:
                visit(child, scope_parts)

    visit(tree, [])
    return calls


def build_graph(repo_root: str) -> dict:
    locations = {}
    all_calls = {}

    for file_path in _iter_python_files(repo_root):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        relpath = _relpath(repo_root, file_path)
        module_name = _module_name(repo_root, file_path)

        for qualname, lineno in _collect_functions(tree, module_name, relpath).items():
            locations[qualname] = f"{relpath}:{lineno}"

        for enclosing, names in _collect_calls(tree, module_name).items():
            all_calls.setdefault(enclosing, []).extend(names)

    last_component_index = {}
    for qualname in locations:
        last_component_index.setdefault(qualname.rsplit(".", 1)[-1], []).append(qualname)

    callers = {}
    for enclosing, called_names in all_calls.items():
        for name in called_names:
            for target in last_component_index.get(name, []):
                callers.setdefault(target, [])
                if enclosing not in callers[target]:
                    callers[target].append(enclosing)

    return {"callers": callers, "locations": locations}


def callers_of(graph: dict, qualname: str) -> list:
    return sorted(graph.get("callers", {}).get(qualname, []))
