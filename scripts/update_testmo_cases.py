"""Update EXISTING Testmo cases in place (name / requirements / steps).

Why: a case's Jira ticket can be corrected long after the case was created, and
Testmo is what the automation team actually reads. Case 3697 was the trigger —
its Jira ticket (QA-2694) was corrected after the dev team confirmed COUP_NEU3F3
is NOT a free-SIM promotion, while the Testmo body still told the engineer to
assert "SIM $0.00" (a false expectation).

Trigger shape (triggers-updates/<name>.json):
  {"name": "<name>", "project_id": 3,
   "updates": [
     {"case_id": 3697,
      "name": "<new case name>",                  # optional
      "custom_requirements": "<new body>",        # optional
      "steps": [{"action": "...", "expected": "..."}]   # optional, REPLACES all steps
     }, ...]}

PATCH /api/v1/projects/{id}/cases with {ids:[<case>], <fields>} — the same
endpoint proven for custom_priority. Steps map to custom_steps
[{text1: action, text3: expected}], the shape create_testmo_cases.py uses.

Result -> completed-updates/<name>.json ({status, updated, total, updates[]}).
"""
import glob
import json
import os
import subprocess
import sys

TOKEN = os.environ["TESTMO_API_TOKEN"]
BASE = "https://rt2.testmo.net/api/v1"


def curl(method, url, payload=None):
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", method, url,
           "-H", f"Authorization: Bearer {TOKEN}"]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    return code, body


trigger_files = sorted(glob.glob("triggers-updates/*.json"))
if not trigger_files:
    print("No case-update trigger files found.")
    sys.exit(0)

os.makedirs("completed-updates", exist_ok=True)

for trigger_path in trigger_files:
    filename = os.path.basename(trigger_path)
    print(f"\nProcessing: {filename}")
    with open(trigger_path, encoding="utf-8") as f:
        data = json.load(f)

    project_id = data.get("project_id", 3)
    updates = data["updates"]
    results = []

    for u in updates:
        cid = u["case_id"]
        fields = {"ids": [cid]}
        if u.get("name"):
            fields["name"] = u["name"]
        if u.get("custom_requirements"):
            fields["custom_requirements"] = u["custom_requirements"]
        if u.get("steps"):
            fields["custom_steps"] = [
                {"text1": s.get("action", ""), "text3": s.get("expected", "")}
                for s in u["steps"]
            ]
        changed = [k for k in fields if k != "ids"]
        code, body = curl("PATCH", f"{BASE}/projects/{project_id}/cases", fields)
        ok = code.startswith("2")
        print(f"  case {cid}: PATCH {changed} -> HTTP {code}")
        if not ok:
            print(f"    body: {body[:400]}")
        results.append({"case_id": cid, "changed": changed,
                        "http": code, "updated": ok,
                        "error": None if ok else body[:400]})

    updated = sum(1 for r in results if r["updated"])
    status = "success" if updated == len(updates) else ("partial" if updated else "error")
    out = {"status": status, "filename": filename,
           "updated": updated, "total": len(updates), "updates": results}

    with open(f"completed-updates/{filename}", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    os.remove(trigger_path)
    print(f"  {status}: {updated}/{len(updates)} -> completed-updates/{filename}")

print("\nAll case-update triggers processed.")
