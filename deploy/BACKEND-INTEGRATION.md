# Backend integration (NestJS) — ingest + queue + reading results

This document is for the **ingest / API team**. The screenshot **worker is already deployed** separately (Docker on a Droplet). The backend does **not** call the worker over HTTP. Integration is:

1. **Write** batch to Mongo (existing ingest flow).
2. **Publish** one queue message with the Mongo `_id`.
3. **Read** outcomes from Mongo when needed (`screenshot_analysis_results` + batch `status`).

Worker repo path: `screenshot_detection_service/worker/`.

---

## End-to-end flow

```text
NestJS ingest
  1. Upload images → DigitalOcean Spaces (existing)
  2. insert device_screenshot_batches { status: "PENDING", items: [...] }
  3. LPUSH Valkey  screenshot:batch:jobs  ← YOU ADD THIS

Worker (separate process)
  4. BRPOP message → claim batch → OCR → write screenshot_analysis_results
  5. Update batch status → COMPLETED / PARTIAL_FAILED / FAILED

NestJS (alerts, UI, webhooks)
  6. Query screenshot_analysis_results and/or watch batch.status
```

---

## What the backend must guarantee before enqueue

| Requirement | Detail |
|-------------|--------|
| Batch `status` | `"PENDING"` when first saved |
| Mongo `_id` | Used as `batchId` in the queue message (24-char ObjectId string) |
| Items | Each item must be readable from Spaces: `gcsBucket` + `gcsPath` (or `storageKey`) |
| Files exist | Objects already in Spaces **before** enqueue (same as today) |
| Database | Same DB as worker, e.g. `CPCRM_DB` in URI path (see worker `.env.staging`) |

Field names `gcsBucket` / `gcsPath` are **Spaces** locations in our staging setup, not Google Cloud Storage.

Supported image types: **`.webp`**, `.jpg`, `.jpeg`, `.png`.

---

## Queue message contract

**List key** (must match worker env `REDIS_QUEUE_KEY`):

```text
screenshot:batch:jobs
```

**Operation:** `LPUSH` (worker uses `BRPOP` — FIFO with LPUSH).

**Payload:** single JSON string (no newlines).

**Minimum (all the worker needs to process):**

```json
{"batchId":"6ae33840f2774758adb1deff"}
```

**Recommended (logging / debugging):**

```json
{
  "batchId": "6ae33840f2774758adb1deff",
  "enqueuedAt": "2026-11-02T06:30:00.000Z",
  "source": "ingest"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `batchId` | **Yes** | `String(insertedBatch._id)` from Mongo — only field used for processing |
| `enqueuedAt` | No | If omitted, worker parser fills with current UTC time; not used after parse |
| `source` | No | If omitted, defaults to `"unknown"`; not used for processing (ops may use `"ingest"` vs `"reconcile"` in logs) |

**Do not** put image bytes or item lists in the queue — only `batchId`.

---

## Environment variables (NestJS / ingest service)

Use the **same Valkey cluster** as the worker:

```env
REDIS_URL=rediss://default:PASSWORD@db-vk-....db.ondigitalocean.com:25061
REDIS_QUEUE_KEY=screenshot:batch:jobs
```

- Use the full **`rediss://`** URI from DigitalOcean (TLS).
- Add the **ingest server IP** to Valkey **trusted sources** if ingest runs outside the VPC.
- Worker and ingest **must** use the same `REDIS_QUEUE_KEY`.

Mongo (already on ingest):

```env
MONGODB_URI=mongodb+srv://...@host/CPCRM_DB?replicaSet=...&tls=true&authSource=admin
```

Collections:

- Inbox: `device_screenshot_batches` (or your name; worker uses `MONGO_BATCHES_COLLECTION`)
- Outbox: `screenshot_analysis_results` (written by worker)

---

## NestJS implementation

### Dependencies

```bash
npm install ioredis
# optional: @nestjs/config if not already used
```

### Queue service

```typescript
// screenshot-batch-queue.service.ts
import { Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import Redis from 'ioredis';

export interface BatchJobMessage {
  batchId: string;
  enqueuedAt: string;
  source: 'ingest' | 'reconcile' | 'manual';
}

@Injectable()
export class ScreenshotBatchQueueService implements OnModuleDestroy {
  private readonly logger = new Logger(ScreenshotBatchQueueService.name);
  private readonly redis: Redis;
  private readonly queueKey: string;

  constructor() {
    const url = process.env.REDIS_URL;
    if (!url) {
      throw new Error('REDIS_URL is required for screenshot batch queue');
    }
    this.queueKey = process.env.REDIS_QUEUE_KEY ?? 'screenshot:batch:jobs';
    this.redis = new Redis(url, {
      maxRetriesPerRequest: 3,
      enableReadyCheck: true,
      // rediss:// in URL enables TLS for DO Valkey
    });
  }

  async onModuleDestroy() {
    await this.redis.quit();
  }

  async publishBatchJob(batchId: string, source: BatchJobMessage['source'] = 'ingest'): Promise<void> {
    const message: BatchJobMessage = {
      batchId: String(batchId),
      enqueuedAt: new Date().toISOString(),
      source,
    };
    const payload = JSON.stringify(message);
    await this.redis.lpush(this.queueKey, payload);
    this.logger.log(`Enqueued screenshot batch job batchId=${message.batchId} key=${this.queueKey}`);
  }
}
```

Register in a module:

```typescript
// screenshot.module.ts
import { Module } from '@nestjs/common';
import { ScreenshotBatchQueueService } from './screenshot-batch-queue.service';

@Module({
  providers: [ScreenshotBatchQueueService],
  exports: [ScreenshotBatchQueueService],
})
export class ScreenshotModule {}
```

### After Mongo insert (ingest handler)

```typescript
// device-screenshot-batch.service.ts (example)
import { Injectable, Logger } from '@nestjs/common';
import { InjectModel } from '@nestjs/mongoose';
import { Model, Types } from 'mongoose';
import { ScreenshotBatchQueueService } from './screenshot-batch-queue.service';

@Injectable()
export class DeviceScreenshotBatchService {
  private readonly logger = new Logger(DeviceScreenshotBatchService.name);

  constructor(
    @InjectModel('DeviceScreenshotBatch') private batchModel: Model<any>,
    private readonly queue: ScreenshotBatchQueueService,
  ) {}

  async createBatchFromDevice(dto: CreateBatchDto) {
    const doc = await this.batchModel.create({
      deviceBatchId: dto.deviceBatchId,
      deviceId: dto.deviceId,
      staffId: dto.staffId,
      status: 'PENDING',
      receivedAt: new Date(),
      items: dto.items,
      counts: { total: dto.items.length, completed: dto.items.length, failed: 0 },
      // ...existing fields
    });

    try {
      await this.queue.publishBatchJob(doc._id.toString(), 'ingest');
    } catch (err) {
      // Batch is saved; worker reconcile cron can re-enqueue PENDING batches.
      // Prefer: retry LPUSH 2–3 times, then alert ops.
      this.logger.error(`Failed to enqueue batch ${doc._id}`, err);
    }

    return doc;
  }
}
```

**Order:** always **insert Mongo first**, then **LPUSH**. If LPUSH fails, the batch stays `PENDING` until ops run reconcile or you retry enqueue.

### Optional: health check

```typescript
async pingValkey(): Promise<boolean> {
  return (await this.redis.ping()) === 'PONG';
}
```

---

## How to “consume” worker output (no worker callback)

The worker **does not** POST to your API. Consume results by reading Mongo:

### Collection: `screenshot_analysis_results`

One document per **screenshot item** (upsert on `batchId` + `itemId`).

**Useful fields for product logic:**

| Field | Description |
|-------|-------------|
| `batchId` | Mongo ObjectId of batch |
| `deviceBatchId` | Your UUID from ingest |
| `itemId` | `jobId` from ingest item |
| `deviceId`, `userId` | From batch |
| `status` | `COMPLETED` or `FAILED` |
| `analysis.severity` | `CRITICAL` \| `HIGH` \| `MEDIUM` \| `LOW` \| `NONE` — primary policy field |
| `analysis.alert` | Boolean — `true` for `CRITICAL`, `HIGH`, and `MEDIUM` (backward compatible) |
| `analysis.reviewRequired` | Manual review |
| `analysis.reasonCode` | e.g. phone in chat message |
| `analysis.screenType` | `CHAT_DETAIL`, `CHATS_LIST`, … |
| `result` | Full POC JSON (optional for UI/debug) |
| `error` | Set when `status === FAILED` |
| `processedAt` | When worker finished |

**Example query (Mongoose):**

```typescript
const results = await this.analysisResultModel
  .find({ deviceBatchId: uuid })
  .select('itemId status analysis processedAt error')
  .lean();
```

**Alerts:** filter `analysis.alert === true` or specific `analysis.reasonCode`.

### Batch document status

`device_screenshot_batches.status` after worker runs:

| Status | Meaning |
|--------|---------|
| `PROCESSING` | Worker claimed batch |
| `COMPLETED` | All items processed successfully |
| `PARTIAL_FAILED` | Some items failed |
| `FAILED` | All items failed |

You can poll `batch.status`, use **MongoDB Change Streams** on `screenshot_analysis_results`, or a cron that processes new rows where `processedAt > lastRun`.

---

## Idempotency and duplicates

- Publishing the **same `batchId` twice** is safe: worker acks duplicate messages if the batch is already finished.
- Ingest should **not** enqueue before Mongo insert succeeds.
- Avoid enqueueing the same batch on every retry of the HTTP request unless you dedupe by `deviceBatchId`.

---

## Failure handling

| Scenario | Suggested backend behavior |
|----------|----------------------------|
| LPUSH fails after Mongo insert | Retry LPUSH; log; ops `worker.reconcile` every 5 min re-enqueues old `PENDING` |
| Item `FAILED` in results | Read `error.message`; often storage or corrupt image |
| Batch stuck `PENDING` | Check worker logs; verify Valkey + enqueue |
| Need re-analysis | Reset batch to `PENDING`, reset item analysis fields, call `publishBatchJob` again |

Worker-side safety net (already on Droplet): cron running `python -m worker.reconcile`.

---

## Checklist for backend team (staging sign-off)

- [ ] `MONGODB_URI` uses app DB path (`/CPCRM_DB`), same as worker
- [ ] Batch insert sets `status: "PENDING"` and valid `gcsBucket` / `gcsPath`
- [ ] After insert, `LPUSH` to `screenshot:batch:jobs` with `{ batchId, enqueuedAt, source: "ingest" }`
- [ ] `REDIS_URL` / `REDIS_QUEUE_KEY` match worker
- [ ] Valkey trusted sources include ingest host IP
- [ ] Product code reads `screenshot_analysis_results` (or change streams)
- [ ] Staging test: one real device batch → results appear within ~minutes depending on queue load

---

## Reference (Python worker)

Same message from worker utilities (for scripts/cron):

```python
from worker.publish import publish_batch_job
publish_batch_job(mongo_id, source="ingest")
```

Message schema: `worker/messages.py` → `BatchJobMessage`.

---

## Related docs

- [SETUP-FROM-SCRATCH.md](SETUP-FROM-SCRATCH.md) — worker + infra setup
- [env.staging.example](env.staging.example) — env template shared with ops
