from unittest.mock import patch, MagicMock
import json
from scripts.github_api import get_pr_for_branch, post_pr_comment, set_commit_status


def _mock_response(data, status=200):
    m = MagicMock()
    m.read.return_value = json.dumps(data).encode()
    m.status = status
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_get_pr_for_branch_returns_number():
    with patch('urllib.request.urlopen', return_value=_mock_response([{'number': 42}])):
        assert get_pr_for_branch('org/repo', 'feature/x', 'tok') == 42


def test_get_pr_for_branch_returns_none_when_empty():
    with patch('urllib.request.urlopen', return_value=_mock_response([])):
        assert get_pr_for_branch('org/repo', 'feature/x', 'tok') is None


def test_post_pr_comment_calls_correct_url():
    with patch('urllib.request.urlopen') as mock_open:
        mock_open.return_value = _mock_response({})
        post_pr_comment('org/repo', 42, 'hello', 'tok')
        url = mock_open.call_args[0][0].full_url
        assert '/repos/org/repo/issues/42/comments' in url


def test_set_commit_status_sends_correct_state():
    with patch('urllib.request.urlopen') as mock_open:
        mock_open.return_value = _mock_response({})
        set_commit_status('org/repo', 'abc123', 'success', 'All good', 'jenkins/pr-check', 'tok')
        body = json.loads(mock_open.call_args[0][0].data)
        assert body['state'] == 'success'
        assert body['context'] == 'jenkins/pr-check'
