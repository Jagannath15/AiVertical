import os
import tempfile

from scripts.call_graph import build_graph, callers_of


def _write(d, relpath, content):
    path = os.path.join(d, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_callers_of_resolves_simple_cross_file_call():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "def helper():\n    return 1\n")
        _write(d, "b.py", "from a import helper\n\ndef main():\n    return helper()\n")
        graph = build_graph(d)
    assert callers_of(graph, "a.helper") == ["b.main"]


def test_callers_of_returns_empty_for_unknown_qualname():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "def f():\n    pass\n")
        graph = build_graph(d)
    assert callers_of(graph, "nonexistent.func") == []


def test_build_graph_records_locations():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "def f():\n    pass\n")
        graph = build_graph(d)
    assert graph["locations"]["a.f"] == "a.py:1"


def test_build_graph_resolves_method_call_by_last_component():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "class Foo:\n    def run(self):\n        pass\n")
        _write(d, "b.py", "def main(foo):\n    foo.run()\n")
        graph = build_graph(d)
    assert "b.main" in callers_of(graph, "a.Foo.run")


def test_build_graph_skips_file_with_syntax_error():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "broken.py", "def f(:\n    pass\n")
        _write(d, "a.py", "def f():\n    pass\n")
        graph = build_graph(d)  # must not raise
    assert "a.f" in graph["locations"]
    assert "broken.f" not in graph["locations"]
