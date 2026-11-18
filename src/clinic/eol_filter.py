"""End-of-dictation (EOL) filter for automatic device imports.

Decides per file found on a device whether the automatic import takes it,
skips it or sends it to clarification. The EOL marker is taken from device
metadata (``FileFacts.eol_flag``); file times are never used as EOL evidence
and ``FileFacts`` deliberately has no time field.

Scope:
- Pure decision logic for one file/policy pair.
- Validation of inputs with stable error codes.

Non-scope:
- No persistence, routes or Flask context.
- No source deletion (``delete_source`` is always ``False``).
- No ASR, no author resolution, no time-based EOL guessing.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

IMPORT = "IMPORT"
SKIP = "SKIP"
CLARIFY = "CLARIFY"


class EolFilterError(ValueError):
    """The file facts or policy were rejected before a decision could be made."""

    code = "eol_filter_invalid"


@dataclass(frozen=True)
class FileFacts:
    name: str
    size: int
    eol_flag: bool | None
    stable: bool
    author_known: bool
    author_conflict: bool = False


@dataclass(frozen=True)
class Policy:
    eol_filter_enabled: bool = True
    auto_import_non_eol: bool = False


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    delete_source: bool = False


def decide(facts: FileFacts, policy: Policy = Policy()) -> Decision:
    """Return the import decision for ``facts`` under ``policy``.

    Reason priority is deterministic:
    1. empty file
    2. conflicting author source
    3. unknown author
    4. EOL / auto-import rules
    """
    _validate_facts(facts)
    _validate_policy(policy)

    if facts.size == 0:
        return Decision(SKIP, "empty")
    if facts.author_conflict:
        return Decision(CLARIFY, "author_conflict")
    if not facts.author_known:
        return Decision(CLARIFY, "author_unknown")

    if policy.eol_filter_enabled:
        if facts.eol_flag is True:
            return Decision(IMPORT, "eol_confirmed")
        if policy.auto_import_non_eol:
            if facts.stable:
                return Decision(IMPORT, "auto_import_stable")
            return Decision(SKIP, "not_stable")
        return Decision(SKIP, "not_eol")

    return Decision(IMPORT, "filter_off")


def _validate_facts(facts: FileFacts) -> None:
    if not isinstance(facts, FileFacts):
        raise EolFilterError("facts must be a FileFacts instance")
    if not isinstance(facts.name, str) or facts.name == "":
        raise EolFilterError("name must be a non-empty string")
    if any(ord(ch) < 32 for ch in facts.name):
        raise EolFilterError("name must not contain control characters")
    if type(facts.size) is not int or facts.size < 0:
        raise EolFilterError("size must be a non-negative integer")
    if facts.eol_flag is not None and not isinstance(facts.eol_flag, bool):
        raise EolFilterError("eol_flag must be a bool or None")
    if not isinstance(facts.stable, bool):
        raise EolFilterError("stable must be a bool")
    if not isinstance(facts.author_known, bool):
        raise EolFilterError("author_known must be a bool")
    if not isinstance(facts.author_conflict, bool):
        raise EolFilterError("author_conflict must be a bool")


def _validate_policy(policy: Policy) -> None:
    if not isinstance(policy, Policy):
        raise EolFilterError("policy must be a Policy instance")
    if not isinstance(policy.eol_filter_enabled, bool):
        raise EolFilterError("eol_filter_enabled must be a bool")
    if not isinstance(policy.auto_import_non_eol, bool):
        raise EolFilterError("auto_import_non_eol must be a bool")
