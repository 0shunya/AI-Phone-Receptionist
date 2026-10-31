# Deployment

Target: **API on Render (Docker, free plan), PostgreSQL on Neon, web app on Vercel**, protected by
`DEMO_ACCESS_CODE` and a daily model-call cap. Nothing is deployed yet, so there is no live URL; the steps below are
ready to follow, and "Verified locally" at the end lists what has actually been run.

| Piece | Where | Defined by |
|---|---|---|
| API (`backend/`) | Render web service `ai-phone-receptionist-api`, Docker | `render.yaml` (Blueprint), `backend/Dockerfile`, `backend/start.sh` |
| Database | Neon | `DATABASE_URL`; schema from Alembic on every start |
| Web (`frontend/`) | Vercel, Vite preset | `frontend/vercel.json` (SPA rewrites), `VITE_API_BASE_URL` |
| Phone line | none | Browser-only by choice (see "Adding a real phone number") |
| SMS | none | Written to the `sms_messages` outbox with status `simulated` |

## What the container does on start

`backend/start.sh` (the Dockerfile's `CMD`):

1. `python -m alembic upgrade head`: creates the `btree_gist` extension, every table and the `bookings_no_overlap`
   exclusion constraint on a fresh database, and is a no-op when already at head.
2. `python -m scripts.seed`: idempotent reference data (4 services, 3 staff, weekly shifts). No demo bookings.
3. `uvicorn` on `$PORT` (Render sets it), one worker, `--proxy-headers`, `--ws-ping-interval 20` so the proxy does
   not drop a quiet call.

If the database is unreachable, step 1 fails and the deploy fails instead of serving a broken app.

## Model quota: what "15 per day" means

The free-tier key allows about 20 requests per day **per model**, and the key is shared with the other roadmap
apps. This API uses its own model, **`gemini-3-flash-preview`** (the Expense Scanner uses `gemini-3.5-flash-lite`).
It was chosen after live tests on 31 Oct 2026:

| Model | Live tool loop (find_slots, then book read-back) | Time per turn |
|---|---|---|
| `gemini-3-flash-preview` | correct | 1.2 to 1.7 s |
| `gemini-3.6-flash` | correct (1 turn tried) | 3.1 s |
| `gemini-3.1-flash-lite` | correct | 12 to 14 s, too slow for voice |

Note that the published evaluation numbers in the README were measured on `gemini-3.5-flash-lite`, not on this model.

`DAILY_MODEL_CALL_LIMIT=15` caps **model calls**, counted over a rolling 24 hours from the `model_calls` table, not
phone calls. One model call happens per caller turn that reaches the model. What does not use one: the greeting,
and turns handled by a guard rule (asking for a person, anger). A typical call uses:

| Call | Model calls |
|---|---|
| Book: ask, pick a slot, give name if needed, say yes | 3 to 5 |
| Reschedule or cancel | 3 to 5 |
| Price or hours question | 1 to 2 |
| "Can I talk to a person?" | 0 |

So the cap allows roughly **3 to 5 complete bookings per day**. When it is reached, the gateway refuses to call the
model, the agent apologises and hands the call to the Handoffs queue (`model_unavailable`), and the rest of the app
(Calendar, Call log, Texts, console bookings) keeps working. A 429 from Google, per-minute or daily, does the same.
`CALLS_PER_HOUR_PER_CLIENT=10` and `TURNS_PER_MINUTE_PER_CALL=12` stop one visitor using the whole budget.

## 1. Database (Neon)

1. Create a Neon project (any region near the Render region), database name of your choice.
2. Copy the connection string from **Connection details**. Use the **pooled** host (contains `-pooler`) and keep
   `?sslmode=require`.
3. Nothing to run by hand: the API creates the extension and schema on start. Neon lists `btree_gist` among its
   supported extensions; it has not been verified on Neon from this repository.

`DATABASE_URL` accepts `postgres://` or `postgresql://`; `backend/app/config.py` rewrites it to the psycopg driver.

## 2. API (Render)

1. Push the repository to GitHub.
2. Render dashboard → **New** → **Blueprint** → select the repository. Render reads `render.yaml` and proposes the
   service `ai-phone-receptionist-api` (Docker, root `backend`, free plan, health check `/api/health`).
3. Fill the variables marked `sync: false`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | the Neon pooled string |
| `GEMINI_API_KEY` | your key (stays in Render's secret store; never reaches the browser) |
| `FRONTEND_ORIGIN` | the Vercel URL, e.g. `https://ai-phone-receptionist.vercel.app` (no trailing slash; comma-separate several) |
| `DEMO_ACCESS_CODE` | a code you will give reviewers |

   The Blueprint already sets `APP_ENV=production`, `AGENT_BRAIN=gemini`, `GEMINI_MODEL=gemini-3-flash-preview`,
   `GEMINI_THINKING_LEVEL=minimal`, `DAILY_MODEL_CALL_LIMIT=15` and the rate limits.
4. Create. The first build takes a few minutes; the log should show `Running upgrade  -> 0001, initial schema` and
   `Seeded 4 services, 3 staff`.
5. If `FRONTEND_ORIGIN` is not known yet, deploy with a placeholder, then set it after step 3 and redeploy.

Keep **one instance** (the rate limiters are in memory). Render's free plan sleeps after about 15 minutes without
traffic; the first request after that takes roughly a minute while it wakes, so open the app once before a demo.

## 3. Web (Vercel)

1. Vercel → **Add New** → **Project** → import the repository.
2. **Root Directory** `frontend`, framework preset **Vite** (build `npm run build`, output `dist`).
3. Environment variable `VITE_API_BASE_URL` = the Render URL, e.g. `https://ai-phone-receptionist-api.onrender.com`
   (public, baked in at build time; no trailing slash).
4. Deploy, then put the Vercel URL into Render's `FRONTEND_ORIGIN` and redeploy the API.

## Voice over the internet: WebSocket, origin, access code, microphone

- **wss**: the browser derives the stream URL from `VITE_API_BASE_URL`, switching `https` to `wss`
  (`streamUrl` in `frontend/src/lib/api.ts`). Render's proxy supports WebSockets on the same service and port; no
  extra configuration.
- **Origin**: CORS does not cover WebSockets, so `WS /api/voice/stream` itself refuses any `Origin` header that is
  not in `FRONTEND_ORIGIN` (close code 4403; `test_voice_stream_refuses_foreign_origins`). HTTP calls use normal
  CORS from the same setting.
- **Access code**: a browser cannot set headers on a WebSocket, so the code travels as `?code=` on the stream URL;
  HTTP calls send `X-Access-Code`. Missing or wrong code: HTTP 401, WebSocket close 4401
  (`test_access_code_guards_api_and_stream`). The code is a demo gate, not user authentication.
- **Microphone**: speech recognition and `getUserMedia` only work in a secure context. Vercel serves HTTPS, so this
  holds in production (and on `localhost` in development). Voice needs Chrome or Edge; other browsers fall back to
  text mode. Chrome's recognition sends the audio to Google's speech service; the app itself never receives audio.

## 4. Smoke test after deploying

```bash
curl -s https://ai-phone-receptionist-api.onrender.com/api/health
```

Expect `"status":"ok","database":"ok","brain":"gemini","model":"gemini-3-flash-preview","model_configured":true`.

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://ai-phone-receptionist-api.onrender.com/api/calls
```

Expect `401` (the access code is enforced).

```bash
curl -s -H "X-Access-Code: <your code>" "https://ai-phone-receptionist-api.onrender.com/api/availability?service=haircut&date_from=2026-11-04&date_to=2026-11-04" | head -c 300
```

Expect a JSON list of slots (use a date in the next few weeks).

Then in the browser, on the Vercel URL:

1. Enter the access code when asked.
2. **Call** page → Text mode → Start call → "Can I talk to a real person?". This uses no model call; the transfer is
   spoken and the call appears on **Handoffs**.
3. Start another call, Voice mode, allow the microphone, and say "I'd like a haircut on Wednesday afternoon", "the
   first one", your name, "yes". This uses about 3 to 4 model calls. Check the booking on **Calendar** and the text on
   **Texts**. If the socket fails to connect, the browser console shows the close code: 4403 means `FRONTEND_ORIGIN`
   does not match the Vercel URL exactly; 4401 means the access code.
4. **Call log** shows the transcript, tool calls and both latencies for that call.

Guarding the wallet (roadmap "Ship it", step 4): the key is only in Render's secret store, the server caps model
calls per day, the access code keeps the public out, and the key stays on the free tier (or set a spend cap in Google
AI Studio if billing is ever enabled).

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

- `alembic upgrade head`, `downgrade base`, `upgrade head` and `alembic check` (no drift) against PostgreSQL 16.
- `docker build backend` succeeds; the container ran `start.sh` (migrations at head, seed) against the compose
  database and answered `GET /api/health` with 200.
- The web app (5174) and API (8010) run together; Vite proxies `/api` and the WebSocket. Live calls through the UI
  booked, moved and handed off correctly.
- `gemini-3-flash-preview` completed the live search → read-back loop (3 model calls in total on 31 Oct).
- **Not verified:** anything on Render, Neon or Vercel, including `wss` through Render's proxy and microphone access
  on the Vercel domain. Run the smoke test above after the first deploy.
