"""Probe how Testmo stores case->Jira issue links. Round 2.

Round 1: no GET route for single cases (/projects/3/cases/{id},
/cases/{id} all 404). Folders list GET works, so try the cases LIST
endpoint and look for an issues field on BYOW cases 3712-3721 (these have
Jira links added via the UI). Read-only GETs.
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


def show(label, url, limit=8000):
    code, body = get(url)
    OUT.append(f"\n===== {label} -> HTTP {code} =====")
    try:
        OUT.append(json.dumps(json.loads(body), indent=2)[:limit])
    except Exception:
        OUT.append(body[:2000])


# Cases list endpoint, various shapes.
show("cases list folder 428 (BYOW, linked)",
     f"{BASE}/projects/3/cases?folder_id=428&per_page=2")
show("cases list plain", f"{BASE}/projects/3/cases?per_page=1")

# If the list works, fetch full first BYOW case separately with a bigger cap
# so nested fields (issues?) aren't truncated away.
code, body = get(f"{BASE}/projects/3/cases?folder_id=428&per_page=100")
OUT.append(f"\n===== full BYOW list -> HTTP {code} =====")
try:
    data = json.loads(body)
    cases = data.get("result", [])
    OUT.append(f"count={len(cases)}")
    if cases:
        c = cases[0]
        OUT.append("keys of first case: " + ", ".join(sorted(c.keys())))
        # Print any key that smells issue/link related, fully.
        for k in sorted(c.keys()):
            lk = k.lower()
            if "issue" in lk or "link" in lk or "jira" in lk or "integration" in lk:
                OUT.append(f"field {k}: " + json.dumps(c[k], indent=2)[:3000])
        OUT.append("first case full: " + json.dumps(c, indent=2)[:6000])
except Exception as e:
    OUT.append(f"parse error: {e}; raw: {body[:1500]}")

os.makedirs("probe-results", exist_ok=True)
with open("probe-results/issues-schema.txt", "w") as f:
    f.write("\n".join(OUT))
print("\n".join(OUT))
