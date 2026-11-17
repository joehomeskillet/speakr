# Interne Adapterverträge
## Decoder
`decode(original_object, key_reference, profile_id) -> audio_derivative, metadata_observations, validation_report`.
Bytes niemals verändern. Beobachtungen enthalten Rohwert, parser_version, evidence_level `fixture|observed|validated`, nicht nur ein nacktes `finished=true`. Unbekannte Completion bleibt unknown. Outputformat PCM mono für ASR, Original bleibt erhalten; Resampling macht keine verlorene Aufnahmeinformation wiederherstellbar. Prozessfehler -> permanenter/temporärer Code ohne sensitive stderr-Ausgabe.

## Transkription
`transcribe(audio_version, model_pin, parameters, attempt_id) -> raw_text, segments, language, model_evidence`.
Existing Speakr connector + Speaches. Lokal festgelegte Adresse. Invalides/fehlendes Timestampfeld sichtbar behandeln, keine erfundenen Zeitmarken. Modell-/Endpointfehler können manuelle Abschrift erlauben, sofern Audio valide. Cancel/Timeout-Ergebnis als Attempt behandeln, korrigierte Version nie ersetzen.

## Kontext/KIS
`resolve_context(opaque_grant, actor)` und `authorize_case` liefern nur verifizierten Kontext plus Rechteentscheidung. `deliver` akzeptiert ausschliesslich eingefrorenen Payload, `lookup` liefert `confirmed|absent|unknown`; `absent` muss gemäss realem Herstellervertrag belastbar sein. Mock kann absichtlich „gespeichert, danach Timeout“ erzeugen und muss Lookup stateful beantworten. DisabledAdapter erzeugt keine Nebenwirkung.

## Quellsimulator
Verzeichnis mit synthetischen Dateien plus **explizit als Simulator markiertem** Sidecar-Manifestschema; Sidecars niemals als Beweis echter Philips-Metadaten verwenden. Cases: draft, completed, reopened, shared-owner, unknown-owner, corrupt, encrypted-without-key, revision, duplicate-copy. Der produktive Agent ignoriert Simulator-Sidecars.

## IdP/Provisionierung
Bestehende lokale Speakr-Anmeldung als Startprofil; gleicher Userbestand für spätere Entra-Verknüpfung. OIDC-Adapter bleibt deaktiviert und wird isoliert mit synthetischen Identitäten getestet, ohne Pflicht-Keycloak. Fachliche Referenz ist user.id; externe Verknüpfung über validiertes issuer/sub und geprüften Linkauftrag. Device-only-Autor ohne interaktiven Login; E-Mail kein externes Linkingmerkmal. Normativ: identity-contract.json und docs/10_Local_Entra.md.

## HTTP-Envelope statt zweite Medienengine
`clinic-api.openapi.json` definiert die **eigenen** Klinikkommandos, nicht vorhandene ines-Routen. Die Ingest-/Chunk-Operationen delegieren an die geprüften vorhandenen Session-/Storage-Dienste. Der bestehende Recorder bleibt erhalten; sein Transport wird bei Bedarf auf das Klinik-Envelope adaptiert. Keine doppelte Medienaufnahme-/Uploadimplementierung. Die vorgeschlagene JSON-Spezifikation muss gegen den implementierten Flask-Code in Contracttests geprüft werden.

Text-Hash: UTF-8, Zeilenenden LF, Unicode NFC, keine automatische Kürzung oder fachliche Umschreibung. Normalisierungsversion im Release speichern. Die Editorserialisierung als gemeinsame Quelle benutzen, damit Text, DOCX und KIS-Payload nicht voneinander abweichen.

## V4-Webgrenze
Realtime-/TTS-/Trainingseigenschaften sind explizite lokale Adaptercapabilities. Agent ist nur Dateiabholung; kein Desktopadapter oder lokale RPC. Browserfähigkeiten werden gemessen, nicht aus Modellname/OSname geschlossen. Original-BT mit nativem Verhalten bleiben als WG offen; keine falsche Erfolgsantwort.
