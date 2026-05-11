from typing import Any, Optional
import pandas as pd
from ..config import Settings
from .sql_service import SQLService


class ExcelIngestionService:
    def __init__(self, settings: Settings):
        self._sql = SQLService(settings)

    async def ingest_all(
        self,
        portfolio_path: Optional[str] = None,
        initiatives_path: Optional[str] = None,
    ) -> dict:
        results: dict = {}
        if portfolio_path:
            results["portfolios"] = await self._ingest_portfolios(portfolio_path)
        if initiatives_path:
            results["ai_initiatives"] = await self._ingest_ai_initiatives(initiatives_path)
        return results

    async def _ingest_portfolios(self, path: str) -> int:
        df = _load(path)
        # AIPortfolio.xlsx columns: Portfolio, Portfolio Lead, UK Lead, AI Scout
        df = _normalise_columns(df, {
            "portfolio_lead": "portfolio_lead",
            "uk_lead": "uk_lead",
            "ai_scout": "ai_scout",
        })
        count = 0
        for _, row in df.iterrows():
            portfolio = row.get("portfolio")
            if _is_empty(portfolio):
                continue
            await self._sql.execute(
                """
                MERGE portfolios AS t
                USING (SELECT ? AS portfolio) AS s ON t.portfolio = s.portfolio
                WHEN MATCHED THEN UPDATE SET
                    portfolio_lead=?, uk_lead=?, ai_scout=?
                WHEN NOT MATCHED THEN INSERT
                    (portfolio, portfolio_lead, uk_lead, ai_scout)
                VALUES (?, ?, ?, ?);
                """,
                (
                    str(portfolio),
                    row.get("portfolio_lead"), row.get("uk_lead"), row.get("ai_scout"),
                    str(portfolio),
                    row.get("portfolio_lead"), row.get("uk_lead"), row.get("ai_scout"),
                ),
            )
            count += 1
        return count

    async def _ingest_ai_initiatives(self, path: str) -> int:
        df = _load(path)
        # AIInitiatives.xlsx columns: Item #, Initiative Name, Portfolio / Team,
        # Owner, Information Capture Date/Last Updated, Stage, Confirmed Scout
        df = _normalise_columns(df, {
            "item_#": "item_id",
            "item_no": "item_id",
            "initiative_name": "initiative_name",
            "portfolio_/_team": "portfolio_team",
            "portfolio_team": "portfolio_team",
            "portfolio_/_team_": "portfolio_team",
            "information_capture_date/last_updated": "last_updated",
            "date/last_updated": "last_updated",
            "confirmed_scout": "confirmed_scout",
        })
        count = 0
        for _, row in df.iterrows():
            item_id = row.get("item_id")
            name = row.get("initiative_name")
            if _is_empty(item_id) or _is_empty(name):
                continue
            await self._sql.execute(
                """
                MERGE ai_initiatives AS t
                USING (SELECT ? AS item_id) AS s ON t.item_id = s.item_id
                WHEN MATCHED THEN UPDATE SET
                    initiative_name=?, portfolio_team=?, owner=?,
                    last_updated=?, stage=?, confirmed_scout=?
                WHEN NOT MATCHED THEN INSERT
                    (item_id, initiative_name, portfolio_team, owner,
                     last_updated, stage, confirmed_scout)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    str(item_id),
                    str(name), row.get("portfolio_team"), row.get("owner"),
                    _normalise_date(row.get("last_updated")), row.get("stage"), row.get("confirmed_scout"),
                    str(item_id),
                    str(name), row.get("portfolio_team"), row.get("owner"),
                    _normalise_date(row.get("last_updated")), row.get("stage"), row.get("confirmed_scout"),
                ),
            )
            count += 1
        return count


# ── helpers ───────────────────────────────────────────────────────────────────

def _load(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df.fillna("")


def _normalise_columns(df: pd.DataFrame, aliases: dict) -> pd.DataFrame:
    return df.rename(columns={k.replace(" ", "_"): v for k, v in aliases.items()})


def _is_empty(val: Any) -> bool:
    return val is None or (isinstance(val, float)) or str(val).strip() in ("", "nan", "None")


def _s(val: Any) -> str:
    """Return a clean string, empty string if missing."""
    if val is None:
        return ""
    s = str(val).strip()
    s = s.replace('\xa0', ' ').replace('’', "'")
    s = ' '.join(s.split())
    return "" if s.lower() in ("nan", "none") else s


_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _normalise_date(val: Any) -> str:
    """Convert Mon-YY (e.g. Apr-26) → YYYY-MM (e.g. 2026-04) for easy SQL comparison."""
    s = _s(val)
    if not s or "-" not in s:
        return s
    parts = s.split("-")
    if len(parts) != 2:
        return s
    mon, yr = parts[0].strip().lower(), parts[1].strip()
    if mon in _MONTH_MAP and yr.isdigit() and len(yr) == 2:
        return f"20{yr}-{_MONTH_MAP[mon]}"
    return s


def _f(val: Any) -> Optional[float]:
    try:
        return float(val) if not _is_empty(val) else None
    except (ValueError, TypeError):
        return None
