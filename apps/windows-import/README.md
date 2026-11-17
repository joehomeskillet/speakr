# Windows import agent

Clinic fork agent that imports dictation files from explicitly configured folders or removable-device roots. It is a machine principal. It is not a speaker, a recorder, or a remote-control client.

## Scope

The library watches only configured absolute roots (not recursive unless that root says so). A file enters the inventory after three unchanged size and mtime samples at least two seconds apart. The inventory is path, size, mtime and SHA-256. Bytes are copied into a bounded AES-GCM spool and uploaded unchanged.

Upload follows the clinic ingest contract: `POST /api/clinic/v1/ingests`, chunk `PUT` with `X-Chunk-SHA256` (max 4 MiB), `POST .../finalize`. Inventory and typed intents use `PUT /api/clinic/v1/agent/inventory` and `GET /api/clinic/v1/agent/intents`. Those two routes are the agent channel from the import contract. `queue_import` stays a user command. The machine principal does not call it and does not call `resolve-author`.

An intent is accepted only when its kind is `import_snapshot` and it names a source id plus snapshot version the agent just reported. The server receipt must be `STORED` / durability `STORED` for that ingest, hash and size. Move-to-backup or delete runs only when `PostImportEnabled` is true (default false) and the live file still matches that snapshot. A changed source is a conflict. The file stays.

Credentials come from `ISecretStore`. The Windows host reads a DPAPI-protected file. Logs and request URLs must not contain the certificate password or spool key.

`IImportServer` has two implementations: `HttpImportServer` (HttpClient, client certificate) and `SimulatorImportServer` (in-memory, used by tests).

## Prohibitions

No microphone or audio capture, no playback, no text editor, no hotkey registration, no clipboard, no Office or window automation, no SendInput, no HID control, no firmware flashing, no local HTTP or WebSocket listener, no process execution, no generic execute or open-file command, no browser-to-localhost tunnel. Tests scan the built assembly references and the `src` tree.

## Build and test

From the worktree root:

```
/root/.dotnet/dotnet test apps/windows-import
```

The library and the tests run on Linux. The worker project is a Windows-service-capable generic host and builds on Linux.

## Not proven

Real Philips / LFH / DPM devices, docks and firmware. Windows service installation. Code signing. A live mTLS handshake against clinic ingress. DPAPI unprotect on Windows (this host is Linux). Hardware acceptance tests stay `not_run`.
