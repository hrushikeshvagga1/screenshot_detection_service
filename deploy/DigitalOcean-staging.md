# Staging: screenshot worker on DigitalOcean

Staging runs **entirely on DigitalOcean**:

| Component | DO product |
|-----------|------------|
| Screenshot files | **Spaces** (already in use) |
| Batch inbox + results | **MongoDB** (Managed Database or your existing cluster) |
| Analysis worker | **Droplet + Docker** (recommended) or **App Platform Worker** |
| Container image (optional) | **Container Registry** |

Production on **AWS** comes later—the worker code and env vars stay the same; only hosting and secrets change. See [aws-production.md](aws-production.md).

---

## Architecture (staging)

```text
Devices → Your ingest API (staging on DO)
            → Spaces upload
            → MongoDB  screenshot_batches { status: PENDING }

DO Droplet (or App Platform Worker)
            → python -m worker.run  (poll every 3s)
            → download from Spaces (same region)
            → analyze() → screenshot_analysis_results
```

Keep the **Space region** and **Droplet region** aligned (e.g. `blr1`).

---

## 1. Prerequisites (already on staging)

- [ ] **Spaces** bucket/Space with screenshots; ingest writes `storageKey` (path or URL).
- [ ] **MongoDB** with database name in the connection string.
- [ ] Ingest creates documents in **`screenshot_batches`** (or set `MONGO_BATCHES_COLLECTION`).

Worker creates **`screenshot_analysis_results`** and indexes on first run.

---

## 2. MongoDB contract (align ingest)

**Batch document** (`screenshot_batches`):

```json
{
  "status": "PENDING",
  "deviceId": "device-uuid",
  "userId": "user-uuid",
  "receivedAt": "2026-10-29T12:00:00Z",
  "claim": { "attempts": 0 },
  "items": [
    {
      "itemId": "item-uuid",
      "storageKey": "path/in/space/screenshot.jpeg",
      "capturedAt": "2026-10-29T11:59:00Z",
      "status": "PENDING"
    }
  ],
  "counts": { "total": 1, "processed": 0, "failed": 0 }
}
```

**Index** (run once):

```javascript
db.screenshot_batches.createIndex({ status: 1, receivedAt: 1 })
```

**Network:** If Mongo is **trusted sources only**, add the worker Droplet’s IP (or use DO VPC + private connection string).

---

## 3. Spaces keys for the worker

1. DO Control Panel → **API** → **Spaces access keys** → Generate key.
2. Note **Endpoint** for your region: `https://<region>.digitaloceanspaces.com`.
3. Worker needs **read** access to objects (keys with read permission on that Space).

---

## 4. Recommended: Droplet + Docker Compose

Best for **Tesseract CPU**, predictable **24/7 poll loop**, and simple ops on staging.

### 4.1 Create Droplet

- **Image:** Ubuntu 22.04 LTS  
- **Size:** at least **2 vCPU / 4 GB** (`s-2vcpu-4gb`) for comfortable OCR; 2 GB can work for light load.  
- **Region:** same as Spaces (e.g. Bangalore `blr1`).  
- **Authentication:** SSH keys.

### 4.2 Install Docker on the Droplet

```bash
ssh root@YOUR_DROPLET_IP
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin git
```

### 4.3 Deploy the worker

On the Droplet (or CI that SSH-deploys):

```bash
git clone <your-repo-url> /opt/screenshot-worker
cd /opt/screenshot-worker/whatsapp_screenshot_poc
cp deploy/env.staging.example .env.staging   # file lives in whatsapp_screenshot_poc/
# Edit .env.staging with real MONGODB_URI, SPACES_*, collection names
docker compose -f deploy/docker-compose.staging.yml --env-file .env.staging up -d --build
docker compose -f deploy/docker-compose.staging.yml logs -f worker
```

Updates:

```bash
cd /opt/screenshot-worker/whatsapp_screenshot_poc
git pull
docker compose -f deploy/docker-compose.staging.yml --env-file .env.staging up -d --build
```

### 4.4 Restart policy

Compose file uses `restart: unless-stopped` so the tick loop survives reboots.

---

## 5. Alternative: App Platform (managed worker)

Use if you prefer not to manage a Droplet. Deploy from the repo **Dockerfile** as a **Worker** component (no public HTTP).

1. Push image to **DO Container Registry** or connect GitHub for auto-build.
2. Create App → **Worker** → Dockerfile path `whatsapp_screenshot_poc/Dockerfile`.
3. Set **instance size** ≥ `basic-s` with **2 vCPU** if available.
4. Add env vars / secrets (same as `.env.staging.example`).
5. **Instance count:** 1 (scale later if queue grows).

Example spec skeleton: [app.staging.yaml](app.staging.yaml) (adjust names/regions).

App Platform workers must be allowed **outbound** to MongoDB and Spaces endpoints.

---

## 6. Environment variables (staging)

Copy [env.staging.example](env.staging.example) → `.env.staging` on the server.

| Variable | Staging |
|----------|---------|
| `MONGODB_URI` | `mongodb+srv://.../staging_db` |
| `SPACES_BUCKET` | Staging Space name |
| `SPACES_ENDPOINT` | `https://blr1.digitaloceanspaces.com` |
| `SPACES_ACCESS_KEY` | Spaces key |
| `SPACES_SECRET_KEY` | Spaces secret |
| `MONGO_BATCHES_COLLECTION` | e.g. `screenshot_batches` |
| `MONGO_RESULTS_COLLECTION` | e.g. `screenshot_analysis_results` |
| `POLL_INTERVAL_SEC` | `3` |
| `STALE_PROCESSING_MINUTES` | `15` |
| `PHONE_REGION` | `IN` |

Do **not** commit `.env.staging` to git.

---

## 7. Verify end-to-end

1. Insert a test batch (or send a device batch through ingest).
2. On Droplet: `docker compose ... logs -f worker` — look for `Item done` / `Batch completed`.
3. MongoDB: `db.screenshot_analysis_results.find().sort({ processedAt: -1 }).limit(1)`
4. Confirm `analysis.alert`, `analysis.screenType`, `analysis.reasonCode`.

---

## 8. Monitoring (staging)

- **Logs:** `docker compose logs -f worker` or DO App Platform logs.
- **Backlog:** `db.screenshot_batches.countDocuments({ status: "PENDING" })` — should stay near 0.
- **Failures:** `db.screenshot_analysis_results.countDocuments({ status: "FAILED" })`.
- Optional: DO Monitoring alert on Droplet CPU > 90% sustained.

---

## 9. Security checklist

- Spaces keys: **read-only** scope if DO allows; rotate periodically.
- MongoDB: user with read/write only on staging DB; IP allowlist includes worker.
- Droplet: UFW allow **SSH only**; no inbound ports needed for the worker.
- Results JSON contains **phone numbers**—restrict Mongo access and disable public exposure.

---

## 10. Moving to AWS (production) later

Same Docker image and env pattern:

- Replace `SPACES_*` with **S3** (`SPACES_*` still works via S3-compatible API + `S3_ENDPOINT_URL` for AWS, or add `GCS_BUCKET`-style S3 in worker—today use `s3://` keys + AWS credentials and endpoint `https://s3.<region>.amazonaws.com`).
- Run worker on **ECS Fargate**, **EC2**, or **EKS** with the same `CMD`.
- MongoDB may stay on Atlas or move to DocumentDB—only `MONGODB_URI` changes.

Details: [aws-production.md](aws-production.md).
