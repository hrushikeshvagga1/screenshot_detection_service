# Step-by-step: deploy worker on DigitalOcean (staging)

Follow in order. Times are approximate.

**Repo path on server:** `/opt/screenshot-worker/screenshot_detection_service`

---

## Phase 0 — Gather info from existing staging (15 min)

You likely already have these from ingest. Write them down before creating the Droplet.

| Item | Where to find it | You need |
|------|------------------|----------|
| **Spaces bucket name** | DO → Spaces → your Space | `SPACES_BUCKET` |
| **Spaces region** | Space settings (e.g. Bangalore = `blr1`) | `SPACES_ENDPOINT=https://blr1.digitaloceanspaces.com` |
| **Example object key** | Open one screenshot in Spaces → copy path | Must match ingest `storageKey` format |
| **MongoDB URI** | DO Managed Mongo or Atlas connection string | Must include **database name** in path |
| **Ingest collection name** | Your backend code / DB | `MONGO_BATCHES_COLLECTION` (default `screenshot_batches`) |

**Check one batch document** in Mongo (Compass or `mongosh`):

- Has `status: "PENDING"` (or will after upload)?
- Each item has `itemId`, `storageKey`, `capturedAt`?
- If field names differ, note them—you may need a small adapter later.

---

## Phase 1 — Spaces access key for the worker (5 min)

The worker only needs to **read** objects.

1. Log in to [DigitalOcean](https://cloud.digitalocean.com).
2. **API** (left sidebar) → **Spaces Keys** → **Generate New Key**.
3. Name it e.g. `screenshot-worker-staging`.
4. Save **Access Key** and **Secret** (secret shown once).

---

## Phase 2 — MongoDB network access (5 min)

1. Open your **Managed Database** (MongoDB) or MongoDB Atlas cluster used by staging.
2. **Trusted sources / Network access:**
   - For first deploy you can temporarily allow **your Droplet’s public IP** (add after Phase 3), or
   - Allow **all** for staging only (tighten later), or
   - Use **VPC** if ingest and DB are already private on DO.
3. Confirm connection string format:

   ```text
   mongodb+srv://USER:PASS@host.mongodb.net/DATABASE_NAME?tls=true&authSource=admin
   ```

   The segment after the host **must be the database name** (worker uses `get_default_database()`).

4. Create index on the batches collection (once):

   ```javascript
   use DATABASE_NAME
   db.screenshot_batches.createIndex({ status: 1, receivedAt: 1 })
   ```

   Replace collection name if yours differs.

---

## Phase 3 — Create the Droplet (10 min)

1. **Create** → **Droplets**.
2. **Region:** same as Spaces (e.g. **Bangalore 1** if Space is `blr1`).
3. **Image:** Ubuntu 22.04 LTS.
4. **Size:** **Basic → Regular → 2 vCPU / 4 GB** (`s-2vcpu-4gb`) minimum recommended.
5. **Authentication:** SSH key (add your laptop’s public key).
6. **Hostname:** e.g. `screenshot-worker-staging`.
7. Create Droplet → note **public IP**.

8. Return to Mongo **trusted sources** → add this Droplet IP if using IP allowlist.

---

## Phase 4 — SSH and install Docker (10 min)

From your laptop:

```bash
ssh root@DROPLET_IP
```

On the Droplet:

```bash
apt-get update
apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

Optional firewall (worker needs **no inbound** ports):

```bash
ufw allow OpenSSH
ufw enable
```

---

## Phase 5 — Deploy the worker code (15 min)

### Option A — Clone from Git (recommended)

```bash
mkdir -p /opt/screenshot-worker
cd /opt/screenshot-worker
git clone YOUR_REPO_URL .
cd screenshot_detection_service
```

### Option B — Copy from laptop

```bash
# On laptop, from repo root:
scp -r screenshot_detection_service root@DROPLET_IP:/opt/screenshot-worker/
ssh root@DROPLET_IP
cd /opt/screenshot-worker/screenshot_detection_service
```

### Configure environment

```bash
cp deploy/env.staging.example .env.staging
nano .env.staging   # or vim
```

Fill in:

- `MONGODB_URI` — full staging URI with database name
- `SPACES_BUCKET`, `SPACES_ENDPOINT`, `SPACES_ACCESS_KEY`, `SPACES_SECRET_KEY`
- `MONGO_BATCHES_COLLECTION` / `MONGO_RESULTS_COLLECTION` if not default

**Permissions:**

```bash
chmod 600 .env.staging
```

### Build and start

From `screenshot_detection_service/`:

```bash
docker compose -f deploy/docker-compose.staging.yml up -d --build
docker compose -f deploy/docker-compose.staging.yml ps
docker compose -f deploy/docker-compose.staging.yml logs -f worker
```

You should see within a few seconds:

```text
Worker started id=... poll=3s batches=screenshot_batches results=screenshot_analysis_results
```

Leave logs running for the test in Phase 6.

---

## Phase 6 — End-to-end test (15 min)

### 6a — Confirm a file exists in Spaces

Upload a test JPEG to your Space (or use an existing screenshot path).  
Example key: `staging/test/sample.jpg`

### 6b — Insert a test batch in Mongo

Use Mongo Compass or `mongosh`:

```javascript
db.screenshot_batches.insertOne({
  status: "PENDING",
  deviceId: "test-device",
  userId: "test-user",
  receivedAt: new Date(),
  claim: { attempts: 0 },
  items: [{
    itemId: "test-item-1",
    storageKey: "staging/test/sample.jpg",
    capturedAt: new Date(),
    status: "PENDING"
  }],
  counts: { total: 1, processed: 0, failed: 0 }
})
```

Use the **exact** `storageKey` format your ingest uses.

### 6c — Watch the worker

Logs should show:

```text
Item done batch=... item=test-item-1 screen=... alert=... reason=...
Batch completed id=... items=1 failed=0
```

### 6d — Check results collection

```javascript
db.screenshot_analysis_results.find().sort({ processedAt: -1 }).limit(1).pretty()
```

Expect `status: "COMPLETED"`, `analysis.screenType`, `analysis.alert`, and full `result` object.

Batch document should move to `status: "COMPLETED"`.

---

## Phase 7 — Connect real ingest (ongoing)

1. Ensure ingest writes the **same batch shape** and `storageKey` format as the test.
2. Keep **one worker** instance on staging (`instance_count: 1`) unless backlog grows.
3. Monitor:

   ```javascript
   db.screenshot_batches.countDocuments({ status: "PENDING" })
   ```

   Should stay near zero under normal load.

---

## Phase 8 — Updates after code changes

On the Droplet:

```bash
cd /opt/screenshot-worker/screenshot_detection_service
git pull
docker compose -f deploy/docker-compose.staging.yml up -d --build
docker compose -f deploy/docker-compose.staging.yml logs -f worker
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| Worker exits on start | Bad env | `docker compose ... logs worker`; check `MONGODB_URI`, all `SPACES_*` set |
| `Cannot resolve storage key` | Wrong `storageKey` format | Match ingest; set `SPACES_BUCKET` for bare paths |
| S3 403 / NoSuchKey | Wrong bucket/key or key permissions | Verify path in Spaces UI; regenerate Spaces key |
| Mongo connection timeout | IP not allowlisted | Add Droplet IP to DB trusted sources |
| Batch stays `PENDING` | Worker not running / wrong collection name | Check logs; verify `MONGO_BATCHES_COLLECTION` |
| `Tesseract not found` | Bad Docker build | Rebuild image; Dockerfile installs `tesseract-ocr` |
| Item `FAILED` | Corrupt image or OCR error | Read `error` field in `screenshot_analysis_results` |

---

## Optional: App Platform instead of Droplet

If you prefer not to SSH to a VM:

1. Push repo to GitHub.
2. DO → **Apps** → **Create App** → **Worker** component.
3. Use `deploy/app.staging.yaml` as a starting spec (edit repo name, region, secrets).
4. Set the same env vars as `.env.staging`.

Droplet + Compose is simpler for debugging OCR and CPU.

---

## Next: production on AWS

When ready, same Docker image; host on ECS and point storage at S3. See [aws-production.md](aws-production.md).
