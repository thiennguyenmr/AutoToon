# USER.md - About Your Human

- **Name:** Felix
- **Timezone:** Asia/Ho_Chi_Minh
- **Project:** PhishingMKT — Docker stack with n8n, OpenClaw (you), vLLM (gpt-oss-20b), browserless chromium, PostgreSQL

## How you work

You are an agent with an `exec` tool. When Felix asks you to do something, you **do it** by calling `exec` with the right shell command. **Never** reply with a code block saying "here is the command you can run" — that is wrong. Always call `exec` yourself, then report the result.

## Project Context

You live inside the PhishingMKT Docker Compose stack. You share a network with these services and reach them by container name:

- **n8n** — `http://n8n:5678` — workflow automation (host port 6789)
- **vLLM** — `http://vllm:8000/v1` — local GPT-OSS-20B (OpenAI-compatible)
- **chromium** — `http://chromium:3000` — browserless for scraping
- **postgres** — `postgres:5432` (user `n8n`, db `n8n`)

n8n workflow templates are mounted read-only at `/workflows/`. Use them as schema references by reading them with the `read` tool.

## Creating / editing n8n workflows

When Felix asks to create, modify, or inspect an n8n workflow, you MUST:
1. Call the `exec` tool (not print the command) to hit the n8n REST API.
2. Do NOT write into `/workflows/` — it's read-only and only imported on n8n startup.
3. After the API call, verify by GETting the workflow back and report: id, name, node count, active state. Never claim success without this verification.

Auth header: `X-N8N-API-KEY: $N8N_API_KEY`. Base URL: `$N8N_BASE_URL` (already exported as env vars).

### Workflow for a create request

Step 1 — read one existing template close to what Felix wants, to copy node shapes:
```
exec: ls /workflows/
exec: cat /workflows/crawl-vnexpress.json
```
Never invent node types from scratch; always copy from a template.

Step 2 — list existing workflows to avoid duplicates and grab credential IDs:
```
exec: curl -sS "$N8N_BASE_URL/api/v1/workflows" -H "X-N8N-API-KEY: $N8N_API_KEY" | node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); d.data.forEach(w=>console.log(w.id+' '+w.name+' active='+w.active))"
```

Step 3 — write the new workflow JSON to `/home/node/.openclaw/workspace/new-workflow.json` via the `write` tool (not exec with heredoc). Required top-level fields: `name`, `nodes`, `connections`, `settings`. Omit `id`. Node names inside `connections` must match `nodes[].name` exactly.

**Correct node formats** (always use these exact `type` + `typeVersion` values):

Schedule Trigger (every hour):
```json
{
  "parameters": {"rule": {"interval": [{"field": "hours"}]}},
  "id": "trigger-1", "name": "Schedule Trigger",
  "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
  "position": [200, 200]
}
```

HTTP Request (GET):
```json
{
  "parameters": {"url": "https://example.com/api", "method": "GET", "options": {}},
  "id": "http-1", "name": "HTTP Request",
  "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
  "position": [400, 200]
}
```

settings block (required, exactly this):
```json
"settings": {"executionOrder": "v1"}
```

connections `type` must be `"main"` (not `"action"`).

Step 4 — POST it via exec:
```
exec: curl -sS -X POST "$N8N_BASE_URL/api/v1/workflows" -H "X-N8N-API-KEY: $N8N_API_KEY" -H "Content-Type: application/json" -d @/home/node/.openclaw/workspace/new-workflow.json
```

**If the response contains a `"message"` key, the call FAILED.** Do NOT claim success. Report the exact error JSON to Felix and stop.

Step 5 — (if requested) activate:
```
exec: curl -sS -X POST "$N8N_BASE_URL/api/v1/workflows/<ID>/activate" -H "X-N8N-API-KEY: $N8N_API_KEY"
```

Step 6 — verify by GETting the workflow back and reporting its id, name, node count, active state.

### Updating a workflow

```
exec: curl -sS -X PUT "$N8N_BASE_URL/api/v1/workflows/<ID>" -H "X-N8N-API-KEY: $N8N_API_KEY" -H "Content-Type: application/json" -d @/home/node/.openclaw/workspace/new-workflow.json
```

## Reminder

The commands above are **examples for you to execute via exec**, not scripts to show Felix. Felix wants results, not instructions. Every n8n workflow request = you calling exec one or more times, then reporting what happened.

## Notes

_Update this section as you learn Felix's preferences and habits._
