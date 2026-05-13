# TROUBLESHOOTING — Known Issues and Fixes

## n8n

### Issue 1: Workflow not auto-imported on container startup

**Error:** `null value in column "id" of relation "workflow_entity" violates not-null constraint`

**Cause:** Workflow JSON file is missing the top-level `id` field. n8n's `import:workflow` command requires it.

**Fix:** Add the `id` field at the top of the JSON file:

```json
{
  "id": "crawl-bachhoaxanh-001",
  "name": "Crawl Bach Hoa Xanh - Save to PostgreSQL",
  "nodes": [...]
}
```

---

### Issue 2: HTTP Request node returns ECONNRESET inside Docker

**Error:** `n8n-nodes-base.httpRequest` returns `ECONNRESET` when calling `bachhoaxanh.com`.

**Cause:** The website uses **TLS fingerprinting** (JA3/JA4) in its WAF. Node.js has a different TLS fingerprint than a browser, so the server rejects the connection. Confirmed by:
- `curl` from host: **200 OK**
- Node.js `https.get` from container: **ECONNRESET**
- `openssl s_client`: TLS handshake succeeds (rules out SSL cert issue)

**Fix:** Replace the HTTP Request node with an **Execute Command** node running `curl`:

```json
{
  "parameters": {
    "command": "curl -s --max-time 30 --retry 3 --retry-delay 2 --retry-all-errors -L -k -H 'User-Agent: Mozilla/5.0 ...' 'https://www.bachhoaxanh.com'"
  },
  "type": "n8n-nodes-base.executeCommand",
  "typeVersion": 1
}
```

Also install `curl` in the Docker image since the n8n hardened image has no `apk`:

```dockerfile
# Multi-stage build to copy curl into the n8n image
FROM alpine:3.22 AS curl-builder
RUN apk add --no-cache curl

FROM n8nio/n8n:2.12.2
COPY --from=curl-builder /usr/bin/curl /usr/bin/curl
COPY --from=curl-builder /usr/lib/libcurl* /usr/lib/
COPY --from=curl-builder /usr/lib/libbrotli* /usr/lib/
COPY --from=curl-builder /usr/lib/libnghttp2* /usr/lib/
COPY --from=curl-builder /usr/lib/libidn2* /usr/lib/
COPY --from=curl-builder /usr/lib/libunistring* /usr/lib/
COPY --from=curl-builder /usr/lib/libpsl* /usr/lib/
```

---

### Issue 3: Code node disallows `require('child_process')`

**Error:** `Module 'child_process' is disallowed [line 1]`

**Cause:** The n8n Code node runs in a sandbox that blocks all system modules like `child_process`, `fs`, `net`, etc.

**Fix:** Use `n8n-nodes-base.executeCommand` instead of a Code node. Execute Command runs shell commands directly without needing `require`.

---

### Issue 4: Execute Command node reports "not currently installed"

**Error:** `This node is not currently installed. It is either from a newer version of n8n, a custom node, or has an invalid structure`

**Cause:** n8n 2.x **excludes** the `executeCommand` node by default for security reasons:

```js
// File: @n8n/config/dist/configs/nodes.config.js
this.exclude = ['n8n-nodes-base.executeCommand', 'n8n-nodes-base.localFileTrigger'];
```

**Fix:** Add the `NODES_EXCLUDE=[]` environment variable in docker-compose.yml:

```yaml
environment:
  - NODES_EXCLUDE=[]
```

---

### Issue 5: curl returns exit code 92 (HTTP/2 stream error)

**Error:** `curl: (92) HTTP/2 stream 1 was closed cleanly, but before getting all response header fields`

**Cause:** The website's WAF detects `User-Agent: curl/x.x.x` and rejects the request. The WAF also rate-limits repeated calls.

**Fix:** Always send a browser-like `User-Agent` and add retry flags:

```bash
curl -s --max-time 30 \
  --retry 3 --retry-delay 2 --retry-all-errors \
  -L -k \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' \
  -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' \
  -H 'Accept-Language: vi-VN,vi;q=0.9,en;q=0.8' \
  -H 'Accept-Encoding: identity' \
  'https://www.bachhoaxanh.com'
```

---

### Issue 6: PostgreSQL credential "does not exist"

**Error:** `Credential with ID "REPLACE_WITH_CREDENTIAL_ID" does not exist for type "postgres"`

**Cause:** The workflow file contains a placeholder instead of a real credential ID.

**Fix:** Look up the real credential ID in the database:

```bash
docker exec n8n-postgres psql -U n8n -d n8n -c "SELECT id, name, type FROM credentials_entity;"
```

Replace `REPLACE_WITH_CREDENTIAL_ID` with the real ID in the workflow JSON.

---

### Issue 7: PostgreSQL credential "Connection refused"

**Error:** Save to PostgreSQL node reports `Connection refused`.

**Cause:** The PostgreSQL credential in n8n is using `localhost` as the host. Inside Docker networks, containers communicate via **service name**, not `localhost`.

**Fix:** Go to n8n UI > Credentials > Postgres account and update:

| Field    | Value          |
|----------|----------------|
| Host     | `postgres`     |
| Port     | `5432`         |
| Database | `n8n`          |
| User     | `n8n`          |
| Password | `n8n_password` |

> **Note:** Host is `postgres` (the service name in docker-compose), NOT `localhost` or `n8n-postgres`.

---

### Issue 8: Duplicate workflows in the database

**Cause:** Importing a workflow multiple times without an `id` field creates a new record each time (n8n generates a random ID). Opening the n8n UI may show an old copy with incorrect node types.

**Fix:** Remove the duplicates:

```bash
# View all workflows
docker exec n8n-postgres psql -U n8n -d n8n -c "SELECT id, name FROM workflow_entity;"

# Delete duplicates, keeping the correct one
docker exec n8n-postgres psql -U n8n -d n8n -c \
  "DELETE FROM workflow_entity WHERE name = 'Workflow Name' AND id <> 'id-to-keep';"
```

---

### Issue 9: HTML Extract node can't read data from Execute Command

**Cause:** Execute Command returns HTML in the `stdout` field, but HTML Extract reads from `data` by default.

**Fix:** Add `sourceData` and `dataProperty` options to the HTML Extract node:

```json
{
  "options": {
    "sourceData": "json",
    "dataProperty": "stdout"
  }
}
```

---

### Issue 10: Transform Data node fails due to missing "Webhook" node reference

**Error:** `$('Webhook').first().json` throws an error.

**Cause:** The workflow uses a Manual Trigger but the code still references a `Webhook` node (copied from another workflow).

**Fix:** Remove the `$('Webhook')` reference and hardcode the URL or read it from another node:

```js
// WRONG
const webhookData = $('Webhook').first().json;
const url = webhookData.url || 'https://...';

// CORRECT
const url = 'https://www.bachhoaxanh.com';
```

---

## OpenClaw + vLLM

### Issue 11: vLLM returns 400 "NoneType object is not iterable"

**Error:** `harmony_utils.py: TypeError: 'NoneType' object is not iterable`

**Cause:** The vLLM GPT-OSS image uses a custom "harmony" mode to parse messages. When OpenClaw sends conversation history containing an assistant message with `content: null` (from tool_calls), the harmony parser crashes because it doesn't handle `None`.

**Fix:** Handled by `docker/vllm/patch-harmony.py` (patches 1, 2, 3, 4 — `harmony_utils.py` and `serving_chat.py`). Applied automatically on container start.

---

### Issue 12: OpenClaw falls back to OpenRouter instead of using vLLM

**Error:** Gateway log shows `model fallback decision: candidate_failed requested=vllm/gpt-oss-20b reason=format next=openrouter/openrouter/auto`

**Cause:** After vLLM returned 400 errors (before patching), OpenClaw recorded the failure and put the provider into **cooldown**. Subsequent requests automatically fall back to OpenRouter without retrying vLLM.

**Fix:**

```bash
# Clear old sessions (may contain tool_calls history causing errors)
docker exec n8n-openclaw-gateway sh -c 'rm -f /home/node/.openclaw/agents/main/sessions/*.jsonl && echo "{}" > /home/node/.openclaw/agents/main/sessions/sessions.json'

# Restart gateway to clear cooldown state
docker compose restart openclaw-gateway

# Verify
docker logs n8n-openclaw-gateway 2>&1 | grep "agent model"
# Expected: agent model: vllm/gpt-oss-20b
```

---

### Issue 13: OpenClaw reports "vLLM requires authentication to be registered as a provider"

**Error:** `FailoverError: Unknown model: vllm/gpt-oss-20b. vLLM requires authentication...`

**Cause:** Missing `VLLM_API_KEY` env var in the openclaw-gateway container.

**Fix:** Add to docker-compose.yml (openclaw-gateway service):

```yaml
environment:
  - VLLM_API_KEY=vllm-local
  - VLLM_BASE_URL=http://vllm:8000/v1
```

Then: `docker compose up -d openclaw-gateway`

---

### Issue 14: vLLM crashes with "Free memory < desired" on startup

**Error:** `torch.OutOfMemoryError: Free memory (64.32 GiB) < desired (125.82 GiB)`

**Cause:** Not enough GPU memory — another process is occupying it.

**Fix:**

```bash
# Check which processes are using the GPU
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader

# See who owns the process
ps -p <PID> -o pid,user,etime,cmd --no-headers

# Kill zombie process (requires sudo)
sudo kill <PID>

# Or switch GPU (in docker-compose.yml)
environment:
  - NVIDIA_VISIBLE_DEVICES=1   # use GPU 1 instead of 0
```

---

### Issue 15: vLLM healthcheck fails (container unhealthy)

**Cause:** The `vllm/vllm-openai` image does not include `curl`. Any healthcheck using curl will always fail.

**Fix:** Use `python3` for the healthcheck (already available in the image):

```yaml
healthcheck:
  test: ["CMD-SHELL", "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\" 2>/dev/null || exit 1"]
  interval: 30s
  timeout: 10s
  retries: 5
  start_period: 120s   # model load takes ~3-5 minutes
```

---

### Issue 16: OPENCLAW_CONFIG_PATH override loses Telegram and model config

**Error:** Telegram bot does not start, model config is not loaded, no related logs.

**Cause:** The env var `OPENCLAW_CONFIG_PATH=/home/node/.openclaw/config.json5` in docker-compose makes the gateway read `config.json5` (which only has `allowedOrigins`) instead of `openclaw.json` (which has Telegram, model, and auth config).

**Fix:** Remove `OPENCLAW_CONFIG_PATH` from the openclaw-gateway environment. Let the gateway auto-discover `openclaw.json` (the default).

---

### Issue 17: openclaw-cli "Cannot restart container" after gateway recreate

**Error:** `docker compose restart openclaw-cli` fails with `cannot join network of a non-running container`

**Cause:** The CLI uses `network_mode: "service:openclaw-gateway"`, which references the old container ID. When the gateway is recreated (new ID), the CLI cannot restart because the old ID no longer exists.

**Fix:** Use `up -d` (recreate) instead of `restart`:

```bash
docker compose up -d openclaw-cli
```

---

### Issue 18: Telegram bot is silent when using streaming + function tools

**Error:** Bot receives messages but returns no reply. `/v1/responses` with `stream=true` + user-defined function tools returns `output: []`.

**Cause:** `StreamingHarmonyContext` in vLLM drops the commentary-channel tool call when generation ends on `<|call|>`. Additionally, `responses_stream_generator` only emits SSE events for built-in tools (`browser.*`, `python`) — there is no code path for `functions.*` (user-defined tools).

Non-streaming produces correct results 5/5; streaming returns `output=[]` 5/5 for the same prompt and tools.

**Fix:** Patch `serving_responses.py` to always use `HarmonyContext` (instead of `StreamingHarmonyContext`) and add `responses_stream_synth_generator` which synthesizes SSE events from a completed non-streaming response.

See `docker/vllm/patch-harmony.py` (patches 5b, 6, 7). Applied automatically on container start.

---

### Issue 19: Streaming returns 17+ duplicate items (duplicate reasoning)

**Error:** After fixing Issue 18, `stream=true` returns 17 items (16 duplicate reasoning + 1 message) instead of 2 correct items.

**Cause:** `HarmonyContext.append_output` was designed for non-streaming (called once with the full token list). When used with streaming (1 token per yield), every yield calls `self._messages.extend(self.parser.messages)` — `parser.messages` is a cumulative list, so every completed message gets appended repeatedly (O(n_tokens × n_messages) duplicates).

**Fix:** Patch `context.py` to track a `_parser_msgs_synced` counter and only extend with the new tail of `parser.messages` on each call.

See `docker/vllm/patch-harmony.py` (patch 8). Applied automatically on container start.

---

### Issue 20: vLLM returns 400 "No call message found for {call_id}"

**Error:** `ValueError: No call message found for call{id}` → HTTP 400. Occurs when conversation history contains a `function_call_output` referencing a previous `function_call`.

**Cause:** `request.input` is declared as `Union[*Param types, ResponseFunctionToolCall]`. OpenClaw sends `function_call` items as plain dicts (TypedDict), not `ResponseFunctionToolCall` objects. The `isinstance(response_msg, ResponseFunctionToolCall)` check is always False → `prev_outputs` is never populated → when processing the next `function_call_output`, the call_id lookup fails → raises ValueError.

**Fix:** Patch `serving_responses.py` to also check `isinstance(response_msg, dict) and response_msg.get("type") == "function_call"`, then build a `ResponseFunctionToolCall` from the dict and append it to `prev_outputs`.

See `docker/vllm/patch-harmony.py` (patch 9). Applied automatically on container start.

---

### Issue 21: vLLM crashes with "NoneType has no attribute startswith" in stream

**Error:** OpenClaw reports `Error Code 400: 'NoneType' object has no attribute 'startswith'`. vLLM returns HTTP 200 but emits an SSE error event in the stream.

**Cause:** `parse_output_message` in `harmony_utils.py` calls `message.recipient.startswith("functions.")` in the `channel == "commentary"` branch without checking for None first. The model sometimes generates a commentary channel message without a recipient (self-directed, between tool calls) → AttributeError.

**Fix:** Patch `harmony_utils.py` to guard all `startswith` calls with `if message.recipient is not None`. Commentary messages with `recipient=None` are treated as reasoning items (no crash).

See `docker/vllm/patch-harmony.py` (patch 10). Applied automatically on container start.

---

### Issue 22: OpenClaw cooldown + Telegram polling stuck after repeated failures

**Symptom:** Bot is completely silent, no activity logs from openclaw for several minutes, even though vLLM is healthy.

**Cause:** OpenClaw uses a circuit-breaker: after repeated failures (before patches were applied), the `vllm` provider is placed into `cooldown`. New requests are rejected immediately without reaching vLLM. The Telegram polling provider also falls into a fallback loop after a DNS issue → no new updates are fetched.

**Fix:** Restart openclaw-gateway to reset both the cooldown state and Telegram polling:

```bash
docker restart n8n-openclaw-gateway
```

The container has config + workspace mounted so no state is lost. After ~60s (healthy), the bot resumes normal operation.

---

### Issue 23: n8n API rejects workflow JSON — "request/body/settings must NOT have additional properties"

**Error:** `{"message":"request/body/settings must NOT have additional properties"}` or `{"message":"request/body must have required property 'settings'"}`.

**Cause:** The model generates workflow JSON with two problems:
1. The `settings` block contains extra properties that the n8n API does not allow
2. The model then removes `settings` entirely → API reports missing field

Additionally, the model **writes** the file to `/home/node/.openclaw/workspace/tmp_test_httpbin.json` but **curl** reads from `/tmp/new-workflow.json` (a stale file from a previous session, missing `settings`) — path mismatch causes n8n to always read the wrong file.

**Fix:**
- Update `docker/openclaw/USER.md` to use a consistent path: `/home/node/.openclaw/workspace/new-workflow.json` for both `write` and `curl`.
- Delete the stale file: `docker exec n8n-openclaw-gateway rm -f /tmp/new-workflow.json`
- Add a correct template to `workflows/schedule-http-template.json` so the model copies the exact format.

---

### Issue 24: n8n workflow created but no nodes visible in the UI

**Symptom:** Workflow appears in the n8n list but the canvas is empty — no nodes.

**Cause:** The model used `typeVersion: 1` for `scheduleTrigger` and `httpRequest` (copied from the old `crawl-vnexpress.json` template which only had `manualTrigger`). Old typeVersions are not rendered by the current n8n.

**Correct typeVersions:**

| Node type | Correct typeVersion | Wrong typeVersion |
|---|---|---|
| `n8n-nodes-base.scheduleTrigger` | **1.2** | 1 |
| `n8n-nodes-base.httpRequest` | **4.2** | 1 |

**Correct parameters format for scheduleTrigger (hourly):**

```json
{"rule": {"interval": [{"field": "hours"}]}}
```

**Fix:** Added `workflows/schedule-http-template.json` with the correct format. When the model runs `ls /workflows/` it will find `schedule-http-template.json` and copy the correct typeVersions. Updated `USER.md` with the exact node format examples.

---

### Issue 25: n8n API key "invalid signature" after container recreate

**Symptoms:**
- OpenClaw bot reports `"invalid signature"` when trying to call the n8n API
- Bot asks the user to "check $N8N_API_KEY and $N8N_BASE_URL" instead of acting
- n8n UI works fine (login, browse workflows) but API calls via `X-N8N-API-KEY` header all fail with 401

**Cause:** n8n signs JWT API keys using an encryption key. Without a pinned `N8N_ENCRYPTION_KEY` env var, n8n generates a **random key on every startup** and stores it only in memory. After a container recreate (`docker compose down && up`), a new random key is generated — the old JWT signature no longer matches → "invalid signature".

The `./mnt/n8n/n8n_data` volume does not help here because n8n does not write the auto-generated key to disk.

**Diagnosis:**

```bash
# 1. Confirm the key directory is empty (root cause)
docker exec n8n ls -la /home/node/.n8n/

# 2. Get the current encryption key from the running process
docker exec n8n sh -c "cat /proc/1/environ | tr '\0' '\n' | grep N8N_ENCRYPTION_KEY"

# 3. Quick API test — should return 200, not 401
curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:6789/api/v1/workflows \
  -H "X-N8N-API-KEY: $(grep N8N_API_KEY .env | cut -d= -f2-)"
```

**Fix:**

Step 1 — Extract the encryption key from the running process and pin it in `.env`:

```bash
# Get current key (copy the value after N8N_ENCRYPTION_KEY=)
docker exec n8n sh -c "cat /proc/1/environ | tr '\0' '\n' | grep N8N_ENCRYPTION_KEY"

# Set it in .env
sed -i 's/^N8N_ENCRYPTION_KEY=.*/N8N_ENCRYPTION_KEY=<VALUE>/' .env
```

Step 2 — Go to n8n UI → Settings → API, delete the old key, create a new one, copy the JWT.

Step 3 — Update `.env` with the new API key:

```bash
sed -i 's/^N8N_API_KEY=.*/N8N_API_KEY=<NEW_JWT>/' .env
```

Step 4 — Restart openclaw-gateway to pick up the new env vars:

```bash
docker compose up -d openclaw-gateway
```

**Permanent fix (prevents recurrence):** `N8N_ENCRYPTION_KEY` is now pinned in `docker-compose.yml` via `${N8N_ENCRYPTION_KEY}`. As long as `.env` has a fixed value, the key never rotates across restarts.

---

## Google Blocking

Google blocks all automated access from server IPs (e.g. `5.183.90.113`) — both Firecrawl and headless browsers receive CAPTCHA/429. Direct Google scraping is not viable.

**Reasons:**
1. **Datacenter IP** — Server is on a VPS/cloud provider. Google aggressively blocks datacenter IPs since bots almost always run from them.
2. **Shared IP reputation** — Other users on the same hosting provider have scraped Google before, getting the entire IP range flagged.
3. **Automated traffic detection** — Headless browsers send patterns Google detects: missing cookies, no browsing history, distinctive TLS fingerprints, no mouse movements.
4. **Rate limiting** — A single request from a flagged IP is enough to trigger CAPTCHA/429.

**Alternatives:**
- **SerpAPI** (recommended) — returns structured Google results including AI Overview as JSON. Free tier: 100 searches/month.
- **Google Custom Search API** — free 100 queries/day, but no AI Overview.
- **Brave Search API** — free tier, bot-friendly, no CAPTCHA.
