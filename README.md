# LLM Sandbox

<!-- TODO: Fill in your project description, challenge rules, and setup guide here. -->

## Quick Start

```bash
cp .env.example .env
# Edit .env and add your API keys

pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Project Structure

```
llm-sandbox/
├── app/
│   ├── main.py                 # App entry point, CORS, router registration
│   ├── core/
│   │   ├── config.py           # Pydantic-settings, all env vars
│   │   └── security.py         # Rate limiting, input sanitisation
│   ├── llm/
│   │   ├── client.py           # Provider-agnostic LLM abstraction
│   │   └── system_prompt.py    # Challenge system prompt + secret flag
│   ├── db/
│   │   ├── models.py           # SQLAlchemy ORM models
│   │   └── session.py          # Engine, session factory, get_db dependency
│   └── api/
│       ├── routes_chat.py      # POST /chat
│       └── routes_admin.py     # GET /admin/logs (protected)
├── tests/
├── requirements.txt
├── .env.example
├── Dockerfile
└── README.md
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | — | Liveness check |
| `POST` | `/api/v1/chat` | — | Send a message to the LLM |
| `GET` | `/api/v1/admin/logs` | `X-Admin-Key` header | View all chat logs |

## Environment Variables

See [`.env.example`](.env.example) for a full list with descriptions.
