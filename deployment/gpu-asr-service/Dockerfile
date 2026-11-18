# OpenVINO-GenAI-ASR-Dienst fuer Intel Arc (GPU.0). Modell wird beim Build geladen und
# gegen model.sha256 geprueft; es liegt nicht im Repo.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
# Ubuntu 24.04 liefert intel-opencl-icd 23.43; Arc Pro B50 (Battlemage) braucht die Runtime des
# Hosts (26.27 / IGC 2.38.2). Pakete aus den GitHub-Releases, SHA-256 geprueft.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates curl ffmpeg libze1 \
 && rm -rf /var/lib/apt/lists/*
RUN mkdir /tmp/gpu-debs && cd /tmp/gpu-debs \
 && CR=https://github.com/intel/compute-runtime/releases/download/26.27.39122.11 \
 && IGC=https://github.com/intel/intel-graphics-compiler/releases/download/v2.38.2 \
 && curl -fsSL --retry 3 -O "$CR/libigdgmm12_22.10.0_amd64.deb" \
 && curl -fsSL --retry 3 -O "$CR/intel-opencl-icd_26.27.39122.11-0_amd64.deb" \
 && curl -fsSL --retry 3 -O "$CR/libze-intel-gpu1_26.27.39122.11-0_amd64.deb" \
 && curl -fsSL --retry 3 -o "intel-igc-core-2_2.38.2+22051_amd64.deb" "$IGC/intel-igc-core-2_2.38.2%2B22051_amd64.deb" \
 && curl -fsSL --retry 3 -o "intel-igc-opencl-2_2.38.2+22051_amd64.deb" "$IGC/intel-igc-opencl-2_2.38.2%2B22051_amd64.deb" \
 && printf '%s\n' \
  '6031a63d6e8a12ce61c14efc15f2c8e727061286e3820b8594e6d00615e04d54  libigdgmm12_22.10.0_amd64.deb' \
  '6e447a783c99fb5634df298c135a81165be07db98672df96cdf413d22f3e6ac4  intel-opencl-icd_26.27.39122.11-0_amd64.deb' \
  '58420df60d4bf8ac79aba03f7de1b8b60a93e995b18142391077ff735ce7b74b  libze-intel-gpu1_26.27.39122.11-0_amd64.deb' \
  '3dbcbe4e716d62e9bd43a4a476d724cf772b4581dbcdd096d70df382e7ccad7e  intel-igc-core-2_2.38.2+22051_amd64.deb' \
  'e265d191590efd5491bfbbd148c144fdd40aea51e0b57f8651130d2da20b8186  intel-igc-opencl-2_2.38.2+22051_amd64.deb' \
  > SHA256SUMS \
 && sha256sum -c SHA256SUMS \
 && apt-get update && apt-get install -y --no-install-recommends ./*.deb \
 && rm -rf /var/lib/apt/lists/* /tmp/gpu-debs

WORKDIR /srv/gpu-asr
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN pip install -r requirements.txt

# Quelle: https://huggingface.co/OpenVINO/whisper-large-v3-turbo-int8-ov
# Auf eine Revision (Commit-SHA) festnageln, sobald sie bekannt ist; die Pruefung unten
# ist massgeblich: ein abweichender SHA-256 bricht den Build ab.
ARG MODEL_REPO=OpenVINO/whisper-large-v3-turbo-int8-ov
ARG MODEL_REV=b568445dd5dc8c695bde596f8acbb4694fd6ba64
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
