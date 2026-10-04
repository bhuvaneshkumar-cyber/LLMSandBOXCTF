"""App assembly: API routers under /api/v1, the built frontend at /, and a health probe."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_admin, routes_auth, routes_chat
from app.db.session import init_db

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Vault Keeper", lifespan=lifespan)


@app.middleware("http")
async def cap_body_size(request: Request, call_next):
    # Prompts are at most 2000 chars; refuse big bodies before they are read into memory.
    if int(request.headers.get("content-length") or 0) > 10_000:
        return JSONResponse({"detail": "Request too large."}, status_code=413)
    return await call_next(request)


for module in (routes_auth, routes_chat, routes_admin):
    app.include_router(module.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Same origin as the API, so no CORS. Mounted last: it answers every path the routes above don't.
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
