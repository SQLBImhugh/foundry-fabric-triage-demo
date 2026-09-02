"""Create the Team and channel the agent posts into.

There was nothing to attach a webhook to: the demo tenant had no Teams at all.
This creates the one the persona walkthrough assumes -- an operations team with
a dedicated alerts channel -- owned by the demo driver and joined by the two
personas, so the channel looks like a place real people work rather than an
empty room.

Idempotent. Team provisioning is asynchronous, so this polls until the channel
actually exists rather than returning on the 202.
"""

from __future__ import annotations

import subprocess
import sys
import time

from _tenant import required

GRAPH = "https://graph.microsoft.com/v1.0"
DOMAIN = required("DEMO_TENANT_DOMAIN", "the domain the Teams channel members belong to")

TEAM_NAME = "Data Platform Operations"
TEAM_DESCRIPTION = "Runs the BI platform. Where refresh failures surface and get decided on."
CHANNEL_NAME = "BI Alerts"
CHANNEL_DESCRIPTION = "Automated triage notifications from the BI request triage agent."

OWNER = required("DEMO_TEAM_OWNER", "the UPN that owns the created team")
MEMBERS = [f"sam.okafor@{DOMAIN}", f"priya.raman@{DOMAIN}"]


def token() -> str:
    return subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://graph.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True, shell=True,
    ).stdout.strip()


def main() -> int:
    import httpx

    headers = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}

    def user_id(upn: str) -> str:
        resp = httpx.get(f"{GRAPH}/users/{upn}?$select=id", headers=headers, timeout=60)
        return resp.json()["id"] if resp.status_code == 200 else ""

    owner_id = user_id(OWNER)
    if not owner_id:
        print(f"Owner {OWNER} not found.")
        return 1

    # --- team ---------------------------------------------------------------
    existing = httpx.get(
        f"{GRAPH}/groups?$filter=displayName eq '{TEAM_NAME}'&$select=id,displayName",
        headers=headers, timeout=60,
    ).json().get("value", [])

    if existing:
        team_id = existing[0]["id"]
        print(f"  team exists: {TEAM_NAME} ({team_id})")
    else:
        resp = httpx.post(
            f"{GRAPH}/teams", headers=headers, timeout=120,
            json={
                "template@odata.bind": f"{GRAPH}/teamsTemplates('standard')",
                "displayName": TEAM_NAME,
                "description": TEAM_DESCRIPTION,
                "members": [{
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": f"{GRAPH}/users('{owner_id}')",
                }],
            },
        )
        if resp.status_code not in (201, 202):
            print(f"  team creation failed: HTTP {resp.status_code} {resp.text[:200]}")
            return 1
        # 202 returns the id in the Location/Content-Location header.
        location = resp.headers.get("Content-Location", "") or resp.headers.get("Location", "")
        team_id = location.split("'")[1] if "'" in location else ""
        print(f"  team creating: {TEAM_NAME} ({team_id})")

        # Provisioning is async. Poll rather than trusting the 202.
        for _ in range(30):
            time.sleep(10)
            check = httpx.get(f"{GRAPH}/teams/{team_id}", headers=headers, timeout=60)
            if check.status_code == 200:
                print("  team provisioned")
                break
        else:
            print("  team did not finish provisioning in time; re-run to continue")
            return 1

    # --- members ------------------------------------------------------------
    # Added through the underlying group rather than /teams/{id}/members. The
    # Teams member API returned 403 for a freshly provisioned team; the group
    # membership endpoint is the same outcome and does not depend on the team's
    # own permission surface having caught up.
    for upn in MEMBERS:
        uid = user_id(upn)
        if not uid:
            print(f"  {upn}: not found, skipped")
            continue
        add = httpx.post(
            f"{GRAPH}/groups/{team_id}/members/$ref", headers=headers, timeout=60,
            json={"@odata.id": f"{GRAPH}/directoryObjects/{uid}"},
        )
        if add.status_code in (204, 201, 200):
            state = "added"
        elif add.status_code == 400 and "already exist" in add.text.lower():
            state = "already a member"
        else:
            state = f"HTTP {add.status_code}"
        print(f"  member {upn.split('@')[0]}: {state}")

    # --- channel ------------------------------------------------------------
    channels = httpx.get(
        f"{GRAPH}/teams/{team_id}/channels?$select=id,displayName",
        headers=headers, timeout=60,
    ).json().get("value", [])
    match = [c for c in channels if c["displayName"] == CHANNEL_NAME]

    if match:
        print(f"  channel exists: {CHANNEL_NAME}")
    else:
        resp = httpx.post(
            f"{GRAPH}/teams/{team_id}/channels", headers=headers, timeout=60,
            json={"displayName": CHANNEL_NAME, "description": CHANNEL_DESCRIPTION,
                  "membershipType": "standard"},
        )
        if resp.status_code not in (200, 201):
            print(f"  channel creation failed: HTTP {resp.status_code} {resp.text[:200]}")
            return 1
        print(f"  channel created: {CHANNEL_NAME}")

    print("\nReady for the webhook. In Teams, signed in as:")
    print(f"  {OWNER}")
    print(f"\n  Team:    {TEAM_NAME}")
    print(f"  Channel: {CHANNEL_NAME}")
    print("\n  ... > Workflows > 'Post to a channel when a webhook request is received'")
    print("  then: python scripts\\setup_teams_webhook.py --set-url \"<url>\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
