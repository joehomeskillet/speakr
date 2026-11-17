# Windows import agent

Clinic fork agent that imports dictation files from explicitly configured folders or removable-device roots. It is a machine principal. It is not a speaker, a recorder, or a remote-control client.

## Scope

The library watches only configured absolute roots (not recursive unless that root says so). A file enters the inventory after the configured number of unchanged size and mtime samples at least `StabilityIntervalSeconds` apart. The inventory is relative path, size, mtime and SHA-256. Bytes are copied into a bounded AES-GCM spool (random nonce, entry name bound as AAD, size taken from the directory, not from an index file) and uploaded unchanged. A committed entry leaves the spool on the next pass; entries of vanished or changed versions are dropped.

Upload follows the clinic ingest contract: `POST /api/clinic/v1/ingests`, chunk `PUT` with `X-Chunk-SHA256` (max 4 MiB, never more than 10000 chunks per file), `POST .../finalize`. Finalize answers 202; the agent re-reads the ingest a bounded number of times and treats anything but a durable `STORED` receipt as unknown. The server-supplied ingest id must be a canonical GUID and must stay identical for the whole session. Server-reported chunk or byte state beyond the local snapshot is a conflict. Transport is HTTPS only (validated certificate, TLS 1.2/1.3, no redirects, mTLS client certificate loaded ephemerally and checked for validity).

An intent is accepted only when its kind is `import_snapshot` and it names a source id plus snapshot version the agent just reported. An intent that names another agent or carries a wrong signature is dropped. The server receipt must be `STORED` / durability `STORED` for that ingest, hash and size, and its decode state must not be `INVALID`. One failing intent does not stop the others in the same pass; the first failure is rethrown after the pass.

Move-to-backup or delete runs only when `PostImportEnabled` is true (default false), the intent carries a valid signature for this agent (otherwise `RefusedUnbound`, the file is untouched), and the file still matches the snapshot. The source directory entry is first renamed to a unique hold name; hash, size and mtime are verified on that captured file, and only the captured file is deleted or copied (copy is re-hashed before the original is removed). A file that appears under the original name afterwards is never touched; a mismatch restores the original name.

Credentials come from `ISecretStore`. The Windows host reads a DPAPI-protected file (CurrentUser scope, application entropy, refused when the file ACL grants read to Everyone, Users or Authenticated Users). Logs and request URLs must not contain the certificate password or spool key; server-controlled strings are stripped of control characters before they are logged.

`IImportServer` has two implementations: `HttpImportServer` (HttpClient, client certificate) and `SimulatorImportServer` (in-memory, used by tests).

## Prohibitions

No microphone or audio capture, no playback, no text editor, no hotkey registration, no clipboard, no Office or window automation, no SendInput, no HID control, no firmware flashing, no local HTTP or WebSocket listener, no process execution, no generic execute or open-file command, no browser-to-localhost tunnel. Tests scan the `src` tree for forbidden fragments and the metadata of both built assemblies for process, socket, pipe, native-interop, registry and assembly-loader references.

## Open contract gaps

`clinic-api.openapi.json` defines the ingest paths only. These are not in the contract and are client assumptions until the contract defines them:

- `PUT /api/clinic/v1/agent/inventory` and `GET /api/clinic/v1/agent/intents` (paths and schemas for inventory report and import intent).
- Intent binding fields `agent_id` and `signature` (HMAC-SHA256, key from the secret store, canonical text in `IntentBinding.Sign`). Without a valid binding the agent uploads what an inventory-matching intent names but never deletes or moves.
- A post-import outcome report (`deleted`, `moved`, `conflict`) back to the server for audit.
- An abort/cancel path for an ingest session after a 409.
- Whether a `STORED` receipt must carry a non-null `dictation_id` before source removal.
- The `agent_id` in the inventory body is self-asserted; the server must take the identity from the mTLS certificate.

## Build and test

From the worktree root:

```
/root/.dotnet/dotnet test apps/windows-import
```

The library and the tests run on Linux. The worker project is a Windows-service-capable generic host and builds on Linux.

## Not proven

Real Philips / LFH / DPM devices, docks and firmware. Windows service installation. Code signing. A live mTLS handshake against clinic ingress. DPAPI unprotect and the ACL check on Windows (this host is Linux). Windows path forms (8.3 names, alternate data streams, device paths, junctions) are rejected by code that only runs on Windows and are not exercised here. Residual race: a file that is replaced or relinked between listing and read in `Collect` is narrowed by a re-check after the read, not eliminated (.NET exposes no final-path-by-handle). The hold-name rename leaves a window for a party with write access to the source directory who can guess the hold name; the name is random. Hardware acceptance tests stay `not_run`.
