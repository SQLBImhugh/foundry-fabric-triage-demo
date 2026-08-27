"""Power BI REST — the one remediation action in this demo.

Two implementations behind one interface. ``MockPowerBIClient`` is what you
rehearse against; ``LivePowerBIClient`` is what you demo against.

Auth note: dataset refresh needs a token the *dataset* accepts. A service
principal works; so does a managed identity, which can be added to a Fabric /
Power BI workspace like any other principal and avoids secret rotation entirely
when the caller runs in Azure. Either way the tenant setting *"Allow service
principals to use Power BI APIs"* must be enabled and the principal must be a
workspace member. That pair is the most common surprise when moving from demo to
production, so it is stated here rather than discovered on stage.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("triage.powerbi")

_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
_API = "https://api.powerbi.com/v1.0/myorg"


@dataclass
class RefreshOutcome:
    status: str  # "Completed" | "Failed" | "Unknown"
    request_id: str = ""
    duration_ms: int = 0
    detail: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "Completed"


class PowerBIClient(Protocol):
    async def refresh_dataset(self, workspace_id: str, dataset_id: str) -> RefreshOutcome: ...
    async def get_refresh_history(
        self, workspace_id: str, dataset_id: str, top: int = 5
    ) -> list[dict[str, Any]]: ...
    async def rebind_gateway(
        self, workspace_id: str, dataset_id: str, gateway_id: str
    ) -> RefreshOutcome: ...


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


@dataclass
class MockPowerBIClient:
    """Scripted client. Deterministic, records every call for assertions."""

    refresh_result: str = "Completed"
    history: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 400
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def refresh_dataset(self, workspace_id: str, dataset_id: str) -> RefreshOutcome:
        self.calls.append(
            ("refresh_dataset", {"workspace_id": workspace_id, "dataset_id": dataset_id})
        )
        await asyncio.sleep(self.latency_ms / 1000)
        outcome = RefreshOutcome(
            status=self.refresh_result,
            request_id=f"mock-refresh-{len(self.calls)}",
            duration_ms=self.latency_ms,
            detail=(
                "Refresh completed successfully."
                if self.refresh_result == "Completed"
                else f"Refresh ended with status {self.refresh_result}."
            ),
        )
        # Reflect the refresh in history so a follow-up read is consistent.
        self.history.insert(
            0,
            {
                "requestId": outcome.request_id,
                "status": outcome.status,
                "refreshType": "ViaApi",
                "startTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        return outcome

    async def get_refresh_history(
        self, workspace_id: str, dataset_id: str, top: int = 5
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_refresh_history", {"top": top}))
        return self.history[:top]

    async def rebind_gateway(
        self, workspace_id: str, dataset_id: str, gateway_id: str
    ) -> RefreshOutcome:
        self.calls.append(("rebind_gateway", {"gateway_id": gateway_id}))
        await asyncio.sleep(self.latency_ms / 1000)
        return RefreshOutcome(
            status="Completed",
            request_id=f"mock-rebind-{len(self.calls)}",
            duration_ms=self.latency_ms,
            detail=f"Dataset rebound to gateway {gateway_id}.",
        )


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


class LivePowerBIClient:
    """Client-credentials flow against the Power BI REST API.

    Uses httpx directly rather than MSAL so the base install stays dependency
    light; swap in MSAL if you need token caching across processes.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        poll_seconds: int = 5,
        poll_timeout_seconds: int = 300,
    ):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._poll_seconds = poll_seconds
        self._poll_timeout = poll_timeout_seconds
        self._token: str = ""
        self._token_expires_at: float = 0.0

    async def _get_token(self) -> str:
        import httpx

        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": _SCOPE,
                },
            )
            resp.raise_for_status()
            payload = resp.json()

        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    async def refresh_dataset(self, workspace_id: str, dataset_id: str) -> RefreshOutcome:
        import httpx

        started = time.time()
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        base = f"{_API}/groups/{workspace_id}/datasets/{dataset_id}/refreshes"

        async with httpx.AsyncClient(timeout=60) as client:
            # Capture the newest existing refresh id BEFORE triggering, so a
            # previously-completed run can never be mistaken for this one.
            # Polling `$top=1` alone will happily return yesterday's success
            # during the window before the new refresh appears.
            prior_ids = {
                r.get("requestId")
                for r in (await self._history(client, base, headers, 5))
                if r.get("requestId")
            }

            resp = await client.post(base, headers=headers, json={"notifyOption": "NoNotification"})
            if resp.status_code not in (200, 202):
                return RefreshOutcome(
                    status="Failed",
                    duration_ms=int((time.time() - started) * 1000),
                    detail=f"HTTP {resp.status_code}: {resp.text[:500]}",
                )

            # The 202 carries the new request id in a header on most tenants.
            # Fall back to "the first id we have not seen before".
            target_id = resp.headers.get("RequestId") or resp.headers.get("x-ms-request-id") or ""

            deadline = time.time() + self._poll_timeout
            while time.time() < deadline:
                await asyncio.sleep(self._poll_seconds)
                rows = await self._history(client, base, headers, 10)

                row = None
                if target_id:
                    row = next((r for r in rows if r.get("requestId") == target_id), None)
                else:
                    row = next(
                        (r for r in rows if r.get("requestId") not in prior_ids), None
                    )
                if row is None:
                    continue

                status = row.get("status", "Unknown")
                if status in ("Completed", "Failed", "Disabled"):
                    return RefreshOutcome(
                        status=status,
                        request_id=row.get("requestId", ""),
                        duration_ms=int((time.time() - started) * 1000),
                        detail=str(row.get("serviceExceptionJson", ""))[:500],
                    )

        return RefreshOutcome(
            status="Unknown",
            duration_ms=int((time.time() - started) * 1000),
            detail=f"Refresh did not reach a terminal state within {self._poll_timeout}s",
        )

    @staticmethod
    async def _history(client, base: str, headers: dict, top: int) -> list[dict[str, Any]]:
        resp = await client.get(f"{base}?$top={top}", headers=headers)
        resp.raise_for_status()
        return resp.json().get("value", [])

    async def get_refresh_history(
        self, workspace_id: str, dataset_id: str, top: int = 5
    ) -> list[dict[str, Any]]:
        import httpx

        token = await self._get_token()
        url = f"{_API}/groups/{workspace_id}/datasets/{dataset_id}/refreshes?$top={top}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            return resp.json().get("value", [])

    async def rebind_gateway(
        self, workspace_id: str, dataset_id: str, gateway_id: str
    ) -> RefreshOutcome:
        """Bind the dataset to a different gateway.

        Reached only after a human has approved it — the controller enforces
        that, not this client. Kept here rather than in the gate so the gate has
        no idea what it is authorising, which is the correct separation: the
        gate decides *whether*, the client decides *how*.
        """
        import httpx

        started = time.time()
        token = await self._get_token()
        url = f"{_API}/groups/{workspace_id}/datasets/{dataset_id}/Default.BindToGateway"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"gatewayObjectId": gateway_id},
            )
            ok = resp.status_code in (200, 202)
            return RefreshOutcome(
                status="Completed" if ok else "Failed",
                duration_ms=int((time.time() - started) * 1000),
                detail=(
                    f"Dataset rebound to gateway {gateway_id}."
                    if ok
                    else f"HTTP {resp.status_code}: {resp.text[:400]}"
                ),
            )
