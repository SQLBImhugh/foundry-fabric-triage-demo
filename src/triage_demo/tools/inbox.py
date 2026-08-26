"""Inbox ingestion — the trigger.

Two mechanisms, because the customer explicitly asked to see the difference:

* **poll** — a timer reads the mailbox every N seconds. Simple, no public
  endpoint, no subscription lifecycle. Latency = half the poll interval on
  average. This is what the demo uses.
* **subscription** — Graph change notifications POST to a public HTTPS
  endpoint, typically within seconds. Requires a reachable validation endpoint
  and renewal before expiry — the ceiling for mail resources is 4230 minutes
  (just under 3 days), and most teams renew at least daily rather than riding
  the limit. A lapsed renewal is a silent outage, which is the reason to know
  about it before production.

Both produce the same :class:`BIRequest`, so the agent code never knows which
one is wired up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from triage_demo.models import BIRequest

logger = logging.getLogger("triage.inbox")

# Deterministic extraction. Anything the regexes miss stays None and the agent
# has to cope — which is the realistic case and worth showing.
_REPORT = re.compile(r"(?:report|dataset|semantic model)[:\s\"']+([A-Za-z0-9 _\-]{3,60})", re.I)
_DATASET_ID = re.compile(
    r"\b(?:dataset[_ ]?id|datasetId)\W{0,3}([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
_WORKSPACE_ID = re.compile(
    r"\b(?:workspace[_ ]?id|groupId)\W{0,3}([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
_ERROR_CODE = re.compile(r"\b(?:error[_ ]?code|code)\W{0,3}([A-Za-z][A-Za-z0-9_]{3,40})", re.I)


def parse_hints(subject: str, body: str) -> dict[str, str | None]:
    blob = f"{subject}\n{body}"
    report = _REPORT.search(blob)
    dataset = _DATASET_ID.search(blob)
    workspace = _WORKSPACE_ID.search(blob)
    code = _ERROR_CODE.search(blob)
    return {
        "report_name": report.group(1).strip() if report else None,
        "dataset_id": dataset.group(1) if dataset else None,
        "workspace_id": workspace.group(1) if workspace else None,
        "error_code": code.group(1) if code else None,
    }


class InboxSource(Protocol):
    async def fetch(self, limit: int = 10) -> list[BIRequest]: ...


@dataclass
class MockInbox:
    """Reads `.json` messages from a directory, newest filename last."""

    directory: Path
    consumed: list[str] = field(default_factory=list)

    async def fetch(self, limit: int = 10) -> list[BIRequest]:
        directory = Path(self.directory)
        if not directory.exists():
            return []

        out: list[BIRequest] = []
        for path in sorted(directory.glob("*.json")):
            if path.name in self.consumed:
                continue
            out.append(self.load(path))
            self.consumed.append(path.name)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def load(path: str | Path) -> BIRequest:
        raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        hints = parse_hints(raw.get("subject", ""), raw.get("body", ""))
        kwargs: dict[str, Any] = {
            "request_id": raw.get("request_id") or Path(path).stem,
            "sender": raw.get("sender", ""),
            "subject": raw.get("subject", ""),
            "body": raw.get("body", ""),
            "report_name": raw.get("report_name") or hints["report_name"],
            "dataset_id": raw.get("dataset_id") or hints["dataset_id"],
            "workspace_id": raw.get("workspace_id") or hints["workspace_id"],
            "error_code": raw.get("error_code") or hints["error_code"],
            "source": "mock",
        }
        # Let the model's default factory supply `received_at` when absent.
        if raw.get("received_at"):
            kwargs["received_at"] = raw["received_at"]
        return BIRequest(**kwargs)

    def reset(self) -> None:
        self.consumed.clear()


class GraphInbox:
    """Polls a monitored mailbox via Microsoft Graph.

    Requires application permission ``Mail.Read`` (admin consented). Marks
    nothing as read — the demo re-runs must be repeatable — so dedup is by
    message id held in memory.
    """

    _BASE = "https://graph.microsoft.com/v1.0"
    _SCOPE = "https://graph.microsoft.com/.default"

    def __init__(self, *, tenant_id: str, client_id: str, client_secret: str, mailbox: str):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._mailbox = mailbox
        self._seen: set[str] = set()
        self._token: str = ""

    async def _get_token(self) -> str:
        import httpx

        if self._token:
            return self._token
        url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._SCOPE,
                },
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
        return self._token

    async def fetch(self, limit: int = 10) -> list[BIRequest]:
        import httpx

        token = await self._get_token()
        url = (
            f"{self._BASE}/users/{self._mailbox}/mailFolders/inbox/messages"
            f"?$top={limit}&$orderby=receivedDateTime desc"
            f"&$select=id,subject,body,from,receivedDateTime"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            messages = resp.json().get("value", [])

        out: list[BIRequest] = []
        for msg in messages:
            mid = msg.get("id", "")
            if mid in self._seen:
                continue
            self._seen.add(mid)

            subject = msg.get("subject", "") or ""
            body = ((msg.get("body") or {}).get("content") or "")[:20000]
            hints = parse_hints(subject, body)
            out.append(
                BIRequest(
                    request_id=mid,
                    received_at=msg.get("receivedDateTime", ""),
                    sender=((msg.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                    subject=subject,
                    body=body,
                    report_name=hints["report_name"],
                    dataset_id=hints["dataset_id"],
                    workspace_id=hints["workspace_id"],
                    error_code=hints["error_code"],
                    source="graph",
                )
            )
        return out
