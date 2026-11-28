"""Per-repo CodeGraphContext wrapper.

CodeGraphContext (github.com/CodeGraphContext/CodeGraphContext) is CLI-only
(`cgc`/`codegraphcontext`, no documented Python library API) and its
documented storage model is one shared graph store per backend - no
documented per-repo DB file/path option. To get true per-repo isolation
anyway (required: a cross-repo query leak would violate this project's
access-control design), each repo's `cgc` invocation gets its own fake HOME
directory via the subprocess env - CodeGraphContext's embedded backends
(KuzuDB on Windows, FalkorDB Lite on Unix) default to storing under
~/.codegraphcontext/, so redirecting HOME per repo gives each one an
entirely separate store without depending on an undocumented CGC flag.
"""
import os
import subprocess


def _repo_path_component(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def _run_codegraphcontext(repo_path: str, graph_path: str) -> None:
    os.makedirs(graph_path, exist_ok=True)
    env = {**os.environ, "HOME": graph_path}
    subprocess.run(["cgc", "index", repo_path], env=env, check=True)


def build_repo_graph(repo_id: str, repo_path: str, graph_dir: str) -> str:
    graph_path = os.path.join(graph_dir, _repo_path_component(repo_id))
    _run_codegraphcontext(repo_path, graph_path)
    return graph_path
