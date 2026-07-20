# Task Digest

Task Digest is a small self-hosted Python service that reads unchecked checklist rows from
[Anchor](https://github.com/ZhFahim/anchor), groups them by note, formats a Telegram-compatible
HTML digest, and sends it through the Telegram Bot API. It can run once from cron or continuously
with morning and evening schedules. An optional OpenAI-compatible provider can add a short,
task-grounded focus recommendation; the deterministic digest never depends on it.

There is no inbound HTTP server. SQLite stores only non-secret runtime preferences such as digest
times. Notes, checklist content, and credentials are never stored by Task Digest.

## Anchor checklist behavior

Anchor stores checklists inside the Quill document of a note. Task Digest:

- includes only rows formatted as unchecked checklist items;
- excludes checked rows, ordinary note text, archived notes, and trashed notes;
- includes both owned notes and notes shared with the API-token account;
- groups rows under the Anchor note title;
- treats rows in pinned notes as high priority;
- carries the account's Anchor tags into the digest; and
- links each row back to its note in Anchor.

Anchor 0.14.0 has no task due-date or reminder field. Its checklist rows therefore appear in the
`Unfinished checklist items` section. The generic digest model retains timezone-aware date support
for a future Anchor release or another provider, but Task Digest does not infer dates from note
text or require special title syntax.

## Architecture

```text
Anchor REST API
  GET /api/notes + GET /api/tags
              │
              ▼
Quill checklist parser → source-neutral tasks → note grouping → safe HTML
                                                        │
                                   optional grounded LLM introduction
                                                        │
                                                        ▼
                                     4096-safe splitting → Telegram

APScheduler runs timezone-aware morning/evening jobs.
A local JSON heartbeat supports health checks without external calls.
SQLite stores validated non-secret schedule preferences.
```

Integration code lives under `task_digest/anchor`, `task_digest/telegram`, and
`task_digest/llm`. Classification and formatting are independent of HTTP in
`task_digest/digest.py`; `task_digest/service.py` coordinates a digest; and
`task_digest/scheduler.py` owns scheduling, locking, graceful shutdown, and heartbeat state.

## Requirements

- Python 3.12 or newer for local execution
- A reachable Anchor instance and API token
- A Telegram bot token and destination chat ID, unless using dry-run mode
- Docker Engine and Docker Compose v2 for container deployment
- Optional: an OpenAI-compatible chat-completions endpoint

## Configure Anchor

1. Sign in to the Anchor account whose checklists should be included.
2. Open the profile/settings page and generate an API token.
3. Put it in `ANCHOR_API_TOKEN` and use the instance root URL for both URL variables.

Anchor API tokens are account-wide bearer tokens, not permission-scoped tokens. They inherit the
account's access: owned notes and notes shared with that account. Keep the token in `.env`, set the
file to mode `600`, and rotate or revoke it if exposed. The client never logs the token or response
bodies from errors.

## Configure Telegram

1. Message [@BotFather](https://t.me/BotFather), run `/newbot`, and follow the prompts.
2. Open the bot and send `/start`; a bot cannot initiate a private chat first.
3. Call `getUpdates` and use `result[].message.chat.id` as `TELEGRAM_CHAT_ID`:

   ```bash
   read -rsp 'Telegram bot token: ' BOT_TOKEN
   echo
   curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates"
   unset BOT_TOKEN
   ```

Telegram limits `sendMessage` text to 4096 characters after entity parsing. Task Digest HTML-
escapes note and checklist data and splits long messages on safe, self-contained line boundaries.

## Configuration

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Default | Description |
| --- | --- | --- |
| `ANCHOR_BASE_URL` | required | Anchor root URL, or a URL ending in `/api`. |
| `ANCHOR_API_TOKEN` | required | Account bearer token; never logged. |
| `ANCHOR_WEB_URL` | required | Browser-facing root used for note links. |
| `ANCHOR_TIMEOUT_SECONDS` | `15` | Anchor HTTP timeout. |
| `TELEGRAM_BOT_TOKEN` | required unless dry-run | Telegram bot token; never logged. |
| `TELEGRAM_CHAT_ID` | required unless dry-run | Destination user/group/channel ID. |
| `TELEGRAM_TIMEOUT_SECONDS` | `15` | Telegram HTTP timeout. |
| `TELEGRAM_MAX_RETRIES` | `3` | Retries for transport errors, 429, and temporary 5xx. |
| `TIMEZONE` | `America/Bahia` | IANA timezone for schedules and date calculations. |
| `MORNING_DIGEST_ENABLED` | `true` | Enable the morning job. |
| `MORNING_DIGEST_TIME` | `08:00` | Local 24-hour `HH:MM` schedule. |
| `EVENING_DIGEST_ENABLED` | `true` | Enable the evening job. |
| `EVENING_DIGEST_TIME` | `17:00` | Local 24-hour `HH:MM` schedule. |
| `UPCOMING_DAYS` | `3` | Future-day window for sources that provide due dates. |
| `LLM_ENABLED` | `false` | Enable the optional focus introduction. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API root. |
| `LLM_API_KEY` | empty | Required only when the LLM is enabled. |
| `LLM_MODEL` | empty | Chat-completions model name. |
| `LLM_TIMEOUT_SECONDS` | `20` | LLM HTTP timeout. |
| `LLM_INCLUDE_DESCRIPTIONS` | `false` | Include descriptions in LLM input. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `DRY_RUN` | `false` | Fetch real checklists and print without Telegram. |
| `HEARTBEAT_PATH` | `/tmp/task-digest/heartbeat` | Service heartbeat file. |
| `HEARTBEAT_INTERVAL_SECONDS` | `20` | Heartbeat update interval. |
| `HEARTBEAT_MAX_AGE_SECONDS` | `90` | Maximum healthy heartbeat age. |
| `SETTINGS_DATABASE_PATH` | `./data/task-digest.sqlite3` | Non-secret SQLite settings file. |

Boolean values accept only `true/false`, `1/0`, `yes/no`, or `on/off`, case-insensitively.
URLs, timezones, times, numeric ranges, conditional credentials, and secrets are validated at
startup. Configuration logs contain only a secret-free summary.

### Persisted digest settings

These preferences can be changed in SQLite without editing `.env`:

- `timezone`
- `morning_digest_enabled`
- `morning_digest_time`
- `evening_digest_enabled`
- `evening_digest_time`
- `upcoming_days`

```bash
python -m task_digest settings show
python -m task_digest settings set morning_digest_time 07:30
python -m task_digest settings set evening_digest_enabled false
python -m task_digest settings reset morning_digest_time
```

Restart service mode after changing a schedule so APScheduler rebuilds its triggers.

## Local development

```bash
uv sync --extra dev
cp .env.example .env
uv run python -m task_digest run --dry-run

uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy task_digest
```

Tests use HTTP mock transports and never make real network requests.

## Execution modes

Run once and propagate Anchor or Telegram failures through a non-zero exit code:

```bash
python -m task_digest run
python -m task_digest run --kind evening
python -m task_digest run --dry-run
```

Dry-run fetches real Anchor data and prints the final HTML, but does not construct or call a
Telegram client.

Run the continuous scheduler:

```bash
python -m task_digest serve
```

Cron triggers use the configured timezone, coalesce missed runs, allow one instance per job, and do
not replay old scheduled times after restart. A runtime lock prevents duplicate scheduler processes.
SIGINT and SIGTERM cause a graceful shutdown.

## Docker Compose deployment

The image uses Python 3.12 slim, a non-root UID/GID 10001, no published ports, a read-only root
filesystem, dropped capabilities, an in-memory `/tmp`, and a persistent SQLite volume.

```bash
cp .env.example .env
# edit .env
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f task-digest
```

The internal health check validates the scheduler PID and recent heartbeat without calling Anchor,
Telegram, or the LLM:

```bash
docker compose exec task-digest python -m task_digest healthcheck
```

## Using cron instead of service mode

Disable both scheduler flags and invoke one-shot mode from cron. For a host using
`America/Bahia`:

```cron
0 8 * * * cd /opt/task-digest && docker compose run --rm task-digest python -m task_digest run
```

Use either cron or service mode, not both, to avoid duplicate messages.

## Optional LLM introduction

The default is `LLM_ENABLED=false`. When enabled, the provider receives only the structured task
ID, exact checklist text, note title, due status, priority, and tags. Descriptions are omitted unless
explicitly enabled. The response must reference supplied IDs and exact titles, contain at most two
sentences, and pass local grounding checks. Any LLM failure falls back to the deterministic digest.

Example local OpenAI-compatible endpoint:

```env
LLM_ENABLED=true
LLM_BASE_URL=http://192.168.15.60:11434/v1
LLM_API_KEY=local-only-value
LLM_MODEL=qwen3:8b
LLM_INCLUDE_DESCRIPTIONS=false
```

## Example digest

```text
Morning checklist digest
Monday, 20 July 2026

Unfinished checklist items

Groceries #Shopping
• Buy milk
• Eggs

House
• Replace hallway bulb
```

Empty sections are omitted, and no Telegram message is sent when no unchecked checklist rows exist.

## Troubleshooting

- **401 / invalid authentication token:** regenerate the Anchor API token and replace
  `ANCHOR_API_TOKEN`.
- **Empty digest:** ensure rows use Anchor's checklist formatting and remain unchecked; ordinary
  bullets and plain text are intentionally ignored.
- **Missing shared list:** share the note with the account that generated the API token.
- **Telegram 400:** verify the chat ID, start the bot conversation, and ensure the bot can post.
- **Health check fails after startup:** inspect `docker compose logs task-digest`; the heartbeat is
  created only after APScheduler starts.
- **Schedule appears unchanged:** restart the container after changing an SQLite schedule setting.

## Security considerations

- Never commit `.env`; it is ignored by Git.
- Use `chmod 600 .env` and restrict access to the Docker host.
- Treat Anchor and Telegram bearer tokens as passwords and rotate them after exposure.
- The app logs exception types and HTTP status codes, not response bodies or credentials.
- Keep `LLM_INCLUDE_DESCRIPTIONS=false` unless the configured provider may receive sensitive note
  content.
- Back up the SQLite volume only if you need persisted preferences; Anchor remains the source of
  truth for checklist data.
