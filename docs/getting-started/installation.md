# Install the clinic build

[Deutsch](../de/getting-started/installation.md)

These instructions build the fork from source for a single-host evaluation. Choose the public `main` branch. Record the commit so you can rebuild the same release.

```sh
git clone --branch main --single-branch https://github.com/joehomeskillet/speakr.git
cd speakr
git rev-parse HEAD
docker build --build-arg PRODUCTION=1 -t speakr-clinic:local .
```

## Prepare the services

Use a Linux x86-64 Docker host with enough free storage for images, audio, backups and model weights. The multistage [Dockerfile](https://github.com/joehomeskillet/speakr/blob/main/Dockerfile) downloads pinned Python packages, frontend assets, FFmpeg and the DSS decoder at build time. Allow build-time access to those sources. Run model inference on your own infrastructure. A successful web image build supplies neither weights nor a recognition service.

Provision a private Docker network named `clinic-services` and connect an operator-managed ASR service. The example below assumes that service resolves as `asr` on port 9000 and implements the multipart `POST /asr` API. Substitute the real private service address and port. An OpenAI-compatible **local** server uses the [other connector](../advanced/custom-connectors.md).

Create a private configuration file at `~/.config/speakr-clinic/app.env`, readable only by the operator (mode 0600). Use these settings for the ASR example:

```dotenv
CLINIC_MODE=true
TRANSCRIPTION_CONNECTOR=asr_endpoint
ASR_BASE_URL=http://asr:9000
```

Set `SECRET_KEY` in that file to a unique strong random value supplied by your secret store. Keep it stable across restarts and replicas. Leave the upstream `ADMIN_USERNAME`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` variables unset; use the clinic bootstrap below. For optional text generation configure a separately approved local model endpoint (`TEXT_MODEL_BASE_URL`) and its served `TEXT_MODEL_NAME`. Keep hosted provider credentials out of this configuration.

## Start and open

```sh
docker run -d --name speakr-clinic --network clinic-services \
  --env-file "$HOME/.config/speakr-clinic/app.env" \
  --publish 127.0.0.1:8899:8899 \
  --volume speakr-clinic-instance:/data/instance \
  --volume speakr-clinic-uploads:/data/uploads \
  speakr-clinic:local
```

The image defaults to SQLite in the instance volume for this evaluation. For a PostgreSQL deployment, provision the database and login first, set `SQLALCHEMY_DATABASE_URI` to the operator-managed PostgreSQL connection string in the private environment, and back up the database separately from the upload volume. The checked-in [Compose example](https://github.com/joehomeskillet/speakr/blob/main/config/docker-compose.example.yml) names an upstream image. If adapting it, set the app image to the exact clinic tag you built, use the private environment and retain both volume mounts.

Open `http://localhost:8899/login` on the Docker host. For users on other computers or phones, terminate trusted HTTPS at your reverse proxy and forward to the private app listener. Remote microphones need a secure browser context.

## Create the first identity administrator

Run this once against the same application and database. Replace the example account and address with the deployment’s chosen technical contact before running it.

```sh
docker exec -it -e SPEAKR_SKIP_BACKGROUND_TASKS=true speakr-clinic \
  python scripts/clinic_bootstrap_admin.py \
  --username clinic-admin --email admin@example.invalid
```

Enter the passphrase at the hidden prompt (15–128 characters). Usernames have 2–20 characters. An already initialized installation reports that it made no change. Sign in at `/login`, create a separate test author through user management and grant only the resources required for a [first dictation](../getting-started.md).

Before inviting users, verify playback, local transcription, reopening a saved correction, scoped access from a second account, and a restore of the database plus associated audio into an isolated instance. Keep build configuration, model identifiers, permissions and the tested commit with your backup procedure. Submit changes to the fork via `dev`.

## Video input on a shared ingestion route

Leave `VIDEO_PASSTHROUGH_ASR=false` for an audio-only service. Setting it to true sends the full original video to ASR on the shared ingestion path; standard audio-only APIs reject unsupported video containers. The clinic dictation form is designed for audio files. Test using synthetic material before enabling video for any separate ingestion route.

## Import from watched folders

For clinic dictations, the Windows import agent uses the folders, devices and filename patterns approved for each source. The list below applies to the separate, optional server-side watch folder in upstream Speakr.

Only files with one of these extensions are picked up, in any letter case: `aac`, `aiff`, `amr`, `asf`, `avi`, `caf`, `flac`, `flv`, `m4a`, `m4v`, `mkv`, `mov`, `mp3`, `mp4`, `mpeg`, `mpg`, `mts`, `oga`, `ogg`, `ogv`, `opus`, `ts`, `vob`, `wav`, `weba`, `webm`, `wma`, `wmv`, `3gp` and `3gpp`. Files with other extensions stay in the folder. The monitor logs them at INFO the first time it skips them.

## Maintenance and upgrades

Keep TLS at the proxy and publish only the private app listener. Restrict access to the app configuration and backups. Keep the same session secret on any replicas. Provision database, instance and upload paths with the permissions of the actual container process. Do not keep uploads inside an ephemeral container filesystem.

Before upgrading, record the running image digest and source commit, take a consistent database and audio backup, and restore it in an isolated test environment. Build the candidate from the target `main` commit. Give it its own test database and volumes. Check login, media playback, creation of a test draft, local recognition and a saved correction. During the actual switch, drain active jobs and use one queue owner. Retain the previous image and the matching backup until the release passes its checks.

For capacity planning, measure the size of your usual recording, the growth of the upload and database volumes, and the time spent waiting versus recognizing. Test a realistic simultaneous load before raising worker counts. See [processing jobs](../advanced/job-queue-system.md) and [storage administration](../admin-guide/storage.md).

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/getting-started/installation.md).
