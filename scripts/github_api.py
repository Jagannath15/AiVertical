"""GitHub REST API v3 utility — stdlib only, no pip deps."""
import json
import sys
import urllib.request

API = "https://api.github.com"


def _headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }


def get_pr_for_branch(repo: str, branch: str, token: str):
    """Return open PR number for branch, or None."""
    owner = repo.split("/")[0]
    url = f"{API}/repos/{repo}/pulls?head={owner}:{branch}&state=open"
    req = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data[0]["number"] if data else None


def post_pr_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    """Post a comment to a PR."""
    url = f"{API}/repos/{repo}/issues/{pr_number}/comments"
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=payload, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req):
        pass


def set_commit_status(
    repo: str, sha: str, state: str, description: str, context: str, token: str
) -> None:
    """Set a GitHub commit status check."""
    url = f"{API}/repos/{repo}/statuses/{sha}"
    payload = json.dumps(
        {"state": state, "description": description, "context": context}
    ).encode()
    req = urllib.request.Request(url, data=payload, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req):
        pass


if __name__ == "__main__":
    import os

    if len(sys.argv) < 4 or sys.argv[1] != "get-pr":
        print("usage: github_api.py get-pr <repo> <branch>", file=sys.stderr)
        sys.exit(2)
    token = os.environ.get("GITHUB_TOKEN", "")
    pr = get_pr_for_branch(sys.argv[2], sys.argv[3], token)
    if pr is None:
        print("none")
        sys.exit(1)
    print(pr)
