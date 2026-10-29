# Optional: run the worker on GCP (tick / poll)

**Staging default:** run the worker on **DigitalOcean** — see [DigitalOcean-staging.md](DigitalOcean-staging.md).

This guide is for hosting **compute on GCP** while screenshots stay on **DigitalOcean Spaces** (or GCS).

Your **ingest backend** writes batch documents to MongoDB and stores images in object storage. The worker polls Mongo, downloads each file, runs the POC, and writes results.

Latency target ~5–10 s is fine; polling every **3–5 seconds** is typical.

---

## Architecture (GCP worker + DigitalOcean Spaces)

```text
Device  →  Ingest API (your stack)
              → DigitalOcean Spaces upload
              → MongoDB insert  screenshot_batches { status: PENDING, items: [{ storageKey }] }

Cloud Run (GCP): screenshot-worker
              → poll Mongo
              → claim batch
              → download from Spaces (S3 API + DO endpoint)
              → analyze() → screenshot_analysis_results
```

Cross-cloud is normal: **compute on GCP**, **objects on DO**. Ensure Cloud Run egress can reach `*.digitaloceanspaces.com` (public internet; no special GCP IAM for DO).

---

## GCP services you need

| Service | Role |
|---------|------|
| **Cloud Run** (or GCE) | Long-running poll loop + Tesseract OCR |
| **Artifact Registry** | Docker images |
| **Secret Manager** | `MONGODB_URI`, `SPACES_ACCESS_KEY`, `SPACES_SECRET_KEY` |
| **Cloud Logging / Monitoring** | Worker logs and alerts |
| **MongoDB Atlas** (any cloud) | Batch inbox + results collections |

You do **not** need **GCS** if all screenshots are on DigitalOcean Spaces.

Optional later: **VPC connector** if MongoDB is private-only.

---

## DigitalOcean Spaces (object storage)

1. In DO control panel: **Spaces** → create a Space (note **region**, e.g. `blr1`).
2. Create **Spaces access keys** (API → Spaces keys): Access Key + Secret.
3. **Endpoint** format: `https://<region>.digitaloceanspaces.com`  
   Example: `https://blr1.digitaloceanspaces.com`
4. Store keys in **GCP Secret Manager** (same as Mongo URI)—never commit them.

### `storageKey` formats the worker accepts

| What ingest stores | Example |
|--------------------|---------|
| Bare object key (recommended) | `uploads/device123/2026/shot.jpeg` |
| S3-style URI | `s3://my-space/uploads/shot.jpeg` |
| DO virtual-hosted URL | `https://my-space.blr1.digitaloceanspaces.com/uploads/shot.jpeg` |
| DO path-style URL | `https://blr1.digitaloceanspaces.com/my-space/uploads/shot.jpeg` |

If the key has **no bucket** in the string, set **`SPACES_BUCKET`** to your Space name.

---

## MongoDB collections

### `screenshot_batches` (ingest)

- `status`: `PENDING` | `PROCESSING` | `COMPLETED` | `PARTIAL_FAILED` | `FAILED`
- `deviceId`, `userId`, `receivedAt`
- `items[]`: `{ itemId, storageKey, capturedAt, contentType, status }`

Index: `{ status: 1, receivedAt: 1 }`

### `screenshot_analysis_results` (worker output)

- `batchId`, `itemId`, `deviceId`, `userId`, `storageKey`, `capturedAt`, `processedAt`
- `status`, `processingMs`, `analysis`, `result`, `error`

Unique index: `{ batchId: 1, itemId: 1 }`

---

## IAM on GCP (worker service account)

Create e.g. `screenshot-worker@PROJECT_ID.iam.gserviceaccount.com`:

| Permission | Why |
|------------|-----|
| `roles/secretmanager.secretAccessor` | Mongo + DO Spaces keys |
| `roles/logging.logWriter` | Cloud Logging |

**No GCS roles** required when using Spaces only.

MongoDB Atlas: allow **Cloud Run outbound IPs** (or `0.0.0.0/0` for Atlas with strong auth—tighten in production).

---

## Docker image

```bash
cd whatsapp_screenshot_poc
gcloud auth configure-docker REGION-docker.pkg.dev
docker build -t REGION-docker.pkg.dev/PROJECT_ID/screenshot-worker/worker:latest .
docker push REGION-docker.pkg.dev/PROJECT_ID/screenshot-worker/worker:latest
```

---

## Cloud Run deploy (DigitalOcean Spaces)

```bash
gcloud run deploy screenshot-worker \
  --image REGION-docker.pkg.dev/PROJECT_ID/screenshot-worker/worker:latest \
  --region asia-south1 \
  --platform managed \
  --no-allow-unauthenticated \
  --min-instances 1 \
  --max-instances 3 \
  --cpu 2 \
  --memory 2Gi \
  --timeout 3600 \
  --set-secrets \
MONGODB_URI=mongodb-uri:latest,\
SPACES_ACCESS_KEY=do-spaces-access-key:latest,\
SPACES_SECRET_KEY=do-spaces-secret-key:latest \
  --set-env-vars \
SPACES_BUCKET=your-space-name,\
SPACES_ENDPOINT=https://blr1.digitaloceanspaces.com,\
MONGO_BATCHES_COLLECTION=screenshot_batches,\
MONGO_RESULTS_COLLECTION=screenshot_analysis_results,\
POLL_INTERVAL_SEC=3,\
STALE_PROCESSING_MINUTES=15
```

Use **`--cpu-boost`** or disable CPU throttling if the poll loop stalls (see current Cloud Run docs for your region).

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MONGODB_URI` | Yes | Mongo connection string (include database name). |
| `SPACES_BUCKET` | Yes* | DigitalOcean Space name. |
| `SPACES_ENDPOINT` | Yes* | e.g. `https://blr1.digitaloceanspaces.com` |
| `SPACES_ACCESS_KEY` | Yes* | Spaces access key (secret). |
| `SPACES_SECRET_KEY` | Yes* | Spaces secret key (secret). |
| `GCS_BUCKET` | Alt | Use GCS instead of Spaces (GCP-native storage). |
| `LOCAL_STORAGE_ROOT` | Dev | Local folder; `storageKey` = relative path. |
| `MONGO_BATCHES_COLLECTION` | No | Default `screenshot_batches`. |
| `MONGO_RESULTS_COLLECTION` | No | Default `screenshot_analysis_results`. |
| `POLL_INTERVAL_SEC` | No | Default `3`. |
| `STALE_PROCESSING_MINUTES` | No | Re-queue stuck `PROCESSING` batches. |
| `PHONE_REGION` | No | Default `IN`. |

\*For production with DO, all four Spaces variables are required.  
Aliases supported: `S3_BUCKET`, `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.

---

## End-to-end checklist

1. **DO Space** + keys; ingest writes files and puts **object key** (or full URL) in `items[].storageKey`.
2. **MongoDB** collections + indexes; batches inserted as `PENDING`.
3. **Secrets** in GCP Secret Manager for Mongo + Spaces keys.
4. **Build & push** worker image.
5. **Deploy Cloud Run** with env/secrets above.
6. **Test batch** → Cloud Logging → document in `screenshot_analysis_results`.
7. **Warnings** from `analysis.alert == true`.

---

## Local dry run

```bash
pip install -r requirements.txt -r requirements-worker.txt
export MONGODB_URI="mongodb+srv://.../dbname"
export SPACES_BUCKET="your-space"
export SPACES_ENDPOINT="https://blr1.digitaloceanspaces.com"
export SPACES_ACCESS_KEY="..."
export SPACES_SECRET_KEY="..."
python -m worker.run
```

Dev without Spaces:

```bash
export LOCAL_STORAGE_ROOT=./screenshots
# storageKey in Mongo: "TEST8.jpeg"
python -m worker.run
```

---

## Optional: GCS instead of Spaces

Set `GCS_BUCKET` and grant the Cloud Run service account `roles/storage.objectViewer`.  
Do not set Spaces credentials. Worker uses Application Default Credentials on GCP.

---

## When to add a queue

If `PENDING` depth grows, add Pub/Sub (or similar) with `batchId` messages; keep **Mongo claim** for idempotency.
