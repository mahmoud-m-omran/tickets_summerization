#!/usr/bin/env python3
"""
RTPL Ready-for-QA Summarizer — GitHub Actions Version

On every run:
  1. Fetches all "Ready for QA" tickets from RTPL updated in the last 7 days.
  2. For NEW tickets (no prior Slack post):
       - Generates a business summary + structured test cases via Claude API.
       - Posts the summary to Slack #tickets_summerization.
       - Stores the Slack thread timestamp and base64-encoded analysis in a
         Jira comment so future runs can recover them without re-calling Claude.
  3. For tickets ALREADY posted to Slack (but not yet in Testmo):
       - Reads the Slack thread for a reply containing the word "approved".
       - If found, creates Testmo test cases under:
           AI-Generated-Test-Cases / {TICKET-KEY} /
       - Adds a Jira comment to prevent re-creation on future runs.
"""

import os
import re
import json
import base64
import logging
import requests
from datetime import datetime, timezone

import anthropic

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Config (from environment / GitHub secrets) ────────────────────────────────

JIRA_CLOUD_ID    = os.environ["JIRA_CLOUD_ID"]
JIRA_BASE        = f"https://api.atlassian.com/ex/jira/{JIRA_CLOUD_ID}/rest/api/3"
JIRA_AUTH        = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])

SLACK_TOKEN      = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL    = os.environ["SLACK_CHANNEL_ID"]
SLACK_HEADERS    = {
    "Authorization": f"Bearer {SLACK_TOKEN}",
    "Content-Type": "application/json",
}

ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TESTMO_BASE      = os.environ["TESTMO_URL"].rstrip("/")
TESTMO_HEADERS   = {
    "Authorization": f"Bearer {os.environ['TESTMO_API_TOKEN']}",
    "Content-Type": "application/json",
}
TESTMO_SUITE_ID  = int(os.environ["TESTMO_SUITE_ID"])

JIRA_JQL = (
    'project = RTPL AND status = "Ready for QA" '
    'AND assignee is not EMPTY AND updated >= -7d '
    'ORDER BY updated DESC'
)

# Sentinel strings written into Jira comments to track processing state.
MARKER_SLACK   = "QA Summary posted to Slack"
MARKER_TESTMO  = "Testmo test cases created by AI agent"

# Regex patterns to extract stored metadata from Jira comments.
RE_SLACK_TS    = re.compile(r'\[slack_ts:(\d+\.\d+)\]')
RE_ANALYSIS    = re.compile(r'\[analysis_b64:([A-Za-z0-9+/=]+)\]')

# ── Jira helpers ──────────────────────────────────────────────────────────────

def jira_get(path, params=None):
    r = requests.get(f"{JIRA_BASE}{path}", auth=JIRA_AUTH, params=params)
    r.raise_for_status()
    return r.json()


def jira_post(path, body):
    r = requests.post(f"{JIRA_BASE}{path}", auth=JIRA_AUTH, json=body)
    r.raise_for_status()
    return r.json()


def jira_search_tickets():
    data = jira_get("/search", {
        "jql": JIRA_JQL,
        "maxResults": 50,
        "fields": "summary,assignee,status,comment,description",
    })
    return data.get("issues", [])


def jira_get_full_issue(key):
    return jira_get(f"/issue/{key}", {
        "fields": "summary,assignee,description,comment",
    })


def jira_add_comment(key, text):
    """Post a plain-text comment to a Jira issue."""
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }],
        }
    }
    jira_post(f"/issue/{key}/comment", payload)


def extract_adf_text(node):
    """Recursively extract plain text from an Atlassian Document Format node."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(extract_adf_text(c) for c in node.get("content", []))
    if isinstance(node, list):
        return "".join(extract_adf_text(n) for n in node)
    return ""


def get_comment_texts(issue):
    """Return list of plain-text strings for all comments on an issue."""
    texts = []
    for c in issue["fields"].get("comment", {}).get("comments", []):
        body = c.get("body", "")
        texts.append(body if isinstance(body, str) else extract_adf_text(body))
    return texts


def get_description_text(issue):
    desc = issue["fields"].get("description") or ""
    if isinstance(desc, str):
        return desc
    return extract_adf_text(desc)

# ── Slack helpers ─────────────────────────────────────────────────────────────

def slack_post(channel, text):
    """Post a message and return its timestamp (thread root ts)."""
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=SLACK_HEADERS,
        json={"channel": channel, "text": text, "mrkdwn": True},
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack post failed: {data.get('error')}")
    return data["ts"]


def slack_channel_history(channel, limit=200):
    r = requests.get(
        "https://slack.com/api/conversations.history",
        headers=SLACK_HEADERS,
        params={"channel": channel, "limit": limit},
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack history failed: {data.get('error')}")
    return data.get("messages", [])


def slack_thread_replies(channel, thread_ts):
    r = requests.get(
        "https://slack.com/api/conversations.replies",
        headers=SLACK_HEADERS,
        params={"channel": channel, "ts": thread_ts},
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack replies failed: {data.get('error')}")
    return data.get("messages", [])


def find_slack_ts(ticket_key, comment_texts):
    """
    Find the Slack message timestamp for a ticket.
    First tries to extract it from a Jira comment (fastest).
    Falls back to scanning recent channel history if not stored.
    """
    for text in comment_texts:
        m = RE_SLACK_TS.search(text)
        if m:
            return m.group(1)
    # Fallback: scan channel history for a message mentioning the ticket key.
    log.info(f"[{ticket_key}] slack_ts not in Jira comment — scanning channel history.")
    for msg in slack_channel_history(SLACK_CHANNEL):
        if ticket_key in msg.get("text", ""):
            log.info(f"[{ticket_key}] Found Slack message ts={msg['ts']} via history scan.")
            return msg["ts"]
    return None


def thread_has_approval(thread_ts):
    """Return True if any reply (after the root post) contains 'approved'."""
    replies = slack_thread_replies(SLACK_CHANNEL, thread_ts)
    for msg in replies[1:]:  # skip index 0 — that's the original post
        if "approved" in msg.get("text", "").lower():
            return True
    return False

# ── Claude — analysis generation ─────────────────────────────────────────────

ANALYSIS_PROMPT = """\
You are a QA analyst. Analyze this Jira ticket and produce a structured QA analysis.

Ticket: {key}
Title:  {title}

Description:
{description}

Comments:
{comments}

Return ONLY valid JSON — no markdown fences, no explanation — in this exact schema:
{{
  "business_summary": "2-3 sentences explaining what this feature/fix does from a user/business perspective. No technical jargon.",
  "test_cases": [
    {{
      "title":           "Action-oriented test case title (max 15 words)",
      "test_data":       "Specific values, user roles, configurations (e.g. amount=$0.00, role=cashier, item count=0)",
      "expected_result": "What should happen when this test passes",
      "priority":        "high | medium | low"
    }}
  ],
  "risk_areas": "2-3 sentences on the riskiest areas that need extra testing attention."
}}

Coverage requirements — include test cases for:
  - Happy path (main successful flow)
  - Edge / boundary cases
  - Negative / error scenarios (invalid input, unauthorized access, missing data)
  - Regression (what existing behaviour could break)
Generate between 6 and 10 test cases total. Make test_data specific and actionable.
"""


def generate_analysis(key, title, description, comment_texts):
    comments_str = "\n---\n".join(comment_texts) if comment_texts else "(none)"
    prompt = ANALYSIS_PROMPT.format(
        key=key,
        title=title,
        description=description[:4000],
        comments=comments_str[:2000],
    )
    response = ANTHROPIC_CLIENT.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip accidental markdown code fences.
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def encode_analysis(analysis):
    return base64.b64encode(json.dumps(analysis).encode()).decode()


def decode_analysis(b64_str):
    return json.loads(base64.b64decode(b64_str).decode())


def load_cached_analysis(comment_texts):
    """Recover the analysis dict stored in a prior Jira comment, if present."""
    for text in comment_texts:
        m = RE_ANALYSIS.search(text)
        if m:
            try:
                return decode_analysis(m.group(1))
            except Exception:
                pass
    return None

# ── Slack message formatter ───────────────────────────────────────────────────

def build_slack_message(key, title, assignee, analysis):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = "\n".join(
        f"| {i+1} | {tc['title']} | {tc['test_data']} | {tc['expected_result']} |"
        for i, tc in enumerate(analysis["test_cases"])
    )
    return (
        f"🔍 *QA Analysis — Auto-generated* | {ts}\n\n"
        f"---\n\n"
        f"🎫 *[{key}] — {title}*\n"
        f"👤 Assigned to: {assignee}\n"
        f"🔗 Jira: https://ezlogic.atlassian.net/browse/{key}\n\n"
        f"*📋 Business Summary*\n{analysis['business_summary']}\n\n"
        f"*🧪 Test Cases*\n\n"
        f"| # | Test Case | Test Data Required | Expected Result |\n"
        f"|---|-----------|-------------------|------------------|\n"
        f"{rows}\n\n"
        f"*⚠️ Risk Areas*\n{analysis['risk_areas']}"
    )

# ── Testmo helpers ────────────────────────────────────────────────────────────

def testmo_get(path):
    r = requests.get(f"{TESTMO_BASE}{path}", headers=TESTMO_HEADERS)
    r.raise_for_status()
    return r.json()


def testmo_post(path, body):
    r = requests.post(f"{TESTMO_BASE}{path}", headers=TESTMO_HEADERS, json=body)
    r.raise_for_status()
    return r.json()


def testmo_list_folders(suite_id):
    data = testmo_get(f"/api/v1/suites/{suite_id}/folders")
    return data.get("data", [])


def testmo_get_or_create_folder(suite_id, name, parent_id=None):
    """
    Return the ID of the folder with the given name (and parent),
    creating it first if it doesn't exist.
    """
    folders = testmo_list_folders(suite_id)
    for f in folders:
        same_name   = f["name"] == name
        same_parent = f.get("parent_id") == parent_id
        if same_name and same_parent:
            log.info(f"Testmo: reusing existing folder '{name}' (id={f['id']})")
            return f["id"]
    # Create it.
    payload = {"name": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    result = testmo_post(f"/api/v1/suites/{suite_id}/folders", payload)
    folder_id = result["data"]["id"]
    log.info(f"Testmo: created folder '{name}' (id={folder_id}, parent={parent_id})")
    return folder_id


def testmo_create_case(suite_id, folder_id, title, test_data, expected_result, priority):
    priority_map = {"high": 2, "medium": 3, "low": 4}
    payload = {
        "suite_id":    suite_id,
        "folder_id":   folder_id,
        "title":       title,
        "template_id": 1,   # Steps template
        "priority_id": priority_map.get(priority.lower(), 3),
        "steps": [{
            "content":  f"Setup / Test Data: {test_data}",
            "expected": expected_result,
            "position": 1,
        }],
    }
    result = testmo_post("/api/v1/cases", payload)
    return result["data"]["id"]


def create_testmo_test_cases(ticket_key, analysis):
    """
    Creates:
      AI-Generated-Test-Cases/
        └── {ticket_key}/
              └── <one test case per entry in analysis["test_cases"]>
    Returns the ticket subfolder ID.
    """
    root_id   = testmo_get_or_create_folder(TESTMO_SUITE_ID, "AI-Generated-Test-Cases")
    folder_id = testmo_get_or_create_folder(TESTMO_SUITE_ID, ticket_key, parent_id=root_id)

    created = 0
    for tc in analysis["test_cases"]:
        try:
            case_id = testmo_create_case(
                suite_id       = TESTMO_SUITE_ID,
                folder_id      = folder_id,
                title          = tc["title"],
                test_data      = tc["test_data"],
                expected_result= tc["expected_result"],
                priority       = tc.get("priority", "medium"),
            )
            log.info(f"[{ticket_key}] Created Testmo case id={case_id}: {tc['title'][:60]}")
            created += 1
        except Exception as exc:
            log.warning(f"[{ticket_key}] Failed to create case '{tc['title'][:60]}': {exc}")

    log.info(f"[{ticket_key}] Testmo: {created}/{len(analysis['test_cases'])} cases created.")
    return folder_id

# ── Main agent loop ───────────────────────────────────────────────────────────

def run():
    log.info("=== RTPL QA Summarizer — run started ===")

    issues = jira_search_tickets()
    if not issues:
        log.info("No Ready-for-QA tickets found. Exiting.")
        return
    log.info(f"Found {len(issues)} ticket(s).")

    for issue in issues:
        key      = issue["key"]
        title    = issue["fields"].get("summary", "(no title)")
        assignee = (issue["fields"].get("assignee") or {}).get("displayName", "Unassigned")

        log.info(f"--- Processing {key}: {title[:60]} ---")

        # Fetch full issue so we have the description and all comments.
        full    = jira_get_full_issue(key)
        desc    = get_description_text(full)
        c_texts = get_comment_texts(full)

        has_slack  = any(MARKER_SLACK  in t for t in c_texts)
        has_testmo = any(MARKER_TESTMO in t for t in c_texts)

        # ── Step A: Generate analysis + post to Slack (first time only) ───────
        if not has_slack:
            log.info(f"[{key}] No Slack post yet — generating analysis.")
            try:
                analysis = generate_analysis(key, title, desc, c_texts)
            except Exception as exc:
                log.error(f"[{key}] Claude analysis failed: {exc}")
                continue

            message = build_slack_message(key, title, assignee, analysis)
            try:
                msg_ts = slack_post(SLACK_CHANNEL, message)
                log.info(f"[{key}] Slack message posted (ts={msg_ts}).")
            except Exception as exc:
                log.error(f"[{key}] Slack post failed: {exc}")
                continue

            # Store slack_ts and encoded analysis in the Jira comment
            # so the next run can find the thread and skip re-generating.
            analysis_b64 = encode_analysis(analysis)
            comment = (
                f"✅ QA Summary posted to Slack (#tickets_summerization) by AI agent. "
                f"[slack_ts:{msg_ts}] "
                f"[analysis_b64:{analysis_b64}]"
            )
            try:
                jira_add_comment(key, comment)
            except Exception as exc:
                log.error(f"[{key}] Failed to add Jira comment: {exc}")

        # ── Step B: Check for Slack 'approved' → create Testmo cases ─────────
        elif not has_testmo:
            slack_ts = find_slack_ts(key, c_texts)
            if not slack_ts:
                log.warning(f"[{key}] Cannot find Slack thread ts — skipping Testmo step.")
                continue

            try:
                approved = thread_has_approval(slack_ts)
            except Exception as exc:
                log.error(f"[{key}] Failed to read Slack thread: {exc}")
                continue

            if not approved:
                log.info(f"[{key}] No 'approved' reply in Slack thread yet.")
                continue

            log.info(f"[{key}] 'approved' detected — creating Testmo test cases.")

            # Re-use cached analysis if available; otherwise regenerate.
            analysis = load_cached_analysis(c_texts)
            if not analysis:
                log.info(f"[{key}] No cached analysis found — regenerating via Claude.")
                try:
                    analysis = generate_analysis(key, title, desc, c_texts)
                except Exception as exc:
                    log.error(f"[{key}] Re-generation failed: {exc}")
                    continue

            try:
                folder_id = create_testmo_test_cases(key, analysis)
                jira_add_comment(
                    key,
                    f"✅ Testmo test cases created by AI agent. "
                    f"Location: AI-Generated-Test-Cases/{key} "
                    f"[testmo_folder:{folder_id}]",
                )
                log.info(f"[{key}] Testmo done; Jira marked.")
            except Exception as exc:
                log.error(f"[{key}] Testmo creation failed: {exc}")

        else:
            log.info(f"[{key}] Already fully processed (Slack ✓, Testmo ✓). Skipping.")

    log.info("=== Run complete ===")


if __name__ == "__main__":
    run()
