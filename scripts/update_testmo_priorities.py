"""Set the Priority field on existing Testmo cases.

Trigger shape (triggers-priorities/<name>.json):
  {"name": "<name>", "project_id": 3,
   "updates": [{"case_id": 3751, "priority": "Critical"}, ...]}

Priority names are the authored Jira-side names; they are resolved against
the project's case template Priority options (High=1 / Normal=2 / Low=3 on
rt2) with Critical/Highest->High and Medium->Normal aliasing. The case
field key is `custom_priority` — PATCH /projects/{id}/cases accepts
{ids: [...], custom_priority: <option id>} (probe round 5).

Cases with the same resolved option id are batched into one PATCH.
Result -> completed-priorities/<name>.json ({status, updated, total, updates[]}).
"""
import glob
import json
import os
import subprocess
import sys

TOKEN = os.environ["TESTMO_API_TOKEN"]
BASE = "https://rt2.testmo.net/api/v1"

PRIORITY_ALIASES = {"critical": "high", "highest": "high", "medium": "normal"}


def curl(method, url, payload=None):
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", method, url,
           "-H", f"Authorization: Bearer {TOKEN}"]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    return code, body


def resolve_priorities(project_id):
    code, body = curl("GET", f"{BASE}/projects/{project_id}/templates")
    if not code.startswith("2"):
        return {}
    try:
        templates = json.loads(body).get("result", [])
    except Exception:
        return {}
    for tpl in templates:
        if tpl.get("id") != 2 and not tpl.get("is_default"):
            continue
        for field in tpl.get("fields", []):
            if field.get("name") == "Priority":
                return {
                    str(o.get("value", "")).strip().lower(): o.get("id")
                    for o in field.get("options", [])
                    if o.get("value") and o.get("id") is not None
                }
    return {}


trigger_files = sorted(glob.glob("triggers-priorities/*.json"))
if not trigger_files:
    print("No priority trigger files found.")
    sys.exit(0)

os.makedirs("completed-priorities", exist_ok=True)

for trigger_path in trigger_files:
    filename = os.path.basename(trigger_path)
    print(f"\nProcessing: {filename}")
    with open(trigger_path) as f:
        data = json.load(f)

    project_id = data.get("project_id", 3)
    updates = data["updates"]
    options = resolve_priorities(project_id)
    print(f"  Priority options: {options}")

    results = []
    # Group case ids by resolved option id -> one PATCH per option value.
    groups = {}
    for u in updates:
        pname = str(u.get("priority", "")).strip().lower()
        pname = PRIORITY_ALIASES.get(pname, pname)
        pid = options.get(pname)
        if pid is None:
            results.append({**u, "linked": False, "error": f"unknown priority {u.get('priority')!r}"})
            continue
        groups.setdefault(pid, []).append(u)

    for pid, members in sorted(groups.items()):
        ids = [m["case_id"] for m in members]
        code, body = curl("PATCH", f"{BASE}/projects/{project_id}/cases",
                          {"ids": ids, "custom_priority": pid})
        ok = code.startswith("2")
        print(f"  PATCH custom_priority={pid} ids={ids} -> HTTP {code}")
        for m in members:
            results.append({**m, "custom_priority": pid, "http": code, "updated": ok})

    updated = sum(1 for r in results if r.get("updated"))
    status = "success" if updated == len(updates) else ("partial" if updated else "error")
    out = {"status": status, "filename": filename,
           "updated": updated, "total": len(updates), "updates": results}

    with open(f"completed-priorities/{filename}", "w") as f:
        json.dump(out, f, indent=2)
    os.remove(trigger_path)
    print(f"  {status}: {updated}/{len(updates)} -> completed-priorities/{filename}")

print("\nAll priority triggers processed.")
