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
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                columns = [col[0] for col in cur.description]
                rows = await cur.fetchall()
                return [dict(zip(columns, row)) for row in rows]

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
        return """
Tables in the database:

1. company_profile
   Columns: id, company_name, industry, employee_count, headquarters,
            strategic_focus, ai_vision, last_updated

2. ai_initiatives
   Columns: initiative_id, initiative_name, status[Active/Completed/Planned/On Hold],
            owner, department, budget_allocated, budget_spent, start_date,
            target_end_date, actual_end_date, priority[High/Medium/Low],
            description, objectives, kpis, progress_percentage, risks,
            last_updated, created_at

3. ai_adoption_index
   Columns: id, dimension, sub_dimension, current_score(1-5), target_score(1-5),
            maturity_level[Initial/Developing/Defined/Managed/Optimizing],
            benchmark_score, gap_analysis, recommendations,
            assessment_date, assessor, notes
"""

    async def get_initiatives_summary(self) -> Dict[str, Any]:
        totals = await self.fetch_one("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='Active'    THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status='Planned'   THEN 1 ELSE 0 END) AS planned,
                SUM(CASE WHEN status='On Hold'   THEN 1 ELSE 0 END) AS on_hold,
                AVG(CAST(progress_percentage AS FLOAT))              AS avg_progress
            FROM ai_initiatives
        """)
        by_dept = await self.fetch_all("""
            SELECT department, COUNT(*) AS count
            FROM   ai_initiatives
            GROUP  BY department
            ORDER  BY count DESC
        """)
        return {"totals": totals, "by_department": by_dept}
