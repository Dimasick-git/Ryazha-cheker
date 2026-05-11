#!/usr/bin/env python3
"""Unit checks for Telegram message formatting."""

from main import MessageBuilder, build_github_file_url, html_code_block


def test_html_code_block_uses_telegram_pre_tag():
    block = html_code_block("docs: add <changelog>")

    assert block == "<pre>docs: add &lt;changelog&gt;</pre>"
    assert "```" not in block


def test_file_url_prefers_github_blob_url():
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


def test_file_url_falls_back_to_commit_sha_not_main_branch():
    url = build_github_file_url(
        "https://github.com/owner/repo/commit/abc123",
        "folder/file name.md",
        {},
    )

    assert url == "https://github.com/owner/repo/blob/abc123/folder/file%20name.md"
    assert "/blob/main/" not in url


def test_message_builder_has_no_markdown_fences_and_keeps_links():
    message, markup = MessageBuilder.build(
        "owner",
        [
            {
                "name": "repo",
                "description": "No description",
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
    assert "<h3>" not in message and "<hr>" not in message
    assert markup is not None
    assert "inline_keyboard" in markup
    assert markup["inline_keyboard"][0][0]["text"] == "📂 Open repo"


if __name__ == "__main__":
    test_html_code_block_uses_telegram_pre_tag()
    test_file_url_prefers_github_blob_url()
    test_file_url_falls_back_to_commit_sha_not_main_branch()
    test_message_builder_has_no_markdown_fences_and_keeps_links()
    print("message formatting tests passed")
