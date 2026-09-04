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

## Endpoints

- `GET  /health` - liveness probe
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
