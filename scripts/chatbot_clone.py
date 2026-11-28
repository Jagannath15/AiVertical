"""Clone/pull a repo for the chatbot's indexer - token never in argv."""
import os
import shutil
import subprocess
import tempfile


def _is_valid_git_dir(dest_dir: str) -> bool:
    return os.path.isdir(os.path.join(dest_dir, ".git"))


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
            os.makedirs(os.path.dirname(dest_dir) or ".", exist_ok=True)
            subprocess.run(["git", "clone", url, dest_dir], env=env, check=True)
        else:
            subprocess.run(["git", "-C", dest_dir, "pull"], env=env, check=True)
    finally:
        os.remove(askpass_path)
