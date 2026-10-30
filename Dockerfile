# Screenshot analysis worker (DigitalOcean staging, AWS/GCP production)
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-worker.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-worker.txt

COPY config.py main.py ocr_engine.py phone_detector.py screen_classifier.py decision_engine.py ./
COPY worker/ ./worker/

ENV PYTHONUNBUFFERED=1
# Queue consumer (Redis staging / SQS production)
CMD ["python", "-m", "worker.run_queue"]
