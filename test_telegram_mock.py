"""Unit tests for TelegramClient using mocks — no live credentials needed."""

from unittest.mock import MagicMock, patch

import pytest

from checker.telegram_client import TelegramClient, TELEGRAM_MAX_LENGTH


@pytest.fixture
def client():
    return TelegramClient("fake_token", "123456")


def _ok_response(json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_data
    resp.ok = True
    return resp


def _error_response(status: int = 400, description: str = "Bad Request") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"ok": False, "description": description}
    resp.ok = False
    return resp


class TestValidate:
    def test_valid_token(self, client):
        with patch.object(client.session, "get", return_value=_ok_response({
            "ok": True,
            "result": {"username": "testbot", "id": 999},
        })):
            assert client.validate() is True

    def test_invalid_token(self, client):
        with patch.object(client.session, "get", return_value=_ok_response({
            "ok": False,
            "description": "Unauthorized",
        })):
            assert client.validate() is False

    def test_network_error(self, client):
        with patch.object(client.session, "get", side_effect=ConnectionError("no network")):
            assert client.validate() is False


class TestSend:
    def test_send_short_message(self, client):
        with patch.object(client.session, "post", return_value=_ok_response({
            "ok": True,
            "result": {"message_id": 1},
        })) as mock_post:
            result = client.send("<b>Hello</b>")
            assert result is True
            mock_post.assert_called_once()
            call_json = mock_post.call_args.kwargs.get("json", {})
            assert call_json["text"] == "<b>Hello</b>"
            assert call_json["parse_mode"] == "HTML"

    def test_send_long_message_splits(self, client):
        long_msg = "A" * (TELEGRAM_MAX_LENGTH + 100)
        responses = [
            _ok_response({"ok": True, "result": {"message_id": i}}) for i in range(5)
        ]
        with patch.object(client.session, "post", side_effect=responses) as mock_post:
            result = client.send(long_msg)
            assert result is True
            assert mock_post.call_count >= 2

    def test_send_returns_false_on_api_error(self, client):
        # Exhaust all retries
        with patch.object(client.session, "post", return_value=_error_response()):
            with patch("time.sleep"):
                result = client.send("msg")
        assert result is False

    def test_send_with_topic_id(self, client):
        client.topic_id = 42
        with patch.object(client.session, "post", return_value=_ok_response({
            "ok": True, "result": {"message_id": 1},
        })) as mock_post:
            client.send("test")
            payload = mock_post.call_args.kwargs.get("json", {})
            assert payload.get("message_thread_id") == 42
