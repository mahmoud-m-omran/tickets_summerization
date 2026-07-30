"""Probe how Testmo stores case->Jira issue links.

Fetches a case that already has a Jira link via the UI (BYOW 3712) and one
that does not (BYOD 3686), dumps both payloads so the issues field schema
can be compared, plus candidate issue endpoints. Read-only GETs.
Output -> probe-results/issues-schema.txt (committed by the workflow).
"""
import json
import os
import subprocess

TOKEN = os.environ["TESTMO_API_TOKEN"]
BASE = "https://rt2.testmo.net/api/v1"
OUT = []


def get(url):
    r = subprocess.run(
        ["curl", "-s", "-w", "\n%{http_code}", url,
         "-H", f"Authorization: Bearer {TOKEN}"],
        capture_output=True, text=True,
    )
    body, _, code = r.stdout.rpartition("\n")
    return code, body


def show(label, url):
    code, body = get(url)
    OUT.append(f"\n===== {label} -> HTTP {code} =====")
    try:
        OUT.append(json.dumps(json.loads(body), indent=2)[:8000])
    except Exception:
        OUT.append(body[:2000])


# Single-case reads: try both URL shapes Testmo might use.
show("case 3712 (HAS Jira link) via /projects/3/cases/3712",
     f"{BASE}/projects/3/cases/3712")
show("case 3712 via /cases/3712", f"{BASE}/cases/3712")
show("case 3686 (NO link) via /projects/3/cases/3686",
     f"{BASE}/projects/3/cases/3686")

# Candidate issue endpoints.
show("case 3712 issues subresource", f"{BASE}/projects/3/cases/3712/issues")
show("project issues", f"{BASE}/projects/3/issues")

os.makedirs("probe-results", exist_ok=True)
with open("probe-results/issues-schema.txt", "w") as f:
    f.write("\n".join(OUT))
print("\n".join(OUT))
