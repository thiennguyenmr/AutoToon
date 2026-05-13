# PhishingMKT - n8n Crawl Workflows

## Architecture

```
docker-compose.yml
├── postgres (PostgreSQL 16)        - port 6868
├── n8n (n8n 2.12.2 + curl)        - port 6789
│   ├── auto-imports workflows on startup
│   ├── saves crawl data to PostgreSQL
│   └── supports Python Code node (external runner)
├── n8n-runner (n8nio/runners)      - Python + JS task runner
├── vllm (vllm-openai:gptoss)      - port 6969 (GPU H200, gpt-oss-20b)
├── openclaw-gateway                - port 18789 (Control UI) / 18790 (Bridge)
├── openclaw-cli                    - CLI companion (tail -f, use with docker exec)
└── chromium (browserless)          - headless browser for n8n
```

## Fresh build on a new machine

Prerequisites: Docker (with `docker compose`) + NVIDIA Container Toolkit + ~150 GB free disk (most of it for ComfyUI models).

### 1. Clone and configure environment

```bash
git clone <repo-url> abc && cd abc
cp .env.example .env
```

Edit `.env`:
- `HF_TOKEN` — HuggingFace token (https://huggingface.co/settings/tokens). Used by both vLLM (gpt-oss-20b) and ComfyUI (FLUX.1-dev).
- `POSTGRES_PASSWORD`, `N8N_RUNNERS_AUTH_TOKEN`, `N8N_ENCRYPTION_KEY`, `OPENCLAW_GATEWAY_TOKEN`, `MINIO_ROOT_PASSWORD` — set non-default secrets.
- Leave `N8N_API_KEY=change_me` for now (generated in step 5).

> **Accept the FLUX.1-dev license** at https://huggingface.co/black-forest-labs/FLUX.1-dev with the same HF account that issued `HF_TOKEN`, BEFORE running the model download in step 4 — one-click "Agree and access repository". The token alone is not enough.

> **Do NOT copy `mnt/` from another machine.** Docker auto-creates these dirs on first start with the right UIDs. Copying brings stale runtime data and breaks permissions.

> **GPU layout**: both `vllm` and `comfyui` services use `NVIDIA_VISIBLE_DEVICES=0` and share GPU 0. vLLM is tuned to ~42 GiB (`--max-num-seqs 4 --max-model-len 32768 --gpu-memory-utilization 0.30`), leaving ~98 GiB on a 140 GiB H200 for ComfyUI / FLUX / Wan 2.2. Edit `docker-compose.yml` if your hardware differs.

### 2. Build images and start core services

```bash
docker compose build
docker compose up -d
```

First-boot timing:
- postgres ready: ~10 s
- n8n: auto-imports workflows from `workflows/` and is healthy within ~30 s
- n8n-runner: picks up the curated crawler script from `docker/n8n-runner/scripts/` immediately
- **vLLM**: downloads `openai/gpt-oss-20b` (~13 GB) + compiles CUDA graphs → ~10 min total
- **ComfyUI**: healthy in ~30 s but has no models yet (download in step 4)
- minio, openclaw-gateway, chromium: ready in seconds

Watch progress:
```bash
docker compose ps
docker logs -f abc-vllm     # CUDA graph compile
```

### 3. Create MinIO buckets (one-time)

Workflows write to `mottruyen` and `mottruyen-refactored`:

```bash
docker exec abc-minio mc alias set local http://localhost:9000 \
    "$(grep ^MINIO_ROOT_USER .env | cut -d= -f2)" \
    "$(grep ^MINIO_ROOT_PASSWORD .env | cut -d= -f2)"
docker exec abc-minio mc mb --ignore-existing local/mottruyen local/mottruyen-refactored
```

### 4. Download ComfyUI models (~104 GB, one-time)

After accepting the FLUX.1-dev license (step 1):

```bash
docker exec -it abc-comfyui bash /app/scripts/download-models.sh
```

What it pulls:
- FLUX.1-dev: UNET (22 GB) + VAE (320 MB) + CLIP-L + T5XXL fp16 (9 GB)
- FLUX.1 Kontext Dev fp8 UNET (12 GB) — for image editing by prompt
- Wan 2.2 14B I2V: high-noise expert (27 GB) + low-noise expert (27 GB)
- Wan UMT5-XXL fp8 text encoder (6 GB), Wan 2.1 VAE (500 MB)
- Wan 2.2 lightx2v 4-step LoRAs (1.2 GB each)

Takes 30–60 min depending on bandwidth (`hf_transfer` enabled). Re-runnable — skips already-present files.

Three curated workflows are baked into the image at `/app/seed-workflows/` and auto-copied to `user/default/workflows/` on first start (idempotent — never overwrites user-edited workflows):

| File | Use case |
|---|---|
| `flux1_dev_text_to_image.json` | Text-to-image with FLUX.1-dev |
| `flux1_kontext_image_edit.json` | Image edit by prompt (FLUX.1 Kontext) — patched to use existing T5XXL fp16 |
| `wan2.2_14B_i2v_fp16.json` | Image-to-video (Wan 2.2 14B I2V) — patched to use fp16 + lightx2v LoRA |

### 5. Pair the OpenClaw Control UI

Open `http://<host>:27889/#token=<OPENCLAW_GATEWAY_TOKEN>` (use `localhost` if same machine — not `127.0.0.1`).

The browser requests pairing. Approve from shell:

```bash
docker exec abc-openclaw-cli openclaw devices approve --latest
```

For a Telegram bot pairing flow:

```bash
docker exec abc-openclaw-cli openclaw pairing approve telegram <CODE>
```

### 6. Generate the n8n API key

1. Open `http://<host>:27878`, create the admin user (first-run only)
2. **Settings → n8n API → Create API key**, copy it
3. Paste into `.env` as `N8N_API_KEY=...`
4. Recreate consumers:

```bash
docker compose up -d --force-recreate openclaw-gateway openclaw-cli
```

### 7. Verify

```bash
docker compose ps
# All services should be (healthy) except n8n-runner, openclaw-cli, minio-init (one-shot or no healthcheck).

curl http://localhost:27880/v1/models                                          # vLLM
curl -H "X-N8N-API-KEY: <key>" http://localhost:27878/api/v1/workflows         # n8n
curl http://localhost:27887/system_stats                                       # ComfyUI
docker exec abc-minio mc ls local/                                             # MinIO buckets
docker exec abc-openclaw-cli openclaw gateway probe                            # OpenClaw
```

### Service ports

| Service | Internal | Host |
|---|---|---|
| n8n UI | 5678 | 27878 |
| Postgres | 5432 | 27832 |
| vLLM API | 8000 | 27880 |
| MinIO API | 9000 | 27800 |
| MinIO Console | 9001 | 27801 |
| ComfyUI | 8188 | 27887 |
| OpenClaw Gateway | 18789 | 27889 (configurable via `OPENCLAW_GATEWAY_PORT`) |
| OpenClaw Bridge | 18790 | 27890 (configurable via `OPENCLAW_BRIDGE_PORT`) |

---

## Getting Started (existing deployment)

```bash
# Create .env from template (if not already done)
cp .env.example .env  # edit secrets as needed

# Build and start
docker compose build
docker compose up -d
```

### Access

| Service          | URL                                                        |
|------------------|------------------------------------------------------------|
| n8n UI           | http://localhost:6789                                      |
| OpenClaw UI      | http://localhost:18789/#token=my_secret_openclaw_token      |
| vLLM API         | http://localhost:6969/v1                                    |
| PostgreSQL       | localhost:6868 (user: n8n / pass: n8n_password)            |

> **Note on OpenClaw UI:** Must access via `localhost`, NOT `127.0.0.1` (WebCrypto secure context requirement).

## Directory Structure

```
├── docker/
│   ├── n8n/
│   │   ├── Dockerfile              # Multi-stage build: Alpine (curl) + n8n
│   │   ├── import-workflows.sh     # Auto-imports workflows on container start
│   │   └── fetch-page.js           # Page fetch script
│   ├── openclaw/
│   │   └── USER.md                 # System prompt for OpenClaw agent
│   └── vllm/
│       ├── patch-harmony.py        # Patches vLLM harmony serving layer on container start
│       └── disadvantage_gpt_oss.md # Field notes on gpt-oss-20b on this stack
├── workflows/
│   ├── crawl-bachhoaxanh.json
│   ├── crawl-vnexpress.json
│   ├── google-search.json
│   ├── seo-audit-bachhoaxanh.json
│   ├── schedule-http-template.json # Template: ScheduleTrigger + HTTP Request
│   └── test-python.json            # Test Python Code node
├── init-db/
│   └── 01-create-tables.sql        # Creates tables on first postgres startup
├── mnt/
│   ├── n8n/
│   │   ├── n8n_data/               # n8n persistent data
│   │   └── postgres_data/          # PostgreSQL data
│   ├── openclaw/
│   │   ├── config/                 # OpenClaw config (.openclaw)
│   │   └── workspace/              # OpenClaw workspace
│   └── vllm/
│       └── cache/                  # HuggingFace model cache
├── .env                            # Secrets and config
├── .dockerignore                   # Excludes mnt/ from build context
├── docker-compose.yml
└── TROUBLESHOOTING.md              # Known issues and fixes
```

## OpenClaw

### Accessing the UI

```
http://localhost:18789/#token=my_secret_openclaw_token
```

### Hatch TUI (terminal)

```bash
# Run from inside the container
docker exec -it n8n-openclaw-cli node openclaw.mjs hatch

# Or from host (requires env var)
export OPENCLAW_GATEWAY_URI=ws://localhost:18789
openclaw hatch
```

### Useful CLI Commands

```bash
# List agents
docker exec n8n-openclaw-cli node openclaw.mjs agents list

# List models
docker exec n8n-openclaw-cli node openclaw.mjs models list

# List all models (including built-ins)
docker exec n8n-openclaw-cli node openclaw.mjs models list --all
```

### Pairing required (when accessing UI or TUI)

If the UI or `openclaw tui` shows **"pairing required"**, pass the token in the URL:

```
http://localhost:18789/#token=my_secret_openclaw_token
```

Or with TUI:

```bash
docker exec -it n8n-openclaw-cli node openclaw.mjs tui --token my_secret_openclaw_token
```

Device management (from inside container):

```bash
# List devices (paired + pending)
docker exec n8n-openclaw-cli node openclaw.mjs devices list

# Approve a pending device
docker exec n8n-openclaw-cli node openclaw.mjs devices approve <device-id>

# Reject a pending device
docker exec n8n-openclaw-cli node openclaw.mjs devices reject <device-id>

# Remove a paired device
docker exec n8n-openclaw-cli node openclaw.mjs devices remove <device-id>

# Probe gateway
docker exec n8n-openclaw-cli node openclaw.mjs gateway probe
```

> **Note:** Must use `localhost`, NOT `127.0.0.1`. `127.0.0.1` is not a secure context for WebCrypto and will cause "control ui requires device identity" error.
>
> **Note:** If CLI reports "gateway closed (1006)", restart the CLI container: `docker compose restart openclaw-cli`. This happens when the gateway restarts but the CLI does not auto-reconnect (due to `network_mode: service:openclaw-gateway`).

### Fix config access permissions on host

Docker userns-remap maps container UID 1000 (node) → host UID 1018503.
If `mnt/openclaw/config/` is not readable from the host:

```bash
docker run --rm -v ./mnt/openclaw/config:/data alpine sh -c "chmod 755 /data && chmod -R o+rX /data"
```

> OpenClaw may reset permissions to 600 when writing config. Re-run the command above if needed.

## vLLM (GPT-OSS-20B)

- Image: `vllm/vllm-openai:gptoss`
- Model: `openai/gpt-oss-20b`
- GPU: NVIDIA H200 (full precision)
- OpenAI-compatible API: `http://localhost:6969/v1`
- Harmony serving layer is patched automatically at startup via `docker/vllm/patch-harmony.py`

```bash
# Test API
curl http://localhost:6969/v1/models

# Test responses endpoint (used by OpenClaw)
curl http://localhost:6969/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer vllm-local" \
  -d '{"model": "gpt-oss-20b", "input": "Hello", "stream": false}'

# Verify patches were applied
docker logs vllm 2>&1 | grep -E '^\[(OK|FAIL|SKIP)\]'

# Tail debug request log (inside container)
docker exec vllm tail -f /tmp/vllm-requests.log
```

## OpenClaw + vLLM Integration

### Architecture

```
Telegram Bot (@ZySEObot)
    │
    ▼
openclaw-gateway ──► vllm (http://vllm:8000/v1)
    │                    │
    │                    └── Model: openai/gpt-oss-20b (GPU, ~13.5 GiB)
    │
    └──► n8n (http://n8n:5678) — create/manage workflows
```

### Configuring vLLM in OpenClaw

**Environment variables** (in `docker-compose.yml`):

```yaml
environment:
  - VLLM_API_KEY=vllm-local
  - VLLM_BASE_URL=http://vllm:8000/v1
  - N8N_BASE_URL=http://n8n:5678
  - N8N_API_KEY=${N8N_API_KEY}
```

**Model provider** (in `mnt/openclaw/config/openclaw.json`):

```json
{
  "models": {
    "providers": {
      "vllm": {
        "baseUrl": "http://vllm:8000/v1",
        "apiKey": "vllm-local",
        "api": "openai-responses",
        "models": [{ "id": "gpt-oss-20b", "reasoning": true }]
      }
    }
  }
}
```

> Use `"api": "openai-responses"` (NOT `openai-completions`). GPT-OSS only works correctly with the `/v1/responses` endpoint — vLLM has no harmony tool parser for `/v1/chat/completions`.

### Verifying the stack is healthy

```bash
# Container status
docker ps --format "table {{.Names}}\t{{.Status}}"

# vLLM healthy
docker exec vllm curl -s http://localhost:8000/health

# OpenClaw is using the right model
docker logs n8n-openclaw-gateway 2>&1 | grep "agent model"
# Expected: agent model: vllm/gpt-oss-20b

# Check vLLM patches
docker logs vllm 2>&1 | grep -E '^\[(OK|FAIL|SKIP)\]'

# GPU usage
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
```

## OmniVoice (TTS for story → audio)

Dedicated container `abc-omnivoice` exposes a small FastAPI wrapper around the
[k2-fsa/OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) zero-shot TTS model.
It runs on GPU 1 alongside ComfyUI and is consumed by the
`Build Story Audio (per slug)` n8n workflow.

### Architecture

```
n8n  →  POST http://omnivoice:8000/tts  {text, ref_audio?, ref_text?, voice_design?}
                       ↓
                 FastAPI / Uvicorn
                       ↓
        omnivoice.OmniVoice.from_pretrained("k2-fsa/OmniVoice")
                       ↓
                  WAV bytes back to caller
```

Files:

- `docker/omnivoice/Dockerfile` — PyTorch 2.8 cu128 + `omnivoice` + FastAPI
- `docker/omnivoice/app.py` — endpoints `/health`, `/load`, `/tts`
- `mnt/omnivoice/cache` — HuggingFace model cache (persisted)
- `mnt/omnivoice/refs` — optional reference voice WAV files (read-only mount at `/refs`)
- `workflows/build-story-audio.json` — n8n pipeline

### One-time setup

```bash
# 1. Create the audio output bucket (idempotent)
docker exec abc-minio mc mb --ignore-existing local/mottruyen-audio

# 2. Build and start the service (model is downloaded on first /load or /tts call)
make build S=omnivoice
docker compose up -d omnivoice

# 3. Watch it come up — first start takes a few minutes to pull the model
docker logs -f abc-omnivoice
curl -s http://localhost:27883/health
```

Health response should show `cuda_available: true` and the device that omnivoice
will use. The model is lazy-loaded; the first `POST /tts` (or an explicit
`POST /load`) triggers the download into `mnt/omnivoice/cache`.

### Running the audio workflow

The pipeline reads `mottruyen-refactored/<slug>/<slug>_full.txt`, chunks it by
paragraph (then by sentence when a paragraph exceeds `chunk_chars`), calls
OmniVoice once per chunk, and uploads each WAV to
`mottruyen-audio/<slug>/chunk_NNNN.wav` plus a `concat.txt` and `manifest.json`.

```bash
# Re-import after editing the workflow JSON
docker exec abc-n8n n8n import:workflow --input=/home/node/workflows/build-story-audio.json
```

Trigger from the n8n UI (`Build Story Audio (per slug)`). Key params on the
`Set Params` node:

| Param | Default | Notes |
|---|---|---|
| `slug` | `a-ham` | folder name under `mottruyen-refactored/` |
| `source_bucket` | `mottruyen-refactored` | where `<slug>_full.txt` lives |
| `audio_bucket` | `mottruyen-audio` | output bucket |
| `chunk_chars` | `1500` | max chars per TTS call |
| `ref_audio` | `""` | filename inside `mnt/omnivoice/refs/` (e.g. `narrator.wav`) |
| `ref_text` | `""` | transcript of the reference clip |
| `voice_design_json` | female adult medium narration | used when no reference is provided |
| `seed` | `42` | per-chunk seed = `seed + chunk_id` |

### Wiring a reference voice (later)

1. Drop a clean 5–15 s WAV into `mnt/omnivoice/refs/<name>.wav`
   (mono, 16 kHz or 24 kHz, no background noise)
2. Note the exact transcript of that clip
3. In the workflow, set `ref_audio = "<name>.wav"` and `ref_text = "<transcript>"`
4. Voice design is ignored when both `ref_audio` and `ref_text` are set

### Stitching chunks into one file

`ffmpeg` is not present inside the n8n runner, so concatenation is a separate
step. The workflow writes a ready-to-use `concat.txt` next to the chunks:

```bash
mc cp -r local/mottruyen-audio/<slug> ./work
cd work/<slug>
ffmpeg -f concat -safe 0 -i concat.txt -c copy <slug>.wav
# Or compress to mp3:
ffmpeg -f concat -safe 0 -i concat.txt -c:a libmp3lame -b:a 128k <slug>.mp3
```

### Direct API smoke test

```bash
# Default voice design (no reference)
curl -X POST http://localhost:27883/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Xin chào, đây là bản kể chuyện thử nghiệm.",
       "voice_design": {"gender":"female","age":"adult","style":"narration"}}' \
  --output sample.wav
```

### Common issues

- `model returned no audio` → the `omnivoice.generate()` signature in your
  installed version doesn't match the call shape; check `docker logs
  abc-omnivoice` and adjust `app.py`. Fallback `model.generate(text)` is already
  in place.
- First call after start is slow (model load + CUDA kernel JIT). Subsequent
  calls are fast (RTF ~0.025 per the model card).
- Bucket missing → `mc mb local/mottruyen-audio` (see setup above).

## Useful Commands

```bash
# Build and start
docker compose build && docker compose up -d

# View logs per service
docker logs n8n
docker logs n8n-openclaw-gateway
docker logs vllm

# Restart to re-import workflows
docker compose restart n8n

# Restart openclaw (resets cooldown state)
docker restart n8n-openclaw-gateway

# Recreate vllm (re-applies patches)
docker compose up -d --force-recreate vllm

# Check workflows in DB
docker exec n8n-postgres psql -U n8n -d n8n -c "SELECT id, name FROM workflow_entity;"

# Check credentials
docker exec n8n-postgres psql -U n8n -d n8n -c "SELECT id, name, type FROM credentials_entity;"

# Manually import a workflow
docker exec n8n n8n import:workflow --input=/home/node/workflows/filename.json

# Fix openclaw config permissions
docker run --rm -v ./mnt/openclaw/config:/data alpine sh -c "chmod 755 /data && chmod -R o+rX /data"

# reimport n8n-workflow using n8n CLI
docker exec abc-n8n n8n import:workflow --input=/home/node/workflows/build-character-bible.json 2>&1 | tail -10
```

---

> See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for known issues and fixes.
