# OpenVINO-GenAI-ASR-Dienst fuer Intel Arc (GPU.0). Modell wird beim Build geladen und
# gegen model.sha256 geprueft; es liegt nicht im Repo.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates curl ffmpeg \
    intel-opencl-icd libze1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/gpu-asr
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN pip install -r requirements.txt

# Quelle: https://huggingface.co/OpenVINO/whisper-large-v3-turbo-int8-ov
# Auf eine Revision (Commit-SHA) festnageln, sobald sie bekannt ist; die Pruefung unten
# ist massgeblich: ein abweichender SHA-256 bricht den Build ab.
ARG MODEL_REPO=OpenVINO/whisper-large-v3-turbo-int8-ov
ARG MODEL_REV=main
COPY model.sha256 .
RUN mkdir -p /models/whisper-large-v3-turbo-int8-ov \
 && awk '{print $2}' model.sha256 | while read -r f; do \
      curl -fsSL --retry 3 -o "/models/whisper-large-v3-turbo-int8-ov/$f" \
        "https://huggingface.co/${MODEL_REPO}/resolve/${MODEL_REV}/$f" || exit 1; \
    done \
 && cd /models/whisper-large-v3-turbo-int8-ov && sha256sum -c /srv/gpu-asr/model.sha256

COPY asr_engine.py asr_service.py ./

ENV ASR_MODEL_DIR=/models/whisper-large-v3-turbo-int8-ov \
    ASR_MODEL_NAME=whisper-large-v3-turbo-int8-ov \
    ASR_DEVICE=GPU.0 \
    ASR_TIMEOUT_S=300
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Genau 1 Worker-Prozess: das Modell wird einmal geladen, Requests laufen seriell.
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "0", "--bind", "0.0.0.0:8000", "asr_service:create_wsgi_app()"]
