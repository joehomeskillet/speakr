###############################################################################
# Stage 1: Builder — install Python deps and download vendor assets
###############################################################################
# The base image is pinned by digest, so a changed image under the same tag is
# never used unnoticed; Dependabot proposes the update when the tag moves.
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS builder

ARG PRODUCTION=0
ARG LIGHTWEIGHT=0

WORKDIR /app

# gcc is needed to compile C extensions during pip install
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from the lock files: every package, including
# the dependencies of dependencies, at an exact version and checked against
# its hash, so a new or changed release on PyPI never reaches an image
# unnoticed. Regenerate them with scripts/lock-requirements.sh.
COPY requirements.lock requirements-embeddings.lock ./
RUN if [ "$LIGHTWEIGHT" = "0" ]; then LOCK=requirements-embeddings.lock; else LOCK=requirements.lock; fi && \
    pip install --no-cache-dir --prefix=/install --require-hashes --no-deps -r "$LOCK"

# Download vendor assets (JS/CSS/fonts)
RUN mkdir -p /app/static/vendor
COPY scripts/download_offline_deps.py scripts/
RUN PRODUCTION=${PRODUCTION} python scripts/download_offline_deps.py && \
    echo "✓ Vendor dependencies downloaded successfully"

###############################################################################
# Stage 2: FFmpeg — download static binaries (much smaller than apt ffmpeg)
#
# Source: BtbN/FFmpeg-Builds. We moved off the johnvansickle static builds
# because that mirror is frozen at 7.0.2 (2024) and therefore ships the MagicYUV
# decoder flaw CVE-2026-8461 ("PixelSmash", heap out-of-bounds write, RCE via
# crafted media), fixed upstream in 8.1.2.
#
# Supply-chain hardening: we pin a dated release (not BtbN's rolling `latest`
# tag) and verify each arch tarball's SHA-256, so a swapped or tampered binary
# fails the build. NOTE: BtbN deletes daily autobuilds after roughly two weeks
# but keeps the last build of each month, so pin a month-end tag
# (autobuild-YYYY-MM-<last day>-*); a daily tag fails with a wget 404 once it is
# removed. To refresh, bump BTBN_TAG, FFMPEG_VER and BOTH checksums together
# (read the new values from the release's checksums.sha256). BtbN binaries nest
# under bin/, hence the adjusted move paths.
###############################################################################
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS ffmpeg-stage

ARG BTBN_TAG=autobuild-2026-10-31-12-59
ARG FFMPEG_VER=n8.1.3-27-g8dca1586f4
ARG FFMPEG_SHA256_amd64=41e9c565594c390622cf968d82fa4135d9e9a11d06c1515644288cbc844e3380
ARG FFMPEG_SHA256_arm64=a08c0ee6e1c4e02139344b303002dcc7c37cf125d639f1fbd15dabee40fcb525

RUN apt-get update && apt-get install -y --no-install-recommends wget xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && case "$(dpkg --print-architecture)" in \
         amd64) BTBN_ARCH=linux64;    SHA256="${FFMPEG_SHA256_amd64}" ;; \
         arm64) BTBN_ARCH=linuxarm64; SHA256="${FFMPEG_SHA256_arm64}" ;; \
         *) echo "Unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
       esac \
    && ASSET="ffmpeg-${FFMPEG_VER}-${BTBN_ARCH}-gpl-8.1.tar.xz" \
    && wget -q "https://github.com/BtbN/FFmpeg-Builds/releases/download/${BTBN_TAG}/${ASSET}" -O /tmp/ff.tar.xz \
    && echo "${SHA256}  /tmp/ff.tar.xz" | sha256sum -c - \
    && mkdir -p /tmp/ffmpeg-dir \
    && tar xf /tmp/ff.tar.xz -C /tmp/ffmpeg-dir --strip-components=1 \
    && mv /tmp/ffmpeg-dir/bin/ffmpeg /usr/local/bin/ffmpeg \
    && mv /tmp/ffmpeg-dir/bin/ffprobe /usr/local/bin/ffprobe \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && rm -rf /tmp/ff.tar.xz /tmp/ffmpeg-dir

###############################################################################
# Stage 2b: dss-decode — the DS2/DSS dictation decoder (dss-codec, MIT), built
# from the pinned upstream commit. The resulting binary's SHA-256 must equal
# the one recorded in clinic-evidence/s06-ds2-decoding.json, otherwise the
# build fails (supply-chain check; also fails when the toolchain produces a
# different binary: then bump the rust image tag and DSS_CODEC_BINARY_SHA256
# together with a new S06 evidence run). The runtime stage sets CLINIC_DSS_CODEC_BIN.
###############################################################################
FROM rust:1.88-slim-bookworm@sha256:38bc5a86d998772d4aec2348656ed21438d20fcdce2795b56ca434cf21430d89 AS dss-codec-stage

ARG DSS_CODEC_REPO=https://github.com/hirparak/dss-codec.git
ARG DSS_CODEC_COMMIT=c38d3192239005b0570c5987cbde62e69dd7dbbe
# Binary hash of the build from the digest-pinned rust image above. It differs
# from the first local S06 build (f1bfd338…, different toolchain); functional
# equivalence was proven on the real DS2 fixture: both binaries decode it to a
# WAV with sha256 27c84c90… (= clinic-evidence/s06-ds2-decoding.json
# decoded_output.output_sha256). See the docker_build section of that file.
ARG DSS_CODEC_BINARY_SHA256=e8f84918dfed24b4f0ec3bd8653f431e1a90f0b14176a9280bd3f56d3317570f

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git init /src/dss-codec \
    && git -C /src/dss-codec fetch --depth 1 "${DSS_CODEC_REPO}" "${DSS_CODEC_COMMIT}" \
    && git -C /src/dss-codec checkout --detach FETCH_HEAD \
    && test "$(git -C /src/dss-codec rev-parse HEAD)" = "${DSS_CODEC_COMMIT}" \
    && cd /src/dss-codec/dss-codec \
    && if [ -f Cargo.lock ]; then cargo build --release --locked --bin dss-decode; \
       else cargo build --release --bin dss-decode; fi \
    && echo "${DSS_CODEC_BINARY_SHA256}  target/release/dss-decode" | sha256sum -c - \
    && install -m 0755 target/release/dss-decode /usr/local/bin/dss-decode

###############################################################################
# Stage 3: CSS - compile static/css/tailwind.css with the standalone Tailwind
# CLI (no Node.js). Only the class names found in templates, static/js and src
# are compiled (tailwind.config.js). The CLI release is pinned and each arch
# binary's SHA-256 is verified; scripts/build_css.sh holds the same pin for
# local builds, and both are updated together.
###############################################################################
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS css-stage

ARG TAILWIND_VERSION=v3.4.19
ARG TAILWIND_SHA256_amd64=4af3198c015616ea7d6617974ec3d70d987ecc00c1ca8463b0a30fd65cc7c06e
ARG TAILWIND_SHA256_arm64=e5b2d27694daa80cc52ec29553ba2c6bd43d86bd51a9d633ed24058b9c05a676

RUN apt-get update && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/* \
    && case "$(dpkg --print-architecture)" in \
         amd64) TW_ARCH=x64;   SHA256="${TAILWIND_SHA256_amd64}" ;; \
         arm64) TW_ARCH=arm64; SHA256="${TAILWIND_SHA256_arm64}" ;; \
         *) echo "Unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
       esac \
    && wget -q "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-${TW_ARCH}" -O /usr/local/bin/tailwindcss \
    && echo "${SHA256}  /usr/local/bin/tailwindcss" | sha256sum -c - \
    && chmod +x /usr/local/bin/tailwindcss

WORKDIR /build
COPY tailwind.config.js ./
COPY static/css/tailwind.input.css static/css/tokens.css static/css/
COPY templates templates
COPY static/js static/js
COPY src src
RUN tailwindcss -c tailwind.config.js -i static/css/tailwind.input.css -o static/css/tailwind.css --minify

###############################################################################
# Stage 4: Runtime — lean final image with only what's needed
###############################################################################
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

WORKDIR /app

# Copy static ffmpeg binaries (~150MB vs ~450MB from apt)
COPY --from=ffmpeg-stage /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-stage /usr/local/bin/ffprobe /usr/local/bin/ffprobe

# DS2/DSS decoder (hash-verified in dss-codec-stage); see src/clinic/decoder.py
COPY --from=dss-codec-stage /usr/local/bin/dss-decode /usr/local/bin/dss-decode

# The base image's setuptools is only needed to install packages, and its
# vendored copies of wheel and jaraco.context carry advisories. It is removed
# before the locked packages are copied in, so the lite image has none (pytz
# falls back when pkg_resources is missing) and the full image keeps only the
# version its lock pins for torch.
RUN pip uninstall -y -q setuptools

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy downloaded vendor assets from builder
COPY --from=builder /app/static/vendor /app/static/vendor

# Copy application code
COPY . .

# Compiled Tailwind stylesheet (not in git; see the css-stage above)
COPY --from=css-stage /build/static/css/tailwind.css /app/static/css/tailwind.css

# Create necessary directories
RUN mkdir -p /data/uploads /data/instance && chmod 755 /data/uploads /data/instance

# Set environment variables
ENV FLASK_APP=src/app.py
ENV SQLALCHEMY_DATABASE_URI=sqlite:////data/instance/transcriptions.db
ENV UPLOAD_FOLDER=/data/uploads
ENV PYTHONPATH=/app
ENV HF_HOME=/data/instance/huggingface
ENV CLINIC_DSS_CODEC_BIN=/usr/local/bin/dss-decode

# Add entrypoint script
COPY scripts/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8899

ENTRYPOINT ["docker-entrypoint.sh"]
# Threaded workers: streaming responses (chat/Inquire SSE, audio/video range
# requests) hold a request slot for their duration; with sync workers 3 such
# streams made the whole app unresponsive (#374). gthread gives 3x8 = 24
# concurrent requests; the app is thread-tolerant (background job threads,
# SQLite WAL) and streams are I/O-bound. The control socket of gunicorn 25.1+
# (runtime worker management through gunicornc) is not used, so it is off.
CMD ["gunicorn", "--workers", "3", "--worker-class", "gthread", "--threads", "8", "--bind", "0.0.0.0:8899", "--timeout", "600", "--no-control-socket", "src.app:app"]
