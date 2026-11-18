# Speaker service: local diarization + speaker embeddings (CPU only).
# Build context: deployment/speaker-service/
FROM python:3.11-slim

ARG SPEAKER_SEG_ARCHIVE_SHA256=24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488
ARG SPEAKER_SEG_MODEL_SHA256=220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079
ARG SPEAKER_EMB_MODEL_SHA256=1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SPEAKER_MODEL_DIR=/models \
    SPEAKER_SEG_MODEL_SHA256=${SPEAKER_SEG_MODEL_SHA256} \
    SPEAKER_EMB_MODEL_SHA256=${SPEAKER_EMB_MODEL_SHA256}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Models are downloaded with a fixed SHA-256; the build fails on any mismatch.
COPY fetch_models.py .
RUN SPEAKER_SEG_ARCHIVE_SHA256=${SPEAKER_SEG_ARCHIVE_SHA256} \
    python fetch_models.py /models \
    && rm fetch_models.py

COPY THIRD-PARTY-NOTICES /app/THIRD-PARTY-NOTICES
COPY speaker_service ./speaker_service

RUN useradd --system --no-create-home speaker
USER speaker
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4).status == 200 else 1)"

# One worker: models are loaded once (about 660 MB RSS); requests are serialised.
CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "1", "--threads", "2", "--timeout", "3600", "speaker_service.wsgi:app"]
