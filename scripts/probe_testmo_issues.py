"""Probe Testmo case->Jira issue linking. Round 3.

Round 1: no GET route for single cases; no /issues routes under projects.
Round 2: cases LIST works but exposes no issues field.
Round 3: official docs (support.testmo.com, "Issues" + "Cases" API reference)
document GET /issues/connections and an `issues` array on POST/PATCH
/projects/{id}/cases ({display_id, integration_id}; connection_project_id
only for GitHub/GitLab). Announced for the "January release" — check
whether it is live on rt2.testmo.net:

1. GET /api/v1/issues/connections (read-only) -> dump connections,
   find the Jira integration id.
2. If found: PATCH /projects/3/cases ids=[3736] issues=[{display_id:
   "QA-2724", integration_id:<jira>}] — live validation on HINT-001,
   which SHOULD be linked to QA-2724 anyway (mapping is 3736..3750 <->
   QA-2724..QA-2738 in order).
Output -> probe-results/issues-schema.txt (committed by the workflow).
"""
import json
import os
import subprocess

TOKEN = os.environ["TESTMO_API_TOKEN"]
BASE = "https://rt2.testmo.net/api/v1"
OUT = []


def curl(method, url, payload=None):
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", method, url,
           "-H", f"Authorization: Bearer {TOKEN}"]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    return code, body


def show(label, code, body, limit=8000):
    OUT.append(f"\n===== {label} -> HTTP {code} =====")
    try:
        OUT.append(json.dumps(json.loads(body), indent=2)[:limit])
    except Exception:
        OUT.append(body[:2000])


# 1. Issue tracker connections (read-only).
code, body = curl("GET", f"{BASE}/issues/connections")
show("GET /issues/connections", code, body)

jira_integration_id = None
if code == "200":
    try:
        for conn in json.loads(body).get("result", []):
            if "jira" in str(conn.get("integration_name", "")).lower():
                jira_integration_id = conn["integration_id"]
                OUT.append(
                    f"\nJira integration_id={jira_integration_id} "
                    f"connection_id={conn.get('connection_id')} "
                    f"project={conn.get('connection_project_name')!r} "
                    f"connection_project_id={conn.get('connection_project_id')}"
                )
                break
    except Exception as e:
        OUT.append(f"parse error: {e}")

# 2. Live PATCH validation: link HINT-001 (case 3736) to QA-2724.
if jira_integration_id is not None:
    payload = {
        "ids": [3736],
        "issues": [{"display_id": "QA-2724",
                    "integration_id": jira_integration_id}],
    }
    code, body = curl("PATCH", f"{BASE}/projects/3/cases", payload)
    show("PATCH /projects/3/cases (3736 <- QA-2724)", code, body)
else:
    OUT.append("\nNo Jira connection found (endpoint missing or empty) — PATCH skipped.")

os.makedirs("probe-results", exist_ok=True)
with open("probe-results/issues-schema.txt", "w") as f:
    f.write("\n".join(OUT))
print("\n".join(OUT))
