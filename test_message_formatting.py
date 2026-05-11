#!/usr/bin/env python3
"""Тесты для проверки форматирования сообщений Telegram и логики памяти."""

from main import MessageBuilder, build_github_file_url, html_code_block


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
    assert "<pre>" in message and "</pre>" in message
    assert "https://github.com/owner/repo/blob/abc1234/docs/changelog.md" in message
    assert "Отчёт мониторинга" in message
    assert markup is not None
    assert "inline_keyboard" in markup
    assert "Открыть repo" in markup["inline_keyboard"][0][0]["text"]


if __name__ == "__main__":
    тест_блок_кода_использует_тег_pre()
    тест_ссылка_на_файл_предпочитает_blob_url()
    тест_ссылка_на_файл_использует_sha_коммита_а_не_ветку_main()
    тест_построитель_сообщений_не_использует_markdown_и_сохраняет_ссылки()
    print("Тесты форматирования сообщений пройдены успешно")
