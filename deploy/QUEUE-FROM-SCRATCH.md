# Queue-based pipeline — setup from scratch

**Pattern:** Device batch → **Ingest** writes Mongo + **enqueues** → **Workers** consume queue → OCR → **results** Mongo.

| Environment | Queue | Workers | Storage |
|-------------|-------|---------|---------|
| **Staging (DigitalOcean)** | **Managed Redis** | 1+ Droplets, Docker | **Spaces** |
| **Production (AWS)** | **SQS** (+ DLQ) | **ECS Fargate** autoscale | **S3** |

MongoDB holds **`screenshot_batches`** (inbox) and **`screenshot_analysis_results`** (output).  
The queue carries only **`{ "batchId": "..." }`** — not image bytes.

---

## Part A — Architecture (read once)

```text
┌─────────┐     batch ~100s      ┌──────────────┐
│ Mobile  │ ─────────────────► │ Ingest API   │
└─────────┘                    └──────┬───────┘
                                      │
                         1. INSERT batch (status PENDING)
                         2. PUBLISH queue message { batchId }
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              ┌──────────┐     ┌──────────┐     ┌──────────┐
              │  Spaces  │     │  MongoDB │     │  Redis/  │
              │  / S3    │     │  batches │     │  SQS     │
              └──────────┘     └──────────┘     └────┬─────┘
                                                     │
                                              BRPOP / long poll
                                                     │
                                              ┌──────▼───────┐
                                              │ Worker pool  │
                                              │ analyze()    │
                                              └──────┬───────┘
                                                     │
                                              results collection
```

**Ingest must do both steps** in order:

1. Save batch (with `items[].storageKey` pointing to Spaces/S3).
2. Enqueue job (same `batchId` as Mongo `_id`).

If step 2 fails, run **reconcile** (Part F) to re-enqueue `PENDING` batches.

**Worker** (`python -m worker.run_queue`):

1. Receive message.
2. **Claim** batch in Mongo (`PENDING` → `PROCESSING`).
3. Download each item, run POC, write results.
4. **Ack** message (Redis/SQS delete).

Duplicate messages are safe: already-`COMPLETED` batches are acked without reprocessing.

---

## Part B — MongoDB schema (both envs)

### Collection: `screenshot_batches`

```javascript
{
  _id: ObjectId("..."),           // use string form in queue message
  status: "PENDING",
  deviceId: "...",
  userId: "...",
  receivedAt: ISODate("..."),
  claim: { attempts: 0 },
  items: [
    {
      itemId: "uuid",
      storageKey: "path/in/space/file.jpg",
      capturedAt: ISODate("..."),
      status: "PENDING"
    }
  ],
  counts: { total: 10, processed: 0, failed: 0 }
}
```

**Indexes:**

```javascript
db.screenshot_batches.createIndex({ status: 1, receivedAt: 1 })
```

### Collection: `screenshot_analysis_results`

Created by worker. Unique: `{ batchId: 1, itemId: 1 }`.

---

## Part C — Ingest API change (your backend team)

After successful Mongo insert:

**Node example (Redis):**

```javascript
await redis.lpush("screenshot:batch:jobs", JSON.stringify({
  batchId: insertedId.toString(),
  enqueuedAt: new Date().toISOString(),
  source: "ingest"
}));
```

**Python (same repo helper):**

```python
from worker.publish import publish_batch_job
publish_batch_job(inserted_id, source="ingest")
```

**AWS SQS:**

```python
sqs.send_message(
    QueueUrl=os.environ["SQS_QUEUE_URL"],
    MessageBody=json.dumps({"batchId": str(inserted_id), "enqueuedAt": ..., "source": "ingest"}),
)
```

Use **transaction/outbox** later if you need strict “never lost between Mongo and queue”; for staging, insert then enqueue with error logging + reconcile is enough.

---

## Part D — Staging on DigitalOcean (step by step)

### D1. Spaces (existing)

- Note **bucket name**, **region** (`blr1`), **endpoint**.
- Create **Spaces access key** (read for worker; read/write for ingest).

### D2. MongoDB (existing)

- Connection string **with database name**.
- Allow **worker Droplet IP** (and ingest) in trusted sources.
- Create index on `screenshot_batches`.

### D3. Managed Redis (new)

1. DO → **Databases** → **Create** → **Redis**.
2. Same **region** as Spaces.
3. Name: e.g. `screenshot-queue-staging`.
4. **Trusted sources:** your worker Droplet (add IP after D4) or VPC.
5. Copy **connection string** → `REDIS_URL`  
   Often `rediss://default:PASSWORD@host:25061` (TLS).

Queue list key (default): `screenshot:batch:jobs` — must match ingest `LPUSH` key.

### D4. Worker Droplet(s)

1. Ubuntu 22.04, **2 vCPU / 4 GB**, same region.
2. Install Docker (see [STAGING-WALKTHROUGH.md](STAGING-WALKTHROUGH.md)).
3. Clone `screenshot_detection_service`, copy env:

```bash
cp deploy/env.staging.example .env.staging
```

Set:

```env
QUEUE_BACKEND=redis
REDIS_URL=rediss://default:PASSWORD@your-redis-host:25061
REDIS_QUEUE_KEY=screenshot:batch:jobs

MONGODB_URI=...
SPACES_BUCKET=...
SPACES_ENDPOINT=https://blr1.digitaloceanspaces.com
SPACES_ACCESS_KEY=...
SPACES_SECRET_KEY=...
```

4. Start worker:

```bash
docker compose -f deploy/docker-compose.staging.yml up -d --build
docker compose -f deploy/docker-compose.staging.yml logs -f worker
```

Expect: `Queue worker started ... backend=redis`.

### D5. Scale staging workers

Run **multiple containers** or Droplets with same `REDIS_URL` — each message goes to one consumer.  
Increase replicas before increasing Droplet size.

### D6. Test

1. Upload image to Spaces.
2. Insert `PENDING` batch in Mongo.
3. **Enqueue manually:**

```bash
docker compose -f deploy/docker-compose.staging.yml exec worker \
  python -c "from worker.publish import publish_batch_job; publish_batch_job('YOUR_OBJECT_ID')"
```

4. Watch logs + `screenshot_analysis_results`.

---

## Part E — Production on AWS (step by step)

### E1. SQS

1. Create queue `screenshot-batch-jobs` (Standard).
2. Create **DLQ** `screenshot-batch-jobs-dlq`, attach redrive policy (max receives 5).
3. Note **Queue URL**.

### E2. S3 + Mongo

- Ingest uploads to S3; `storageKey` as `s3://bucket/key` or key + `S3_BUCKET`.
- MongoDB Atlas (AWS region) or DocumentDB — same batch schema.

### E3. ECS Fargate worker

1. Build Docker image → **ECR**.
2. Task definition env:

```env
QUEUE_BACKEND=sqs
SQS_QUEUE_URL=https://sqs.ap-south-1.amazonaws.com/ACCOUNT/screenshot-batch-jobs
AWS_REGION=ap-south-1
MONGODB_URI=...
SPACES_* or S3 via IAM role + bucket env
```

3. Task role: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `s3:GetObject`.
4. Service **desired count** start at 2; autoscale on `ApproximateNumberOfMessagesVisible`.

### E4. Ingest on AWS

After Mongo insert → `SQS SendMessage` with `{ batchId }`.

---

## Part F — Reconcile cron (both envs)

Catches batches stuck `PENDING` if enqueue failed.

**Every 5 minutes** (cron on Droplet or ECS scheduled task):

```bash
python -m worker.reconcile
```

Env same as worker. Re-enqueues up to `RECONCILE_BATCH_LIMIT` (default 500) oldest `PENDING` batches.

---

## Part G — Operations checklist

| Monitor | Action |
|---------|--------|
| Redis `LLEN screenshot:batch:jobs` or SQS depth | Scale workers |
| Mongo `count(PENDING)` | Should stay low; run reconcile if high |
| Mongo `count(PROCESSING)` stale | Worker crash; `requeue_stale_processing` runs in worker loop |
| DLQ depth (AWS) | Inspect failed messages |
| Worker CPU | OCR-bound; add vCPU or replicas |

**Device jitter:** random 0–30 s on batch upload interval to avoid synchronized spikes.

---

## Part H — Local dev

```bash
docker run -d -p 6379:6379 redis:7
export QUEUE_BACKEND=redis REDIS_URL=redis://localhost:6379/0
export LOCAL_STORAGE_ROOT=./screenshots MONGODB_URI=...
python -m worker.run_queue
# other terminal:
python -c "from worker.publish import publish_batch_job; publish_batch_job('...')"
```

---

## Part I — Commands reference

| Command | Role |
|---------|------|
| `python -m worker.run_queue` | Main consumer (Docker CMD) |
| `python -m worker.reconcile` | Re-enqueue PENDING batches |
| `worker.publish.publish_batch_job(id)` | Ingest calls after insert |

Legacy tick-only: `python -m worker.run` (not used in queue deployment).

---

## Next steps for you

1. Create **DO Managed Redis** in staging.
2. Update **ingest** to LPUSH after Mongo insert (same key as `REDIS_QUEUE_KEY`).
3. Deploy **one worker** Droplet; run test batch + manual publish.
4. Load-test with synthetic batches; add **2nd worker** if queue depth grows.
5. Before 10K users: load test OCR throughput; plan **ECS + SQS** autoscaling for production.
