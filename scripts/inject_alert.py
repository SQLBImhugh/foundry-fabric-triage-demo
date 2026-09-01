"""Place a realistic Power BI alert into the monitored mailbox.

Used to exercise ingestion end to end -- the agent finding an alert on its own,
rather than being handed one by an invocation.

Creates the message directly in the inbox rather than sending it. That needs
Mail.ReadWrite instead of Mail.Send, which is the narrower permission: it cannot
email anyone, only write into the one mailbox the app is already scoped to.

The sender is set to Power BI's real no-reply address so the message passes the
same relevance filter a genuine alert would. Nothing here bypasses the filter --
if the filter rejected this, it would be telling us something true.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime

from _tenant import required

MAILBOX = required("GRAPH_MAILBOX", "the mailbox the alert is written into")
TENANT = required("GRAPH_TENANT_ID", "the tenant that owns the mailbox")
CLIENT_ID = required("GRAPH_CLIENT_ID", "the app registration with Mail.ReadWrite")
GRAPH = "https://graph.microsoft.com/v1.0"

SUBJECT = "Power BI: Refresh failed for 'Completions Daily Rollup'"
BODY = """The scheduled refresh for the semantic model 'Completions Daily Rollup' failed.

Error code: DM_GWPipeline_Gateway_TimeoutError
Workspace: BI Triage Demo
Start time: {start}
Duration: 02:00:14

The gateway did not respond within the configured timeout. This is the first
reported failure for this model today.

You are receiving this because you are listed as a contact for this workspace.
"""


def secret() -> str:
    out = subprocess.run(
        ["azd", "env", "get-values"], capture_output=True, text=True, shell=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("GRAPH_CLIENT_SECRET="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def main() -> int:
    import httpx

    client_secret = secret()
    if not client_secret:
        print("No GRAPH_CLIENT_SECRET in the azd environment.")
        return 1

    token = httpx.post(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=60,
    )
    if token.status_code >= 300:
        print(f"Token request failed: HTTP {token.status_code}")
        return 1
    access = token.json()["access_token"]

    now = datetime.now(UTC)
    message = {
        "subject": SUBJECT,
        "body": {"contentType": "Text", "content": BODY.format(start=now.isoformat())},
        "from": {"emailAddress": {
            "address": "no-reply-powerbi@microsoft.com", "name": "Microsoft Power BI"}},
        "sender": {"emailAddress": {
            "address": "no-reply-powerbi@microsoft.com", "name": "Microsoft Power BI"}},
        "toRecipients": [{"emailAddress": {"address": MAILBOX}}],
        "isRead": False,
        "receivedDateTime": now.isoformat().replace("+00:00", "Z"),
    }

    resp = httpx.post(
        f"{GRAPH}/users/{MAILBOX}/mailFolders/inbox/messages",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
        json=message, timeout=60,
    )
    if resp.status_code >= 300:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")[:300]
        except Exception:
            detail = resp.text[:300]
        print(f"Could not create the message: HTTP {resp.status_code} {detail}")
        return 1

    created = resp.json()
    print("Alert placed in the mailbox.")
    print(f"  id:       {created.get('id', '')[:40]}...")
    print(f"  from:     {((created.get('from') or {}).get('emailAddress') or {}).get('address')}")
    print(f"  received: {created.get('receivedDateTime')}")
    print("\nNow let the agent find it:")
    print("  azd ai agent invoke bi-triage-controller \"sweep\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
