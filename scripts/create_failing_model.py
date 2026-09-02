"""Create a semantic model whose refresh genuinely fails.

The demo needs a real failure to remediate, not a simulated one. This model
imports from a SQL Server hostname that does not resolve, so a refresh attempt
produces an authentic Power BI gateway/data-source error rather than a fixture.
"""

from __future__ import annotations

import base64
import json
import sys
import time

import httpx
from _tenant import az_token, required

# Read from configuration like every other operational script. This previously
# read a hardcoded path under one developer's Windows profile, at import time,
# so the module could not even be imported on anyone else's machine -- and it
# put a username into a public repository.
WS = required("POWERBI_WORKSPACE_ID", "the workspace the failing model is created in")
NAME = "Completions Daily Rollup"


def token(resource: str) -> str:
    return az_token(resource)


def b64(obj: dict | str) -> str:
    raw = obj if isinstance(obj, str) else json.dumps(obj)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


model_bim = {
    "name": NAME,
    "compatibilityLevel": 1567,
    "model": {
        "culture": "en-US",
        "defaultPowerBIDataSourceVersion": "powerBI_V3",
        "sourceQueryCulture": "en-US",
        "tables": [
            {
                "name": "Completions",
                "columns": [
                    {"name": "CompletionId", "dataType": "int64", "sourceColumn": "CompletionId"},
                    {"name": "Barrels", "dataType": "double", "sourceColumn": "Barrels"},
                ],
                "partitions": [
                    {
                        "name": "Completions",
                        "mode": "import",
                        "source": {
                            "type": "m",
                            "expression": (
                                "let\n"
                                '    Source = Sql.Database("bi-triage-demo-gw.internal.contoso.com", "CompletionsDW"),\n'
                                '    Data = Source{[Schema="dbo",Item="Completions"]}[Data]\n'
                                "in\n"
                                "    Data"
                            ),
                        },
                    }
                ],
            }
        ],
    },
}

body = {
    "displayName": NAME,
    "description": "Demo model with an unreachable SQL source, so refresh fails for real.",
    "definition": {
        "parts": [
            {"path": "definition.pbism", "payload": b64({"version": "4.2", "settings": {}}),
             "payloadType": "InlineBase64"},
            {"path": "model.bim", "payload": b64(model_bim), "payloadType": "InlineBase64"},
        ]
    },
}

fab = token("https://api.fabric.microsoft.com")
url = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/semanticModels"
r = httpx.post(url, headers={"Authorization": f"Bearer {fab}"}, json=body, timeout=120)
print("create semantic model HTTP", r.status_code)

if r.status_code == 202:
    loc = r.headers.get("Location", "")
    for _ in range(30):
        time.sleep(5)
        poll = httpx.get(loc, headers={"Authorization": f"Bearer {fab}"}, timeout=60)
        state = poll.json().get("status", "")
        print("  provisioning:", state)
        if state in ("Succeeded", "Failed"):
            break
elif r.status_code >= 300:
    print(r.text[:700])
    sys.exit(1)

pbi = token("https://analysis.windows.net/powerbi/api")
ds = httpx.get(
    f"https://api.powerbi.com/v1.0/myorg/groups/{WS}/datasets",
    headers={"Authorization": f"Bearer {pbi}"}, timeout=60,
).json()
for d in ds.get("value", []):
    print(f"dataset: {d['id']}  {d['name']}")
    if d["name"] == NAME:
        # Printed rather than written to a machine-local temp file, so the
        # operator can put it wherever their configuration lives.
        print(f"\nSet POWERBI_DATASET_ID={d['id']} to point the demo at this model.")
