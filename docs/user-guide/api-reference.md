# Clinic API reference for users

English · [Deutsch](../de/user-guide/api-reference.md)

The clinic interface uses the signed-in browser session and object-specific permissions. The following routes explain what the application reads. For everyday use, open the named screen instead of issuing requests yourself.

| Screen | Clinic request | Result |
|---|---|---|
| **Aufnahmen** | `GET /api/clinic/v1/recordings` | A bounded list of visible dictations with processing information. |
| **Arbeitsliste** | `GET /api/clinic/v1/worklist` | Work and assignments visible to the current account. |
| **Diktatdetails** | `GET /api/clinic/v1/dictations/{id}/detail` | Permitted metadata and the available actions for this dictation. |
| **Prüfen und Freigeben** | `GET /api/clinic/v1/dictations/{id}/review` | The review state and revisions to which a decision would apply. |

Clinically significant changes are separate writes. The writing workspace supplies the current revision and lease when saving a correction. The server checks these along with the session, CSRF protection and the required action right. A stale revision requires reloading and comparing text. Seeing the details does not give permission to stream the audio or export the original file.

The registered importer uses a dedicated machine-authenticated channel under `/api/clinic/v1/agent`. The public API of original Speakr at `/api/v1` is a different interface. Its scope contract is preserved below for maintainers of upstream integrations. Those entries do not enable tools or bearer tokens for clinic users. [Authentication](api-tokens.md).

<details markdown="1">
<summary>Original Speakr API v1 scope contract</summary>

```http
GET /api/v1/tokens/current
```

**Scope:** none (any valid token)

```http
POST /api/v1/tokens/exchange
```

**Scope:** none (any valid token passes the scope check, but only an SSO access token is accepted)

```http
GET /api/v1/sso/status?client_id=CLIENT_ID
```

**Scope:** none (no authentication; a token sent here is ignored). Requests are limited per IP address.

```http
DELETE /api/v1/tokens/current
```

**Scope:** none (any valid token, full or scoped)

```http
GET /api/v1/capabilities
```

**Scope:** none (any valid token or a signed-in session)

```http
GET /api/v1/stats
```

**Scope:** `read`

```http
GET /api/v1/users/me
```

**Scope:** `read`

```http
POST /api/v1/recordings/upload
```

**Scope:** `upload`

```http
POST /api/v1/integrations/asr-voice-recorder/upload
```

**Scope:** `upload`

```http
GET /api/v1/recordings
```

**Scope:** `read`

```http
GET /api/v1/recordings/{id}/external-refs
```

**Scope:** `read`

```http
POST /api/v1/recordings/{id}/external-refs
```

**Scope:** `write`

```http
PUT /api/v1/recordings/{id}/external-refs?system=mailr
```

**Scope:** `write`

```http
DELETE /api/v1/recordings/{id}/external-refs/{ref_id}
```

**Scope:** `write`

```http
GET /api/v1/recordings/changes
```

**Scope:** `read`

```http
GET /api/v1/recordings/{id}
```

**Scope:** `read`

```http
GET /api/v1/recordings/{id}/transcript
```

**Scope:** `read`

```http
GET /api/v1/recordings/{id}/summary
```

**Scope:** `read`

```http
GET /api/v1/recordings/{id}/notes
```

**Scope:** `read`

```http
GET /api/v1/recordings/{id}/status
```

**Scope:** `read`

```http
PATCH /api/v1/recordings/{id}
```

**Scope:** `write`

```http
PUT /api/v1/recordings/{id}/notes
```

**Scope:** `write`

```http
PUT /api/v1/recordings/{id}/summary
```

**Scope:** `write`

```http
POST /api/v1/recordings/{id}/delete-audio
```

**Scope:** `delete`

```http
DELETE /api/v1/recordings/{id}
```

**Scope:** `delete`

```http
GET /api/v1/search
```

**Scope:** `read`

```http
GET /api/v1/tags
```

**Scope:** `read`

```http
POST /api/v1/tags
```

**Scope:** `write`

```http
PUT /api/v1/tags/{id}
```

**Scope:** `write`

```http
DELETE /api/v1/tags/{id}
```

**Scope:** `delete`

```http
POST /api/v1/recordings/{id}/tags
```

**Scope:** `write`

```http
PUT /api/v1/recordings/{id}/tags
```

**Scope:** `write`

```http
DELETE /api/v1/recordings/{id}/tags/{tag_id}
```

**Scope:** `write`

```http
GET /api/v1/speakers
```

**Scope:** `read`

```http
POST /api/v1/speakers
```

**Scope:** `write`

```http
PUT /api/v1/speakers/{id}
```

**Scope:** `write`

```http
DELETE /api/v1/speakers/{id}
```

**Scope:** `delete`

```http
GET /api/v1/recordings/{id}/speakers
```

**Scope:** `read`

```http
POST /api/v1/recordings/{id}/transcribe
```

**Scope:** `process`

```http
POST /api/v1/recordings/{id}/summarize
```

**Scope:** `process`

```http
POST /api/v1/recordings/{id}/chat
```

**Scope:** `process`

```http
POST /api/v1/inquire
```

**Scope:** `process`

```http
GET /api/v1/recordings/{id}/audio
```

**Scope:** `read`

```http
PATCH /api/v1/recordings/batch
```

**Scope:** `write`

```http
DELETE /api/v1/recordings/batch
```

**Scope:** `delete`

```http
POST /api/v1/recordings/batch/transcribe
```

**Scope:** `process`

```http
GET /api/v1/folders
```

**Scope:** `read`

```http
GET /api/v1/folders/{id}
```

**Scope:** `read`

```http
POST /api/v1/folders
```

**Scope:** `write`

```http
DELETE /api/v1/folders/{id}
```

**Scope:** `delete`

```http
GET /api/v1/transcription
```

**Scope:** `read`

```http
GET /api/v1/webhooks
```

**Scope:** `webhooks`

```http
POST /api/v1/webhooks
```

**Scope:** `webhooks`

```http
GET /api/v1/webhooks/{id}
```

**Scope:** `webhooks`

```http
PATCH /api/v1/webhooks/{id}
```

**Scope:** `webhooks`

```http
DELETE /api/v1/webhooks/{id}
```

**Scope:** `webhooks`

```http
POST /api/v1/webhooks/{id}/rotate-secret
```

**Scope:** `webhooks`

```http
POST /api/v1/webhooks/{id}/test
```

**Scope:** `webhooks`

```http
GET /api/v1/webhooks/{id}/deliveries
```

**Scope:** `webhooks`

```http
GET /api/v1/webhooks/{id}/deliveries/{delivery_id}
```

**Scope:** `webhooks`

```http
POST /api/v1/webhooks/{id}/deliveries/{delivery_id}/replay
```

**Scope:** `webhooks`

</details>

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/user-guide/api-reference.md)
