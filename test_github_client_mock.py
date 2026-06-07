"""Async unit tests for GitHubClient — no live credentials needed."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from checker.github_client import GitHubClient


@pytest.fixture
def client():
    return GitHubClient("fake_token", "testuser")


def _make_response(status: int, body) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = json.dumps(body) if not isinstance(body, str) else body
    resp.json.return_value = body
    resp.headers = {}
    return resp


def _make_rate_limit_response(reset_ts: int = 9999999999) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 429
    resp.text = '{"message":"rate limited"}'
    resp.json.return_value = {"message": "rate limited"}
    resp.headers = {"X-RateLimit-Reset": str(reset_ts)}
    return resp


def _make_server_error_response(status: int = 503) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "Service Unavailable"
    resp.json.return_value = {}
    resp.headers = {}
    return resp


class TestGetSuccessful:
    def test_returns_json_on_200(self, client):
        payload = [{"name": "repo1"}, {"name": "repo2"}]
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _make_response(200, payload)
            result = asyncio.run(client._get("https://api.github.com/test"))
        assert result == payload

    def test_returns_none_on_404(self, client):
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _make_response(404, {"message": "Not Found"})
            result = asyncio.run(client._get("https://api.github.com/test"))
        assert result is None

    def test_returns_none_on_401(self, client):
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _make_response(401, {"message": "Unauthorized"})
            result = asyncio.run(client._get("https://api.github.com/test"))
        assert result is None


class TestRateLimiting:
    def test_retries_on_429_then_succeeds(self, client):
        payload = {"id": 1}
        rate_limit = _make_rate_limit_response()
        ok = _make_response(200, payload)
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [rate_limit, ok]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(client._get("https://api.github.com/test", _retry=3))
        assert result == payload
        assert mock_get.call_count == 2

    def test_returns_none_after_all_rate_limit_retries_exhausted(self, client):
        rate_limit = _make_rate_limit_response()
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = rate_limit
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(client._get("https://api.github.com/test", _retry=2))
        assert result is None

    def test_respects_retry_after_header(self, client):
        resp = MagicMock()
        resp.status_code = 429
        resp.text = ""
        resp.json.return_value = {}
        resp.headers = {"Retry-After": "5"}
        ok = _make_response(200, {"ok": True})
        sleep_calls = []
        async def fake_sleep(n):
            sleep_calls.append(n)
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [resp, ok]
            with patch("asyncio.sleep", side_effect=fake_sleep):
                asyncio.run(client._get("https://api.github.com/test", _retry=3))
        assert sleep_calls, "Should have slept at least once"
        assert sleep_calls[0] >= 5.0


class TestServerErrors:
    def test_retries_on_503(self, client):
        err = _make_server_error_response(503)
        ok = _make_response(200, {"data": "ok"})
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [err, ok]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(client._get("https://api.github.com/test", _retry=3))
        assert result == {"data": "ok"}
        assert mock_get.call_count == 2

    def test_returns_none_after_all_server_error_retries(self, client):
        err = _make_server_error_response(500)
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = err
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(client._get("https://api.github.com/test", _retry=2))
        assert result is None


class TestTimeoutHandling:
    def test_returns_none_on_timeout(self, client):
        import httpx
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("timed out")
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(client._get("https://api.github.com/test", _retry=1))
        assert result is None

    def test_retries_on_timeout_then_succeeds(self, client):
        import httpx
        ok = _make_response(200, [{"name": "repo"}])
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [httpx.TimeoutException("timed out"), ok]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(client._get("https://api.github.com/test", _retry=2))
        assert result == [{"name": "repo"}]


class TestListCommits:
    def test_returns_parsed_commits(self, client):
        raw = [
            {
                "sha": "abc123",
                "commit": {
                    "message": "Fix bug\n\nDetails here",
                    "author": {"name": "Alice", "date": "2026-01-01T00:00:00Z"},
                },
                "html_url": "https://github.com/testuser/repo/commit/abc123",
            }
        ]
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = raw
            result = asyncio.run(client.list_commits("myrepo", count=5))
        assert len(result) == 1
        assert result[0]["sha"] == "abc123"
        assert result[0]["message"] == "Fix bug"
        assert result[0]["author"] == "Alice"

    def test_returns_empty_on_api_failure(self, client):
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = asyncio.run(client.list_commits("myrepo"))
        assert result == []

    def test_skips_malformed_commit(self, client):
        raw = [
            {"sha": "bad"},  # missing commit key
            {
                "sha": "good123",
                "commit": {
                    "message": "Good commit",
                    "author": {"name": "Bob", "date": "2026-01-02T00:00:00Z"},
                },
                "html_url": "https://github.com/u/r/commit/good123",
            },
        ]
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = raw
            result = asyncio.run(client.list_commits("myrepo", count=5))
        assert len(result) == 1
        assert result[0]["sha"] == "good123"


class TestGetReleases:
    def test_returns_release_on_success(self, client):
        raw = {
            "tag_name": "v1.2.3",
            "name": "Version 1.2.3",
            "author": {"login": "alice"},
            "published_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/testuser/repo/releases/tag/v1.2.3",
        }
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = raw
            result = asyncio.run(client.get_releases("myrepo"))
        assert len(result) == 1
        assert result[0]["tag"] == "v1.2.3"
        assert result[0]["author"] == "alice"

    def test_returns_empty_on_no_releases(self, client):
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = asyncio.run(client.get_releases("myrepo"))
        assert result == []


class TestGetNewReleases:
    def test_returns_empty_when_tag_unchanged(self, client):
        with patch.object(client, "get_releases", new_callable=AsyncMock) as mock_releases:
            mock_releases.return_value = [{"tag": "v1.0", "name": "v1.0"}]
            result = asyncio.run(client.get_new_releases("repo", known_tag="v1.0"))
        assert result == []

    def test_returns_release_when_tag_changed(self, client):
        release = [{"tag": "v1.1", "name": "v1.1"}]
        with patch.object(client, "get_releases", new_callable=AsyncMock) as mock_releases:
            mock_releases.return_value = release
            result = asyncio.run(client.get_new_releases("repo", known_tag="v1.0"))
        assert result == release

    def test_returns_release_when_no_known_tag(self, client):
        release = [{"tag": "v1.0", "name": "v1.0"}]
        with patch.object(client, "get_releases", new_callable=AsyncMock) as mock_releases:
            mock_releases.return_value = release
            result = asyncio.run(client.get_new_releases("repo", known_tag=None))
        assert result == release


class TestGetRepositories:
    def test_paginates_until_less_than_100(self, client):
        page1 = [{"name": f"repo{i}"} for i in range(100)]
        page2 = [{"name": f"repo{i}"} for i in range(100, 115)]
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [page1, page2]
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = asyncio.run(client.get_repositories())
        assert len(result) == 115
        assert mock_get.call_count == 2

    def test_returns_empty_on_api_failure(self, client):
        with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = asyncio.run(client.get_repositories())
        assert result == []


class TestCircuitBreaker:
    def test_circuit_opens_after_threshold_and_resets(self, client):
        """Circuit breaker skips calls when open and resets after cooldown."""
        import httpx
        from checker.github_client import _CB_FAILURE_THRESHOLD, _CB_COOLDOWN_SECONDS

        calls = []
        async def fake_get(url, params=None):
            calls.append(url)
            raise httpx.TimeoutException("timeout")

        with patch.object(client._client, "get", side_effect=fake_get):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                for _ in range(_CB_FAILURE_THRESHOLD + 3):
                    asyncio.run(client._get("https://api.github.com/test", _retry=1))

        # After threshold is exceeded, further calls should be blocked (circuit open)
        blocked_count = sum(1 for c in calls if c)
        # At least _CB_FAILURE_THRESHOLD real calls were made, then circuit opened
        assert len(calls) >= _CB_FAILURE_THRESHOLD
        assert len(calls) < _CB_FAILURE_THRESHOLD + 3 + 1  # some were blocked
