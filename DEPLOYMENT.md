# Deployment

The target is the same as Build 02: **API on Render (Docker), PostgreSQL on Neon, web app on Vercel**. Nothing is
deployed yet; the steps below are the plan. This repository has only been run locally (see "Verified locally").

| Piece | Where | Notes |
|---|---|---|
| API (`backend/`) | Render web service, Docker runtime | `backend/Dockerfile`; `start.sh` runs `alembic upgrade head`, the idempotent seed, then uvicorn |
| Database | Neon (or Render Postgres) | Needs the `btree_gist` extension (the migration runs `CREATE EXTENSION IF NOT EXISTS btree_gist`; Neon and Render both allow it) |
| Web (`frontend/`) | Vercel, Vite preset | `frontend/vercel.json` rewrites every path to `index.html` for client-side routes |
| Phone line | none | This build is browser-only by choice. See "Adding a real phone number" below |
| SMS | none | Texts are written to the `sms_messages` outbox with status `simulated` |

## 1. Database (Neon)

1. Create a project and copy the **pooled** connection string.
2. Nothing else: the API's start command creates the extension, tables and the exclusion constraint.

`DATABASE_URL` accepts `postgres://` or `postgresql://`; it is rewritten to the psycopg driver
(`backend/app/config.py`). Add `?sslmode=require` if the provider does not include it.

## 2. API (Render)

1. New **Web Service** from this repository, **Root directory** `backend`, **Runtime** Docker.
2. Health check path: `/api/health` (returns 200 only when the database answers).
3. Environment variables (use Render's secret store, never the repo):

| Variable | Value |
|---|---|
| `APP_ENV` | `production` (JSON logs, `/docs` disabled) |
| `DATABASE_URL` | the Neon string |
| `AGENT_BRAIN` | `gemini` (or `rules` for a demo that needs no key) |
| `GEMINI_API_KEY` | your key (server-side only) |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` (the model the published live evaluation used) |
| `DAILY_MODEL_CALL_LIMIT` | e.g. `300`; the gateway refuses calls past this per rolling 24 h and the agent hands off |
| `FRONTEND_ORIGIN` | your Vercel URL, no trailing slash (comma-separate several) |
| `DEMO_ACCESS_CODE` | optional; recommended on a public URL so strangers cannot spend the model quota |
| `CALLS_PER_HOUR_PER_CLIENT`, `TURNS_PER_MINUTE_PER_CALL` | rate limits (defaults 30 and 20) |

Every other setting has a default in `backend/app/config.py` and is listed in `backend/.env.example`.

Run **one** instance with one uvicorn worker: the rate limiters are in memory. Each call turn already runs in a
worker thread, and the calendar's correctness never depends on the instance count (Postgres enforces it).

Render's proxy supports WebSockets, which the voice stream (`/api/voice/stream`) needs. `start.sh` passes
`--ws-ping-interval 20` so idle calls are not dropped by the proxy.

## 3. Web (Vercel)

1. New project from this repository, **Root directory** `frontend`, framework preset Vite.
2. Environment variable `VITE_API_BASE_URL` = the Render URL (for example `https://ai-phone-receptionist-api.onrender.com`).
   It is public and baked in at build time. The WebSocket URL is derived from it (`https` → `wss`).
3. Add the Vercel URL to the API's `FRONTEND_ORIGIN` and redeploy the API.

Voice needs the Web Speech API's recognition, which Chrome and Edge provide (and only on HTTPS or localhost). Other
browsers fall back to text mode automatically.

## 4. After deploying

```bash
curl https://<api>/api/health
```

Then make a call from the web app, book a slot, and check that it shows on the Calendar page and in Texts. The
Call log page shows p50/p95 latency for real calls on that deployment.

Guarding the wallet (roadmap "Ship it", step 4): the key is only in Render's secret store, `DAILY_MODEL_CALL_LIMIT`
caps calls on the server, `DEMO_ACCESS_CODE` keeps the public out, and you should also set a spend cap or keep the
key on the free tier in Google AI Studio.

## Running the container locally

```bash
docker compose --profile app up --build
```

This builds `backend/Dockerfile` and runs it against the compose database on `http://localhost:8010`, reading
`backend/.env` if it exists.

## Adding a real phone number (not built)

Milestone M4 was skipped by choice (it needs a paid telephony account). What it would take, given this code:

1. A Twilio (or Exotel) number whose voice webhook points at `POST /api/voice/incoming`, returning TwiML
   `<Connect><Stream url="wss://…/api/voice/stream?call_id=…"/></Connect>`.
2. A stream adapter that decodes Twilio's base64 μ-law 8 kHz frames, runs streaming STT and endpointing, feeds
   `Engine.handle_turn`, and sends TTS audio back, plus Twilio's `clear` message for barge-in. The engine, tools and
   calendar would not change; today the browser plays that role.
3. `transfer_to_human` would call the provider's API to move the call (warm transfer) instead of only queueing it.
4. An `SmsSender` implementation in `backend/app/services/notify.py` that calls the SMS API.

## Verified locally

- `docker compose up -d db`, `alembic upgrade head`, `alembic downgrade base`, `alembic upgrade head` and
  `alembic check` (no drift) all succeed against PostgreSQL 16.
- The API and web app run together with `uvicorn` and `npm run dev` (Vite proxies `/api` and the WebSocket).
- `docker build backend` succeeds; the container ran `start.sh` (migrations at head, seed) against the compose
  database and answered `GET /api/health` with 200.
- The Render/Neon/Vercel deployment has **not** been done, so there is no live URL.
