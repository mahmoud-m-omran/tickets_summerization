"""Probe Testmo case PRIORITY support. Round 5.

Round 4 findings (probe-results/priority-schema.txt @ 53d207b run):
- /priorities endpoints: 404 everywhere -> create script's map was empty.
- GET /projects/3/templates WORKS: template 2 "Case (steps)" has field
  id=2 "Priority" type=8 options High=1 / Normal=2 / Low=3.
- GET /projects/3/cases?folder_id=431 -> 422 (wrong param).
- Folders: 426 BYOD at root; 430 HINT + 431 Astock under 382.

Round 5:
1. Show the 422 body for cases list; retry with ?folder=431 and no
   filter + per_page, to find the right list param and dump case 3751's
   real field keys.
2. PATCH /projects/3/cases {ids:[3751], priority_id:1}  (High).
   If that 4xxes, retry key variants: custom_priority, priority.
3. Read the case back and show its priority-ish keys.
Output -> probe-results/priority-schema.txt
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


def show(label, code, body, limit=6000):
    OUT.append(f"\n===== {label} -> HTTP {code} =====")
    try:
        OUT.append(json.dumps(json.loads(body), indent=2)[:limit])
    except Exception:
        OUT.append(body[:2000])


def jbody(body):
    try:
        return json.loads(body)
    except Exception:
        return {}


def find_case(cases, cid=3751):
    return next((c for c in cases if c.get("id") == cid), None)


# 1. Find the working cases-list call.
case = None
for label, url in [
    ("GET cases?folder_id=431 (show 422 body)", f"{BASE}/projects/3/cases?folder_id=431"),
    ("GET cases?folder=431", f"{BASE}/projects/3/cases?folder=431"),
    ("GET cases (no filter, page 1)", f"{BASE}/projects/3/cases?per_page=100&page=38"),
]:
    code, body = curl("GET", url)
    data = jbody(body)
    cases = data.get("result", []) if isinstance(data, dict) else []
    hit = find_case(cases)
    if hit is not None:
        case = hit
        show(label + " [case 3751 found]", code, json.dumps(hit))
        break
    show(label, code, body, limit=1500)

# Fallback: walk pages of the unfiltered list until 3751 appears.
if case is None:
    for page in range(1, 60):
        code, body = curl("GET", f"{BASE}/projects/3/cases?per_page=100&page={page}")
        data = jbody(body)
        cases = data.get("result", []) if isinstance(data, dict) else []
        if not cases:
            break
        hit = find_case(cases)
        if hit is not None:
            case = hit
            show(f"case 3751 found on page {page}", code, json.dumps(hit))
            break

if case is not None:
    OUT.append(f"\npriority-ish keys BEFORE: "
               f"{ {k: v for k, v in case.items() if 'prio' in k.lower()} }")
    OUT.append(f"case top-level keys: {sorted(case.keys())}")

# 2. PATCH attempts — stop at first 2xx.
patched_shape = None
for label, payload in [
    ("priority_id", {"ids": [3751], "priority_id": 1}),
    ("custom_priority", {"ids": [3751], "custom_priority": 1}),
    ("priority", {"ids": [3751], "priority": 1}),
]:
    code, body = curl("PATCH", f"{BASE}/projects/3/cases", payload)
    show(f"PATCH cases {label}=1", code, body, limit=1500)
    if code.startswith("2"):
        patched_shape = label
        break

OUT.append(f"\npatched_shape = {patched_shape!r}")

# 3. Read back.
if patched_shape and case is not None:
    code, body = curl("GET", f"{BASE}/projects/3/cases?per_page=100&page=1")
    # cheap read-back: walk pages again for 3751
    after = None
    for page in range(1, 60):
        code, body = curl("GET", f"{BASE}/projects/3/cases?per_page=100&page={page}")
        data = jbody(body)
        cases = data.get("result", []) if isinstance(data, dict) else []
        if not cases:
            break
        after = find_case(cases)
        if after is not None:
            break
    if after is not None:
        OUT.append(f"\npriority-ish keys AFTER: "
                   f"{ {k: v for k, v in after.items() if 'prio' in k.lower()} }")

os.makedirs("probe-results", exist_ok=True)
with open("probe-results/priority-schema.txt", "w") as f:
    f.write("\n".join(OUT))
print("\n".join(OUT))
