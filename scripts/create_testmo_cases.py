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
    """{lowercase name: id}. Best-effort; empty dict if unavailable."""
    for ep in ("priorities",):
        data = testmo_get(ep)
        items = data.get("result", []) if isinstance(data, dict) else []
        mapping = {p["name"].strip().lower(): p["id"] for p in items if p.get("name")}
        if mapping:
            return mapping
    return {}


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
    folder_name = f"{ticket_key} — {ticket_title}"
    # Optional: post directly into an existing folder (skip create-under-parent).
    folder_id_override = data.get("folder_id")

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
            # Check if folder already exists to avoid duplicates
            existing_r = subprocess.run(
                ["curl", "-s",
                 f"https://rt2.testmo.net/api/v1/projects/{PROJECT_ID}/folders?parent_id={PARENT_ID}",
                 "-H", f"Authorization: Bearer {TOKEN}"],
                capture_output=True, text=True,
            )
            existing_data = json.loads(existing_r.stdout)
            folder_id = None
            for existing_folder in existing_data.get("result", []):
                if existing_folder["name"] == folder_name:
                    folder_id = existing_folder["id"]
                    print(f"  Folder already exists: ID {folder_id}")
                    break

            if folder_id is None:
                folder_resp = testmo_post(
                    "folders",
                    {"folders": {"0": {"name": folder_name, "parent_id": PARENT_ID}}},
                )
                if "result" not in folder_resp:
                    raise Exception(f"Folder creation failed: {folder_resp}")
                folder_id = folder_resp["result"][0]["id"]
                print(f"  Created folder ID {folder_id}: {folder_name}")

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
            pid = PRIORITIES.get(str(tc.get("priority", "")).strip().lower())
            if pid is not None:
                case["priority_id"] = pid
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
