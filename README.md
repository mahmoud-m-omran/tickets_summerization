# tickets_summerization — RT Platform QA Automation

GitHub Actions workflow for creating Testmo test cases from Slack-approved QA summaries.

## How it works

1. **CCR Routine** pushes a `triggers/RTPL-XXXX-{slack_ts}.json` file via GitHub API
2. **`create-testmo-cases` workflow** fires automatically, creates the Testmo folder + test cases, writes `completed/RTPL-XXXX-{slack_ts}.json`
3. **Next CCR Routine run** reads the completion file and posts the ✅ Slack notification

## Required GitHub secret

| Secret | Value |
|--------|-------|
| `TESTMO_API_TOKEN` | Testmo API token for rt2.testmo.net |

## Trigger file format

Path: `triggers/RTPL-XXXX-{slack_ts_nodot}.json`

```json
{
  "ticket_key": "RTPL-2252",
  "ticket_title": "BE - Mock CheckPortInEligibility (Metro Proxy)",
  "slack_channel_id": "C0B1V5DH4AY",
  "slack_thread_ts": "1778072441.182899",
  "test_cases": [
    {
      "name": "TC1 — Verify eligible response for standard MDN prefix",
      "test_data": "MDN: 4155550000\nEnableEndpointMocking=true\nConfigCat flag=true",
      "steps": [
        {"action": "Enable both mocking flags", "expected": "Mock is armed"},
        {"action": "POST to CheckPortInEligibility with MDN 4155550000", "expected": "portableInd is not N (eligible)"}
      ]
    }
  ]
}
```

## Completion file format

Path: `completed/RTPL-XXXX-{slack_ts_nodot}.json`

Success:
```json
{
  "status": "success",
  "folder_id": 397,
  "folder_name": "RTPL-2252 — BE - Mock CheckPortInEligibility (Metro Proxy)",
  "cases_created": 5,
  "case_ids": [3502, 3503, 3504, 3505, 3506]
}
```

Failure:
```json
{
  "status": "error",
  "error": "Testmo folder creation failed: ...",
  "ticket_key": "RTPL-2252"
}
```