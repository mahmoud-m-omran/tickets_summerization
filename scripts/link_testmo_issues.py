"""Link Testmo cases to Jira issues via the Testmo REST API.

Processes triggers-links/*.json:
    {
      "name": "hint",                      # optional label (defaults to filename)
      "project_id": 3,                     # optional, default 3 (RTGO)
      "integration_id": 1,                 # optional; auto-resolved (first 'jira' match)
      "links": [ {"case_id": 3736, "display_id": "QA-2724"}, ... ]
    }

One PATCH /projects/{pid}/cases per link (each case gets its own issue).
NOTE: the `issues` array on PATCH sets the case's issue references — only
use on cases whose links you own (fresh AI cases); do not run against
cases with hand-made links you want to keep unless you include them too.

Writes completed-links/<filename> with per-link results, removes trigger.
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
    try:
        return code, json.loads(body)
    except Exception:
        return code, {"raw": body[:500]}


def resolve_jira_integration_id():
    code, data = curl("GET", f"{BASE}/issues/connections")
    if code != "200":
        raise Exception(f"GET /issues/connections -> HTTP {code}: {data}")
    for conn in data.get("result", []):
        if "jira" in str(conn.get("integration_name", "")).lower():
            return conn["integration_id"]
    raise Exception("No Jira integration found in /issues/connections")


trigger_files = sorted(glob.glob("triggers-links/*.json"))
if not trigger_files:
    print("No link trigger files found.")
    sys.exit(0)

os.makedirs("completed-links", exist_ok=True)

for trigger_path in trigger_files:
    filename = os.path.basename(trigger_path)
    print(f"\nProcessing: {filename}")

    with open(trigger_path) as f:
        data = json.load(f)

    project_id = data.get("project_id", 3)
    links = data["links"]
    result = {"status": "error", "filename": filename, "links": []}

    try:
        integration_id = data.get("integration_id") or resolve_jira_integration_id()
        print(f"  integration_id={integration_id}, {len(links)} links")

        ok = 0
        for ln in links:
            case_id = ln["case_id"]
            display_id = ln["display_id"]
            code, resp = curl(
                "PATCH", f"{BASE}/projects/{project_id}/cases",
                {"ids": [case_id],
                 "issues": [{"display_id": display_id,
                             "integration_id": integration_id}]},
            )
            linked = (
                code == "200"
                and any(i.get("display_id") == display_id
                        for c in resp.get("result", [])
                        for i in c.get("issues", []))
            )
            result["links"].append({
                "case_id": case_id, "display_id": display_id,
                "http": code, "linked": linked,
                **({} if linked else {"response": resp}),
            })
            ok += linked
            print(f"  case {case_id} <- {display_id}: HTTP {code} linked={linked}")

        result["status"] = "success" if ok == len(links) else "partial"
        result["linked"] = ok
        result["total"] = len(links)
        print(f"  Done: {ok}/{len(links)} linked.")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ERROR: {e}", file=sys.stderr)

    with open(f"completed-links/{filename}", "w") as out:
        json.dump(result, out, indent=2)
    os.remove(trigger_path)
    print(f"  Result -> completed-links/{filename}")

print("\nAll link triggers processed.")
