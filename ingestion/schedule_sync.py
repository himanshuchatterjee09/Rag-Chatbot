"""
Azure Function entry-point for weekly scheduled SQL→Search sync.
Trigger: TimerTrigger (cron: "0 0 2 * * 1" = every Monday at 02:00 UTC)

Deploy this file as an Azure Function App (Python v2 programming model).
Set the same environment variables as the backend .env.
"""
import asyncio
import logging
import os
import azure.functions as func

# Adjust path if bundled differently in the Function App package
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.config import get_settings
from app.services.search_service import SearchService
from app.services.sql_service import SQLService

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 2 * * 1",  # every Monday 02:00 UTC
    arg_name="timer",
    run_on_startup=False,
)
async def weekly_sync(timer: func.TimerRequest) -> None:
    logging.info("Weekly SQL→Search sync started")
    settings = get_settings()
    sql = SQLService(settings)
    search = SearchService(settings)

    try:
        count = await search.index_from_sql(sql, table="all", recreate=False)
        logging.info("Sync complete: %d documents indexed", count)
    except Exception as exc:
        logging.error("Sync failed: %s", exc)
        raise
    finally:
        await sql.close()
