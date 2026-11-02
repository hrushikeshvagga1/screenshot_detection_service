# Screenshot analysis worker — setup from scratch (DigitalOcean staging)

Use this guide to stand up the **queue-based** worker again: **Valkey queue → Droplet worker → MongoDB → DigitalOcean Spaces**.

Production on **AWS (SQS + ECS)** is outlined at the end; details live in [aws-production.md](aws-production.md).

---

## Architecture

```text
Mobile app
    → Ingest API
        → Upload files to Spaces
        → Insert batch in Mongo (status: PENDING)
        → LPUSH job { batchId } to Valkey

Valkey list: screenshot:batch:jobs
    → Worker(s) BRPOP
        → Claim batch in Mongo (PENDING → PROCESSING)
        → Download each item from Spaces (.webp / .jpg / .png)
        → OCR + rules (analyze())
        → Upsert screenshot_analysis_results
        → Mark batch COMPLETED
        → ACK queue message
```

| Component | Staging (DO) | Notes |
|-----------|--------------|--------|
| Files | **Spaces** | S3-compatible; ingest may use fields `gcsBucket` + `gcsPath` (names are legacy — still Spaces) |
| Inbox + results | **MongoDB** | e.g. `device_screenshot_batches`, `screenshot_analysis_results` |
| Queue | **Managed Valkey** | Redis-compatible; DO no longer creates new “Redis” clusters — use **Valkey** |
| Worker | **Droplet + Docker** | `python -m worker.run_queue` |

The queue carries only **`{ "batchId", "enqueuedAt", "source" }`**, not image bytes.

---

## Prerequisites checklist

Before creating the Droplet, collect:

- [ ] MongoDB connection string (with **app database name** in the path, not only `authSource=admin`)
- [ ] Spaces **bucket name**, **region** (e.g. `sgp1`), read-capable access key
- [ ] Valkey **TLS URI** (`rediss://...`)
- [ ] Batch collection name (e.g. `device_screenshot_batches`)
- [ ] SSH key for the Droplet
- [ ] This repo on GitHub or a way to `scp` to the server

---

## Phase 1 — MongoDB

### 1.1 Database and collections

- **Batches:** e.g. `device_screenshot_batches` (ingest writes here).
- **Results:** `screenshot_analysis_results` (worker creates on first run).

### 1.2 Connection string (critical)

The path **after the hostname** is the database PyMongo uses for collections.

**Wrong** (worker sees empty `admin` DB):

```text
mongodb+srv://user:pass@host.mongodb.net/admin?authSource=admin&...
```

**Right** (example app DB `CPCRM_DB`):

```text
mongodb+srv://user:pass@host.mongodb.net/CPCRM_DB?replicaSet=...&tls=true&authSource=admin
```

Keep `authSource=admin` if DO/your provider requires it; change **`/CPCRM_DB`** to your real database name.

### 1.3 Index (once)

```javascript
use CPCRM_DB   // your DB name

db.device_screenshot_batches.createIndex({ status: 1, receivedAt: 1 })
```

### 1.4 Network

Add the **worker Droplet public IP** to Mongo **trusted sources** (or use VPC).

### 1.5 Ingest batch shape (reference)

Worker understands ingest documents with:

- Batch: `status: "PENDING"`, `deviceId`, `deviceBatchId`, `items[]`
- Item: `jobId`, `gcsBucket`, `gcsPath`, `contentType: "image/webp"`, upload `status: "completed"` (lowercase)

Optional aliases: `storageKey`, `itemId`, `staffId` / `cpId` for user id.

---

## Phase 2 — DigitalOcean Spaces

### 2.1 Worker environment

```env
SPACES_BUCKET=property-media-temp
SPACES_ENDPOINT=https://sgp1.digitaloceanspaces.com
SPACES_ACCESS_KEY=...
SPACES_SECRET_KEY=...
```

Rules:

- **`SPACES_BUCKET`** = Space name **only** (no path suffix).
- **`SPACES_ENDPOINT`** = **regional** URL (`https://<region>.digitaloceanspaces.com`), not `https://bucket.region...`.
- Object keys come from Mongo `gcsPath` (e.g. `phone-capture/.../file.webp`).

### 2.2 Keys

DO → **API** → **Spaces keys** → create key with **read** access to the bucket (ingest needs write separately).

---

## Phase 3 — Managed Valkey (queue)

1. DO → **Databases** → **Create** → engine **Valkey** (same region as Spaces).
2. **Trusted sources:** worker Droplet IP (and ingest server if it enqueues from DO).
3. Copy **connection string** (TLS, port ~25061):

   ```text
   rediss://default:PASSWORD@db-vk-....db.ondigitalocean.com:25061
   ```

4. Queue list key (default, must match ingest): **`screenshot:batch:jobs`**

No queue object is created in the UI; the list appears on first `LPUSH`.

### Test connectivity from Droplet (optional)

```bash
docker run --rm redis:7 sh -c \
  'redis-cli -u "rediss://default:PASSWORD@HOST:25061" --tls --insecure ping'
```

Expect `PONG`. (`--insecure` is only for this CLI test in the minimal Redis image.)

---

## Phase 4 — Create the Droplet

1. **Create** → **Droplets** → Ubuntu **22.04**.
2. **Region:** same as Spaces / Valkey (e.g. Singapore `sgp1`).
3. **Size:** **2 vCPU / 4 GB** minimum (OCR is CPU-heavy).
4. **SSH key** → create → note **public IP**.
5. Add IP to **Valkey** and **Mongo** trusted sources.

### Install Docker (on Droplet)

```bash
ssh root@DROPLET_IP
apt-get update && apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sh
docker compose version
```

Optional: `ufw allow OpenSSH && ufw enable` (no inbound ports needed for the worker).

---

## Phase 5 — Deploy the worker

### 5.1 Code on server

```bash
mkdir -p /opt/screenshot-worker
cd /opt/screenshot-worker
git clone YOUR_REPO_URL .
cd screenshot_detection_service
```

Or from laptop: `scp -r screenshot_detection_service root@DROPLET_IP:/opt/screenshot-worker/`

### 5.2 Environment file

```bash
cp deploy/env.staging.example .env.staging
nano .env.staging
chmod 600 .env.staging
```

Fill all values — see [env.staging.example](env.staging.example). Minimum:

| Variable | Example / notes |
|----------|-----------------|
| `MONGODB_URI` | `.../CPCRM_DB?...&authSource=admin` |
| `MONGO_BATCHES_COLLECTION` | `device_screenshot_batches` |
| `MONGO_RESULTS_COLLECTION` | `screenshot_analysis_results` |
| `SPACES_*` | Bucket + regional endpoint + keys |
| `QUEUE_BACKEND` | `redis` (works with Valkey) |
| `REDIS_URL` | Full `rediss://...` URI |
| `REDIS_QUEUE_KEY` | `screenshot:batch:jobs` |
| `PHONE_REGION` | `IN` |

### 5.3 Build and run

From `screenshot_detection_service/`:

```bash
docker compose -f deploy/docker-compose.staging.yml up -d --build
docker compose -f deploy/docker-compose.staging.yml logs -f worker
```

Success log:

```text
Queue worker started ... backend=redis batches=device_screenshot_batches ...
```

Verify Valkey from worker container:

```bash
docker compose -f deploy/docker-compose.staging.yml exec worker \
  python -c "import os,redis; print(redis.from_url(os.environ['REDIS_URL']).ping())"
```

Expect `True`.

---

## Phase 6 — Ingest: write Mongo + enqueue

After each batch insert, ingest **must** publish a queue message (worker does **not** scan Mongo for new work).

**Message JSON:**

```json
{"batchId":"<Mongo _id as string>","enqueuedAt":"2026-11-02T12:00:00Z","source":"ingest"}
```

**Node (Valkey):**

```javascript
await redis.lpush(
  process.env.REDIS_QUEUE_KEY || "screenshot:batch:jobs",
  JSON.stringify({
    batchId: insertedId.toString(),
    enqueuedAt: new Date().toISOString(),
    source: "ingest",
  })
);
```

**Python (same repo, same env as worker):**

```python
from worker.publish import publish_batch_job
publish_batch_job(inserted_id, source="ingest")
```

If enqueue fails after Mongo insert, run **reconcile** (Phase 8).

---

## Phase 7 — End-to-end test

### 7.1 Probe storage (one file)

```bash
docker compose -f deploy/docker-compose.staging.yml exec worker \
  python -m worker.probe_storage --device-batch-id YOUR-DEVICE-BATCH-UUID --download
```

Expect `download ok, bytes: ...`.

### 7.2 Enqueue one batch

Use **`deviceBatchId`** (UUID) or Mongo **`_id`**:

```bash
docker compose -f deploy/docker-compose.staging.yml exec worker \
  python -m worker.enqueue_batch --device-batch-id fc1a736e-d59a-4d58-a9cc-c35a2e2cf702
```

Or enqueue all `PENDING` batches:

```bash
docker compose -f deploy/docker-compose.staging.yml exec worker python -m worker.reconcile
```

Reconcile logs: `Reconcile scan db=CPCRM_DB collection=... pending=N`.

### 7.3 Watch processing

```bash
docker compose -f deploy/docker-compose.staging.yml logs -f worker
```

Look for `Item done` and `Batch completed ... failed=0`.

### 7.4 Mongo results

```javascript
use CPCRM_DB

db.screenshot_analysis_results.find({ deviceBatchId: "..." }).limit(1).pretty()
db.device_screenshot_batches.findOne(
  { deviceBatchId: "..." },
  { status: 1, counts: 1 }
)
```

### 7.5 Re-run a failed batch

```javascript
db.device_screenshot_batches.updateOne(
  { deviceBatchId: "..." },
  {
    $set: {
      status: "PENDING",
      "items.$[].status": "completed",
      "items.$[].analysisStatus": "PENDING"
    },
    $unset: { completedAt: "" }
  }
)
```

Then `enqueue_batch` or `reconcile` again.

---

## Phase 8 — Operations

### 8.1 Reconcile cron (safety net)

Every 5 minutes on the Droplet:

```cron
*/5 * * * * cd /opt/screenshot-worker/screenshot_detection_service && docker compose -f deploy/docker-compose.staging.yml exec -T worker python -m worker.reconcile >> /var/log/screenshot-reconcile.log 2>&1
```

### 8.2 Deploy code updates

```bash
cd /opt/screenshot-worker/screenshot_detection_service
git pull
docker compose -f deploy/docker-compose.staging.yml up -d --build
```

### 8.3 Scale workers

Same `.env.staging` and `REDIS_URL` on multiple Droplets or scale compose replicas — each consumer pulls different jobs from the same list.

### 8.4 Monitoring

| Signal | Action |
|--------|--------|
| Valkey list length / queue depth | Add worker replicas |
| `db.device_screenshot_batches.countDocuments({ status: "PENDING" })` high | Fix ingest enqueue or run reconcile |
| Worker CPU sustained high | Bigger Droplet or more replicas |
| `screenshot_analysis_results` with `status: "FAILED"` | Read `error` field; often storage or image format |

---

## Phase 9 — CLI reference

Run inside the worker container (`docker compose ... exec worker ...`):

| Command | Purpose |
|---------|---------|
| `python -m worker.run_queue` | Consumer (Docker CMD) |
| `python -m worker.reconcile` | Re-enqueue `PENDING` batches |
| `python -m worker.enqueue_batch --device-batch-id UUID` | Enqueue one batch by ingest id |
| `python -m worker.enqueue_batch OBJECT_ID` | Enqueue by Mongo `_id` |
| `python -m worker.probe_storage --device-batch-id UUID --download` | Test Spaces download |
| `worker.publish.publish_batch_job(id)` | Called from ingest after insert |

Legacy Mongo poll worker (`python -m worker.run`) — **not** used in queue deployments.

---

## Phase 10 — Production (AWS, later)

Same Docker image and batch schema; swap:

| Staging | Production |
|---------|------------|
| Valkey / `QUEUE_BACKEND=redis` | SQS / `QUEUE_BACKEND=sqs` |
| Spaces | S3 |
| Droplet | ECS Fargate |

See [aws-production.md](aws-production.md) and [QUEUE-FROM-SCRATCH.md](QUEUE-FROM-SCRATCH.md) Part E.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|--------|-----|
| `Re-enqueued 0` but batches exist in Compass | Wrong DB in URI (`db: admin`) | Use `/CPCRM_DB` (your app DB) in path; keep `authSource=admin` |
| `missing batch id=...` | Same as above, or wrong `_id` | Fix URI; enqueue with Mongo `_id` or `--device-batch-id` |
| `NoSuchKey` on Spaces | Wrong endpoint/bucket/key | Regional `SPACES_ENDPOINT`; bucket name only; verify path in Spaces UI |
| `Unsupported file extension .webp` | Old worker image | Rebuild after WebP support in `config.py` |
| Worker exits `Timeout reading from socket` on idle | Old redis client timeout | Rebuild with current `worker/queue_backend.py` |
| `Invalid URI scheme` testing Redis with Docker | `$REDIS_URL` expanded on host | Use `sh -c 'redis-cli -u "$REDIS_URL" ...'` inside container or `--insecure` one-off |
| SSL verify failed in `redis:7` CLI | No CA bundle in image | `--tls --insecure` for test; worker uses Python redis |
| Batch `COMPLETED` but 10 failed | Storage or OCR errors | Check logs and `screenshot_analysis_results.error` |
| Items skipped | Old worker without ingest adapter | Deploy code with `worker/ingest_schema.py` |
| Can't find Redis in DO UI | Product renamed | Create **Valkey** under Databases |

---

## Local development (optional)

```bash
docker run -d -p 6379:6379 redis:7
export QUEUE_BACKEND=redis REDIS_URL=redis://localhost:6379/0
export MONGODB_URI=... LOCAL_STORAGE_ROOT=./screenshots
pip install -r requirements.txt -r requirements-worker.txt
python -m worker.run_queue
```

---

## Related docs

| File | Contents |
|------|----------|
| [env.staging.example](env.staging.example) | Env template |
| [docker-compose.staging.yml](docker-compose.staging.yml) | Droplet compose |
| [QUEUE-FROM-SCRATCH.md](QUEUE-FROM-SCRATCH.md) | Queue design + AWS SQS |
| [DigitalOcean-staging.md](DigitalOcean-staging.md) | Shorter DO overview |
| [STAGING-WALKTHROUGH.md](STAGING-WALKTHROUGH.md) | Step-by-step walkthrough (partially superseded by this doc) |

**Start here for a full rebuild:** this file (**SETUP-FROM-SCRATCH.md**).
