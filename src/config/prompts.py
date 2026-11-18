"""Default prompt templates — single source of truth.

Kept dependency-free (no app/model imports) so both ``src/init_db.py`` (which
seeds the admin setting on a fresh database) and ``src/tasks/processing.py``
(which uses it as a runtime fallback) can import it cheaply without circular
imports. Previously this text was copy-pasted in three places and could drift;
change it here only.
"""

# The summary prompt a fresh install ships with, and the fallback used at
# summarization time when no per-recording / tag / folder / user / admin prompt
# is set. To change the shipped default, edit this string.
DEFAULT_SUMMARY_PROMPT = """Erstelle eine umfassende Zusammenfassung als Markdown. Erfasse zuerst das Sitzungsprotokoll, danach die besprochenen Kernthemen, die wichtigsten Erkenntnisse und die nächsten Schritte mit den jeweils verantwortlichen Personen. Dokumentiere anschliessend alle weiteren wesentlichen Informationen, auch wenn sie nicht ausdrücklich angefordert wurden. Bewahre wichtige Einzelheiten und Nuancen. Erfinde keine Aussagen oder Verantwortlichkeiten.

Beispielformat:

### Sitzungsprotokoll

**Teilnehmende:**
- Bob
- Alice

---

**1. Einleitung und Überblick:**
- Alice möchte die Zuständigkeiten der Nordabteilung und Möglichkeiten für technische Innovationen verstehen.

### Besprochene Kernthemen

### Wichtige Erkenntnisse

### Nächste Schritte und Verantwortliche

### Weitere wichtige Informationen

Führe nur Abschnitte mit belegbaren Inhalten aus dem Transkript auf."""


# The admin-editable instruction body for CONTEXTUAL speaker labelling — used
# when a recording has no voice embeddings and the app falls back to naming
# speakers from the transcript, constrained to the user's saved speaker
# profiles. This is only the guidance paragraph; the code appends the concrete
# candidate list, the speaker labels, and the strict JSON output contract after
# it, so those cannot be broken by an edit here. It is deliberately the trailing
# (variable) part of a transcript-first prompt so it stays prefix-cache friendly
# (see PREFIX_CACHE_OPTIMIZED_PROMPTS): the transcript is the stable prefix, this
# guidance is the editable suffix.
DEFAULT_CONTEXTUAL_SPEAKER_PROMPT = """Work out which of the known speaker profiles each speaker label most likely corresponds to, using clues in the conversation such as the names people are addressed by, self-introductions, and references to roles or relationships. Only assign a known profile when the conversation clearly supports it; if you are not confident about a speaker, leave that speaker unassigned with an empty string. Never invent a name that is not in the known profiles list."""


# Instructions for AI-generated recording titles (#400), and the fallback when
# no tag / folder / user / admin title prompt is set. Only the instructions are
# editable: the transcript, the output-language directive and the rule to reply
# with the title text alone are added in code, after the transcript, so an edit
# here never disturbs prefix caching.
DEFAULT_TITLE_PROMPT = """- Formuliere einen aussagekräftigen Titel.
- Höchstens 8 Wörter.
- Keine Einleitungen wie «Besprechung über» oder «Sitzung zu».
- Nenne nur das Hauptthema."""
