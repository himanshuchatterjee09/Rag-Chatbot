import asyncio
import json
import os
import shutil
import struct
import subprocess
import time
from typing import Any, Dict, List, Optional
import pyodbc
from ..config import Settings

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_RESOURCE = "https://database.windows.net/"


class SQLService:
    """Connects to the local Azure SQL DB using Managed Identity (in Azure)
    or the local az CLI token (in dev). No SQL username/password used.

    Note: aioodbc doesn't expose attrs_before reliably across versions, so we run
    pyodbc synchronously in a thread-pool executor (the same pattern as the
    external view service). Throughput is fine because queries are small.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._conn_str = (
            f"DRIVER={{{settings.azure_sql_driver}}};"
            f"SERVER={settings.azure_sql_server};"
            f"DATABASE={settings.azure_sql_database};"
            "Encrypt=yes;TrustServerCertificate=no;"
        )
        self._cached_token: Optional[str] = None
        self._cached_expiry: float = 0.0

    def _get_token(self) -> str:
        if self._cached_token and time.time() < self._cached_expiry - 300:
            return self._cached_token

        # Azure-hosted: Managed Identity
        if os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT") or os.getenv("CONTAINER_APP_NAME"):
            try:
                from azure.identity import ManagedIdentityCredential
                cred = ManagedIdentityCredential()
                tok = cred.get_token(_RESOURCE + ".default")
                self._cached_token = tok.token
                self._cached_expiry = tok.expires_on
                return self._cached_token
            except Exception:
                pass

        # Local dev: Azure CLI
        az = shutil.which("az") or shutil.which("az.cmd")
        if not az:
            raise RuntimeError(
                "No auth available. Run inside Azure with a Managed Identity, "
                "or install Azure CLI locally and run `az login`."
            )
        out = subprocess.run(
            [az, "account", "get-access-token", "--resource", _RESOURCE, "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            raise RuntimeError(f"az get-access-token failed: {out.stderr.strip()}")
        data = json.loads(out.stdout)
        self._cached_token = data["accessToken"]
        self._cached_expiry = data["expires_on"]
        return self._cached_token

    def _token_struct(self) -> bytes:
        token_bytes = self._get_token().encode("UTF-16-LE")
        return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    async def _run(self, fn, *args):
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    def _connect(self) -> pyodbc.Connection:
        return pyodbc.connect(
            self._conn_str,
            timeout=30,
            attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: self._token_struct()},
        )

    def _fetch_all_sync(self, sql: str, params: tuple) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    return []
                columns = [col[0] for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _execute_sync(self, sql: str, params: tuple) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
                return cur.rowcount

    async def fetch_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        # Retry once on transient errors (40613 = DB paused/waking, HYT00 = login timeout)
        for attempt in range(2):
            try:
                return await self._run(self._fetch_all_sync, sql, params)
            except Exception as exc:
                msg = str(exc)
                transient = "40613" in msg or "HYT00" in msg or "40615" in msg
                if attempt == 0 and transient:
                    await asyncio.sleep(2)
                    continue
                raise

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        results = await self.fetch_all(sql, params)
        return results[0] if results else None

    async def execute(self, sql: str, params: tuple = ()) -> int:
        return await self._run(self._execute_sync, sql, params)

    async def ping(self) -> bool:
        try:
            await self.fetch_one("SELECT 1 AS ok")
            return True
        except Exception:
            return False

    async def close(self):
        # No persistent pool to close — connections are opened per query
        pass

    async def get_schema_context(self) -> str:
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        return f"""
Local table (portfolios only — all initiative data lives in the external view):

portfolios
  Columns: id, portfolio, portfolio_lead, uk_lead, ai_scout
  Description: AI portfolio areas and their leads. ai_scout is an email address.
  Use this table ONLY for "who is X?" / "who leads X?" questions about portfolio leadership.

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
