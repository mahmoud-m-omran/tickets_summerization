"""Probe Testmo case PRIORITY support. Round 4.

Rounds 1-3 solved case->Jira issue links (see git history).
Round 4: priorities never stick — create_testmo_cases.py resolves
GET /projects/3/priorities into a {name: id} map, and that map is
evidently empty at run time, so priority_id is never sent.

Questions this probe answers:
1. What does GET /projects/{id}/priorities actually return here?
2. Is there a global GET /priorities (or another fields endpoint)
   that exposes the priority value list + ids?
3. What priority-ish keys does a real case carry? (LIST cases in
   folder 431 and dump case 3751's full JSON.)
4. Can PATCH /projects/3/cases set the priority on case 3751
   (Astock happy-path tablet, authored priority: Critical)?
   Try the documented shape {ids, priority_id} with the id
   discovered in 1-3; verify by re-listing the case.
5. Where does folder 431 live (parent_id)? — confirms whether the
   unpushed root-folder fix is needed retroactively.

Output -> probe-results/priority-schema.txt (committed by the workflow).
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


def jbody(body):
    try:
        return json.loads(body)
    except Exception:
        return {}


# 1-2. Candidate endpoints for the priority value list.
priority_map = {}
for label, url in [
    ("GET /projects/3/priorities", f"{BASE}/projects/3/priorities"),
    ("GET /priorities", f"{BASE}/priorities"),
    ("GET /projects/3/case-fields", f"{BASE}/projects/3/case-fields"),
    ("GET /case-fields", f"{BASE}/case-fields"),
    ("GET /projects/3/templates", f"{BASE}/projects/3/templates"),
]:
    code, body = curl("GET", url)
    show(label, code, body, limit=4000)
    if code == "200" and not priority_map:
        data = jbody(body)
        items = data.get("result", []) if isinstance(data, dict) else []
        # direct priorities list
        cand = {str(p.get("name", "")).strip().lower(): p.get("id")
                for p in items if isinstance(p, dict) and p.get("name") and p.get("id")}
        if cand and "priorities" in url:
            priority_map = cand
        # fields/templates: hunt nested option lists mentioning priority
        for it in items if isinstance(items, list) else []:
            s = json.dumps(it).lower()
            if "priority" in s:
                OUT.append(f"\n--- priority-ish entry in {label}: ---")
                OUT.append(json.dumps(it, indent=2)[:3000])

# 3. Dump a real case's fields (folder 431, case 3751).
code, body = curl("GET", f"{BASE}/projects/3/cases?folder_id=431&per_page=5")
data = jbody(body)
cases = data.get("result", []) if isinstance(data, dict) else []
target = next((c for c in cases if c.get("id") == 3751), cases[0] if cases else None)
if target is not None:
    show("case 3751 (or first in folder 431) full JSON", code, json.dumps(target))
    prio_keys = {k: v for k, v in target.items() if "prio" in k.lower()}
    OUT.append(f"\npriority-ish keys on the case: {prio_keys}")
else:
    show("GET cases?folder_id=431", code, body, limit=2000)

# 5. Folder 431 location.
code, body = curl("GET", f"{BASE}/projects/3/folders")
data = jbody(body)
for f in data.get("result", []) if isinstance(data, dict) else []:
    if f.get("id") in (426, 430, 431) or f.get("name") in ("Astock - AI", "HINT - AI", "BYOD - AI"):
        OUT.append(f"\nfolder {f.get('id')} {f.get('name')!r} parent_id={f.get('parent_id')}")

# 4. Guarded live PATCH: set 3751 to Critical (authored priority).
patch_id = None
for name in ("critical", "highest", "high"):
    if name in priority_map:
        patch_id = priority_map[name]
        OUT.append(f"\nUsing priority_map[{name!r}] = {patch_id}")
        break
if patch_id is None and target is not None and "priority_id" in target:
    OUT.append("\nNo value list found, but case carries priority_id — trying raw ids 1..4 read-back style is unsafe; PATCH with 1 as a probe.")
    patch_id = 1

if patch_id is not None:
    code, body = curl("PATCH", f"{BASE}/projects/3/cases",
                      {"ids": [3751], "priority_id": patch_id})
    show(f"PATCH /projects/3/cases ids=[3751] priority_id={patch_id}", code, body)
    code, body = curl("GET", f"{BASE}/projects/3/cases?folder_id=431&per_page=5")
    data = jbody(body)
    after = next((c for c in data.get("result", []) if c.get("id") == 3751), None)
    if after is not None:
        OUT.append(f"\nafter PATCH, 3751 priority-ish keys: "
                   f"{ {k: v for k, v in after.items() if 'prio' in k.lower()} }")
else:
    OUT.append("\nNo priority id discovered anywhere — PATCH skipped.")

os.makedirs("probe-results", exist_ok=True)
with open("probe-results/priority-schema.txt", "w") as f:
    f.write("\n".join(OUT))
print("\n".join(OUT))
