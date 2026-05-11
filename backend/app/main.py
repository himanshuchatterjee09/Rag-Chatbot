import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .models import HealthResponse, IndexRequest, QueryRequest, QueryResponse
from .services.intent_router import IntentRouter
from .services.search_service import SearchService
from .services.sql_service import SQLService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.router = IntentRouter(settings)
    app.state.search = SearchService(settings)
    app.state.sql = SQLService(settings)
    await app.state.search.ensure_index_exists()
    yield
    await app.state.sql.close()


def create_app() -> FastAPI:
    settings = get_settings()
    return FastAPI(
        title=settings.app_name,
        description="RAG chatbot for AI initiatives and adoption index",
        version="1.0.0",
        lifespan=lifespan,
    )


app = create_app()

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health():
    sql_ok = await app.state.sql.ping()
    return HealthResponse(
        status="healthy" if sql_ok else "degraded",
        services={"sql": sql_ok, "search": True, "llm": True},
    )


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    start = time.monotonic()
    try:
        response = await app.state.router.route(request)
        response.processing_time_ms = round((time.monotonic() - start) * 1000, 1)
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/query/stream")
async def query_stream(request: QueryRequest):
    async def generate():
        async for chunk in app.state.router.route_stream(request):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/index")
async def index_data(request: IndexRequest):
    try:
        count = await app.state.search.index_from_sql(
            app.state.sql,
            table=request.table,
            recreate=request.recreate_index,
        )
        return {"indexed": count, "table": request.table}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/initiatives/summary")
async def initiatives_summary():
    return await app.state.sql.get_initiatives_summary()


@app.get("/api/initiatives/portfolios")
async def portfolios():
    rows = await app.state.sql.fetch_all(
        "SELECT DISTINCT portfolio_team FROM ai_initiatives "
        "WHERE portfolio_team IS NOT NULL ORDER BY portfolio_team"
    )
    return [r["portfolio_team"] for r in rows]


@app.get("/api/initiatives/stages")
async def stages():
    rows = await app.state.sql.fetch_all(
        "SELECT stage, COUNT(*) AS count FROM ai_initiatives "
        "WHERE stage IS NOT NULL GROUP BY stage ORDER BY count DESC"
    )
    return rows


@app.get("/api/portfolios")
async def portfolio_leads():
    return await app.state.sql.fetch_all(
        "SELECT portfolio, portfolio_lead, uk_lead, ai_scout FROM portfolios ORDER BY portfolio"
    )


# Serve the frontend SPA from /frontend
_frontend = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
