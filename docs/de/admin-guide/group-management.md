---
layout: default
title: Gruppenverwaltung
parent: Administrationshandbuch
nav_order: 6
---

# Gruppenverwaltung: Autorenpools und Schreibkräfte-Pools
<small>[English](../../admin-guide/group-management.md)</small>

Im Upstream-Speakr beschreibt diese Seite Gruppen (Teams), Gruppen-Tags, Gruppenordner und Gruppenadministratoren. In der Klinikversion mit eingeschaltetem `CLINIC_MODE` ist diese Gruppenverwaltung abgeschaltet. An ihre Stelle treten zwei getrennte Poolarten und ausdrückliche Rechte je Benutzer (**Fachrechte**). Diese Seite erklärt Pools, Poolleitungen und Fachrechte, wo sie verwaltet werden, wer sie ändern darf und was protokolliert wird.

`CLINIC_MODE` gilt bei `1`, `true`, `yes` oder `on` als eingeschaltet und wird bei jeder Anfrage gelesen. Ist der Modus ausgeschaltet, gilt keine Regel dieser Seite, und das Upstream-Verhalten bleibt unverändert.

## Statusübersicht

| Funktion | Status |
|---|---|
| Upstream-Gruppen, Teams, Gruppen-Tags, Gruppenordner, der Reiter **Gruppen** und `/group-management` | Nur Upstream |
| **Autorenpools** und **Schreibkräfte-Pools** | Verfügbar. Anlegen, Umbenennen, Deaktivieren und Verknüpfen von Autorenpools nur über die API (kein Bedienelement). |
| Poolleitung (**Leitung**) | Verfügbar |
| Fachrechte mit Erlaubnis- und Verweigerungseinträgen | Verfügbar |
| Abnahmerichtlinie (**Abnahme**) | Verfügbar. Hinweis: Eine Abnahme ist erst nötig, wenn eine aktive globale Administration für ein Diktat eine Richtlinie veröffentlicht. |
| Endgültige Freigabe (**Endgültig freigeben**, in **Prüfen und Freigeben**: **Fertigstellen**) | Verfügbar; braucht einen vertrauenswürdigen KIS-Kontext. KIS: Simuliert (nur mit `CLINIC_PROFILE=staging`, synthetischer MockKIS, kein KIS-Nachweis); sonst ist der Adapter deaktiviert, und die endgültige Freigabe wird abgelehnt. |
| Recht **Originalaudio exportieren** | Als Recht verfügbar. Ein lokaler Mailentwurf mit Audioanhang: Offene Lücke (WG-EXP-009). |

## Was von der Upstream-Gruppenverwaltung bleibt

- **Verwaltungsoberfläche.** Im Klinikmodus zeigt `/admin` statt der Upstream-Reiterleiste die Klinik-Navigation **Verwaltung**. Die Upstream-Reiter **Benutzerverwaltung**, **Standard-Prompts**, **Vektorspeicher** und **Gruppen** (englische Oberfläche: User management, Default prompts, Vector store, Groups) werden nicht dargestellt.
- **Routen.** Die Upstream-Team-Endpunkte (`groups.*`), Gruppen-Tags (`tags.create_group_tag`, `tags.get_group_tags`), Gruppenordner (`folders.create_group_folder`, `folders.get_group_folders`) und die Seite `/group-management` gehören zur Klinikfunktion `internal_sharing`. Die Vertragsdatei `clinic-spec/config/dev.defaults.json` nennt diese Funktion nicht, und eine Funktion, die dort nicht auf `true` steht, ist gesperrt. Diese Routen antworten mit `403` und `clinic_feature_disabled`. Auch bei aufgehobener Sperre weist die Routenprüfung des Klinikmodus sie ab (`clinic_feature_unsupported`), weil sie keine prüfbare Aufnahme nennen.
- **Recht zum Anlegen von Gruppen.** Das Kontrollkästchen **Gruppen erstellen** (Upstream-Recht `create_groups`, im Upstream-Benutzerdialog «Darf Gruppen erstellen») erscheint im Konteneditor nur bei entsperrtem `internal_sharing` und nie für ein Konto mit **Administrationsrolle**; es ist daher ausgeblendet. Die Upstream-Einstellung `permission_create_groups` hat keine Wirkung auf Gruppenfunktionen, weil keine Gruppenroute erreichbar ist. Im Modus `selected` oder `everyone` blendet sie nur den Menüeintrag **Gruppen** ein (siehe unten). Ihr Standardwert ist `admins`; belassen Sie ihn.
- **Upstream-Umgebungsvariablen.** `ENABLE_INTERNAL_SHARING`, `SHOW_USERNAMES_IN_UI` und `USER_SEARCH_SCOPE` sind nur Upstream: Der Code dieser Version liest sie nicht.
- **Gruppenadministratoren.** Wer die Upstream-Rolle Gruppenadministrator hat, aber keine Administrationsrolle, erhält auf `/admin` den Status `403`.
- **Menüeintrag Gruppen.** Poolleitungen ohne **Administrationsrolle** sehen im Kontomenü den Upstream-Eintrag **Gruppen** (englische Oberfläche: Groups), weil sie die Upstream-Rolle Gruppenadministrator tragen. Er führt auf `/group-management` und endet mit `403` `clinic_feature_disabled`. Dasselbe gilt für Benutzer, denen `permission_create_groups` im Modus `selected` oder `everyone` das Anlegen von Gruppen erlaubt.
- **Wiederverwendetes Datenmodell.** Jeder Schreibkräfte-Pool ist genau eine Upstream-Gruppenzeile. Seine Schreibkräfte sind die Mitgliedschaftszeilen dieser Gruppe, und eine Poolleitung trägt darin die Upstream-Rolle Gruppenadministrator. Eine Gruppe ohne zugehörige Schreibkräfte-Pool-Zeile ist kein Pool. Ein neuer Schreibkräfte-Pool kann nicht den Namen einer bestehenden Gruppe erhalten.

Upstream-Referenz: [Group Management, Upstream-Speakr-Dokumentation](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/group-management.md). Sie beschreibt nur das Upstream-Verhalten und ist keine Anleitung für die Klinikversion.

## Zwei Poolarten

Der Klinikauftrag verlangt, dass Autorenressourcenpools und Schreibpools getrennt bleiben. Sie liegen in zwei Tabellen, daher kann ein Pool seine Art nicht wechseln.

| | **Autorenpool** | **Schreibkräfte-Pool** |
|---|---|---|
| Tabelle | `clinic_author_pool` | `clinic_staff_pool`, je eine Upstream-Gruppe |
| Mitglieder | **Diktierende** | Schreibkräfte |
| Zweck | Fasst Diktierende und ihre gemeinsamen Ressourcen zusammen (Verweise auf Wortschatz und Vorlagen); jedes Diktat trägt seinen Autorenpool | Zuständigkeit für Schreib- und Korrekturarbeit |
| Rechte allein aus der Mitgliedschaft | Keine über die eigenen Standardrechte hinaus | **Ansehen** für Diktate verknüpfter Autorenpools |

Eine **Verknüpfung** verbindet einen Autorenpool mit einem Schreibkräfte-Pool. Verknüpfungen bestimmen:

- welche Schreibkräfte für die Diktate des Autorenpools das Recht **Ansehen** haben. Das ist ein Recht, kein Listeneintrag: **Arbeitsliste** und **Aufnahmen** zeigen einer Schreibkraft nur eigene und ihr zugewiesene Diktate; nur eine Poolleitung sieht zusätzlich die nicht zugewiesenen, gesendeten Diktate verknüpfter Autorenpools (siehe Poolleitungen unten);
- wer bei einer Zuweisung als Empfänger angeboten wird (aktuelle Mitglieder der verknüpften, aktiven Schreibkräfte-Pools);
- welche Poolleitungen die nicht zugewiesenen, gesendeten Diktate des Autorenpools sehen.

Verknüpfungen lassen sich nur über die API anlegen, und keine Route entfernt eine Verknüpfung. Um die Zuteilung an einen Schreibkräfte-Pool zu beenden, deaktivieren Sie ihn oder begrenzen Sie seinen Gültigkeitszeitraum (siehe unten).

### Diktierende brauchen einen aktiven Autorenpool

Eine Webaufnahme oder ein Upload wird nur dann zum Diktat, wenn die diktierende Person einem aktiven Autorenpool angehört. Sonst lehnt der Server mit `403` `clinic_no_author_pool` und der Meldung «Ihr Benutzerkonto ist keinem Autorenpool zugeordnet. Die Aufnahme wurde nicht als Diktat angelegt.» ab.

Die Seite **Diktierende und Autorenpools** nennt die Regel «Ein Benutzer gehört höchstens einem Autorenpool an.» Der Server setzt sie nicht durch: Eine zweite Mitgliedschaft wird angenommen. Der Code wählt dann selbst einen aktiven Pool, und nicht jeder Codepfad wählt denselben. Ordnen Sie jede diktierende Person genau einem Autorenpool zu.

### Namen

Ein Poolname hat 1 bis 100 Zeichen ohne führende oder nachgestellte Leerzeichen. Namen von Autorenpools sind unter den Autorenpools eindeutig, Namen von Schreibkräfte-Pools unter allen Gruppen. Ein vergebener Name wird mit `409` `name_taken` abgewiesen («Dieser Name ist bereits vergeben.»). Namen werden exakt verglichen (Gross- und Kleinschreibung zählt); anders als bei der Upstream-Gruppenregel gelten `Sales` und `sales` als verschiedene Namen.

## Wer Pools und Fachrechte verwalten darf

Diese Rollen sind getrennt. Keine schliesst eine andere ein.

| Rolle | Pools, Mitgliedschaften, Verknüpfungen, Leitungen | Fachrechte |
|---|---|---|
| Globale Administration: **Administrationsrolle** (`User.is_admin`) mit aktivem Klinikkonto, im Folgenden «aktive globale Administration» | Alle Änderungen | Lesen und ändern |
| Lokale Identitätsadministration (Merkmal **Identitätsadministration**, zusätzliches Merkmal für die Kontenverwaltung) | Fügt nichts hinzu | Fügt nur Lesezugriff über die API hinzu (`GET /api/clinic/v1/identity/users/<user_id>/grants`); das Merkmal öffnet `/admin` nicht |
| Poolleitung (**Leitung**) | Diktate zuweisen, Verwendungszahlen der geleiteten Pools lesen | Eigene Liste, nur lesen |
| Alle anderen Benutzer | Sehen nur ihre eigenen Pools und darin nur sich selbst | Eigene Liste, nur lesen |

Der Pooldienst lässt zusätzlich Inhaber eines Rechts `manage_pools` zu. `manage_pools` ist jedoch keine vergebbare Aktion, und weder Oberfläche noch API können es anlegen. In der Praxis verwaltet nur die aktive globale Administration Pools. `/admin` selbst verlangt das Merkmal Administrationsrolle; alle anderen erhalten `403`.

Zur Kontenverwaltung (**Konten**, Merkmale Administrationsrolle und Identitätsadministration) siehe [Benutzerverwaltung](user-management.md).

## Wo in der Oberfläche

Alle Seiten liegen unter **Verwaltung › Konten und Pools** auf `/admin`. Eine Seite, deren API das Konto abweist, wird zurückgehalten und im Hinweis «Für dieses Konto nicht freigegeben: …» genannt. Die Bezeichnungen der Klinikverwaltung gibt es nur auf Deutsch.

### Schreibkräfte-Pools

Direktlink `/admin#/schreibkraefte-pools`. Die Seite lädt nur für die aktive globale Administration und verwaltet nur Schreibkräfte-Pools.

- **Pool hinzufügen** öffnet **Schreibkräfte-Pool anlegen**.
- Je Pool, nach **Schreibkräfte-Pool bearbeiten**: **Umbenennen**; **Gültigkeit** mit **Gültig ab**, **Gültig bis** und **Gültigkeit speichern**; **Verwendung** mit **Offene Diktate**, **Mitglieder** und **Verknüpfte Autorenpools**; **Deaktivieren** oder **Reaktivieren**.
- **Mitglieder**: Aktive Konten fügen Sie über **Benutzer nach Name suchen** und **Hinzufügen** hinzu. **Mitglied entfernen** fragt zuerst «Mitgliedschaft wirklich entfernen?». Das Kontrollkästchen **Leitung** neben einem Mitglied setzt oder entfernt die Poolleitung.
- Statusanzeigen: **Aktiv**, **Deaktiviert**, **Aktiv, aktuell ausserhalb der Gültigkeit**.
- Ein Pool ohne Mitglieder zeigt «Keine Mitglieder. Der Pool ist nicht besetzt; neue Diktate an diesen Pool können nicht bearbeitet werden.»

### Diktierende und Autorenpools

Direktlink `/admin#/diktierende`. Die Tabelle listet alle Benutzer, die mindestens einem Autorenpool angehören, mit den Spalten **Anzeigename**, **Autorenpools**, **Schreibkräfte-Pools**, **Diktierrolle** und **Kontostatus**. **Diktierrolle** ist **Diktierend** oder **Diktierend und Schreibkraft**. Die Spalte **Schreibkräfte-Pools** nennt die Schreibkräfte-Pools, in denen die Person Mitglied ist. Verknüpfungen zeigt sie nicht; «Kein Pool zugewiesen» bedeutet, dass die Person in keinem Schreibkräfte-Pool ist.

Der aufklappbare Bereich **Autorenpools und Schreibkräfte-Pools** enthält den Editor **Pools verwalten**. Er listet **Autorenpools** und **Schreibpools** getrennt und ist die einzige Oberfläche für Mitgliedschaften in Autorenpools:

- Mitglieder erscheinen als `ID <Nummer>`. Sie fügen eines über **Benutzer-ID des neuen Mitglieds** und **Mitglied hinzufügen** hinzu.
- Das Entfernen fragt zuerst «Mitgliedschaft wirklich entfernen?».
- Jedes Speichern sendet die vollständige Mitgliederliste mit dem zuletzt gelesenen Stand (**Stand**). Wurde inzwischen irgendeine Pool- oder Rechteänderung erfasst, wird das Speichern abgewiesen, und der Editor zeigt «Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.»
- Ein Speichern wendet Hinzufügungen und Entfernungen einzeln an. Bricht es mittendrin ab, bleiben die Änderungen vor dem Fehler bestehen.

### Benutzer und Rollen

Direktlink `/admin#/benutzer`. Wählen Sie unter **Benutzer** ein Konto.

- **Poolbezüge** zeigt Autorenpools und Schreibkräfte-Pools des Kontos nur lesend, mit dem Hinweis *Mitgliedschaften werden unter «Pools verwalten» geändert.*
- **Fachrechte** öffnet den unten beschriebenen Rechteeditor.

### Nicht in der Oberfläche verfügbar

Diese Funktionen gibt es nur in der API (siehe API-Referenz unten): Anlegen, Umbenennen, Deaktivieren und Gültigkeit von Autorenpools, Anlegen von Verknüpfungen sowie Verwendungszahlen von Autorenpools.

## Deaktivierung, Gültigkeit und Verwendung

Pools werden nie gelöscht. Keine Route löscht einen Pool.

- Ein deaktivierter Pool oder ein Pool ausserhalb seines Gültigkeitszeitraums ist nicht zuteilbar. Neue Verknüpfungen zu oder von ihm werden mit `409` `pool_inactive` abgewiesen. Ein solcher Autorenpool wird für neue Webdiktate nicht gewählt. Ein solcher Schreibkräfte-Pool bietet bei Zuweisungen keine Empfänger an, und seine Leitungen können über ihn nicht zuweisen.
- Vorhandene Diktate, der Verlauf und bestehende Lesezugriffe bleiben. Offene Diktate werden nicht umverteilt, und **Diktiert von** ändert sich nicht. Mitglieder eines deaktivierten Schreibkräfte-Pools behalten **Ansehen** für Diktate verknüpfter Autorenpools.
- Der Gültigkeitszeitraum hat einen optionalen Beginn und ein optionales Ende. Die API erwartet ISO-8601-Zeitstempel mit Zeitzonenversatz; `null` lässt eine Seite offen. Der Beginn muss vor dem Ende liegen. Ein Pool mit Beginn in der Zukunft kann vor diesem Datum nicht verknüpft werden.
- Die Verwendungsvorschau liefert nur Zahlen: offene Diktate (des Pools, bei einem Schreibkräfte-Pool die seiner verknüpften Autorenpools), Mitglieder und Verknüpfungen. Die Seite zeigt sie vor einer Deaktivierung zusammen mit «Neue Zuweisungen an diesen Pool werden abgelehnt. Vorhandene Diktate und der Verlauf bleiben erhalten, offene Diktate werden nicht automatisch umverteilt. Diktiert von bleibt unverändert.»
- Die Spalten `active`, `valid_from` und `valid_to` werden bestehenden Pooltabellen beim Anwendungsstart automatisch hinzugefügt. Bestehende Pools gelten als aktiv.

## Poolleitungen (Leitung)

Eine Poolleitung ist ein Mitglied eines Schreibkräfte-Pools mit der Upstream-Rolle Gruppenadministrator (gespeichert als `GroupMembership.role` `admin`). Nur die aktive globale Administration setzt oder entfernt sie über das Kontrollkästchen **Leitung**. Die Person muss bereits Mitglied des Pools sein.

Eine Poolleitung darf:

- in der Arbeitsliste die nicht zugewiesenen, gesendeten Diktate (keine Entwürfe) der Autorenpools sehen, die mit einem von ihr geleiteten Schreibkräfte-Pool verknüpft sind, zusätzlich zu den eigenen und den ihr zugewiesenen Diktaten;
- ein gesendetes Diktat eines solchen Autorenpools mit **Zuweisen** zuweisen oder neu zuweisen (Dialog **Diktat zuweisen**, Feld **Schreibkraft**). Voraussetzung: Der Schreibkräfte-Pool ist aktiv und gültig, die Poolleitung hat **Ansehen** für das Diktat, und keine Verweigerung von **Neu zuordnen** greift. Empfänger sind die aktuellen Mitglieder der verknüpften, aktiven Schreibkräfte-Pools. **Für mich übernehmen** erscheint, wenn die Poolleitung selbst dazugehört;
- die Verwendungszahlen eines von ihr geleiteten Schreibkräfte-Pools oder eines damit verknüpften Autorenpools lesen (nur API).

Eine Poolleitung darf keine Pools anlegen, umbenennen oder deaktivieren, keine Mitgliedschaften ändern, keine Leitungen setzen, keine Fachrechte ändern und `/admin` nicht öffnen. Die Leitungsrolle gibt keine Inhaltsrechte: Ansehen stammt aus der Poolmitgliedschaft, und **Abspielen**, **Bearbeiten**, **Exportieren** und **Freigeben** brauchen Fachrechte.

Jede geänderte Zuweisung schreibt das Auditereignis `recording.edited` mit dem Feld `assignee`. Kann diese Zeile nicht geschrieben werden, wird die Zuweisung mit `503` `AUDIT_UNAVAILABLE` («Auditprotokoll nicht verfügbar») abgewiesen.

## Fachrechte

### Sichere Standardrechte

| Wer | Standard für ein Diktat |
|---|---|
| Diktierende, für eigene Diktate | **Ansehen**, **Abspielen**, **Bearbeiten**. Export, Originalaudio-Export, Freigabe, Archivieren und Löschen brauchen auch für eigene Diktate ein ausdrückliches Recht. |
| Mitglied eines Schreibkräfte-Pools, der mit dem Autorenpool des Diktats verknüpft ist | Nur **Ansehen** |
| Poolleitung | Wie ein Mitglied, dazu die Zuweisung |
| Aktive globale Administration | **Ansehen** und **Abspielen** jedes Klinikdiktats sowie das Lesen seiner Korrekturen; diese werden vor Erlaubnissen und Verweigerungen entschieden. Sonst nichts ohne Fachrecht. |
| Nur Identitätsadministration | Nichts |

Eine Schreibkraft, die schreiben soll, braucht ausdrückliche Rechte, zum Beispiel **Abspielen** und **Bearbeiten** mit Geltungsbereich **Pool** für den Autorenpool. Die Zuweisung eines Diktats gibt allein keine Rechte.

Den Lese- und Abspielzugriff der aktiven globalen Administration beschreibt [Benutzerverwaltung](user-management.md), seine Protokollierung das [Auditprotokoll](audit-log.md): Die Einträge entstehen nur bei eingeschaltetem `audit_log_enabled`, Einträge zum Abspielen zusätzlich nur bei eingeschaltetem `audit_log_record_views`.

### Fachrechte bearbeiten

Fachrechte werden unter **Benutzer und Rollen › Fachrechte** im Bereich **Berechtigungen** bearbeitet. Die Felder **Geltungsbereich**, **Wirkung** und **Aktionen**, die Schaltflächen **Speichern** und **Entziehen** sowie der Umgang mit dem **Stand** sind in der [Benutzerverwaltung](user-management.md#fachrechte) beschrieben. Für Pools gilt:

- Der Geltungsbereich **Pool** erfasst die Diktate, deren Autorenpool der gewählte **Autorenpool** ist. Ein Schreibkräfte-Pool lässt sich nicht als Geltungsbereich wählen.
- **Wirksames Ergebnis nach dem Speichern** zeigt je Aktion, ob die gespeicherten Einträge zusammen mit dem Entwurf sie erlauben oder verweigern. Die Anzeige berücksichtigt weder Geltungsbereich noch Pool und enthält weder die eigenen Standardrechte noch die Verknüpfungen von Schreibkräfte-Pools noch den Zugriff der globalen Administration. Sie ist daher nicht das wirksame Recht an einem bestimmten Diktat.

Regeln, die der Server durchsetzt:

- Nur die aktive globale Administration darf Fachrechte ändern. Die lokale Identitätsadministration und alle anderen Benutzer erhalten `403`.
- Der Server weist ein Recht für das eigene Konto der globalen Administration nicht ab.
- Jede Änderung trägt den zuletzt gelesenen **Stand**, der für alle Benutzer gemeinsam gilt. Ein veralteter Stand ergibt `409` `REVISION_CONFLICT`.
- Eine passende Verweigerung gewinnt über jede Erlaubnis, über die Verknüpfung des Schreibkräfte-Pools und über die eigene Urheberschaft. Ausnahme: **Ansehen** und **Abspielen** (sowie das Lesen von Korrekturen) der aktiven globalen Administration werden vor Erlaubnissen und Verweigerungen entschieden; eine Verweigerung entzieht sie nicht.
- Eine Änderung gilt ab der nächsten Anfrage, weil die Rechte jeweils aktuell gelesen werden.

Der Server nimmt zusätzlich **Löschen**, **Umbenennen**, **Priorität ändern**, **Neu zuordnen** und **Wieder öffnen** an, welche die Oberfläche nicht anbietet; die Liste zeigt sie an. **Löschen** wird ausgewertet (Löschen braucht zusätzlich **Bearbeiten**). Erlaubnisse für die übrigen vier haben in dieser Version keine Wirkung: Umbenennen und Priorität ändern brauchen **Bearbeiten**, Wiederöffnen braucht **Freigeben**, eine Neuzuordnung über die Metadaten braucht **Bearbeiten** und **Freigeben**. Ausgewertet wird nur eine Verweigerung von **Neu zuordnen**: Sie sperrt die Zuweisung (siehe Poolleitungen).

### Wirkung auf gemeinsame Poolressourcen

Fachrechte und Poolmitgliedschaft entscheiden auch darüber, wer die gemeinsamen Ressourcen eines Autorenpools ändern darf. Diese Prüfungen sind von den oben beschriebenen Diktatrechten getrennt.

| Ressource | Wer sie ändern darf |
|---|---|
| Gemeinsame Wortliste eines Autorenpools (**Wortschatz** › **Wörter**, Bereich **Autorenpool**): importieren, hinzufügen, unterdrücken, reaktivieren | Ein Mitglied dieses Autorenpools, das zusätzlich die **Administrationsrolle** hat, den Pool angelegt hat oder eine Erlaubnis mit Geltungsbereich **Pool** für diesen Pool oder **Alle Diktate** besitzt, die **Bearbeiten** oder **Freigeben** nennt. Auch das Lesen der Poolliste verlangt die Mitgliedschaft. |
| Entscheidungen über Wortschatzvorschläge für den Pool (**Für den Pool übernehmen**) | Dieselbe Befugnis, ohne die Bedingung der Mitgliedschaft. Ein Konto mit der **Administrationsrolle** darf jeden Vorschlag entscheiden. |
| Sprachbefehle und Vorlagen des Pools in **Befehle und Vorlagen** | Jedes Mitglied des Autorenpools. Es gibt keine Prüfung auf Leitung, Administration oder Fachrecht. |
| Systemvorlagen | Jedes Konto mit der **Administrationsrolle**. |

- Diese Prüfungen berücksichtigen keine Verweigerungen, und ein Konto, dessen Klinikkonto nicht aktiv ist, wird abgewiesen.
- Das Beispiel der Schreibkraft unter [Sichere Standardrechte](#sichere-standardrechte) (**Bearbeiten** mit Geltungsbereich **Pool**) erlaubt ihr deshalb auch, die Wortschatzvorschläge des Pools zu entscheiden, und, sobald sie auch Mitglied des Autorenpools ist, seine Wortliste zu ändern.
- Eine globale Administration, die nicht Mitglied eines Autorenpools ist, kann dessen Wortliste weder öffnen noch ändern.
- Die Vorschlagsliste (**Vorschläge**) bleibt in der freigegebenen Version leer, weil kein Code Vorschläge anlegt. Siehe [Lokale Spracherkennung](whisperx-setup.md#die-seite-wortschatz).

## Freigabe (Freigeben) und Abnahme

Freigabe, Abnahme und Textexport sind getrennt. Die Aktion **Freigeben** hat standardmässig niemand, auch nicht die Diktierenden für eigene Diktate.

- Die endgültige Freigabe braucht **Freigeben**. Ihre Schaltfläche heisst in der Diktatansicht und in der Arbeitsliste **Endgültig freigeben**, in der Ansicht **Prüfen und Freigeben** **Fertigstellen**. **Zusätzlich abnehmen**, **Zur Korrektur zurückweisen** und **Wieder öffnen** brauchen ebenfalls **Freigeben**. **Zur Abnahme einreichen** braucht **Bearbeiten** und **Freigeben**.
- **Wieder öffnen** braucht eine Begründung von höchstens 2000 Zeichen und macht die aktive Freigabe ungültig; das Diktat wechselt zu **Zu korrigieren**.
- Die endgültige Freigabe prüft jede Bedingung und weist im Zweifel ab: Berechtigung der aufrufenden Person, aktuelle Bearbeitungsübernahme und Bearbeitungssperre, eine menschliche Korrektur an der Spitze des Transkripts, ein vertrauenswürdiger KIS-Kontext, keine offenen Prüfmarkierungen und, wo verlangt, die Abnahme. Sie bindet die exakten Audio-, Transkript- und Kontextrevisionen sowie den Text-Hash. Sie schreibt das Auditereignis `recording.edited` mit den Feldern `workflow_status` und `workflow_finalize` und reiht danach die Folgearbeit als `ProcessingJob` ein.
- Vertrauenswürdiger Kontext: Ohne `CLINIC_PROFILE=staging` ist der KIS-Adapter deaktiviert, die endgültige Freigabe wird daher abgewiesen (`untrusted_context`). Mit `CLINIC_PROFILE=staging` wird ein synthetischer MockKIS-Adapter verwendet. Er ist kein Nachweis einer KIS-Anbindung und gilt unter `CLINIC_PROFILE=production` nie als vertrauenswürdig.

Die Abnahmerichtlinie wird je Diktat unter **Verwaltung › Richtlinien › Abnahme** (`/admin#/abnahme`) festgelegt:

- Geben Sie die **Diktat-ID** ein und wählen Sie **Richtlinie laden**.
- Unter **Pflichtfreigabe** macht das Kontrollkästchen **Freigabe erforderlich** die Abnahme verpflichtend. **Berechtigte Freigebende** fügen Sie über **Benutzer-ID** und **Hinzufügen** hinzu. **Speichern** veröffentlicht eine neue Version. **Entscheidungen** listet die Entscheide.
- Veröffentlichen darf nur die aktive globale Administration. Andere Benutzer, die das Diktat lesen dürfen, erhalten `403`; die Abweisung wird als `recording.edited` mit dem Ergebnis `denied` und dem Feld `acceptance_policy` protokolliert, auch bei ausgeschaltetem Auditprotokoll. Benutzer, die das Diktat nicht lesen dürfen, erhalten `404`, und es entsteht kein Eintrag. Eine veröffentlichte Änderung wird nur bei eingeschaltetem `audit_log_enabled` als `recording.edited` mit dem Feld `acceptance_policy` protokolliert.
- Freigebende müssen bestehende Benutzer mit aktivem Klinikkonto sein, sonst `422` `INVALID_APPROVER`. Ein Speichern auf veralteter Version ergibt `409` `STALE_VERSION`.
- Ohne veröffentlichte Richtlinie ist keine Abnahme verlangt.

Die Seite **Abnahme** verwendet das Wort «Freigabe» für die Abnahme. Auch die Ansicht **Prüfen und Freigeben** beschriftet die Abnahme mit **Freigeben** und die Rückgabe mit **Zurückweisen**; dort heisst die endgültige Freigabe **Fertigstellen**. Für einen Entscheid braucht es beides: Die Person muss als Freigebende eingetragen sein und die Aktion **Freigeben** besitzen. Ist niemand eingetragen, kann niemand abnehmen. Eine Rückgabe braucht eine Begründung von 3 bis 2000 Zeichen; das Diktat wechselt zu **Zu korrigieren**, und die aktuell zugewiesene Person bleibt zugewiesen und wird als bearbeitende Person der Rückgabe festgehalten.

## Originalaudio exportieren

**Originalaudio exportieren** (Aktion `audio_export`) ist ein eigenes Recht, getrennt von **Exportieren** (Textexport).

- Es gehört nicht zu den eigenen Standardrechten der Diktierenden, und die globale Administration erhält es nicht mit ihrem Lese- und Abspielzugriff. Vergeben Sie es ausdrücklich.
- Der Server prüft es auf den Upstream-Routen für Originaldateien: `recordings.get_audio` mit `download=true` und `api_v1.download_audio`. Die Aufnahmedaten melden es dem Browser als `can_download_audio`. Derzeit ruft keine Klinikansicht einen Originalaudio-Download auf.
- Das Klinikmodul für hashgeprüfte Originalausgabe mit einer nicht gesendeten `.eml`-Datei (bis 10 MiB) hat in der Anwendung keinen Aufrufer; nur Tests verwenden es. Das Öffnen eines lokalen Mailentwurfs mit Audioanhang ist nicht umgesetzt; Status: Offene Lücke (WG-EXP-009).

Der Textexport (TXT, DOCX, KIS) braucht **Exportieren** und exportiert nur die freigegebene Revision, sonst `409` `NOT_RELEASED`.

## Was protokolliert wird

Änderungen an Pools, Mitgliedschaften, Verknüpfungen, Leitungen und Fachrechten sind keine Auditereignisse. Sie gehen in den Poolverlauf `clinic_pool_history`:

| Änderung | Eintrag im Poolverlauf |
|---|---|
| Pool angelegt, umbenannt, deaktiviert, reaktiviert, Gültigkeit gesetzt | `pool`: `created`, `renamed`, `deactivated`, `reactivated`, `validity_set`, mit altem und neuem Wert (eine Umbenennung über `PUT /pools/<kind>/<pool_id>` speichert nur den neuen Namen) |
| Mitglied hinzugefügt oder entfernt | `membership`: `added`, `removed` |
| Leitung gesetzt oder entfernt | `membership`: `role_changed`, mit alter und neuer Rolle |
| Verknüpfung angelegt | `route`: `added` |
| Fachrecht hinzugefügt oder widerrufen | `grant`: `added`, `revoked`, mit Benutzer, Wirkung, Geltungsbereich, Pool und Aktionen |

- Jeder Eintrag hält Zeitpunkt und handelnde Person fest. Das Anwendungsprotokoll erhält zusätzlich die Zeile `clinic pool: <change> <entity> <id> by <user id>`.
- Der Poolverlauf ist in der Anwendung nur fortschreibbar: Änderungen und Löschungen werden abgewiesen. Kein Datenbank-Trigger schützt ihn.
- Keine Seite und keine Route zeigt den Poolverlauf an, und er erscheint nicht im **System-Auditprotokoll**. Der **Stand** in **Pools verwalten** ist die Anzahl seiner Zeilen.
- Eine abgewiesene Rechteänderung schreibt kein Auditereignis. Konten ohne **Administrationsrolle** weist die Routenprüfung mit `403` `forbidden` ab, bevor der Rechteendpunkt läuft. Der Rechteendpunkt schreibt die Warnung `clinic grants: change refused for actor … on user …` nur für ein Konto mit **Administrationsrolle**, dessen Klinikkonto nicht aktiv ist, ins Anwendungsprotokoll. Andere Abweisungen (fehlendes oder falsches CSRF-Token, ungültiger Inhalt, veralteter Stand, unbekannter Benutzer) protokolliert er nicht.

Diese Vorgänge schreiben Auditereignisse (siehe [Auditprotokoll](audit-log.md)). Auch bei ausgeschaltetem `audit_log_enabled` geschrieben werden: die Zuweisung (`recording.edited`, Feld `assignee`), die endgültige Freigabe (`recording.edited`, Felder `workflow_status` und `workflow_finalize`), Einreichen, Abnehmen, Zurückweisen und Wiederöffnen (`recording.edited`, Feld `workflow_status` sowie `workflow_submit`, `workflow_accept`, `workflow_reject` oder `workflow_reopen`) und eine abgewiesene Veröffentlichung der Abnahmerichtlinie (`recording.edited`, Ergebnis `denied`, Feld `acceptance_policy`). Nur bei eingeschaltetem `audit_log_enabled` geschrieben werden: eine veröffentlichte Abnahmerichtlinie (`recording.edited`, Feld `acceptance_policy`) und jede fremde Diktatzeile, welche die aktive globale Administration in der Arbeitsliste erhält (`recording.content.read`, `part` `worklist`).

## API-Referenz

Alle Routen gibt es nur im Klinikmodus (sonst `404`), und sie brauchen eine angemeldete Browsersitzung. Schreibende Anfragen brauchen das CSRF-Token der Sitzung im Header `X-CSRF-Token`. Die handelnde Person stammt immer aus der Sitzung. Die Pool- und Poolverwaltungsrouten (`/pools`, `/pool-admin`) weisen einen Anfragekörper mit `actor`, `actor_id`, `user`, `user_id`, `role`, `roles`, `is_admin` oder `created_by` ab; Ausnahme ist der Körper `{"role": …}` der Leitungsroute. Die Rechterouten nehmen nur `expected_revision`, `effect`, `scope`, `pool_id` und `actions` an; der Körper zum Widerrufen ist genau `{"expected_revision": <n>}`. Der Körper der Zuweisung ist genau `{"assignee_user_id": <id>}`; `null` hebt die Zuweisung auf (die Oberfläche bietet das nicht an). Der Körper der Abnahmerichtlinie ist genau `{"acceptance_required": <bool>, "approver_ids": [<id>, …]}`.

| Methode und Pfad (Präfix `/api/clinic/v1`) | Zweck | Wer |
|---|---|---|
| `GET /pools` | Pools mit Mitgliedern und Stand | Alle; ohne Verwaltungsrecht nur eigene Pools und nur sich selbst |
| `PUT /pools/<kind>/<pool_id>` mit `expected_revision`, `name`, `member_user_ids` | Name und vollständige Mitgliedschaft setzen; veralteter Stand ergibt `409` `stale_revision` | Aktive globale Administration |
| `GET /pool-admin/pools` | Poolliste mit Aktiv-, Gültigkeits- und Zuteilbarkeitsstatus | Aktive globale Administration |
| `POST /pool-admin/pools` mit `kind` (`author` oder `staff`) und `name` | Pool anlegen (`201`) | Aktive globale Administration |
| `PUT /pool-admin/pools/<kind>/<pool_id>/name` mit `name` | Umbenennen | Aktive globale Administration |
| `PUT /pool-admin/pools/<kind>/<pool_id>/active` mit `active` | Deaktivieren oder reaktivieren | Aktive globale Administration |
| `PUT /pool-admin/pools/<kind>/<pool_id>/validity` mit `valid_from`, `valid_to` | Gültigkeitszeitraum setzen | Aktive globale Administration |
| `POST /pool-admin/pools/author/<pool_id>/links` mit `staff_pool_id` | Autorenpool mit Schreibkräfte-Pool verknüpfen (`201`; `409` `already_linked`) | Aktive globale Administration |
| `GET /pool-admin/pools/<kind>/<pool_id>/usage` | Verwendungszahlen | Aktive globale Administration oder eine Leitung des Pools |
| `PUT /pool-admin/pools/staff/<pool_id>/members/<user_id>/role` mit `role` (`lead` oder `member`) | Leitung setzen oder entfernen | Aktive globale Administration |
| `GET /identity/users/<user_id>/grants` | Liste der Fachrechte mit Stand | Aktive globale Administration, aktive Identitätsadministration, die betroffene Person |
| `POST /identity/users/<user_id>/grants` mit `expected_revision`, `effect`, `scope`, `pool_id`, `actions` | Erlaubnis oder Verweigerung hinzufügen | Aktive globale Administration |
| `DELETE /identity/users/<user_id>/grants/<grant_id>` mit `expected_revision` | Eintrag widerrufen | Aktive globale Administration |
| `GET /dictations/<id>/assignment/candidates`, `POST /dictations/<id>/assignment` | Empfänger und Zuweisung; `POST` braucht `If-Match` und einen `Idempotency-Key` | Globale Administration oder Poolleitung |
| `GET`, `PUT /dictations/<id>/acceptance-policy` | Abnahmerichtlinie lesen oder veröffentlichen; `PUT` braucht `If-Match` | Veröffentlichen: aktive globale Administration |

## Fehlerbehebung

- **Eine Aufnahme wird nicht als Diktat angelegt.** Die diktierende Person ist in keinem aktiven Autorenpool. Fügen Sie sie unter **Pools verwalten** hinzu und prüfen Sie, ob der Pool aktiv und gültig ist.
- **Eine Schreibkraft sieht ein ihr zugewiesenes Diktat, kann es aber nicht abspielen oder bearbeiten.** Die Mitgliedschaft im Schreibkräfte-Pool gibt nur Ansehen, und die Zuweisung gibt keine Rechte. Vergeben Sie **Abspielen** und **Bearbeiten**, zum Beispiel mit Geltungsbereich **Pool** für den Autorenpool.
- **Der Zuweisungsdialog bietet niemanden an.** Der Autorenpool ist mit keinem aktiven Schreibkräfte-Pool mit Mitgliedern verknüpft. Legen Sie die Verknüpfung über die API an und prüfen Sie Status und Mitglieder des Schreibkräfte-Pools.
- **Eine Poolleitung kann nicht zuweisen.** Prüfen Sie, ob die Person als **Leitung** markiert ist, ob der Schreibkräfte-Pool aktiv und mit dem Autorenpool des Diktats verknüpft ist und ob keine Verweigerung von **Neu zuordnen** greift.
- **In der Verwaltung fehlt eine Seite.** Die API der Seite hat das Konto abgewiesen; der Hinweis «Für dieses Konto nicht freigegeben: …» nennt sie.

Verwandte Seiten: [Benutzerverwaltung](user-management.md), [Auditprotokoll](audit-log.md), [Aufbewahrung](retention.md), [Systemeinstellungen](system-settings.md). Quellcode des Poolmodells (Klinik-Fork): [src/clinic/pools.py](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/pools.py).

Zurück zum [Administrationshandbuch](index.md)
