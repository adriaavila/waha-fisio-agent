# CLAUDE.md

Guidance for AI assistants (Claude Code and others) working in this repository.

---

## Working Principles

These come first because they govern *how* you make every change below.

### 1. Think Before Coding
State your assumptions and surface confusion before writing code. If a request is
ambiguous, ask. If there are several reasonable approaches, name them and recommend
one rather than silently picking. This codebase mixes Spanish (user-facing strings,
comments, logs) and English (code, identifiers) — preserve that convention instead
of "fixing" it.

### 2. Simplicity First
Write the minimum code that solves the problem. Nothing speculative. This is a small,
single-process FastAPI service backed by SQLite — resist adding ORMs, message queues,
async frameworks, or abstraction layers it doesn't need. Before adding something, ask:
"Would a senior engineer call this overcomplicated?"

### 3. Surgical Changes
Make focused edits that match the surrounding style. Each module already has a clear,
consistent shape (HTTP client classes with `@property` config readers, try/except with
Spanish logging, dict/bool return values). Follow it. Only touch what your change
requires; don't refactor unrelated code or "clean up" pre-existing patterns you didn't
break.

### 4. Goal-Driven Execution
Turn the request into a verifiable outcome before you start. Define what "done and
working" looks like (e.g. "the webhook returns 200 and a confirmation message is sent"),
then work toward it. Because there is no test suite, verification is manual — see
[Verifying Changes](#verifying-changes).

---

## What This Project Is

**KineLife AI Booking Agent** — a WhatsApp conversational agent ("KineBot") for a
physiotherapy clinic. Patients message the clinic's WhatsApp number; an LLM-driven agent
checks calendar availability and books appointments on their behalf, then sends
confirmations and automated reminders. A password-protected web dashboard lets the
clinic connect WhatsApp (via QR), edit configuration, and view bookings and activity logs.

**Stack:** Python 3.11 · FastAPI · Uvicorn · SQLite · Jinja2 · Tailwind (CDN) ·
Gemini / OpenAI · WAHA (WhatsApp HTTP API) · Cal.com API v2.

---

## Architecture

```
WhatsApp user
     │  (message)
     ▼
WAHA gateway ──webhook──► POST /webhook/whatsapp ─┐
                                                  │  asyncio.create_task
                                                  ▼
                                          BookingAgent.process_message
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                          ▼                        ▼                        ▼
                  Gemini / OpenAI          tool: check_availability   tool: book_appointment
                  (function calling)              │                        │
                          │                        ▼                        ▼
                          │                  CalcomClient.get_slots   CalcomClient.create_booking
                          ▼                                                  │
                  reply text ──► WahaClient.send_message ──► WhatsApp user   ▼
                                                                      save_booking (SQLite)

Cal.com ──webhook──► POST /webhook/calcom ──► sync SQLite + WhatsApp confirm/cancel

Background thread (every 5 min) ──► scan bookings ──► send 24h / 2h reminders via WAHA

Clinic admin ──► GET /dashboard (HTTP Basic) ──► WhatsApp QR/status, settings, bookings, logs
```

The whole service runs in **one process**: the FastAPI app plus one daemon thread
running an asyncio loop for reminders. State lives entirely in a local SQLite file.

---

## File Map

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app. Webhook endpoints (`/webhook/whatsapp`, `/webhook/calcom`), dashboard routes (`/dashboard`, settings, WhatsApp start/logout), landing page, and the background reminder loop. Entry point: `uvicorn main:app`. |
| `agent.py` | `BookingAgent` — the LLM brain. Defines the two tool functions `check_availability` and `book_appointment`, runs them through Gemini (`_run_gemini`) or OpenAI (`_run_openai`), and falls back to `_mock_respond` when no API key is set. Holds the system prompt. |
| `calcom.py` | `CalcomClient` — Cal.com API **v2** wrapper: `get_available_slots`, `create_booking`, `cancel_booking`. |
| `waha_client.py` | `WahaClient` — WAHA WhatsApp HTTP API wrapper: `send_message`, session lifecycle (`start_session`, `logout_session`, `get_session_status`), and `get_qr_code`. |
| `database.py` | All SQLite access. Schema creation (`init_db`, runs on import), plus helpers for chat history, bookings, logs, and the settings key/value store. |
| `templates/index.html` | The admin dashboard (Tailwind via CDN, glassmorphism UI). |
| `templates/landing.html` | Public marketing/landing page served at `/`. |
| `requirements.txt` | Python dependencies. |
| `Dockerfile` | Container build; runs Uvicorn on port 8000. |
| `.env.example` | Template for required environment variables. |

There is no `tests/` directory, no linter config, and no CI.

---

## Configuration & the Settings Precedence Rule

This is the single most important convention to internalize.

Almost every external credential is read through `database.get_setting(key, default)`,
where the default is itself an environment-variable lookup. The effective precedence is:

```
SQLite `settings` table  >  environment variable / .env  >  hardcoded default
```

The client classes (`WahaClient`, `CalcomClient`, `BookingAgent`) expose their config as
`@property` methods that re-read this on **every access** — there is no caching. This is
deliberate: it lets the dashboard's **Settings** form (`POST /dashboard/settings`) change
WAHA URLs, API keys, the LLM keys, and the system prompt **at runtime without a restart**.

**Implications when editing:**
- To add a new configurable value, add a `@property` that calls `db.get_setting("KEY",
  os.getenv("KEY", "<default>"))`, surface it in the dashboard settings form in both
  `view_dashboard` (read) and `save_settings` (write), and add the field to
  `templates/index.html`.
- Don't read `os.getenv` directly in business logic if the value should be
  dashboard-editable — go through `get_setting` so the precedence holds.
- Note the modules use a deferred `import database as db` *inside* the property bodies to
  avoid circular imports. Keep that pattern.

### Environment variables (see `.env.example`)

| Variable | Purpose |
|----------|---------|
| `WAHA_BASE_URL` | WAHA gateway base URL (default `http://localhost:3000`). |
| `WAHA_API_KEY` | WAHA `X-Api-Key` (also accepts legacy `WHATSAPP_API_KEY`). |
| `WAHA_SESSION_NAME` | WAHA session name (default `default`). |
| `CALCOM_API_KEY` | Cal.com API key (`cal_live_...`). |
| `CALCOM_EVENT_TYPE_ID` | Numeric Cal.com event type the agent books. |
| `GEMINI_API_KEY` | Google Gemini key. If set, Gemini is used. |
| `OPENAI_API_KEY` | OpenAI key. Used only if `GEMINI_API_KEY` is empty. |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | HTTP Basic auth for `/dashboard` (defaults `admin` / `admin123` — change in production). |
| `SESSION_SECRET_KEY` | Reserved for session signing. |

**LLM selection:** Gemini takes priority over OpenAI. If **neither** key is configured,
the agent silently uses `_mock_respond` (canned Spanish demo replies) — useful for local
UI work, but note it never actually touches Cal.com.

---

## Data Model (SQLite)

Created automatically by `init_db()` when `database.py` is imported. DB file:
`database.db` (gitignored, recreated on first run).

- **`chat_history`** — `(id, phone_number, role['user'|'assistant'], content, timestamp)`.
  Per-phone conversation memory; `get_chat_history` returns the last N in chronological order.
- **`bookings`** — `(id, cal_booking_id UNIQUE, client_name, client_phone, client_email,
  start_time[ISO], status, reminder_24h_sent, reminder_2h_sent, created_at)`. Local mirror
  of Cal.com bookings, powering the dashboard and reminder scheduler.
- **`agent_logs`** — `(id, phone_number, action, details, timestamp)`. Activity feed shown
  on the dashboard; written throughout via `add_log(...)`.
- **`settings`** — `(key, value)` key/value store backing the runtime config above
  (upserted via `set_setting`).

---

## Key Flows

**Inbound WhatsApp message** (`/webhook/whatsapp`): filters to message events, ignores
`fromMe` (critical — prevents infinite reply loops), strips the `@c.us` suffix from the
chat id, and dispatches `process_and_reply` as a fire-and-forget `asyncio` task so WAHA
gets a fast `200`. The agent generates a reply and sends it back; on error a generic
Spanish apology is sent.

**Agent tool loop** (`agent.py`): the LLM is given two tools. Gemini uses *automatic*
function calling (`enable_automatic_function_calling=True`); OpenAI uses a manual
two-pass loop (first call may return `tool_calls`, results are appended, a second call
produces the final text). Both tools return human-readable **Spanish** strings, not JSON.

**Cal.com webhook** (`/webhook/calcom`): on `BOOKING_CREATED` / `BOOKING_CANCELLED`,
upserts the local `bookings` row and notifies the patient over WhatsApp.

**Reminders** (`check_and_send_reminders` in `main.py`): a daemon thread loops every
300 s, scanning confirmed bookings. Sends a 24h reminder when the appointment is 22–24h
out, and a 2h reminder when 1–2h out, marking each as sent so it fires once. Times are
handled in UTC.

**Dashboard** (`/dashboard`): HTTP Basic auth. Shows WAHA session status (with QR when
status is `SCAN_QR_CODE`), upcoming bookings, recent logs, and an editable settings form.

---

## Development

### Run locally
```bash
pip install -r requirements.txt
cp .env.example .env          # fill in keys (or leave LLM keys blank for mock mode)
uvicorn main:app --reload --port 8000
```
- Landing page: `http://localhost:8000/`
- Dashboard: `http://localhost:8000/dashboard` (Basic auth)
- WhatsApp webhook: `POST http://localhost:8000/webhook/whatsapp`
- Cal.com webhook: `POST http://localhost:8000/webhook/calcom`

A running **WAHA** instance is required for real WhatsApp send/receive (commonly Docker,
e.g. `devlikeapro/waha` on port 3000). Point WAHA's webhook at `/webhook/whatsapp` and
set its message events. For inbound webhooks during local dev, expose the port with a
tunnel (ngrok/cloudflared).

### Docker
```bash
docker build -t kinelife-agent .
docker run -p 8000:8000 --env-file .env kinelife-agent
```

### Verifying Changes
There is no automated test suite, so verify manually:
- Import-check after edits: `python -c "import main"` (this also runs `init_db()`).
- Mock-mode smoke test: leave both LLM keys blank, POST a sample WAHA payload to
  `/webhook/whatsapp`, and confirm `process_and_reply` produces a reply (watch the logs).
- For LLM/Cal.com paths, use a real key and a Cal.com sandbox event type.
- Confirm the dashboard still renders and the settings form round-trips a value through
  the `settings` table.

If you add anything testable and non-trivial, a `tests/` dir with `pytest` is a
reasonable first introduction — but keep it proportional to the project (principle #2).

---

## Conventions & Gotchas

- **Language:** Spanish for everything user-facing (messages, log actions like
  `BOOKING_CREATED`, comments) and English for code identifiers. Keep both.
- **Config access:** go through `get_setting` (see precedence rule), never bypass it for
  dashboard-editable values.
- **No caching of config:** properties re-read on each call by design — don't "optimize"
  this away.
- **`fromMe` guard:** never remove the outbound-message filter in the WhatsApp webhook.
- **Phone formatting:** WhatsApp ids are `digits@c.us`; `WahaClient._format_phone`
  normalizes this. The agent strips `@c.us` for storage/keys.
- **Times are UTC / ISO 8601** (`YYYY-MM-DDTHH:MM:SSZ`); Cal.com calls default to the
  `America/Santiago` timezone. Be careful when touching the reminder math.
- **Models:** Gemini uses `gemini-1.5-flash`; OpenAI uses `gpt-4o-mini`. Both are set as
  string literals in `agent.py`.
- **Error handling style:** HTTP clients catch exceptions and return `{}` / `False`
  rather than raising; callers check truthiness. Match this.
- **Secrets:** `.env` and `database.db` are gitignored — never commit them or hardcode
  real keys.
- **Startup side effect:** importing `database` creates the DB and tables; the FastAPI
  `startup` event launches the reminder thread.

---

## Git Workflow

- Develop on the assigned feature branch; create it locally if missing.
- Commit with clear, descriptive messages; push with `git push -u origin <branch>`.
- Do **not** open a pull request unless explicitly asked.
- Never push to `main` without explicit permission.
