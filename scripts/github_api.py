"""GitHub REST API v3 utility — stdlib only, no pip deps."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
TIMEOUT_SECONDS = 30


def _headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }


def get_pr_for_branch(repo: str, branch: str, token: str):
    """Return open PR number for branch, or None."""
    owner = repo.split("/")[0]
    head = urllib.parse.quote(f"{owner}:{branch}", safe="")
    url = f"{API}/repos/{repo}/pulls?head={head}&state=open"
    req = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read())
    return data[0]["number"] if data else None


def post_pr_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    """Post a comment to a PR."""
    url = f"{API}/repos/{repo}/issues/{pr_number}/comments"
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=payload, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS):
        pass


def set_commit_status(
    repo: str, sha: str, state: str, description: str, context: str, token: str
) -> None:
    """Set a GitHub commit status check."""
    url = f"{API}/repos/{repo}/statuses/{sha}"
    payload = json.dumps(
        {
            "state": state,
            "description": description,
            "context": context,
            "target_url": os.environ.get("BUILD_URL", ""),
        }
    ).encode()
    req = urllib.request.Request(url, data=payload, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS):
        pass


def _cli_get_pr(repo, branch, token):
    try:
        pr = get_pr_for_branch(repo, branch, token)
    except urllib.error.HTTPError as e:
        print(f"GitHub API error: {e.code} {e.reason}", file=sys.stderr)
        return 2
    if pr is None:
        print("none")
        return 0
    print(pr)
    return 0


def _cli_set_status(repo, sha, state, description, context, token):
    try:
        set_commit_status(repo, sha, state, description, context, token)
    except urllib.error.HTTPError as e:
        print(f"GitHub API error: {e.code} {e.reason}", file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    token = os.environ.get("GITHUB_TOKEN", "")

    if len(argv) >= 3 and argv[0] == "get-pr":
        return _cli_get_pr(argv[1], argv[2], token)
    if len(argv) >= 6 and argv[0] == "set-status":
        return _cli_set_status(argv[1], argv[2], argv[3], argv[4], argv[5], token)

    print(
        "usage: github_api.py get-pr <repo> <branch> | "
        "set-status <repo> <sha> <state> <description> <context>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
