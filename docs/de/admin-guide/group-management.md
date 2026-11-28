# Autorenpools und Schreibkräfte-Pools

[English](../../admin-guide/group-management.md)

Der Klinik-Fork verwendet getrennte **Autorenpools** und **Schreibkräfte-Pools**. Öffentliche und interne Freigaben, Ordner und die ursprüngliche freigabeorientierte Gruppenoberfläche werden nicht angeboten. Eine vorhandene ursprüngliche Gruppe wird nicht automatisch zu einem Klinikpool.

## Poolarten und Zuordnung

| Pool | Mitglieder | Zweck |
| --- | --- | --- |
| **Autorenpool** | Diktierende Autoren | Gemeinsame Autorenressourcen und der einem Diktat zugeordnete Autorenpool-Geltungsbereich |
| **Schreibkräfte-Pool** | Schreibkräfte | Zuständigkeit für die Bearbeitung von Diktaten verknüpfter Autorenpools |

Die beiden Arten bleiben auch bei gleichen Namen getrennt. Ein Schreibkräfte-Pool verwendet eine ursprüngliche Gruppe und deren Mitgliedschaften; ein Autorenpool besitzt eine eigene Mitgliedschaftstabelle. Eine Verknüpfung ordnet einen Autorenpool einem Schreibkräfte-Pool zu. Aktuelle Mitglieder verknüpfter, aktiver Schreibkräfte-Pools werden als Zuweisungsempfänger angeboten.

Ein Autor braucht einen aktiven Autorenpool innerhalb seiner Gültigkeit, um durch Aufnahme oder Upload ein Klinikdiktat anzulegen. Andernfalls wird der Upload mit `clinic_no_author_pool` verweigert. Halten Sie jeden Autor in genau einem Autorenpool. Die Oberfläche nennt diese Regel, der Mitgliedschaftsdienst verweigert mehrere Mitgliedschaften aber nicht; beim Anlegen eines Diktats gibt es keine Autorenpool-Auswahl.

Schreibkräfte-Pool-Mitgliedschaft zusammen mit einer Verknüpfung erteilt **Ansehen** für die Diktate dieses Autorenpools. Sie erteilt weder Abspielen noch Bearbeiten oder Freigeben. Ein Ansichtsrecht für ein Objekt bedeutet zudem nicht, dass jedes solche Objekt in der Arbeitsliste erscheint: Gewöhnliche Schreibkräfte sehen eigene und zugewiesene Diktate; Leitungen zusätzlich nicht zugewiesene, eingereichte Diktate der ihren Teams zugeordneten Autorenpools.

## Verwaltungsseiten

Eine aktive Administration verwaltet Pools unter **Verwaltung → Konten und Pools**. Identitätsadministration allein und Poolleitung erteilen keine Poolverwaltung.

### Schreibkräfte-Pools

Öffnen Sie `/admin#/schreibkraefte-pools`.

1. Wählen Sie **Pool hinzufügen**, um einen Schreibkräfte-Pool mit eindeutigem Namen anzulegen.
2. Öffnen Sie **Schreibkräfte-Pool bearbeiten**. Ergänzen Sie aktive Konten über **Benutzer nach Name suchen** und **Hinzufügen**.
3. Setzen Sie **Leitung** neben einem Mitglied, das Zuweisungen koordinieren soll. Das Entfernen einer Mitgliedschaft benötigt Bestätigung.
4. Verwenden Sie **Umbenennen**, **Gültigkeit** und **Gültigkeit speichern** für Name und Gültigkeitszeitraum.
5. Prüfen Sie **Verwendung** vor **Deaktivieren**: offene Diktate, Mitglieder und verknüpfte Autorenpools. **Reaktivieren** setzt den Pool wieder aktiv.

Ein Team ohne Mitglieder wird als unbesetzt angezeigt. Ein Pool ausserhalb seiner Gültigkeit erscheint als **Aktiv, aktuell ausserhalb der Gültigkeit**.

### Diktierende und Autorenpools

Öffnen Sie `/admin#/diktierende`. Die Tabelle zeigt Autoren, deren Autorenpools, eigene Schreibkräfte-Pool-Mitgliedschaften, Diktierrolle und Kontostatus. Die Schreibkräfte-Pool-Spalte beschreibt persönliche Mitgliedschaften, keine Verknüpfungen zur Weiterleitung.

Erweitern Sie **Autorenpools und Schreibkräfte-Pools**, um **Pools verwalten** zu verwenden. Ergänzen Sie Autorenpool-Mitglieder per Benutzer-ID oder entfernen Sie eine Mitgliedschaft nach Bestätigung. Jeder Speichervorgang sendet die vollständige Mitgliederliste und deren zuletzt gelesenen Stand. Laden Sie nach einem Konflikt neu. Mitgliedschaftsänderungen werden einzeln bestätigt; scheitert ein Speichervorgang teilweise, prüfen Sie die aktuelle Mitgliederliste vor der Wiederholung.

Für Autorenpool-Anlage, Umbenennung, Deaktivierung, Gültigkeit und Verknüpfungen bestehen derzeit API-Endpunkte, hier aber keine entsprechenden Autorenpool-Bedienelemente. **Benutzer und Rollen → Poolbezüge** ist schreibgeschützt.

## Pooloperationen nur über die API

Dies sind Klinik-Endpunkte für Browsersitzungen, nicht die entfernte allgemeine API v1. Alle Schreibzugriffe benötigen eine berechtigte Sitzung und deren CSRF-Token in `X-CSRF-Token`. Übermitteln Sie keinen Akteur oder keine Rolle im Anfragerumpf; der Server ermittelt die Berechtigung aus der Sitzung.

| Methode und Pfad nach `/api/clinic/v1` | Rumpf oder Zweck |
| --- | --- |
| `POST /pool-admin/pools` | `{"kind":"author","name":"<name>"}`; `staff` erstellt einen Schreibkräfte-Pool |
| `PUT /pool-admin/pools/<kind>/<pool_id>/name` | `{"name":"<name>"}` |
| `PUT /pool-admin/pools/<kind>/<pool_id>/active` | `{"active":false}` zum Deaktivieren, `true` zum Reaktivieren |
| `PUT /pool-admin/pools/<kind>/<pool_id>/validity` | `valid_from` und `valid_to`: ISO-8601-Zeitstempel mit Zeitzonenversatz oder `null` |
| `POST /pool-admin/pools/author/<pool_id>/links` | `{"staff_pool_id":"<id>"}` |
| `GET /pool-admin/pools/<kind>/<pool_id>/usage` | Anzahl Mitglieder, offene Diktate und Verknüpfungen |
| `GET /pools` | Mitgliederlisten und aktueller Stand |
| `PUT /pools/<kind>/<pool_id>` | `expected_revision`, `name`, vollständige Liste `member_user_ids` |

`<kind>` ist `author` oder `staff`. Poolnamen enthalten 1–100 Zeichen, dürfen keine äusseren Leerzeichen haben und müssen in ihrem Namensraum eindeutig sein; Schreibkräfte-Pool-Namen müssen auch unter ursprünglichen Gruppen frei sein. Der Gültigkeitsbeginn muss vor dem Ende liegen. Ein zukünftiger Pool lässt sich erst verknüpfen, wenn er zuordnungsfähig wird. Es gibt keine Route zum Entfernen einer Verknüpfung.

## Deaktivierung und Leitung

Pools werden deaktiviert, niemals gelöscht. Deaktivierung und abgelaufene Gültigkeit verhindern neue Zuordnungen über den Pool. Sie erhalten Diktate und Verlauf, verteilen offene Arbeit nicht um und ändern den Autor nicht. Bestehender Lesezugriff aus einer Schreibkräfte-Pool-Verknüpfung bleibt erhalten; Deaktivierung ersetzt keinen Zugriffs- oder Mitgliedschaftsentzug.

Eine Leitung kann eingereichte Diktate verknüpfter Autorenpools zuweisen oder neu zuweisen, solange der Schreibkräfte-Pool zuordnungsfähig ist. Sie benötigt Ansichtsrecht und darf keine passende Verweigerung für **Neu zuordnen** haben. Eine aktive Administration darf ohne Leitungsrolle oder Ansichtserlaubnis zuweisen, dieselbe Neuzuordnungsverweigerung gilt aber weiterhin. Empfänger müssen verknüpften, zuordnungsfähigen Schreibkräfte-Pools angehören. Eine Zuweisung erteilt keine zusätzlichen Inhaltsrechte.

Die Leitungsrolle erlaubt weder Poolanlage noch Umbenennung, Mitgliedschaftsänderungen, Rollenänderungen, Fachrechteverwaltung oder Zugriff auf `/admin`. Verwendungszahlen des geleiteten Pools sind über die Pool-API verfügbar. Zuweisungsänderungen benötigen zwingend einen Auditeintrag; ein Fehler beim Speichern des Audits verweigert die Änderung.

## Fachrechte

Verwalten Sie **Fachrechte** unter **Benutzer und Rollen**. Wählen Sie Benutzer, Wirkung (**Erlauben** oder **Verweigern**), Geltungsbereich (**Eigene Diktate**, einen Autoren-**Pool** oder **Alle Diktate**) und ausdrückliche Aktionen. Nur eine aktive Administration darf diese Einträge ändern. **Entziehen** widerruft einen Eintrag. Ein veralteter Stand ergibt einen Konflikt; laden Sie vor der Wiederholung neu.

| Konto oder Beziehung | Standardzugriff auf Diktate |
| --- | --- |
| Autor auf eigenes Diktat | Ansehen, Abspielen und Bearbeiten |
| Mitglied eines mit dem Autorenpool verknüpften Schreibkräfte-Pools | Nur Ansehen |
| Poolleitung | Gleicher Inhaltszugriff wie ein Mitglied, zusätzlich Zuweisungsbefugnis |
| Aktive Administration | Klinikdiktate ansehen, abspielen und Korrekturen lesen |
| Kennzeichen Identitätsadministration allein | Kein zusätzlicher Diktatzugriff |

Passende Verweigerungen überstimmen Eigentümerschaft, Teamverknüpfungen und ausdrückliche Erlaubnisse. **Ausnahme:** Ansehen, Abspielen und Korrekturlesen durch eine aktive Administration werden vor Verweigerungseinträgen geprüft. Diese Ausnahme erteilt weder Bearbeitung noch Export, Freigabe oder Originalaudio-Download.

Die Vorschau **Wirksames Ergebnis nach dem Speichern** vergleicht Aktionseinträge ohne Objektgeltungsbereich, Eigentümerschaft, Teamverknüpfungen oder administrativen Zugriff. Sie ist keine vollständige Berechtigungsentscheidung für ein bestimmtes Diktat. Testen Sie die beabsichtigte Aktion und einen ausgeschlossenen Geltungsbereich mit synthetischem Material.

Poolressourcenrechte unterscheiden sich ebenfalls von Diktatrechten. Eine Bearbeitungs- oder Freigabeerlaubnis für einen Autorenpool kann Entscheidungen über dessen Wortschatzvorschläge erlauben; das Bearbeiten seiner Wortliste benötigt zusätzlich Autorenpool-Mitgliedschaft. Poolbefehle und Vorlagen verwenden Mitgliedschaftsprüfungen. Betrachten Sie ein Diktatrecht nicht als vollständige Bestandsaufnahme der Ressourcenbefugnisse.

## Freigabe, Abnahme und Originalaudio

**Freigeben** ist ein ausdrückliches Recht, auch für Autoren und Administration. Das Einreichen zur Abnahme benötigt Bearbeitungs- und Freigaberechte; Abnahme, Zurückweisung und Wiederöffnung benötigen Freigaberechte. Die Zuweisung über **Zuweisen** folgt den getrennten Leitungs-/Administrationsregeln oben.

Die Seite **Abnahme** setzt eine Abnahmerichtlinie für ein ausgewähltes Diktat: ob Abnahme erforderlich ist und welche aktiven Benutzer abnehmen dürfen. Nur eine aktive Administration kann die Richtlinie veröffentlichen. Ohne veröffentlichte Richtlinie ist keine Abnahme erforderlich. Eine aufgeführte abnehmende Person benötigt weiterhin das Freigaberecht. Endgültige Freigabe ist von Abnahme getrennt und erfordert die aktuellen Revisionen. Die KIS-Anbindung ist rein lesend: Das KIS öffnet Speakr mit einer Fall-ID als URL-Kontext, und Speakr zeigt nur Diktate, die mit diesem Fall verknüpft sind und die die Person ohnehin sehen darf. Nichts wird an das KIS geliefert oder zurückgeschrieben (keine Dokumentlieferung, keine Quittungen, keine Neuausstellung oder Ergänzung). Eine Simulation beweist keine funktionierende klinische Anbindung.

**Originalaudio exportieren** (`audio_export`) ist vom Dokument-**Exportieren** getrennt. Weder eigene Standardrechte noch administrativer Abspielzugriff umfassen den Originalaudio-Download. Eine Übergabe an ein Mailprogramm oder ein Mailanhang wird in diesem Fork nicht angeboten. Siehe [Datenschutz](privacy.md) und [Aufbewahrung](retention.md) zum Umgang mit Daten.

## Verlauf und häufige Probleme

Pool-, Mitgliedschafts-, Zuordnungs-, Leitungs- und Rechteänderungen werden in `clinic_pool_history` getrennt vom System-Auditprotokoll aufgezeichnet. Die Anwendung behandelt diesen Verlauf als nur ergänzbar. Die Verwaltung bietet dafür keine Verlaufsansicht. Zuweisungs- und Workflowaktionen verwenden Auditereignisse; siehe [Auditprotokoll](audit-log.md).

- **Aufnahme kann kein Diktat werden:** Prüfen Sie die aktive, aktuell gültige Autorenpool-Mitgliedschaft des Autors.
- **Zugewiesenes Diktat lässt sich nicht abspielen oder bearbeiten:** Erteilen Sie die erforderliche Aktion im passenden Autorenpool-Geltungsbereich; Zuweisung allein reicht nicht.
- **Zuweisung bietet niemanden an:** Prüfen Sie Verknüpfungen, Gültigkeit des Schreibkräfte-Pools und aktive Mitglieder.
- **Leitung kann nicht zuweisen:** Prüfen Sie Leitungsrolle, Ansichtsrecht und Neuzuordnungsverweigerungen.
- **Verwaltungsseite fehlt:** Prüfen Sie **Für dieses Konto nicht freigegeben** und die Kontorolle.

Siehe [Benutzerverwaltung](user-management.md) für Konten und [Systemstatistik](statistics.md) für die getrennte Einschränkung der Statistikrechte.
