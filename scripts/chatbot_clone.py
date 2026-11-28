"""Clone/pull a repo for the chatbot's indexer - token never in argv."""
import os
import shutil
import subprocess
import tempfile


def _is_valid_git_dir(dest_dir: str) -> bool:
    return os.path.isdir(os.path.join(dest_dir, ".git"))


def _clone(url: str, dest_dir: str, env: dict) -> None:
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(os.path.dirname(dest_dir) or ".", exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", url, dest_dir], env=env, check=True)


def _pull(dest_dir: str, env: dict) -> None:
    # fetch+reset+clean rather than `git pull`: recovers cleanly from a
    # force-pushed/rebased remote and leaves no untracked leftovers, and
    # doesn't need to know the default branch name ("origin HEAD" tracks it).
    subprocess.run(["git", "-C", dest_dir, "fetch", "--depth", "1", "origin", "HEAD"], env=env, check=True)
    subprocess.run(["git", "-C", dest_dir, "reset", "--hard", "FETCH_HEAD"], env=env, check=True)
    subprocess.run(["git", "-C", dest_dir, "clean", "-fdx"], env=env, check=True)


def clone_or_pull(repo: str, dest_dir: str, token: str) -> None:
    """repo is 'org/repo'. Clones into dest_dir if missing/corrupted, else pulls."""
    url = f"https://github.com/{repo}.git"

    # Pass the token via GIT_ASKPASS rather than embedding it in the URL
    # (which would appear in argv and in git's own remote-url config).
    # A short-lived askpass script just echoes the token back to git.
    askpass_fd, askpass_path = tempfile.mkstemp(prefix="askpass_", suffix=".sh")
    try:
        with os.fdopen(askpass_fd, "w") as f:
            f.write("#!/bin/sh\necho \"$CHATBOT_GIT_TOKEN\"\n")
        os.chmod(askpass_path, 0o700)
        env = {**os.environ, "GIT_ASKPASS": askpass_path, "CHATBOT_GIT_TOKEN": token}

        if os.path.isdir(dest_dir) and not _is_valid_git_dir(dest_dir):
            shutil.rmtree(dest_dir)

        if not os.path.isdir(dest_dir) or not _is_valid_git_dir(dest_dir):
            _clone(url, dest_dir, env)
        else:
            try:
                _pull(dest_dir, env)
            except subprocess.CalledProcessError:
                # A corrupted/partial clone (killed mid-fetch, OOM, a force-pushed
                # history fetch+reset can't reconcile) self-heals by re-cloning
                # rather than failing every reindex from this point on.
                _clone(url, dest_dir, env)
    finally:
        os.remove(askpass_path)
