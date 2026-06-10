#!/usr/bin/env python3
"""
Тест интеграции с Telegram API.
Пропускается автоматически если переменные окружения не заданы,
чтобы не блокировать CI.
"""

import asyncio
import os
import pytest
import httpx


@pytest.mark.skipif(
    not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'),
    reason='TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы — пропускаем интеграционный тест',
)
def test_telegram_bot_info():
    """Проверяет корректность токена через getMe (без отправки сообщений)."""
    bot_token = os.environ['TELEGRAM_BOT_TOKEN']
    url = f'https://api.telegram.org/bot{bot_token}/getMe'

    async def _run():
        async with httpx.AsyncClient(timeout=10) as client:
            return await client.get(url)

    resp = asyncio.run(_run())
    assert resp.status_code == 200, f'HTTP {resp.status_code}'
    data = resp.json()
    assert data.get('ok'), f'getMe failed: {data.get("description")}'
    assert 'username' in data['result']


@pytest.mark.skipif(
    not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'),
    reason='TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы — пропускаем интеграционный тест',
)
def test_telegram_send_message():
    """Отправляет реальное тестовое сообщение — только при наличии credentials."""
    bot_token = os.environ['TELEGRAM_BOT_TOKEN']
    chat_id = os.environ['TELEGRAM_CHAT_ID']
    url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': 'TEST github-monitor\n\nif visible, integration ok.',
    }

    async def _run():
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.post(url, json=payload)

    resp = asyncio.run(_run())
    assert resp.status_code == 200, f'HTTP {resp.status_code}: {resp.text}'
    assert resp.json().get('ok'), f'sendMessage failed: {resp.json().get("description")}'
