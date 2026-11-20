# Klinik-API-Referenz für Benutzer

[English](../../user-guide/api-reference.md) · Deutsch

Der Webclient nutzt die angemeldete Browsersitzung und prüft die Rechte am jeweiligen Diktat. Diese Lesezugriffe erklären, was die Oberfläche anzeigt. Für die tägliche Arbeit öffnen Sie die genannte Seite.

| Seite | Klinik-Aufruf | Ergebnis |
|---|---|---|
| **Aufnahmen** | `GET /api/clinic/v1/recordings` | Begrenzte Liste sichtbarer Diktate mit Verarbeitungsstand. |
| **Arbeitsliste** | `GET /api/clinic/v1/worklist` | Arbeit und Zuweisungen, die das angemeldete Konto sehen darf. |
| **Diktatdetails** | `GET /api/clinic/v1/dictations/{id}/detail` | Erlaubte Metadaten und verfügbare Aktionen zu einem Diktat. |
| **Prüfen und Freigeben** | `GET /api/clinic/v1/dictations/{id}/review` | Prüfstand und Fassungen, an die eine Entscheidung gebunden wäre. |

Fachliche Änderungen sind eigene Schreibvorgänge. Beim Speichern einer Korrektur übermittelt der Schreibplatz die aktuelle Revision und die Bearbeitungssperre. Der Server prüft diese zusammen mit Sitzung, CSRF-Schutz und Fachrecht. Bei veraltetem Stand laden und vergleichen Sie die Fassungen. Die Sicht auf Metadaten erteilt kein Recht zur Wiedergabe oder zum Originalaudio-Export.

Der registrierte Importer verwendet seinen eigenen Maschinenkanal unter `/api/clinic/v1/agent`. Die ursprüngliche Speakr-API unter `/api/v1` ist davon getrennt. Für bestehende Upstream-Integrationen enthält der Anhang die unveränderten technischen Scope-Bezeichnungen. Sie schalten keine Klinik-Benutzertokens frei. [Anmeldung und Importagenten](api-tokens.md).

<details markdown="1">
<summary>Scope-Vertrag der ursprünglichen Speakr-API v1</summary>

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

[Handbuch der Originalversion](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/user-guide/api-reference.md)
