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


class ExternalViewService:
    """Queries the read-only Initiative_Details_View on the external Azure SQL DB.

    Authentication uses an AAD access token fetched via the local `az` CLI.
    Tokens are cached in-process until ~5 minutes before expiry.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER=tcp:{settings.external_sql_server},1433;"
            f"DATABASE={settings.external_sql_database};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
        )
        self._cached_token: Optional[str] = None
        self._cached_expiry: float = 0.0

    def _get_token(self) -> str:
        if self._cached_token and time.time() < self._cached_expiry - 300:
            return self._cached_token

        # Only try Managed Identity if we're actually running in Azure
        # (IDENTITY_ENDPOINT / MSI_ENDPOINT are set by the Azure host)
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

        # Local dev: fall back to the Azure CLI
        az = shutil.which("az") or shutil.which("az.cmd")
        if not az:
            raise RuntimeError(
                "No auth available. Either run inside Azure with a Managed Identity, "
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

    @property
    def view(self) -> str:
        return self._settings.external_sql_view

    async def fetch_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_sync, sql, params)

    def _fetch_sync(self, sql: str, params: tuple) -> List[Dict[str, Any]]:
        with pyodbc.connect(
            self._conn_str,
            timeout=30,
            attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: self._token_struct()},
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None:
                    return []
                columns = [col[0] for col in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]

    async def fetch_initiatives_aggregated(self) -> List[Dict[str, Any]]:
        """Returns one row per initiative with participants aggregated."""
        sql = f"""
            SELECT
                CAST(Initiative_ID AS NVARCHAR(50))                                AS item_id,
                MAX(Initiative_Name)                                                AS initiative_name,
                MAX(Initiative_Description)                                         AS description,
                MAX(Deliverable_Type)                                               AS deliverable_type,
                MAX(Stage)                                                          AS stage,
                MAX(Banking_Area_Domain)                                            AS banking_domain,
                MAX(CAST(Impact AS NVARCHAR(MAX)))                                  AS impact,
                MAX(CAST(Value_Dimensions AS NVARCHAR(MAX)))                        AS value_dimensions,
                MAX(Created_By)                                                     AS created_by,
                MAX(CONVERT(NVARCHAR(7), Updated_At, 126))                          AS last_updated,
                MAX(Portfolio_Label)                                                AS portfolio_team,
                MAX(Sub_Portfolio_Label)                                            AS sub_portfolio,
                STRING_AGG(Participant_Name, ', ') WITHIN GROUP (ORDER BY Participant_Name) AS owner
            FROM {self.view}
            GROUP BY Initiative_ID
        """
        return await self.fetch_all(sql)

    def schema_context(self) -> str:
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        return f"""
External SQL view (read-only) on Azure SQL: {self.view}

Each row represents one (initiative, participant) pair. An initiative with N
participants appears N times in the view.

Columns:
  Initiative_ID, Initiative_Name, Initiative_Description, Deliverable_Type, Stage,
  Banking_Area_Domain, Impact, Value_Dimensions, Created_By, Created_By_Email,
  Created_At, Updated_At, Portfolio_Label, Sub_Portfolio_Label,
  Participant_Name, Participant_Email

Stage values: Proposed, Live, In Progress, Completed, On Hold, Blocked, PoC/Pilot, Stopped.

Rules for querying:
- ALWAYS use COUNT(DISTINCT Initiative_ID) when counting initiatives.
- Use SELECT DISTINCT Initiative_ID, ... or GROUP BY Initiative_ID to avoid duplicates.
- For "who owns the most", GROUP BY Participant_Name and COUNT(DISTINCT Initiative_ID).
- Updated_At is a DATETIME — use it directly: WHERE Updated_At >= '2026-01-01'.
- Use LIKE '%Name%' for partial matches.

Today's date is {today}.
"""
