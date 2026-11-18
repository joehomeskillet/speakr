"""German clinic defaults for the summary and title prompts.

The upstream defaults in src/config/prompts.py stay untouched. With CLINIC_MODE
on, the shipped default and an admin value that is still the upstream default
resolve to the German clinic prompts below. Any other admin, user, tag or
folder prompt wins as before.
"""

from __future__ import annotations

from src.clinic.config import clinic_mode_enabled

CLINIC_DEFAULT_SUMMARY_PROMPT = """Fasse das ärztliche Diktat aus einer Suchtklinik (Entzugs- und Suchtbehandlung) als strukturierte Klinik-Zusammenfassung auf Deutsch zusammen.

Gliedere in Markdown mit diesen Überschriften, in dieser Reihenfolge:

### Anlass / Fragestellung

### Suchtanamnese
Substanzen, Konsummuster, letzte Einnahme, frühere Entzüge und Therapien.

### Aktueller Befund / Psychischer Status
Einschließlich Entzugssymptomen und genannter Scores (z. B. CIWA-Ar, COWS).

### Diagnosen
Wie diktiert; ICD-10-Codes nur, wenn sie genannt werden.

### Medikation / Entzugsbehandlung

### Procedere / Nächste Schritte

### Offene Punkte / Unklarheiten
Unklare oder widersprüchliche Stellen im Diktat.

Regeln:
- Verwende ausschließlich Inhalte aus dem Transkript. Erfinde nichts.
- Lasse einen Abschnitt vollständig weg, wenn das Diktat dazu nichts sagt.
- Füge keine zusätzlichen Diagnosen, Dosierungen oder Empfehlungen hinzu.
- Übernimm Zahlen, Dosierungen, Daten und Namen exakt wie diktiert.
- Schreibe in medizinischem Deutsch, knapp, in Stichpunkten oder kurzen Sätzen.
- Markiere unsichere oder unverständliche Passagen der Spracherkennung mit «[bitte prüfen]», statt zu raten."""

CLINIC_DEFAULT_TITLE_PROMPT = """- Titel auf Deutsch
- Höchstens 8 Wörter
- Keine Wendungen wie "Diktat zu" oder "Bericht über"
- Nur das Hauptthema nennen (z. B. Aufnahme, Verlauf, Entzug, Austritt)"""


def shipped_summary_prompt() -> str:
    """The shipped summary default: German clinic prompt in clinic mode."""
    from src.config.prompts import DEFAULT_SUMMARY_PROMPT
    return CLINIC_DEFAULT_SUMMARY_PROMPT if clinic_mode_enabled() else DEFAULT_SUMMARY_PROMPT


def shipped_title_prompt() -> str:
    """The shipped title default: German clinic prompt in clinic mode."""
    from src.config.prompts import DEFAULT_TITLE_PROMPT
    return CLINIC_DEFAULT_TITLE_PROMPT if clinic_mode_enabled() else DEFAULT_TITLE_PROMPT


def admin_summary_prompt(value):
    """Admin summary prompt; in clinic mode an unset or upstream-default value becomes the clinic default."""
    from src.config.prompts import DEFAULT_SUMMARY_PROMPT
    if clinic_mode_enabled() and (not value or str(value).strip() == DEFAULT_SUMMARY_PROMPT.strip()):
        return CLINIC_DEFAULT_SUMMARY_PROMPT
    return value


def admin_title_prompt(value):
    """Admin title prompt; same rule as admin_summary_prompt."""
    from src.config.prompts import DEFAULT_TITLE_PROMPT
    if clinic_mode_enabled() and (not value or str(value).strip() == DEFAULT_TITLE_PROMPT.strip()):
        return CLINIC_DEFAULT_TITLE_PROMPT
    return value
