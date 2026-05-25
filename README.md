# Ryazha-cheker

GitHub repository monitor. Опрашивает GitHub API по cron'у, отправляет diff (commits / releases / PRs / workflow runs) в Telegram.

## Stack

- `main.py` — single-file monitor (~1100 LOC). GitHub API + Telegram Bot API через `requests`.
- `repo_states_<user>.json` — persistent state (last seen sha по каждому репо). Кешируется между запусками GitHub Actions.
- `.github/workflows/monitor.yml` — cron `*/30 * * * *`, Python 3.11, ubuntu-latest.

## Env

```
G_TOKEN=<github_pat>
G_USERNAME=<owner>
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=<chat_id>
TELEGRAM_TOPIC_ID=<thread_id>     # optional, для супергрупп
SKIP_REPOS=repo1,repo2            # optional, исключения
```

GitHub PAT: scopes `repo` (read), `read:user`. Без `repo` приватные не видны.
Telegram chat_id: `@userinfobot`. Bot token: `@BotFather`.

## Deploy

В репозитории `Settings -> Secrets and variables -> Actions` создать все env как secrets с такими же именами. Workflow стартует автоматически по cron'у каждые 30 минут + по `workflow_dispatch`.

## Run locally

```sh
pip install -r requirements.txt
cp .env.example .env       # заполнить env
python main.py             # один tick
python main.py --dry-run   # без отправки в Telegram
```

## Test

```sh
python -m pytest test_message_formatting.py
python -m pytest test_telegram.py   # требует живые TELEGRAM_* env
```

## Output format

Telegram сообщение собирается из секций:

```
GITHUB MONITOR [username]
ts: 2026-05-25 14:30

repos_changed=3 | commits_new=12
prs_total=4 | issues=7

[repo-name] python  stars=42 forks=3 issues=1
    by author @ 2026-05-25 14:12
    - path/to/file.py
    - another/file.cpp

RELEASE: v2.4.0 — short title
PR: #123 title | #145 another
```

Workflow runs показываются с тегами `[OK]`, `[FAIL]`, `[RUN]`, `[QUEUE]`, `[CANCEL]`, `[SKIP]`.

## Лицензия

MIT. См. `LICENSE`. Автор: Dimasick-git.
