> 🚀 **Production Starter Kit:** Want the pre-configured Docker stack, `.env.example` templates, and complete source zip for instant deployment?  
> 👉 **[Get the Packaged Backend on Gumroad ($47)](https://abishnavi.gumroad.com/l/voice-ai-backend)**
>👉 **[Get the Packaged Backend on Topmate(₹1,499 UPI)](https://topmate.io/abishnavi)**

# Voice Agent Backend

An asynchronous backend that ingests audio, transcribes it, generates a
context-aware reply, remembers the conversation, caches results, and stores
everything in PostgreSQL. Built with FastAPI, faster-whisper, Hugging Face
Transformers, PostgreSQL, Redis, and Docker.

    audio -> Whisper (speech->text) -> load conversation history
          -> check Redis cache
               hit  -> return cached reply instantly
               miss -> run the LLM, then cache the reply
          -> save the turn in PostgreSQL -> return {transcript, reply, cached}

## Architecture

| Component | Tech | Role |
|-----------|------|------|
| API layer | FastAPI (async) | endpoints, validation, auto docs at /docs |
| Speech-to-text | faster-whisper | transcribes uploaded audio (CPU, CTranslate2) |
| Reply model | Transformers (FLAN-T5-small) | generates the agent's reply; swappable |
| Memory | PostgreSQL via SQLAlchemy async | conversations + turns (one-to-many) |
| Cache | Redis | caches computed replies with a TTL |
| Packaging | Docker + docker-compose | one command runs app + db + cache |

## Run with Docker (recommended)

Requires Docker Desktop running.

    docker compose up --build

First build downloads base images and Python packages; first request downloads
the Whisper (~150 MB) and FLAN-T5 (~300 MB) models into a persistent volume.

Then open http://localhost:8000/docs

Stop with Ctrl+C; remove containers with `docker compose down` (data persists in
named volumes). To wipe data too: `docker compose down -v`.

## Speed-to-lead voice agent (phone calls via Vapi)

The original `/chat` pipeline works on uploaded audio files. For real phone calls
this backend now also acts as the **brain behind a Vapi voice agent**: Vapi
handles the phone line, speech and voice; this backend decides who to call,
supplies the agent's actions (free times, booking, notes) and keeps the records.

    ad form / CRM / Zapier --POST /leads--> backend --> Vapi phones the lead
    during the call:  Vapi --POST /vapi/webhook (tool-calls)--> check times / book / save notes
    after the call:   Vapi --POST /vapi/webhook (end-of-call-report)--> transcript, summary, status

Lead statuses: `new`, `calling`, `pending_window` (waiting for calling hours),
`booked`, `qualified`, `not_interested`, `no_answer`, `failed`, `completed`.

### Setup

1. **Accounts.** Create a Vapi account and get a phone number (Vapi number, or
   import your own). Copy `.env.example` to `.env`.
2. **Invent two secrets** (`openssl rand -hex 32`): `LEADS_API_KEY` (for whoever
   sends you leads) and `WEBHOOK_SECRET` (for Vapi). Fill in `VAPI_API_KEY`,
   `VAPI_PHONE_NUMBER_ID`, `BUSINESS_NAME`, `BUSINESS_TZ`, `BUSINESS_HOURS`.
3. **Vapi credential.** In the Vapi dashboard: Integrations -> Server
   Configuration -> Add Custom Credential -> Bearer Token. Set the token to your
   `WEBHOOK_SECRET`, header name `X-Vapi-Secret`, and turn "Include Bearer
   prefix" off. Note the credential's id (`VAPI_CREDENTIAL_ID`). Vapi attaches it
   to the saved tools/assistant created in step 5. (The backend also accepts
   `Authorization: Bearer <secret>`.)
4. **Run and expose the backend.** `docker compose up --build`, then give it a
   public https URL (while developing: ngrok or a Cloudflare Tunnel on port 8000).
5. **Create the agent.** On your machine (`pip install httpx`):

        export VAPI_API_KEY=... PUBLIC_BASE_URL=https://your-url VAPI_CREDENTIAL_ID=...
        python -m scripts.create_assistant --dry-run   # inspect first
        python -m scripts.create_assistant

   Put the printed assistant id in `.env` as `VAPI_ASSISTANT_ID` and restart.
   Edit `SYSTEM_PROMPT` in `app/assistant_config.py` to fit each client's offer.
6. **Try it** (the number must be one you are allowed to call):

        curl -X POST http://localhost:8000/leads \
          -H "X-API-Key: $LEADS_API_KEY" -H "Content-Type: application/json" \
          -d '{"name":"Test","phone":"+91XXXXXXXXXX","consent":true,"message":"Interested in a demo"}'

   Then `GET /leads/{id}` (same header) shows status, transcript, summary and bookings.
7. **Connect real lead sources** (Meta/Google lead forms, a website form, a CRM)
   through Zapier, Make or n8n: have them POST each new lead to `/leads` with the
   `X-API-Key` header and `"consent": true` only for people who agreed to be contacted.

### Before calling real people

- **Consent and rules.** `/leads` refuses `consent: false`. You are responsible for
  local calling law (e.g. India DND/TRAI rules, US TCPA). Calls are only placed
  inside `CALL_WINDOW`; leads that arrive outside it wait and are called when it opens.
- **Disclosure.** The agent introduces itself as a virtual assistant and says so if asked.
- **Test first.** Call your own phone 20+ times and read the transcripts before a client's leads.

### Known limits (next things to build)

- Bookings are stored in the database and sent to `NOTIFY_WEBHOOK_URL` (Slack,
  Zapier, ...). **No Google Calendar sync and no SMS/email confirmation yet.**
- One business per deployment (one set of hours/prompt). Run one deployment per client.
- `/chat` and `/conversations/{id}` have no authentication (unchanged). Do not
  expose them on a public URL; put them behind a proxy rule or remove them.
- Fields read from Vapi's end-of-call report beyond the documented ones
  (`summary`, `durationSeconds`) are optional; if a call log looks empty, inspect
  a real payload in Vapi's webhook logs and adjust `app/vapi_parse.py`.

## Tests

    pip install -r requirements-dev.txt
    pytest                              # API tests run on SQLite with Vapi faked
    python -m unittest tests.test_logic # pure logic only; needs no installs

## Endpoints

- `GET  /health` - liveness probe
- `POST /leads` - (X-API-Key) new lead -> stored -> outbound call starts
- `GET  /leads`, `GET /leads/{id}` - (X-API-Key) status, qualification, transcript, bookings
- `POST /leads/{id}/call` - (X-API-Key) call again (e.g. after no answer)
- `GET  /appointments` - (X-API-Key) upcoming bookings
- `POST /vapi/webhook` - (webhook secret) tool calls and end-of-call reports from Vapi
- `POST /chat` - upload audio (+ optional conversation_id) -> transcript + reply
- `GET  /conversations/{id}` - full turn history for a conversation

## Run locally without Docker (for development)

Needs Python 3.12, FFmpeg, PostgreSQL, and Redis installed and running.

    conda create -n voiceagent python=3.12 -y
    conda activate voiceagent
    conda install -c conda-forge ffmpeg postgresql redis-server -y
    pip install -r requirements.txt

    # start services
    initdb -D ~/voiceagent-db          # once
    pg_ctl -D ~/voiceagent-db -l ~/voiceagent-db/logfile start
    createdb voiceagent                # once
    redis-server --daemonize yes

    # run
    uvicorn app.main:app
    # open http://127.0.0.1:8000/docs

Config is read from environment variables (defaults for local use):

    DATABASE_URL=postgresql+asyncpg://localhost/voiceagent
    REDIS_URL=redis://localhost:6379/0

## Design notes

- Async throughout (FastAPI, asyncpg, redis.asyncio) so slow model inference
  never blocks other requests.
- Each component (model, DB, cache) sits behind a small interface, so any one is
  swappable without touching the pipeline. To upgrade reply quality, change the
  single model id in `app/agent.py`.
- Config via environment variables means the identical code runs locally and in
  Docker; only the URLs differ.
- The cache key is a hash of the full conversation context, so identical inputs
  hit the cache; a TTL keeps it fresh and bounded. Cache failures degrade
  gracefully and never break a request.
- Reply quality is bounded by the small CPU model chosen to keep the project
  free and offline; the architecture is model-agnostic.
