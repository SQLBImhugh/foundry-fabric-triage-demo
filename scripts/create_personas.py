"""Create the demo persona accounts and licence them.

The persona walkthrough is written around two people: an analyst who reads the
report and an engineer who gets paged. Giving them real accounts means the demo
can show a real mailbox, a real Teams presence and a real Power BI viewer --
rather than names on a slide.

Idempotent: re-running updates rather than duplicating, so it is safe to run
after a partial failure.

Passwords are generated, printed once, and never written to the repo. They are
demo accounts in a demo tenant, but a password in a git history is a password in
a git history.
"""

from __future__ import annotations

import secrets
import string
import subprocess
import sys

DOMAIN = "mngenvmcap777813.onmicrosoft.com"
GRAPH = "https://graph.microsoft.com/v1.0"

# Licences: E5 (no Teams) plus the standalone Teams SKU this tenant uses, and
# Power BI so the analyst can actually open a report.
LICENCES = [
    "18a4bd3f-0b5b-4887-b04f-61dd0ee15f5e",  # Microsoft_365_E5_(no_Teams)
    "7e31c0d9-9551-471d-836f-32ee72be4a01",  # Microsoft_Teams_Enterprise_New
    "a403ebcc-fae0-4ca2-8c8c-7a907fd6c235",  # POWER_BI_STANDARD
]

PERSONAS = [
    {
        "alias": "priya.raman",
        "displayName": "Priya Raman",
        "givenName": "Priya",
        "surname": "Raman",
        "jobTitle": "Finance Analyst",
        "department": "Finance",
        "usageLocation": "US",
    },
    {
        "alias": "sam.okafor",
        "displayName": "Sam Okafor",
        "givenName": "Sam",
        "surname": "Okafor",
        "jobTitle": "BI Platform Engineer",
        "department": "Data Platform",
        "usageLocation": "US",
    },
]


def token() -> str:
    return subprocess.run(
        ["az", "account", "get-access-token", "--resource", GRAPH.rsplit("/v1.0", 1)[0],
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True, shell=True,
    ).stdout.strip()


def password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(20))


def main() -> int:
    import httpx

    headers = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    created: list[tuple[str, str]] = []

    for persona in PERSONAS:
        upn = f"{persona['alias']}@{DOMAIN}"

        existing = httpx.get(f"{GRAPH}/users/{upn}", headers=headers, timeout=60)
        if existing.status_code == 200:
            user_id = existing.json()["id"]
            print(f"  {persona['displayName']:<14} exists ({user_id})")
        else:
            pwd = password()
            body = {
                "accountEnabled": True,
                "displayName": persona["displayName"],
                "givenName": persona["givenName"],
                "surname": persona["surname"],
                "jobTitle": persona["jobTitle"],
                "department": persona["department"],
                "usageLocation": persona["usageLocation"],
                "mailNickname": persona["alias"].replace(".", ""),
                "userPrincipalName": upn,
                "passwordProfile": {
                    "password": pwd,
                    # A demo account that never rotates its initial password is
                    # a demo account someone reuses for something real.
                    "forceChangePasswordNextSignIn": True,
                },
            }
            resp = httpx.post(f"{GRAPH}/users", headers=headers, json=body, timeout=60)
            if resp.status_code >= 300:
                print(f"  {persona['displayName']:<14} FAILED {resp.status_code}: "
                      f"{resp.text[:200]}")
                continue
            user_id = resp.json()["id"]
            created.append((upn, pwd))
            print(f"  {persona['displayName']:<14} created ({user_id})")

        lic = httpx.post(
            f"{GRAPH}/users/{user_id}/assignLicense",
            headers=headers,
            json={"addLicenses": [{"skuId": s, "disabledPlans": []} for s in LICENCES],
                  "removeLicenses": []},
            timeout=120,
        )
        if lic.status_code >= 300:
            detail = ""
            try:
                detail = lic.json().get("error", {}).get("message", "")[:160]
            except Exception:
                detail = lic.text[:160]
            print(f"    licences: HTTP {lic.status_code} {detail}")
        else:
            print("    licences: assigned")

    if created:
        print("\n  Initial passwords (shown once, not stored anywhere):")
        for upn, pwd in created:
            print(f"    {upn}  {pwd}")
        print("  All are set to force a change at first sign-in.")

    print("\n  Mailboxes take a few minutes to provision after licensing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
