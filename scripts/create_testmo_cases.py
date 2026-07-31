import os, json, subprocess, glob, sys

TOKEN = os.environ["TESTMO_API_TOKEN"]
PROJECT_ID = 3
PARENT_ID = 382


def testmo_post(endpoint, payload):
    r = subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://rt2.testmo.net/api/v1/projects/{PROJECT_ID}/{endpoint}",
         "-H", f"Authorization: Bearer {TOKEN}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(payload)],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout)


def testmo_get(endpoint):
    r = subprocess.run(
        ["curl", "-s",
         f"https://rt2.testmo.net/api/v1/projects/{PROJECT_ID}/{endpoint}",
         "-H", f"Authorization: Bearer {TOKEN}"],
        capture_output=True, text=True,
    )
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def resolve_priorities():
    """{lowercase option value: option id} for the case Priority field.

    There is NO /priorities route on this instance (404) — the value list
    lives on GET /projects/{id}/templates: field name "Priority" (type 8),
    options High=1 / Normal=2 / Low=3. Cases carry it as `custom_priority`
    (probe round 5, probe-results/priority-schema.txt).
    """
    data = testmo_get("templates")
    templates = data.get("result", []) if isinstance(data, dict) else []
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


# Authored priorities that have no Testmo option — map onto the scale.
PRIORITY_ALIASES = {"critical": "high", "highest": "high", "medium": "normal"}


trigger_files = sorted(glob.glob("triggers/*.json"))
if not trigger_files:
    print("No trigger files found.")
    sys.exit(0)

os.makedirs("completed", exist_ok=True)

PRIORITIES = resolve_priorities()

for trigger_path in trigger_files:
    filename = os.path.basename(trigger_path)
    print(f"\nProcessing: {filename}")

    with open(trigger_path) as f:
        data = json.load(f)

    ticket_key = data["ticket_key"]
    ticket_title = data["ticket_title"]
    test_cases = data["test_cases"]
    # Optional explicit folder name (else derived from ticket key/title).
    folder_name = data.get("folder_name") or f"{ticket_key} — {ticket_title}"
    # Optional: post directly into an existing folder (skip create-under-parent).
    folder_id_override = data.get("folder_id")
    # Where to create the folder:
    #   - explicit "folder_parent_id" in the trigger wins (null => repository root)
    #   - a named feature folder (e.g. "BYOD - AI") => repository ROOT / main category
    #   - the legacy ticket_key/title auto-folder => under AI-Generated-Test-Cases (PARENT_ID)
    if "folder_parent_id" in data:
        folder_parent_id = data["folder_parent_id"]
    elif data.get("folder_name"):
        folder_parent_id = None  # root (main category)
    else:
        folder_parent_id = PARENT_ID

    result = {
        "status": "error",
        "filename": filename,
        "ticket_key": ticket_key,
        "ticket_title": ticket_title,
    }

    try:
        if folder_id_override:
            folder_id = folder_id_override
            print(f"  Using existing folder ID {folder_id} (override)")
        else:
            # Check if a folder with this name already exists at the target scope
            # (root when folder_parent_id is None) to avoid duplicates.
            list_url = f"https://rt2.testmo.net/api/v1/projects/{PROJECT_ID}/folders"
            if folder_parent_id:
                list_url += f"?parent_id={folder_parent_id}"
            existing_r = subprocess.run(
                ["curl", "-s", list_url, "-H", f"Authorization: Bearer {TOKEN}"],
                capture_output=True, text=True,
            )
            existing_data = json.loads(existing_r.stdout)
            folder_id = None
            for existing_folder in existing_data.get("result", []):
                # Match by name AND scope (root folders have no/zero parent_id).
                same_scope = (existing_folder.get("parent_id") or None) == (folder_parent_id or None)
                if existing_folder["name"] == folder_name and same_scope:
                    folder_id = existing_folder["id"]
                    print(f"  Folder already exists: ID {folder_id}")
                    break

            if folder_id is None:
                new_folder = {"name": folder_name}
                if folder_parent_id:
                    new_folder["parent_id"] = folder_parent_id
                folder_resp = testmo_post("folders", {"folders": {"0": new_folder}})
                if "result" not in folder_resp:
                    raise Exception(f"Folder creation failed: {folder_resp}")
                folder_id = folder_resp["result"][0]["id"]
                scope = f"parent {folder_parent_id}" if folder_parent_id else "root"
                print(f"  Created folder ID {folder_id} ({scope}): {folder_name}")

        # Create each test case
        case_ids = []
        for i, tc in enumerate(test_cases):
            case = {
                "name": tc["name"],
                "folder_id": folder_id,
                "template_id": 2,
                # Prefer an explicit requirements block (description + preconditions);
                # fall back to the legacy test_data field.
                "custom_requirements": tc.get("custom_requirements", tc.get("test_data", "")),
                "custom_steps": [
                    {"text1": s["action"], "text3": s["expected"]}
                    for s in tc["steps"]
                ],
            }
            pname = str(tc.get("priority", "")).strip().lower()
            pname = PRIORITY_ALIASES.get(pname, pname)
            pid = PRIORITIES.get(pname)
            if pid is not None:
                # Case field key is custom_priority (priority_id -> 400).
                case["custom_priority"] = pid
            if tc.get("tags"):
                case["tags"] = tc["tags"]

            case_resp = testmo_post("cases", {"cases": {"0": case}})
            if "result" not in case_resp:
                raise Exception(f"Case {i + 1} creation failed: {case_resp}")
            cid = case_resp["result"][0]["id"]
            case_ids.append(cid)
            print(f"  TC{i + 1} (ID {cid}): {tc['name']}")

        result = {
            "status": "success",
            "filename": filename,
            "ticket_key": ticket_key,
            "ticket_title": ticket_title,
            "folder_id": folder_id,
            "folder_name": folder_name,
            "cases_created": len(case_ids),
            "case_ids": case_ids,
        }
        print(f"  Done: {len(case_ids)} test cases created.")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ERROR: {e}", file=sys.stderr)

    # Write completion file and remove trigger
    completion_path = f"completed/{filename}"
    with open(completion_path, "w") as out:
        json.dump(result, out, indent=2)
    os.remove(trigger_path)
    print(f"  Result -> {completion_path}")

print("\nAll triggers processed.")
