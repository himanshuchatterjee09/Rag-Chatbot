from typing import Any, Optional
import pandas as pd
from ..config import Settings
from .sql_service import SQLService


class ExcelIngestionService:
    def __init__(self, settings: Settings):
        self._sql = SQLService(settings)

    async def ingest_all(
        self,
        company_profile_path: Optional[str] = None,
        ai_initiatives_path: Optional[str] = None,
        ai_adoption_path: Optional[str] = None,
    ) -> dict:
        results: dict = {}
        if company_profile_path:
            results["company_profile"] = await self._ingest_company_profile(company_profile_path)
        if ai_initiatives_path:
            results["ai_initiatives"] = await self._ingest_ai_initiatives(ai_initiatives_path)
        if ai_adoption_path:
            results["ai_adoption_index"] = await self._ingest_ai_adoption_index(ai_adoption_path)
        return results

    async def _ingest_company_profile(self, path: str) -> int:
        df = _load(path)
        df = _normalise_columns(df, {
            "name": "company_name",
            "employees": "employee_count",
            "hq": "headquarters",
            "strategic focus": "strategic_focus",
            "ai vision": "ai_vision",
        })
        count = 0
        for _, row in df.iterrows():
            name = row.get("company_name")
            if _is_empty(name):
                continue
            await self._sql.execute(
                """
                MERGE company_profile AS t
                USING (SELECT ? AS company_name) AS s ON t.company_name = s.company_name
                WHEN MATCHED THEN UPDATE SET
                    industry=?, employee_count=?, headquarters=?,
                    strategic_focus=?, ai_vision=?, last_updated=GETDATE()
                WHEN NOT MATCHED THEN INSERT
                    (company_name, industry, employee_count, headquarters,
                     strategic_focus, ai_vision, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, GETDATE());
                """,
                (
                    name,
                    row.get("industry"), _i(row.get("employee_count")),
                    row.get("headquarters"), row.get("strategic_focus"), row.get("ai_vision"),
                    name,
                    row.get("industry"), _i(row.get("employee_count")),
                    row.get("headquarters"), row.get("strategic_focus"), row.get("ai_vision"),
                ),
            )
            count += 1
        return count

    async def _ingest_ai_initiatives(self, path: str) -> int:
        df = _load(path)
        df = _normalise_columns(df, {
            "name": "initiative_name",
            "initiative": "initiative_name",
            "budget": "budget_allocated",
            "end date": "target_end_date",
            "end_date": "target_end_date",
            "progress": "progress_percentage",
        })
        count = 0
        for _, row in df.iterrows():
            name = row.get("initiative_name")
            if _is_empty(name):
                continue
            await self._sql.execute(
                """
                MERGE ai_initiatives AS t
                USING (SELECT ? AS initiative_name) AS s ON t.initiative_name = s.initiative_name
                WHEN MATCHED THEN UPDATE SET
                    status=?, owner=?, department=?,
                    budget_allocated=?, budget_spent=?,
                    start_date=?, target_end_date=?, actual_end_date=?,
                    priority=?, description=?, objectives=?, kpis=?,
                    progress_percentage=?, risks=?, last_updated=GETDATE()
                WHEN NOT MATCHED THEN INSERT (
                    initiative_name, status, owner, department,
                    budget_allocated, budget_spent, start_date, target_end_date,
                    actual_end_date, priority, description, objectives,
                    kpis, progress_percentage, risks, last_updated, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE(), GETDATE());
                """,
                (
                    name,
                    row.get("status"), row.get("owner"), row.get("department"),
                    _f(row.get("budget_allocated")), _f(row.get("budget_spent")),
                    _d(row.get("start_date")), _d(row.get("target_end_date")),
                    _d(row.get("actual_end_date")), row.get("priority"),
                    row.get("description"), row.get("objectives"), row.get("kpis"),
                    _i(row.get("progress_percentage")), row.get("risks"),
                    name,
                    row.get("status"), row.get("owner"), row.get("department"),
                    _f(row.get("budget_allocated")), _f(row.get("budget_spent")),
                    _d(row.get("start_date")), _d(row.get("target_end_date")),
                    _d(row.get("actual_end_date")), row.get("priority"),
                    row.get("description"), row.get("objectives"), row.get("kpis"),
                    _i(row.get("progress_percentage")), row.get("risks"),
                ),
            )
            count += 1
        return count

    async def _ingest_ai_adoption_index(self, path: str) -> int:
        df = _load(path)
        df = _normalise_columns(df, {
            "area": "dimension",
            "category": "dimension",
            "score": "current_score",
            "benchmark": "benchmark_score",
            "maturity": "maturity_level",
            "gap": "gap_analysis",
        })
        count = 0
        for _, row in df.iterrows():
            dimension = row.get("dimension")
            if _is_empty(dimension):
                continue
            sub = row.get("sub_dimension")
            await self._sql.execute(
                """
                MERGE ai_adoption_index AS t
                USING (SELECT ? AS dimension, ? AS sub_dimension) AS s
                    ON t.dimension = s.dimension
                   AND (t.sub_dimension = s.sub_dimension
                        OR (t.sub_dimension IS NULL AND s.sub_dimension IS NULL))
                WHEN MATCHED THEN UPDATE SET
                    current_score=?, target_score=?, maturity_level=?,
                    benchmark_score=?, gap_analysis=?, recommendations=?,
                    assessment_date=?, assessor=?, notes=?
                WHEN NOT MATCHED THEN INSERT (
                    dimension, sub_dimension, current_score, target_score,
                    maturity_level, benchmark_score, gap_analysis,
                    recommendations, assessment_date, assessor, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    str(dimension), sub if not _is_empty(sub) else None,
                    _f(row.get("current_score")), _f(row.get("target_score")),
                    row.get("maturity_level"), _f(row.get("benchmark_score")),
                    row.get("gap_analysis"), row.get("recommendations"),
                    _d(row.get("assessment_date")), row.get("assessor"), row.get("notes"),
                    str(dimension), sub if not _is_empty(sub) else None,
                    _f(row.get("current_score")), _f(row.get("target_score")),
                    row.get("maturity_level"), _f(row.get("benchmark_score")),
                    row.get("gap_analysis"), row.get("recommendations"),
                    _d(row.get("assessment_date")), row.get("assessor"), row.get("notes"),
                ),
            )
            count += 1
        return count


# ── helpers ──────────────────────────────────────────────────────────────────

def _load(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df.where(pd.notna(df), None)


def _normalise_columns(df: pd.DataFrame, aliases: dict) -> pd.DataFrame:
    return df.rename(columns={k.replace(" ", "_"): v for k, v in aliases.items()})


def _is_empty(val: Any) -> bool:
    return val is None or (isinstance(val, float)) or str(val).strip() in ("", "nan", "None")


def _f(val: Any) -> Optional[float]:
    try:
        return float(val) if not _is_empty(val) else None
    except (ValueError, TypeError):
        return None


def _i(val: Any) -> Optional[int]:
    try:
        return int(float(val)) if not _is_empty(val) else None
    except (ValueError, TypeError):
        return None


def _d(val: Any) -> Optional[str]:
    if _is_empty(val):
        return None
    try:
        ts = pd.Timestamp(str(val))
        return ts.strftime("%Y-%m-%d") if not pd.isna(ts) else None
    except Exception:
        return None
