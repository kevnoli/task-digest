# Task Digest

Task Digest is a small, self-hosted Python service that reads incomplete tasks from
[Vikunja](https://vikunja.io/), classifies them in a configured timezone, formats a
Telegram-compatible HTML digest, and sends it through the Telegram Bot API. It can run once from
cron or stay up as a morning/evening scheduler. An optional OpenAI-compatible provider can add a
short, task-grounded focus recommendation; the deterministic digest never depends on it.

There is no inbound HTTP server. A local SQLite database stores only non-secret runtime
preferences such as digest times; task data and credentials are never stored in it.

## Architecture

```text
Vikunja REST API
      │  async, token-authenticated, paginated
      ▼
classification → project grouping → deterministic HTML formatter
                                          │
                       optional focus intro from an LLM
                                          │
                                          ▼
                      safe 4096-character splitting → Telegram Bot API

APScheduler runs timezone-aware morning/evening jobs.
A local JSON heartbeat supports Docker health checks without external API calls.
SQLite stores validated, non-secret digest preferences on a persistent local volume.
```

Integration code lives under `task_digest/vikunja`, `task_digest/telegram`, and
`task_digest/llm`. Classification and formatting are independent of HTTP in
`task_digest/digest.py`; `task_digest/service.py` coordinates one digest; and
`task_digest/scheduler.py` owns scheduling, process locking, graceful shutdown, and heartbeat
state.

## Requirements

- Python 3.12 or newer for local execution
- A reachable Vikunja instance and API token
- A Telegram bot token and destination chat ID, unless using dry-run mode
- Docker Engine with Docker Compose v2 for the container deployment
- Optional: an OpenAI-compatible chat-completions endpoint

The Proxmox deployment created for this project runs Vikunja 2.3.0 in CT 122 and is
published through OpenResty at `https://vikunja.kevnoli.myaddr.io`.

## Configure Vikunja

1. Open your Vikunja instance and create or sign in to the account that should supply tasks.
2. Open **Settings → API Tokens**.
3. Create a token with **Tasks → Read All** and **Projects → Read All**. No write or administrative
   permissions are required. The token can only see resources the account itself can access.
4. Put the token in `VIKUNJA_API_TOKEN`. Use the instance root URL, not an individual API endpoint,
   for `VIKUNJA_BASE_URL` and `VIKUNJA_WEB_URL`.

Vikunja documents Bearer API tokens and its generated API specification in its
[API documentation](https://vikunja.io/docs/api-documentation/). Task Digest uses
`GET /api/v1/tasks` and `GET /api/v1/projects`, follows pagination, and excludes completed tasks
again locally even though the server request also filters on `done = false`.

## Configure Telegram

1. Message [@BotFather](https://t.me/BotFather), run `/newbot`, and follow the prompts. Store the
   returned token as `TELEGRAM_BOT_TOKEN`. Telegram's official
   [bot tutorial](https://core.telegram.org/bots/tutorial) treats this token like a password.
2. Open the new bot and send it a message such as `/start`. A bot cannot initiate a private
   conversation before the user contacts it.
3. Obtain the chat ID from `getUpdates`. To keep the token out of shell history:

   ```bash
   read -rsp 'Telegram bot token: ' BOT_TOKEN
   echo
   curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates"
   unset BOT_TOKEN
   ```

   Find `result[].message.chat.id` in the JSON and use it as `TELEGRAM_CHAT_ID`. Group and
   supergroup IDs are usually negative. Add the bot to the group and send it a message first if the
   destination is a group.

Telegram limits `sendMessage` text to 4096 characters after entity parsing. Task Digest renders
safe HTML and splits on self-contained line boundaries before sending.

## Configuration

Copy the example and fill in secrets:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Default | Description |
| --- | --- | --- |
| `VIKUNJA_BASE_URL` | required | Vikunja root URL, or a URL ending in `/api/v1`. |
| `VIKUNJA_API_TOKEN` | required | Bearer API token; never logged. |
| `VIKUNJA_WEB_URL` | required | Browser-facing root used to build task links. |
| `VIKUNJA_TIMEOUT_SECONDS` | `15` | Vikunja HTTP timeout. |
| `TELEGRAM_BOT_TOKEN` | required unless dry-run | Telegram bot token; never logged. |
| `TELEGRAM_CHAT_ID` | required unless dry-run | User, group, supergroup, or channel chat ID. |
| `TELEGRAM_TIMEOUT_SECONDS` | `15` | Telegram HTTP timeout. |
| `TELEGRAM_MAX_RETRIES` | `3` | Retries for transport errors, 429, and temporary 5xx responses. |
| `TIMEZONE` | `America/Bahia` | IANA timezone for classification and schedules. |
| `MORNING_DIGEST_ENABLED` | `true` | Enable the morning scheduler job. |
| `MORNING_DIGEST_TIME` | `08:00` | Local 24-hour `HH:MM` schedule. |
| `EVENING_DIGEST_ENABLED` | `true` | Enable the evening scheduler job. |
| `EVENING_DIGEST_TIME` | `17:00` | Local 24-hour `HH:MM` schedule. |
| `UPCOMING_DAYS` | `3` | Future days included by the morning digest, 0–30. |
| `LLM_ENABLED` | `false` | Enable the optional focus recommendation. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API root. |
| `LLM_API_KEY` | empty | Required only when the LLM is enabled. |
| `LLM_MODEL` | empty | Chat-completions model name. |
| `LLM_TIMEOUT_SECONDS` | `20` | LLM HTTP timeout. |
| `LLM_INCLUDE_DESCRIPTIONS` | `false` | Include descriptions in LLM input. Digest descriptions are unaffected. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `DRY_RUN` | `false` | Fetch real tasks and print, without Telegram. |
| `HEARTBEAT_PATH` | `/tmp/task-digest/heartbeat` | Service-mode heartbeat file. |
| `HEARTBEAT_INTERVAL_SECONDS` | `20` | Heartbeat update interval. |
| `HEARTBEAT_MAX_AGE_SECONDS` | `90` | Maximum age accepted by `healthcheck`. |
| `SETTINGS_DATABASE_PATH` | `./data/task-digest.sqlite3` | SQLite file for non-secret runtime preferences. Compose overrides this to `/data/task-digest.sqlite3`. |

Boolean values accept only `true/false`, `1/0`, `yes/no`, or `on/off`, case-insensitively. URLs,
timezone names, times, numeric ranges, conditional credentials, and secrets are validated at
startup with actionable errors. Configuration logging contains only a secret-free summary.

### Persisted digest settings

The following preferences can be overridden in SQLite without editing `.env`:

- `timezone`
- `morning_digest_enabled`
- `morning_digest_time`
- `evening_digest_enabled`
- `evening_digest_time`
- `upcoming_days`

Secrets, URLs, chat identity, timeouts, logging, and LLM credentials remain environment-only. View
the effective values and their source:

```bash
python -m task_digest settings show
```

Set or reset a value:

```bash
python -m task_digest settings set morning_digest_time 07:30
python -m task_digest settings set upcoming_days 5
python -m task_digest settings reset morning_digest_time
```

Values are validated through the same Pydantic configuration model before being committed. Restart
service mode after a change so APScheduler rebuilds its cron triggers:

```bash
docker compose restart task-digest
```

## Local development

Using [uv](https://docs.astral.sh/uv/) is the quickest setup:

```bash
uv sync --extra dev
cp .env.example .env
uv run python -m task_digest run --dry-run
```

With standard `venv` and pip:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m task_digest run --dry-run
```

Quality checks do not make real network requests:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy task_digest
```

## Execution modes

Run the morning behavior once and propagate any Vikunja or Telegram failure through a non-zero exit
code:

```bash
python -m task_digest run
```

Run evening behavior once:

```bash
python -m task_digest run --kind evening
```

Force dry-run regardless of `DRY_RUN` in the environment:

```bash
python -m task_digest run --dry-run
```

Dry-run still fetches the real Vikunja API and prints the final HTML digest to stdout, but it never
constructs or calls a Telegram client.

Run the continuous APScheduler service:

```bash
python -m task_digest serve
```

Cron triggers use the configured timezone, coalesce missed runs, allow one instance per job, and do
not replay a previous scheduled time after restart. A non-blocking runtime lock also prevents two
service processes from sending duplicates. The process handles SIGINT/SIGTERM and updates its
heartbeat during graceful shutdown.

## Docker Compose deployment

The image uses Python 3.12 slim, installs the package in a builder stage, and runs as UID/GID 10001.
The Compose service has no published ports, drops capabilities, uses a read-only root filesystem,
provides a writable in-memory `/tmp` for heartbeat state, and keeps SQLite on the local named volume
`task-digest-data`.

```bash
cp .env.example .env
# edit .env, then:
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f task-digest
```

Check health manually inside the container:

```bash
docker compose exec task-digest python -m task_digest healthcheck
```

The health check validates that the scheduler started, its PID still exists, and its heartbeat is
recent. It deliberately makes no Vikunja, Telegram, or LLM request.

## Using cron instead of service mode

Set both schedule flags to `false` if desired; one-shot mode does not require a scheduler job. Then
add a local-time cron entry. For a host already configured for `America/Bahia`:

```cron
0 8 * * * cd /opt/task-digest && /opt/task-digest/.venv/bin/python -m task_digest run >> /var/log/task-digest.log 2>&1
```

Or invoke the Compose image as an ephemeral one-shot container:

```cron
0 8 * * * cd /opt/task-digest && docker compose run --rm task-digest python -m task_digest run
```

Use only one scheduling mechanism to avoid duplicate messages.

## Optional LLM focus recommendation

The service works fully with `LLM_ENABLED=false`, which is the default. When enabled, it sends a
structured list containing task ID, exact title, project, due date/status, priority, and labels.
Descriptions are excluded unless `LLM_INCLUDE_DESCRIPTIONS=true`.

The prompt requires a JSON response with referenced task IDs and exact supplied titles. The client
rejects unknown IDs, unknown quoted task names, missing exact titles, more than two sentences, and
overlong output. Any connection, HTTP, parsing, or grounding failure is logged by exception type and
the deterministic digest is still sent.

For a local OpenAI-compatible server, use its `/v1` root and advertised chat model. Example:

```env
LLM_ENABLED=true
LLM_BASE_URL=http://192.168.15.60:11434/v1
LLM_API_KEY=local-only-value
LLM_MODEL=qwen3:8b
LLM_INCLUDE_DESCRIPTIONS=false
```

Some local servers ignore the key but the application still requires a non-empty value when the LLM
is enabled, keeping provider configuration explicit.

## Example digest

```text
Morning task digest
Sunday, 19 July 2026

Suggested focus: "Fix Oracle migration" first because it is overdue and high priority.

Overdue

Work
• Fix Oracle migration [WORK-18] — 2 days overdue [HIGH P4] #database

Today's tasks

Personal
• Buy groceries [HOME-42] — due today at 18:00 #errands

Upcoming

Personal
• Replace garage camera [HOME-43] — tomorrow at 09:30 #hardware
```

Project names and task data are HTML-escaped. Empty sections are omitted, completed or undated tasks
are excluded, and no message is sent when the entire digest is empty.

## Troubleshooting

- **Configuration error at startup:** read the named variable in the Pydantic validation output.
  Check that `.env` exists in the Compose project directory and times use `HH:MM`.
- **Vikunja 401/403:** recreate the token, grant read access for tasks/projects, and confirm the API
  account can open the affected projects. `VIKUNJA_BASE_URL` should normally be the instance root.
- **No tasks appear:** only incomplete tasks due today, overdue, or inside the morning upcoming window
  are included. Undated and completed tasks are intentionally omitted.
- **Telegram 400 / chat not found:** send the bot a private message first, add it to the group, and
  re-check the signed chat ID from `getUpdates`.
- **Telegram rejects formatting:** titles, labels, projects, descriptions, and LLM text are escaped;
  capture a dry-run and open an issue with the rendered input if this still occurs.
- **Container is unhealthy:** run the manual health-check command and inspect
  `docker compose logs task-digest`. A missing heartbeat usually means configuration prevented
  service startup; a stale heartbeat indicates a blocked or stopped process.
- **Digest sent twice:** make sure cron and service mode are not both active and only one Compose
  project is running. The runtime lock prevents duplicates only inside the same runtime filesystem.
- **LLM unavailable:** this is non-fatal by design. Disable it or correct the model/base URL; the
  deterministic digest continues to send.

## Security considerations

- Treat Vikunja, Telegram, and LLM keys as passwords. Keep `.env` mode `0600`, never commit it, and
  rotate a token immediately if exposed.
- Grant the Vikunja API token only the read scopes and project access it needs.
- Task titles, labels, descriptions, and due dates are sent to Telegram. Confirm that the destination
  chat is appropriate for that data.
- LLM descriptions are opt-in because they may contain more sensitive detail. Prefer a local provider
  when task metadata must stay on the home network.
- Logs are token-redacted and avoid response bodies, but operational metadata and exception types are
  still logged.
- Back up the separate Vikunja server's database and files. The `task-digest-data` volume contains
  only non-secret preferences and can also be backed up if desired.
