# Windows import agent

Clinic fork agent that imports dictation files from explicitly configured folders or removable-device roots. It is a machine principal. It is not a speaker, a recorder, or a remote-control client.

## Scope

The library watches only configured absolute roots (not recursive unless that root says so). A file enters the inventory after the configured number of unchanged size and mtime samples at least `StabilityIntervalSeconds` apart. The inventory is relative path, size, mtime and SHA-256. Bytes are copied into a bounded AES-GCM spool (random nonce, entry name bound as AAD, size taken from the directory, not from an index file) and uploaded unchanged. A committed entry leaves the spool on the next pass; entries of vanished or changed versions are dropped.

Upload follows the agent channel. `PUT /api/clinic/v1/agent/inventory` omits `agent_id` (identity is the client certificate). `GET /api/clinic/v1/agent/intents` sends no agent id. `POST /api/clinic/v1/agent/ingests` sends only `intent_id`, `declared_size` and `declared_sha256`, plus an `Idempotency-Key`. Chunk `PUT` sends `X-Chunk-SHA256` and `X-Chunk-Offset` (max 4 MiB, never more than 10000 chunks). `POST .../finalize` expects HTTP 200 and a terminal state (`STORED`, `INVALID`, `UNKNOWN`, `ABORTED`). The agent re-reads only while the state is not terminal and accepts only a durable `STORED` receipt. `UNKNOWN` is never treated as stored. A begin that already reports `STORED` is not uploaded again. The server-supplied ingest id must be a canonical GUID and must stay identical for the whole session. Server-reported chunk or byte state beyond the local snapshot is a conflict. Transport is HTTPS only (validated certificate, TLS 1.2/1.3, no redirects, mTLS client certificate loaded ephemerally and checked for validity).

A source id is `rootId:relative-path` with slashes replaced by colons when that fits the contract id pattern (at most 128 of `A-Z a-z 0-9 . _ : @ -`). Otherwise it is the SHA-256 of the root id and the relative path. Relative paths may be up to 1024 characters and must not contain an empty, `.` or `..` segment or a drive prefix.

An intent is accepted only when its kind is `import_snapshot`, it names a source id plus snapshot version the agent just reported, and its HMAC-SHA256 signature is version `speakr-import-intent-v2`. The signed text is eight LF-joined lines and no trailing LF: version, intent id, kind, agent id, source id, snapshot version, post step, `expires_at` (`yyyy-MM-ddTHH:mm:ssZ`). An intent that names another agent, carries a wrong signature, or is already expired is dropped. The receipt must repeat the source id, snapshot version, intent id and size. `permitted_post_step` is `none`, `move_backup` or `delete`. `deletion_permitted` is true only together with `delete`. One failing intent does not stop the others in the same pass; the first failure is rethrown after the pass. Outcomes (`stored`, `moved`, `deleted`, `conflict`, `refused`) go to `POST /api/clinic/v1/agent/outcomes`. A stored upload whose post step was not performed is also reported as `refused`. Transient upload failures are not reported. `POST .../abort` is implemented and is not called on the retry path, because the server replays an aborted session for the same idempotency key.

Move-to-backup or delete runs only when `PostImportEnabled` is true (default false), the intent signature is valid (otherwise `RefusedUnbound`), the receipt permits that step, and the file still matches the snapshot. The source directory entry is first renamed to a unique hold name; hash, size and mtime are verified on that captured file, and only the captured file is deleted or copied (copy is re-hashed before the original is removed). A file that appears under the original name afterwards is never touched; a mismatch restores the original name.

Credentials come from `ISecretStore`. The Windows host reads a DPAPI-protected file (CurrentUser scope, application entropy). A secret file that is missing, not a DPAPI blob, or whose ACL cannot be verified stops the process with `SecretStoreException` before the host runs. An ACL that grants read to Everyone, Users or Authenticated Users does the same. The message names the problem and does not include the path, the password or the key bytes. Cycle failures are logged and, on Windows, written to the Application event log; a failure of that write does not crash the cycle. Server-controlled strings are stripped of control characters before they are logged.

On Windows, a path segment that is a generated 8.3 alias is refused: all capitals, one tilde, a short prefix and an optional 1–3 character extension (`PROGRA~1`, `REPORT~1.DSS`). A mixed-case name such as `report~1.dss` is kept. An all-caps file that is spelled exactly like a generated alias is still refused.

`IImportServer` has two implementations: `HttpImportServer` (HttpClient, client certificate) and `SimulatorImportServer` (in-memory, used by tests).

## Prohibitions

No microphone or audio capture, no playback, no text editor, no hotkey registration, no clipboard, no Office or window automation, no SendInput, no HID control, no firmware flashing, no local HTTP or WebSocket listener, no process execution, no generic execute or open-file command, no browser-to-localhost tunnel. Tests scan the `src` tree for forbidden fragments and the metadata of both built assemblies for process, socket, pipe, native-interop, registry and assembly-loader references.

## Build and test

```
dotnet test apps/windows-import/Speakr.Clinic.ImportAgent.sln
```

The suite runs on the Windows host that has the .NET 10 SDK. Tests that touch DPAPI, the secret-file ACL or Windows path admission return immediately on other operating systems.

### Windows drive pickup simulation

On a Windows test machine with the .NET 10 SDK, run:

```powershell
.\apps\windows-import\tests\Test-ApprovedDrivePickup.ps1
```

The script maps a fresh temporary directory to an unused drive letter with `subst` and runs the agent against its explicitly allowed source folder. A neutral WAV is stored once by the in-process import simulator. On a second poll, the receipt, chunk count and source bytes stay the same. A second test creates two separate synthetic DS2 headers with different author codes. The test unmaps the drive and removes its temporary files. For offline use, `-TestAssembly` accepts the built `Speakr.Clinic.ImportAgent.Tests.dll` with its normal test output directory. Physical recorders and the clinic network service require separate acceptance.

The agent can read an untrusted author-code hint from a complete three-block DS2 header when the short ID agrees with the recorder's explicitly labelled Author keyword. Unsupported or contradictory headers yield no hint. The observation leaves recording completion unknown and grants no permissions. The agent upload contract carries the unchanged original bytes. [Pinned DS2 header layout](https://github.com/hirparak/dss-codec/blob/c38d3192239005b0570c5987cbde62e69dd7dbbe/dss-codec/CODEC_SPECIFICATION.md#ds2-file-header-1536-bytes--0x600)

## Not proven

Real Philips / LFH / DPM devices, docks and firmware. Windows service installation. Code signing. A live mTLS handshake against clinic ingress. The in-process simulator grants `permitted_post_step` from the intent without a dictation id; a production receipt usually says `none` unless the server allows removal. Residual race: a file that is replaced or relinked between listing and read in `Collect` is narrowed by a re-check after the read, not eliminated (.NET exposes no final-path-by-handle). The hold-name rename leaves a window for a party with write access to the source directory who can guess the hold name; the name is random. Hardware acceptance tests stay `not_run`.
