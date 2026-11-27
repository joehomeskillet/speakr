# Was ersetzt Ambrose im Klinikablauf?

[English](../../admin-guide/ambrose.md)

Der Upstream-Assistent Ambrose bietet einen Aufnahmechat und eine separate Ask-Ansicht. Der Klinikarbeitsplatz hat weder eine Ambrose-Schaltfläche noch einen Chatbereich oder eine aufnahmeübergreifende Ask-Seite. Ein Assistentenname oder eine Upstream-Inquire-Einstellung ergänzt diese Bedienelemente nicht. Die Verwaltungsreiter **Standard-Prompts** und **Vektorspeicher** sowie der Kontoreiter **Prompt-Optionen** sind im Klinikmodus ausgeblendet.

## Mit einem Diktat arbeiten

Öffnen Sie das Diktat unter **Aufnahmen** oder **Arbeitsliste**. Hören Sie die betreffende Passage ab und vergleichen Sie sie mit dem gespeicherten Transkript. Für einen kurzen Überblick öffnen Sie **KI-Zusammenfassung** im **Schreibplatz** oder in den **Diktatdetails**.

Dieser Zusammenfassungsbereich gehört zum aktuellen Klinik-Build. Er nimmt keine Fragen entgegen und verändert den Diktattext nicht. Seine Zusammenfassung gehört zu einer gespeicherten Textrevision, nicht zu einer Suche über andere Aufnahmen. Nach einer Korrektur wird die bisherige Zusammenfassung als veraltet gekennzeichnet. Ungespeicherte Änderungen sind nicht enthalten.

- Zum Lesen ist **Ansehen** für das Diktat erforderlich.
- **Zusammenfassung erstellen** oder **Zusammenfassung neu erstellen** erfordert **Bearbeiten**, einen verwendbaren gespeicherten Text mit mindestens 10 Zeichen und ein konfiguriertes lokales Textmodell.
- Die erste geeignete Spracherkennungsrevision kann eine automatische Zusammenfassung einreihen, wenn sowohl die Systemeinstellung als auch die Präferenz der diktierenden Person dies erlauben. Spätere Korrekturen erzeugen sie nicht automatisch neu.
- Das Öffnen des Bereichs liest den Status; es fordert keine neue Zusammenfassung an. Eine gespeicherte Zusammenfassung bleibt lesbar, wenn der Modelldienst nicht verfügbar ist.

Die [Modellwahl und Zusammenfassungen](model-configuration.md) beschreiben die Prüfung lokaler Dienste, Rechte und Fehlerzustände; die [Prompts](prompts.md) beschreiben die Anweisungen für den erzeugten Text. Vergleichen Sie generierte Inhalte mit der Quelle; eine flüssige Zusammenfassung belegt keine klinische Richtigkeit.

## Verbliebener Upstream-Code

Vorhandene Chat- und Inquire-Handler bedeuten nicht, dass die Klinikoberfläche einen Assistenten anbietet. Im Klinikmodus hängen ihre Funktionssperren von einer lokalen `TEXT_MODEL_BASE_URL` und ebenfalls lokalen konfigurierten Chat-, Übersetzungs- und Embedding-Endpunkten ab. Selbst wenn diese Sperre entfällt, weist der Guard eine Inquire-Anfrage ohne Aufnahmeziel mit `403 clinic_feature_unsupported` ab. Aufnahmebezogene Aufrufe unterliegen weiterhin den Inhaltsrechten und bei Änderungen an Klinikdiktaten der Bearbeitungssperre.

Belassen Sie `ENABLE_INQUIRE_MODE=false`. Nicht alle Upstream-Indexierungspfade erzwingen die lokale Modellregel des Klinikmodus; ihre Aktivierung kann Transkripte verarbeiten, ohne eine Klinik-Suchoberfläche bereitzustellen. Siehe [Diktate ohne Vektorindex finden](vector-store.md).

Inkognito-Aufnahmen bietet der Klinik-Fork nicht an.

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/ambrose.md)
