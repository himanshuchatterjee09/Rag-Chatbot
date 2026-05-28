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
from .services.document_ingestion import DocumentIngestion
from .services.external_view_service import ExternalViewService
from .services.intent_router import IntentRouter
from .services.search_service import SearchService
from .services.sql_service import SQLService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.router = IntentRouter(settings)
    app.state.search = SearchService(settings)
    app.state.sql = SQLService(settings)
    app.state.view = ExternalViewService(settings)
    await app.state.search.ensure_index_exists()

    # Auto-ingest any PDFs dropped into the docs folder on startup.
    # DOCS_DIR env var lets Azure point to the mounted file share at /mnt/docs.
    docs_dir = os.environ.get(
        "DOCS_DIR",
        os.path.join(os.path.dirname(__file__), "..", "docs"),
    )
    try:
        await DocumentIngestion(settings, docs_dir).ingest_all()
    except Exception as exc:
        print(f"[DOC] startup ingestion failed: {exc}")

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
        async for event in app.state.router.route_stream(request):
            if event.get("type") == "chunk":
                yield f"data: {json.dumps({'chunk': event['text']})}\n\n"
            elif event.get("type") == "meta":
                yield f"data: {json.dumps({'meta': event}, default=str)}\n\n"
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


@app.post("/api/index/docs")
async def index_docs():
    """Re-scan the docs folder (mounted volume in Azure, local folder in dev)
    and re-index any PDFs found."""
    settings = get_settings()
    docs_dir = os.environ.get(
        "DOCS_DIR",
        os.path.join(os.path.dirname(__file__), "..", "docs"),
    )
    try:
        count = await DocumentIngestion(settings, docs_dir).ingest_all()
        return {"indexed_chunks": count, "docs_dir": docs_dir}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/initiatives/summary")
async def initiatives_summary():
    view = f"{app.state.view.view}"
    totals = await app.state.view.fetch_all(f"""
        SELECT
            COUNT(DISTINCT Initiative_ID) AS total,
            COUNT(DISTINCT CASE WHEN Stage='Live'        THEN Initiative_ID END) AS live,
            COUNT(DISTINCT CASE WHEN Stage='Completed'   THEN Initiative_ID END) AS completed,
            COUNT(DISTINCT CASE WHEN Stage='Proposed'    THEN Initiative_ID END) AS proposed,
            COUNT(DISTINCT CASE WHEN Stage='On Hold'     THEN Initiative_ID END) AS on_hold,
            COUNT(DISTINCT CASE WHEN Stage='In Progress' THEN Initiative_ID END) AS in_progress,
            COUNT(DISTINCT CASE WHEN Stage='PoC/Pilot'   THEN Initiative_ID END) AS poc_pilot,
            COUNT(DISTINCT CASE WHEN Stage='Blocked'     THEN Initiative_ID END) AS blocked
        FROM {view}
    """)
    by_portfolio = await app.state.view.fetch_all(f"""
        SELECT Portfolio_Label AS portfolio_team, COUNT(DISTINCT Initiative_ID) AS count
        FROM {view}
        GROUP BY Portfolio_Label
        ORDER BY count DESC
    """)
    return {"totals": totals[0] if totals else {}, "by_portfolio": by_portfolio}


@app.get("/api/initiatives/portfolios")
async def portfolios():
    rows = await app.state.view.fetch_all(
        f"SELECT DISTINCT Portfolio_Label FROM {app.state.view.view} "
        "WHERE Portfolio_Label IS NOT NULL ORDER BY Portfolio_Label"
    )
    return [r["Portfolio_Label"] for r in rows]


@app.get("/api/initiatives/stages")
async def stages():
    rows = await app.state.view.fetch_all(
        f"SELECT Stage AS stage, COUNT(DISTINCT Initiative_ID) AS count FROM {app.state.view.view} "
        "WHERE Stage IS NOT NULL GROUP BY Stage ORDER BY count DESC"
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
