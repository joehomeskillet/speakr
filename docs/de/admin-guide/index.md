# Klinikadministration

[English](../../admin-guide/index.md)

Dieses Handbuch beschreibt den aktuellen Klinik-Fork mit aktiviertem `CLINIC_MODE`. Aufnahme, Wiedergabe, Korrektur und Diktatverwaltung laufen im Webclient. Die einzige lokale Anwendung, `apps/windows-import`, importiert Dateien aus freigegebenen Geräten und Ordnern; sie ist kein nativer Diktier-, Wiedergabe- oder Office-Automationsclient.

Der ausgelieferte Vertrag `local-web-parity-lab` gilt für synthetische Daten und setzt `allow_real_data` sowie `production_approved` auf `false`. Eine hier als verfügbar beschriebene Funktion ist im Code vorhanden; dies ist weder klinische Freigabe noch Nachweis eines Echtgerätebetriebs oder vollständiger Altproduktparität.

## Verwaltung öffnen

Ein Konto mit **Administrationsrolle** kann die **Verwaltung** unter `/admin` öffnen. Ihre Startseite ist **Benutzer und Rollen**, die Konten, Poolbezüge und **Fachrechte** zusammenführt. Die folgenden Bezeichnungen entsprechen der deutschen Klinikoberfläche.

| Navigationsgruppe | Seiten und Direktadressen | Anleitung |
| --- | --- | --- |
| **Konten und Pools** | **Benutzer und Rollen** (`/admin#/benutzer`), **Schreibkräfte-Pools** (`/admin#/schreibkraefte-pools`), **Diktierende und Autorenpools** (`/admin#/diktierende`), **Verzeichnis** (`/admin#/verzeichnis`) | [Konten](user-management.md), [Pools](group-management.md), [Identitätsanbieter](sso-setup.md) |
| **Inhalte** | **Kategorien** (`/admin#/kategorien`), **Regeln** (`/admin#/regeln`), **Spalten** (`/admin#/spalten`), **Wortschatz** (`/admin#/wortschatz`), **Befehle und Vorlagen** (`/admin#/befehle-vorlagen`) | Editoren für gemeinsamen Wortschatz, Metadaten und Ressourcen; [Erkennungshinweise](whisperx-setup.md) |
| **Richtlinien** | **Abnahme** (`/admin#/abnahme`) | Abnahmerichtlinie je Diktat; [Freigaberechte](group-management.md) |
| **Betrieb** | **Vorgaben** (`/admin#/vorgaben`), **Sprachprofile** (`/admin#/sprachprofile`), **Import** (`/admin#/import`), **Verarbeitung** (`/admin#/verarbeitung`), **Aktivität und Audit** (`/admin#/aktivitaet`) | [Aufnahme/Import](recording-sessions.md), [lokale ASR](whisperx-setup.md), [Statistik und Auftragszustand](statistics.md) |
| **System** | **Systemstatistik** (`/admin#/system/statistik`), **Systemeinstellungen** (`/admin#/system/einstellungen`), **Modellwahl** (`/admin#/system/modelle`), **System-Auditprotokoll** (`/admin#/system/audit`) | [Statistik](statistics.md), [Einstellungen](system-settings.md), [Modelle](model-configuration.md), [Audit](audit-log.md) |

Die meisten Bereiche prüfen vor der Anzeige ihre API-Berechtigungen. Eine abgewiesene Prüfung kann eine Seite ausblenden und **Für dieses Konto nicht freigegeben: …** anzeigen. Eine sichtbare Seite belegt nicht, dass jede Aktion aktiviert ist. Insbesondere fehlt für die bereichsgebundene Statistik hinter **Aktivität und Audit** ein Anwendungsablauf zur Rechtevergabe; die separate **Systemstatistik** bleibt für die Standortadministration verfügbar.

Das Kopfmenü **Verwaltung & Seiten** verlinkt ausgewählte Verwaltungsseiten. **Dateien und Geräteimport** öffnet die Benutzer-Importansicht `/#/import`, nicht die Verwaltungs-Importseite. Die Upstream-Reiter für Benutzerverwaltung, Standardprompts, Gruppen und Vektorspeicher sind nicht die Kliniknavigation.

## Die richtige Rolle wählen

| Rolle oder Recht | Gewährt | Gewährt nicht |
| --- | --- | --- |
| **Administrationsrolle** | Verwaltungszugriff; mehrere Klinikaktionen verlangen zusätzlich ein Klinikkonto mit Status **Aktiv** | Automatisches Recht, fremde Diktate zu bearbeiten, zu exportieren oder freizugeben |
| Aktive Standortadministration | Auditierter Zugriff zum Ansehen/Abspielen von Klinikdiktaten und Lesen ihrer Korrekturen; Pool- und Rechteverwaltung | Allgemeine Ausnahme von Bearbeitungs-, Export-, Freigabe- oder Originalaudio-Rechten |
| **Identitätsadministration** | Zusammen mit Standortadministration und frischer lokaler Anmeldung: Konten anlegen/bearbeiten, Rollenflags verwalten, deaktivieren/reaktivieren und zulässige Einmalcodes ausstellen | Zusätzliche Diktatrechte allein durch das Identitätsflag |
| **Leitung** | Koordination der Zuweisung im zugeordneten Schreibkräfte-Pool gemäss Zuweisungsrichtlinie | Standortadministration, Kontoverwaltung oder unbeschränkten Inhaltszugriff |
| **Fachrechte** | Explizite Aktionen mit Geltungsbereich eigene Diktate, ein Pool oder alle Diktate | Erlaubnis ausserhalb der gewährten Aktion und ihres Geltungsbereichs |
| **Freigeben** | Eigenständiges Freigaberecht für Abnahme- und Freigabeaktionen | Implizites Recht durch Administrator- oder Autorenstatus |
| **Originalaudio exportieren** | Eigenständiges Recht zum Originalaudio-Export | Durch Wiedergabe oder Textexport implizierte Erlaubnis |

Autoren haben sichere Standards zum Ansehen, Abspielen und Bearbeiten eigener Diktate. Andere Aktionen brauchen explizite Rechte. Passende Verbote gewinnen normalerweise; der Zugriff aktiver Standortadministratoren auf Ansicht, Wiedergabe und Korrekturlesen ist eine explizite, auditierte Richtlinienausnahme. Ein Lese- oder Wiedergaberecht erlaubt nie automatisch eine Diktatänderung.

Lokale Kontoverwaltung verlangt beide Rollenflags, ein aktives Konto und eine frische lokale Anmeldung. Einmalcodes ersetzen von Administratoren gesetzte Passwörter. Konten werden deaktiviert statt gelöscht. Die [Benutzerverwaltung](user-management.md) beschreibt Kontowiederherstellung, Grenzen und Bedingungen der erneuten Anmeldung.

Autorenpools organisieren Autorenressourcen und Sichtbarkeit. Schreibkräfte-Pools organisieren die Bearbeitungszuständigkeit. Beide sind getrennt und explizit miteinander verbunden. Die [Gruppenverwaltung](group-management.md) beschreibt Mitgliedschaft, Leitung, Gültigkeit und Zuweisungsregeln.

Die Seite **Abnahme** legt fest, ob ein Diktat eine Abnahme braucht und wer abnehmen darf. Die Veröffentlichung der Richtlinie verlangt aktive Standortadministration. Die endgültige Freigabe hängt zusätzlich von Workflow und vertrauenswürdigem KIS-Kontext ab; ein simulierter Adapter belegt keine funktionierende klinische KIS-Integration.

## Klinikmodus und Verarbeitung

`CLINIC_MODE` akzeptiert `1`, `true`, `yes` oder `on`. Seine Vertragsdateien stammen aus `clinic-spec/config` oder `CLINIC_CONFIG_DIR`. Funktionen sind standardmässig gesperrt; unbekannte Routen werden abgewiesen. Gewöhnliche Systemeinstellungen können Klinikrichtliniengrenzen nicht aufheben. Chat-, Inquire- und generative Zusammenfassungssperren folgen einer eigenen lokalen Modellregel statt ihren Entwicklungsstandardflags.

Spracherkennung nutzt lokale Dienste. Der OpenAI-kompatible ASR-Pfad und das optionale GPU-Ziel prüfen die Lokalität ihres Endpunkts; der Konnektor `asr_endpoint` tut dies nicht, weshalb der Betrieb `ASR_BASE_URL` lokal halten muss. GPU-zuerst-Verarbeitung ist optional und fällt bei den dokumentierten Fehlerfällen einmal auf den CPU-Pfad zurück. Hotwords und Profilhinweise sind kein akustisches Training. Siehe [lokale ASR einrichten](whisperx-setup.md).

**Modellwahl** und revisionsgebundene **KI-Zusammenfassung** gehören zum aktuellen Build. Ein lokales Textmodell, Rechte und Zusammenfassungspräferenzen bestimmen, ob eine Zusammenfassung erzeugt werden kann. Das Transkript bleibt getrennt. Ältere Titel-/Aufnahmezusammenfassungspfade und Upstream-Indexierung haben zusätzliche Konfigurationsgrenzen; siehe [Modelle](model-configuration.md), [Prompts](prompts.md) und [Vektorsuche](vector-store.md). Der Klinikarbeitsplatz bietet keine [Ambrose-Chatoberfläche](ambrose.md).

## Entfernte Upstream-Funktionen

Der Klinik-Fork bietet keine Ereignis-/Kalenderextraktion, öffentlichen Freigabelinks, internen Freigaben, Webhooks, allgemeine API v1, Inkognito-Funktion, Cloud-ASR-Konnektoren, Ordner, Selbstregistrierung, Mailverifikation, Passwortzurücksetzung per Mail, What's-New-Popups oder harte automatische Löschung. API-Tokens sind dem Import-Agenten vorbehalten. Die Aufbewahrung archiviert statt zu löschen. Siehe [Webhooks](webhooks.md), [E-Mail und Wiederherstellung](email-setup.md) sowie [Aufbewahrung](retention.md).

## Status und Nachweisgrenzen

| Begriff | Bedeutung in diesem Handbuch |
| --- | --- |
| Verfügbar | Im aktuellen Klinikcode implementiert, vorbehaltlich seiner Rechte und Konfiguration |
| Standardmässig aus | Ein dokumentierter Konfigurationsschalter existiert, die Funktion ist aber standardmässig ausgeschaltet |
| Vorbereitet, deaktiviert | Code oder Vertrag existiert, aber die Anwendung stellt keine unterstützte aktive Integration bereit |
| Simuliert | Nutzt Fixtures oder einen Mock; kein Nachweis für echtes Gerät, Verzeichnis oder KIS |
| Nur Upstream | Eine Upstream-Funktion ist im Klinikablauf ausgeblendet, gesperrt oder nicht verfügbar |
| Offene Lücke | Eine Anforderung bleibt ungelöst; eine verfügbare Web-Teilalternative schliesst sie nicht |

Das Web-Paritätsregister `clinic-spec/planning/web-parity-gaps.json` hält 13 Lücken offen und setzt `full_parity_claim_allowed=false`: WG-ADM-009, WG-LIVE-002, WG-LIVE-006, WG-LIVE-007, WG-IMP-009, WG-IMP-010, WG-EXP-004, WG-EXP-005, WG-EXP-007, WG-EXP-009, WG-EXP-010, WG-OPS-003 und WG-OPS-004. Dazu gehören native Fenster-/Office-Interaktion, Geräteschreibzugriffe, globale Tastenkürzel und Hintergrundclientverhalten. Der Webablauf oder Import-Agent schliesst diese Lücken nicht automatisch.

Beginnen Sie bei einer bestehenden Installation mit dem [Migrationsleitfaden](migration-guide.md). Planen Sie [Speicher und Wiederherstellung](storage.md), [Archivierung](retention.md) und [Datenschutz](privacy.md) gemeinsam; UI-Verfügbarkeit allein belegt keinen korrekten Betrieb von Sicherungen, Aufbewahrung oder klinischem Zugriff.

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/index.md)
