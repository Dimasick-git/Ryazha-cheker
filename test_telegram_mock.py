"""Unit tests for TelegramClient using mocks — no live credentials needed."""

import asyncio
import json

import httpx
import pytest

from checker.telegram_client import TelegramClient, TELEGRAM_MAX_LENGTH


@pytest.fixture
def client():
    return TelegramClient("fake_token", "123456")


def _ok_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=json_data)


def _error_response(status: int = 400, description: str = "Bad Request") -> httpx.Response:
    return httpx.Response(status, json={"ok": False, "description": description})


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch):
    """Route every httpx.AsyncClient created by TelegramClient through a MockTransport
    so tests can control responses without touching the network."""
    handler_box = {}

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler_box["handler"])
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("checker.telegram_client.httpx.AsyncClient", _PatchedAsyncClient)
    return handler_box


class TestValidate:
    def test_valid_token(self, client, _patch_async_client):
        def handler(request):
            return _ok_response({"ok": True, "result": {"username": "testbot", "id": 999}})

        _patch_async_client["handler"] = handler
        assert asyncio.run(client.validate_async()) is True

    def test_invalid_token(self, client, _patch_async_client):
        def handler(request):
            return _ok_response({"ok": False, "description": "Unauthorized"})

        _patch_async_client["handler"] = handler
        assert asyncio.run(client.validate_async()) is False

    def test_network_error(self, client, _patch_async_client):
        def handler(request):
            raise httpx.ConnectError("no network")

        _patch_async_client["handler"] = handler
        assert asyncio.run(client.validate_async()) is False


class TestSend:
    def test_send_short_message(self, client, _patch_async_client):
        calls = []

        def handler(request):
            calls.append(request)
            return _ok_response({"ok": True, "result": {"message_id": 1}})

        _patch_async_client["handler"] = handler
        result = asyncio.run(client.send_async("<b>Hello</b>"))
        assert result is True
        assert len(calls) == 1
        body = json.loads(calls[0].content)
        assert body["text"] == "<b>Hello</b>"
        assert body["parse_mode"] == "HTML"

    def test_send_long_message_splits(self, client, _patch_async_client):
        long_msg = "A" * (TELEGRAM_MAX_LENGTH + 100)
        calls = []

        def handler(request):
            calls.append(request)
            return _ok_response({"ok": True, "result": {"message_id": len(calls)}})

        _patch_async_client["handler"] = handler
        result = asyncio.run(client.send_async(long_msg))
        assert result is True
        assert len(calls) >= 2

    def test_send_returns_false_on_api_error(self, client, _patch_async_client, monkeypatch):
        def handler(request):
            return _error_response()

        _patch_async_client["handler"] = handler

        async def no_sleep(*_args, **_kwargs):
            return None

        monkeypatch.setattr("checker.telegram_client.asyncio.sleep", no_sleep)
        result = asyncio.run(client.send_async("msg"))
        assert result is False

    def test_send_with_topic_id(self, client, _patch_async_client):
        client.topic_id = 42
        calls = []

        def handler(request):
            calls.append(request)
            return _ok_response({"ok": True, "result": {"message_id": 1}})

        _patch_async_client["handler"] = handler
        asyncio.run(client.send_async("test"))
        body = json.loads(calls[0].content)
        assert body.get("message_thread_id") == 42
