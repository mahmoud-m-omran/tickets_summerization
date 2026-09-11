"""Dump EVERY Testmo case in a project — id, name, folder, steps, linked Jira keys.

Why: the feature-testcases-to-jira skill needs to know what already exists BEFORE
it authors anything. On 2026-09-11 two BYOT tickets (QA-2825, QA-2826) were
created that duplicated QA-2684 and QA-2686 — both Done, both under the same
epic. Nothing in the flow looked at what was already there.

Testmo is the right surface to read: every ticket this flow creates gets a Testmo
case, AND Testmo holds cases that never got a ticket at all (folder 429 held ten
BYOT cases with no Jira issue), which a Jira-only check cannot see. Cases also
carry their linked Jira keys, so one dump covers both.

Trigger shape (triggers-dumps/<name>.json):
  {"name": "<name>", "project_id": 3}
  optional: "folder_ids": [429, 426]   # restrict; omit for the whole project

Result -> completed-dumps/<name>.json:
  {"status", "project_id", "count",
   "cases": [{"id", "name", "folder_id", "actions": [...], "issues": ["QA-2684"]}]}

The list endpoint's paging param was unknown (an earlier probe got 422 on
?folder_id=), so _list_cases tries the documented shapes in order and keeps the
first that returns 200.
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


def _json(body):
    try:
        return json.loads(body)
    except Exception:
        return None


def _list_cases(project_id, page):
    """Return (cases, raw) for one page, trying known param shapes."""
    attempts = [
        f"{BASE}/projects/{project_id}/cases?page={page}&per_page=100",
        f"{BASE}/projects/{project_id}/cases?page={page}",
        f"{BASE}/projects/{project_id}/cases",
    ]
    for url in attempts:
        code, body = curl("GET", url)
        if code == "200":
            data = _json(body)
            if data is None:
                continue
            # Testmo wraps collections in {"result": [...]} on some endpoints.
            if isinstance(data, dict):
                for key in ("result", "cases", "data"):
                    if isinstance(data.get(key), list):
                        return data[key], data
                return [], data
            if isinstance(data, list):
                return data, data
        print(f"  list attempt {url.split('/cases')[1] or '(bare)'} -> {code}")
    return None, None


def _steps_of(case):
    """Actions only — the gate compares rep actions, not expectations."""
    actions = []
    for step in case.get("custom_steps") or []:
        if isinstance(step, dict):
            text = step.get("text1") or step.get("action") or ""
            if text:
                actions.append(text)
    return actions


def _issues_of(case):
    # Testmo stores the Jira key as `display_id` (that is what
    # link_testmo_issues.py PATCHes and reads back). Checking only
    # name/key/external_id silently produced an empty list for every case even
    # where the link existed — verified 2026-09-11 on 3781-3784.
    keys = []
    for issue in case.get("issues") or []:
        if isinstance(issue, dict):
            k = (issue.get("display_id") or issue.get("name")
                 or issue.get("key") or issue.get("external_id"))
            if k:
                keys.append(str(k))
        elif issue:
            keys.append(str(issue))
    return keys


trigger_files = sorted(glob.glob("triggers-dumps/*.json"))
if not trigger_files:
    print("No dump trigger files found.")
    sys.exit(0)

os.makedirs("completed-dumps", exist_ok=True)

for trigger_path in trigger_files:
    filename = os.path.basename(trigger_path)
    print(f"\nProcessing: {filename}")
    with open(trigger_path, encoding="utf-8") as f:
        data = json.load(f)

    project_id = data.get("project_id", 3)
    keep_folders = data.get("folder_ids")
    collected, page, seen = [], 1, set()

    while page <= 60:
        cases, raw = _list_cases(project_id, page)
        if cases is None:
            print(f"  FAILED to list cases on page {page}")
            break
        fresh = [c for c in cases if c.get("id") not in seen]
        if not fresh:
            break
        for c in fresh:
            seen.add(c.get("id"))
        collected.extend(fresh)
        print(f"  page {page}: {len(fresh)} case(s), {len(collected)} total")
        if len(cases) < 100:
            break
        page += 1

    out = []
    for c in collected:
        folder = c.get("folder_id") or c.get("folder")
        if keep_folders and folder not in keep_folders:
            continue
        actions = _steps_of(c)
        # The list payload carries steps but NOT linked issues (verified
        # 2026-09-11: cases 3781-3784 came back with issues [] minutes after
        # being linked to QA-2824..2827). Always fetch the detail for the Jira
        # keys — a snapshot that cannot name the existing ticket makes the
        # prior-art gate's abort message useless to act on.
        code, body = curl("GET", f"{BASE}/cases/{c.get('id')}")
        if code == "200":
            detail = _json(body) or {}
            detail = detail.get("result", detail)
            actions = actions or _steps_of(detail)
            if not c.get("issues"):
                c["issues"] = detail.get("issues")
        out.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "folder_id": folder,
            "actions": actions,
            "issues": _issues_of(c),
        })

    # Diagnostic: if links come back empty again, this says whether the detail
    # endpoint carries an `issues` field at all, and under which keys — so the
    # next fix is based on the payload instead of another guess.
    diag = {}
    for probe_id in (3781, 3784):
        code, body = curl("GET", f"{BASE}/cases/{probe_id}")
        detail = (_json(body) or {})
        detail = detail.get("result", detail)
        diag[str(probe_id)] = {
            "http": code,
            "top_level_keys": sorted(detail.keys()) if isinstance(detail, dict) else None,
            "issues_raw": detail.get("issues") if isinstance(detail, dict) else None,
        }

    result = {
        "status": "success" if out else "empty",
        "filename": filename,
        "project_id": project_id,
        "count": len(out),
        "link_diagnostic": diag,
        "cases": out,
    }
    dest = os.path.join("completed-dumps", filename)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  -> {dest} ({len(out)} case(s))")

    try:
        os.remove(trigger_path)
        print(f"  removed trigger {trigger_path}")
    except OSError as exc:
        print(f"  could not remove trigger: {exc}")
