#!/usr/bin/env python3
"""Тесты для проверки форматирования сообщений Telegram и логики памяти."""

from checker.formatter import MessageBuilder, build_github_file_url, html_code_block


def тест_блок_кода_использует_тег_pre():
    блок = html_code_block("docs: add <changelog>")

    assert блок == "<pre>docs: add &lt;changelog&gt;</pre>"
    assert "```" not in блок


def тест_ссылка_на_файл_предпочитает_blob_url():
    file_info = {
        "blob_url": "https://github.com/owner/repo/blob/abc123/docs/changelog.md",
        "raw_url": "https://raw.githubusercontent.com/owner/repo/abc123/docs/changelog.md",
    }

    assert (
        build_github_file_url(
            "https://github.com/owner/repo/commit/abc123",
            "docs/changelog.md",
            file_info,
        )
        == file_info["blob_url"]
    )


def тест_ссылка_на_файл_использует_sha_коммита_а_не_ветку_main():
    url = build_github_file_url(
        "https://github.com/owner/repo/commit/abc123",
        "folder/file name.md",
        {},
    )

    assert url == "https://github.com/owner/repo/blob/abc123/folder/file%20name.md"
    assert "/blob/main/" not in url


def тест_построитель_сообщений_не_использует_markdown_и_сохраняет_ссылки():
    message, markup = MessageBuilder.build(
        "owner",
        [
            {
                "name": "repo",
                "description": "Описание",
                "recent_commits": [
                    {
                        "sha": "abc1234",
                        "message": "docs: add changelog",
                        "author": "hexkyz",
                        "date": "2026-04-07T21:53:00Z",
                        "html_url": "https://github.com/owner/repo/commit/abc1234",
                        "files": [
                            {
                                "filename": "docs/changelog.md",
                                "changes": 3,
                                "additions": 3,
                                "deletions": 0,
                                "blob_url": "https://github.com/owner/repo/blob/abc1234/docs/changelog.md",
                            }
                        ],
                    }
                ],
                "releases": [],
                "open_prs": [],
            }
        ],
    )

    assert "```" not in message
    assert "<code>" in message
    assert "https://github.com/owner/repo/blob/abc1234/docs/changelog.md" in message
    assert "GITHUB MONITOR" in message
    assert markup is not None
    assert "inline_keyboard" in markup
    assert "open: repo" in markup["inline_keyboard"][0][0]["text"]


def тест_ссылка_на_файл_без_blob_url_и_raw_url_строится_из_sha():
    """Если blob_url и raw_url отсутствуют, URL собирается из частей commit_url."""
    url = build_github_file_url(
        "https://github.com/owner/repo/commit/deadbeef",
        "src/main.cpp",
        {},
    )
    assert "deadbeef" in url
    assert "src/main.cpp" in url or "src%2Fmain.cpp" in url


def тест_построитель_сообщений_коммит_без_файлов():
    """Коммит с пустым списком файлов не должен вызывать исключений."""
    message, markup = MessageBuilder.build(
        "owner",
        [
            {
                "name": "repo",
                "description": None,
                "recent_commits": [
                    {
                        "sha": "aaa1111",
                        "message": "chore: empty commit",
                        "author": "bot",
                        "date": "2026-01-01T00:00:00Z",
                        "html_url": "https://github.com/owner/repo/commit/aaa1111",
                        "files": [],
                    }
                ],
                "releases": [],
                "open_prs": [],
            }
        ],
    )
    assert "aaa1111" in message
    assert "```" not in message


def тест_построитель_сообщений_экранирует_html_в_коммите():
    """HTML-спецсимволы в тексте коммита должны быть экранированы."""
    message, _ = MessageBuilder.build(
        "owner",
        [
            {
                "name": "repo",
                "description": None,
                "recent_commits": [
                    {
                        "sha": "bbb2222",
                        "message": 'fix: <script>alert("xss")</script>',
                        "author": "attacker",
                        "date": "2026-01-01T00:00:00Z",
                        "html_url": "https://github.com/owner/repo/commit/bbb2222",
                        "files": [],
                    }
                ],
                "releases": [],
                "open_prs": [],
            }
        ],
    )
    assert "<script>" not in message
    assert "&lt;script&gt;" in message or "script" not in message


def тест_telegram_client_разбивает_длинное_сообщение():
    """TelegramClient._split должен разбивать сообщения длиннее 4096 символов."""
    from checker.telegram_client import TelegramClient
    client = TelegramClient("dummy_token", "dummy_chat")
    long_text = "A" * 5000
    parts = client._split(long_text)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= 4096


if __name__ == "__main__":
    тест_блок_кода_использует_тег_pre()
    тест_ссылка_на_файл_предпочитает_blob_url()
    тест_ссылка_на_файл_использует_sha_коммита_а_не_ветку_main()
    тест_ссылка_на_файл_без_blob_url_и_raw_url_строится_из_sha()
    тест_построитель_сообщений_не_использует_markdown_и_сохраняет_ссылки()
    тест_построитель_сообщений_коммит_без_файлов()
    тест_построитель_сообщений_экранирует_html_в_коммите()
    тест_telegram_client_разбивает_длинное_сообщение()
    print("Тесты форматирования сообщений пройдены успешно")
