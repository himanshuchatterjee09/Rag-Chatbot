from typing import Any, Dict, List, Optional
import aioodbc
from ..config import Settings


class SQLService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._pool: Optional[aioodbc.Pool] = None

    @property
    def _conn_str(self) -> str:
        s = self._settings
        return (
            f"DRIVER={{{s.azure_sql_driver}}};"
            f"SERVER={s.azure_sql_server};"
            f"DATABASE={s.azure_sql_database};"
            f"UID={s.azure_sql_username};"
            f"PWD={s.azure_sql_password};"
            "Encrypt=yes;TrustServerCertificate=no;"
        )

    async def _get_pool(self) -> aioodbc.Pool:
        if self._pool is None:
            self._pool = await aioodbc.create_pool(
                dsn=self._conn_str, minsize=1, maxsize=5
            )
        return self._pool

    async def fetch_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        for attempt in range(2):
            try:
                pool = await self._get_pool()
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(sql, params)
                        columns = [col[0] for col in cur.description]
                        rows = await cur.fetchall()
                        return [dict(zip(columns, row)) for row in rows]
            except Exception as exc:
                if attempt == 0 and self._pool is not None:
                    self._pool.close()
                    self._pool = None
                else:
                    raise

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        results = await self.fetch_all(sql, params)
        return results[0] if results else None

    async def execute(self, sql: str, params: tuple = ()) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                await conn.commit()
                return cur.rowcount

    async def ping(self) -> bool:
        try:
            await self.fetch_one("SELECT 1 AS ok")
            return True
        except Exception:
            return False

    async def close(self):
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()

    async def get_schema_context(self) -> str:
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        return f"""
Tables in the database:

1. portfolios
   Columns: id, portfolio, portfolio_lead, uk_lead, ai_scout
   Description: AI portfolio areas and their leads. ai_scout is an email address.

2. ai_initiatives
   Columns: item_id, initiative_name, portfolio_team, owner, last_updated, stage, confirmed_scout
   stage values: Proposed, Live, PoC/Pilot, On Hold, Completed, Blocked, In Progress, Reframed, Stopped
   portfolio_team references the portfolio column in the portfolios table.
   owner can contain multiple names separated by commas.
   last_updated is stored as YYYY-MM text (e.g. '2026-04' = April 2026, '2024-10' = October 2024).
   Use plain string comparison for date filters: last_updated < '2026-04-01' or last_updated >= '2025-10-01'.
   Empty string means no date recorded.
   Today's date is {today}.
"""

    async def get_initiatives_summary(self) -> Dict[str, Any]:
        totals = await self.fetch_one("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN stage='Live'        THEN 1 ELSE 0 END) AS live,
                SUM(CASE WHEN stage='Completed'   THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN stage='Proposed'    THEN 1 ELSE 0 END) AS proposed,
                SUM(CASE WHEN stage='On Hold'     THEN 1 ELSE 0 END) AS on_hold,
                SUM(CASE WHEN stage='In Progress' THEN 1 ELSE 0 END) AS in_progress,
                SUM(CASE WHEN stage='PoC/Pilot'   THEN 1 ELSE 0 END) AS poc_pilot,
                SUM(CASE WHEN stage='Blocked'     THEN 1 ELSE 0 END) AS blocked
            FROM ai_initiatives
        """)
        by_portfolio = await self.fetch_all("""
            SELECT portfolio_team, COUNT(*) AS count
            FROM   ai_initiatives
            GROUP  BY portfolio_team
            ORDER  BY count DESC
        """)
        return {"totals": totals, "by_portfolio": by_portfolio}
