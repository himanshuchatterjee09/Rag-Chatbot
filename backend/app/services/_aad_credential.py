"""Shared AAD credential that works in both Azure (Managed Identity) and locally (az CLI).
Used by SQL, Search, and any other Azure service that needs AAD auth.
"""
import asyncio
import json
import os
import shutil
import subprocess
import time
from typing import Optional
from azure.core.credentials import AccessToken


class _BaseTokenProvider:
    def __init__(self):
        self._cache: dict[str, tuple[str, float]] = {}

    def _fetch(self, scope: str) -> tuple[str, float]:
        cached = self._cache.get(scope)
        if cached and time.time() < cached[1] - 300:
            return cached

        # Azure-hosted: Managed Identity
        if os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT") or os.getenv("CONTAINER_APP_NAME"):
            try:
                from azure.identity import ManagedIdentityCredential
                cred = ManagedIdentityCredential()
                tok = cred.get_token(scope)
                pair = (tok.token, float(tok.expires_on))
                self._cache[scope] = pair
                return pair
            except Exception:
                pass

        # Local: Azure CLI
        az = shutil.which("az") or shutil.which("az.cmd")
        if not az:
            raise RuntimeError("No auth available. Run inside Azure or `az login` locally.")
        # The CLI expects a resource URL (no /.default suffix), not a scope
        resource = scope.replace("/.default", "").rstrip("/")
        if not resource.endswith("/"):
            resource = resource + "/"
        out = subprocess.run(
            [az, "account", "get-access-token", "--resource", resource, "--output", "json"],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            raise RuntimeError(f"az get-access-token failed: {out.stderr.strip()}")
        data = json.loads(out.stdout)
        pair = (data["accessToken"], float(data["expires_on"]))
        self._cache[scope] = pair
        return pair


_provider = _BaseTokenProvider()


class HybridAsyncTokenCredential:
    """Async credential compatible with azure.search etc. SDKs."""

    async def get_token(self, *scopes, **kwargs) -> AccessToken:
        scope = scopes[0]
        token, expires = await asyncio.get_event_loop().run_in_executor(
            None, _provider._fetch, scope
        )
        return AccessToken(token, int(expires))

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class HybridSyncTokenCredential:
    """Sync version for use with pyodbc connections etc."""

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        token, expires = _provider._fetch(scopes[0])
        return AccessToken(token, int(expires))
