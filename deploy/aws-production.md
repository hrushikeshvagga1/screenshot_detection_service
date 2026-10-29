# Production on AWS (later)

Staging uses **DigitalOcean** end-to-end ([DigitalOcean-staging.md](DigitalOcean-staging.md)).  
Production keeps the **same worker image and tick logic**; only infrastructure and secrets change.

---

## What stays the same

- Docker image (`Dockerfile`, Tesseract + `python -m worker.run`)
- Mongo collections: `screenshot_batches` → `screenshot_analysis_results`
- Poll + atomic claim in Mongo
- POC analysis code (`main.analyze`)

---

## What changes on AWS

| Staging (DO) | Production (AWS) |
|--------------|------------------|
| Spaces | **S3** bucket |
| `SPACES_*` env | **`SPACES_*` or S3**: set `S3_ENDPOINT_URL=https://s3.ap-south-1.amazonaws.com`, `S3_BUCKET=...`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (or IAM role on ECS/EC2—prefer role over keys) |
| Droplet / DO App Worker | **ECS Fargate** service (min 1 task) or **EC2** + Docker |
| DO Managed Mongo | **MongoDB Atlas** (AWS region) or DocumentDB if you standardize on AWS |

The worker’s boto3 client already supports any S3-compatible endpoint; for **native AWS S3**, use path-style or `s3://bucket/key` `storageKey` values and IAM task role (no static keys on ECS).

---

## Suggested AWS layout

```text
Ingest API (existing) → S3 PutObject → Mongo insert PENDING
ECS Fargate: screenshot-worker (1+ tasks, CPU 1024–2048)
  → poll Mongo → GetObject S3 → analyze → write results
Optional: EventBridge/Lambda for alerts on analysis.alert
```

---

## Migration checklist

1. Create S3 bucket + IAM policy `s3:GetObject` on prefix used by ingest.
2. Point ingest `storageKey` to `s3://bucket/key` or bare key + `S3_BUCKET`.
3. Build/push image to **ECR**; deploy ECS service with secrets from **Secrets Manager**.
4. Run parallel worker in staging (DO) until production worker is verified; then disable DO worker.
5. Load-test pending batch depth and scale ECS `desiredCount`.

No code fork required—only env vars and deployment manifests.
