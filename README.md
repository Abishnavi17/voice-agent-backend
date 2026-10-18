# Voice Agent Backend (Vapi Integration)

An asynchronous FastAPI backend built for Vapi.ai voice agents. Handles real-time webhooks, custom tool calls, lead qualification, and stores structured call reports in PostgreSQL.

---

## Architecture Overview

+----------------+       +------------------+       +------------------+
|  User (Caller) | <---> | Twilio Platform  | <---> |  Vapi.ai Engine  |
+----------------+       +------------------+       +------------------+
                                                             |
                                                    Real-time Webhook
                                                    (POST /vapi/webhook)
                                                             |
                                                             v
                                                    +------------------+
                                                    | FastAPI Backend  |
                                                    +------------------+
                                                             |
                                                      Database Queries
                                                             |
                                                             v
                                                    +------------------+
                                                    | PostgreSQL /     |
                                                    | SQLite Database  |
                                                    +------------------+

---

## How an Incoming Call Works (Step-by-Step Flow)

1. **User Calls In:** A user calls your phone number. The call reaches Twilio and gets routed immediately to the Vapi platform.
2. **Speech Recognition:** Vapi listens to the caller's voice and instantly converts the audio into text.
3. **AI Reasoning:** The Vapi AI agent processes what the caller said and decides on the best response using your configured system instructions.
4. **Backend Webhook & Tools (FastAPI):** If the AI needs to look up or store data during the call, Vapi sends a webhook request to your FastAPI server (`POST /vapi/webhook`). FastAPI processes the request, updates PostgreSQL, and returns the result back to Vapi.
5. **Voice Response back to User:** Vapi converts the AI's textual response into realistic speech and streams the audio back through Twilio to the caller.
6. **Call Summary & Lead Saving:** After the user hangs up, Vapi sends a final summary report to FastAPI. FastAPI extracts the full transcript and lead information, then permanently saves it into your database.

---

## Key Features

- **Vapi Webhook Integration:** Real-time event handling for live function execution and server messages during active voice calls.
- **End-of-Call Processing:** Automatically parses complete call transcripts, call durations, summaries, and metadata upon call termination.
- **Lead Qualification Pipeline:** Extracts qualified lead details from voice interactions and stores them directly in the database.
- **Assistant Automation Script:** Programmatic assistant creation via `scripts/create_assistant.py` to sync system prompts and tools with the Vapi API.
- **Automated Testing Suite:** Unit and integration tests powered by `pytest` inside the `tests/` directory.

---

## Project Structure

voice-agent-backend/
├── app/
│   ├── main.py                  # FastAPI entry point & webhook endpoints
│   ├── database.py              # Database models, sessions, and schema
│   └── routers/                 # Webhook routers & lead management endpoints
├── scripts/
│   └── create_assistant.py      # Script for automated Vapi assistant creation
├── tests/                       # Pytest test suite for API and business logic
├── .env.example                 # Environment variables configuration template
├── requirements.txt             # Project dependencies
└── docker-compose.yml           # Multi-container setup for Postgres & FastAPI

---

## Setup & Local Development

### 1. Clone & Setup Virtual Environment

    git clone https://github.com/Abishnavi17/voice-agent-backend.git
    cd voice-agent-backend
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt

### 2. Configure Environment Variables

Copy `.env.example` to create your local `.env` file:

    cp .env.example .env

Set your configuration values inside `.env`:

    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/voiceagent
    WEBHOOK_SECRET=my_vapi_secret_123
    VAPI_API_KEY=your_vapi_private_api_key

### 3. Start the Backend Server

    export $(grep -v '^#' .env | xargs)
    uvicorn app.main:app --reload --port 8000

---

## Exposing Backend to Vapi (Ngrok Setup)

To process live webhooks from your Vapi Assistant:

1. Start an ngrok tunnel on port 8000:

    ngrok http 8000

2. Copy the generated HTTPS URL (e.g., `https://xxxx.ngrok-free.app`).
3. Configure your Vapi Assistant's **Server URL** in the Vapi Dashboard:
   `https://xxxx.ngrok-free.app/vapi/webhook`

---

## API Endpoints Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Server status and health check |
| `POST` | `/vapi/webhook` | Receives live tool calls & end-of-call reports from Vapi |
| `GET` | `/leads` | Fetches stored qualified leads extracted from call logs |

---

## Running Automated Tests

Execute the test suite locally using `pytest`:

    pytest
