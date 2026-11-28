"""Durable clipboard mapping for ``routes_clipboard.create_blueprint``.

Claims and outcomes use the existing append-only parity receipt table. The
caller commits before returning flavours, and commits outcomes together with
their local output receipt. Conflicts roll back the whole transaction, including
any output receipt a stale worker already appended. No text is stored here.

Keys already bind actor, request, dictation, channel and target in
``freeze_output_attempts``. A dictation-row write serializes competing writers
on PostgreSQL and SQLite; unresolved claims block new delivery for that dictation.
Terminal replays raise OutputAttemptError, matching the existing route contract.
Even SUCCEEDED means browser-reported copying, never recipient-side paste.
"""

from collections.abc import MutableMapping
from dataclasses import asdict, replace

from sqlalchemy import select, update

from src.clinic.models import ClinicDictation
from src.clinic.output import CHANNEL_CLIPBOARD
from src.clinic.output_attempts import (
    FAILED, SUCCEEDED, UNKNOWN, OutputAttempt, OutputAttemptError, OutputSource,
)
from src.clinic.parity_receipts import ClinicParityReceipt, scope_key
from src.database import db


class AuditClipboardLedger(MutableMapping[str, OutputAttempt]):
    """Stateless mapping: each access uses the current Flask database session.

    Instantiate once for blueprint wiring. No schema change or process-local
    cache is needed. Reads expose only clipboard entries, never other receipts.
    """

    def _entries(self, dictation_id=None, key=None):
        query = select(ClinicParityReceipt.result).where(
            ClinicParityReceipt.result['kind'].as_string() == 'clipboard_attempt')
        if dictation_id is not None:
            query = query.where(
                ClinicParityReceipt.result['dictation_id'].as_string() == dictation_id)
        if key is not None:
            query = query.where(
                ClinicParityReceipt.result['attempt']['idempotency_key'].as_string() == key)
        entries = {}
        for result in db.session.execute(query).scalars():
            data = dict(result['attempt'])
            data['source'] = OutputSource(**data['source'])
            data['format_settings'] = tuple(tuple(item) for item in data['format_settings'])
            attempt = OutputAttempt(**data)
            previous = entries.get(attempt.idempotency_key)
            rank = lambda row: (row.generation, row.status != UNKNOWN)
            if previous is None or rank(attempt) > rank(previous):
                entries[attempt.idempotency_key] = attempt
        return entries

    def attempts_for(self, dictation_id: str) -> dict[str, OutputAttempt]:
        return self._entries(dictation_id=dictation_id)

    def __getitem__(self, key):
        entries = self._entries(key=key)
        # Optimistic read token, scoped to this DB session, not authoritative state.
        transaction = db.session().get_transaction()
        snapshot = db.session.info.get('clipboard_ledger_reads')
        if snapshot is None or snapshot[0] is not transaction:
            snapshot = (transaction, {})
            db.session.info['clipboard_ledger_reads'] = snapshot
        snapshot[1].setdefault(key, entries.get(key))
        return entries[key]

    def __iter__(self):
        return iter(self._entries())

    def __len__(self):
        return len(self._entries())

    def __delitem__(self, key):
        raise OutputAttemptError('clipboard evidence is append-only')

    def __setitem__(self, key, value):
        try:
            if (not isinstance(value, OutputAttempt) or key != value.idempotency_key
                    or value.channel != CHANNEL_CLIPBOARD or value.target != 'browser'):
                raise OutputAttemptError('invalid clipboard claim')
            dictation_id = value.source.dictation_id
            # Leave keys untouched: concurrent output receipts hold FK key-share locks.
            locked = db.session.execute(
                update(ClinicDictation).where(ClinicDictation.id == dictation_id)
                .values(version_counter=ClinicDictation.version_counter)
                .execution_options(synchronize_session=False))
            if locked.rowcount != 1:
                raise OutputAttemptError('clipboard dictation does not exist')
            entries = self._entries(dictation_id)
            old = self._entries(key=key).get(key)
            snapshot = db.session.info.get('clipboard_ledger_reads')
            observed = (snapshot[1] if snapshot is not None
                        and snapshot[0] is db.session().get_transaction() else {})
            if key not in observed or observed[key] != old:
                raise OutputAttemptError('clipboard claim changed since it was read')
            if old is None or old.status == FAILED:
                generation = 1 if old is None else old.generation + 1
                if (value.status != UNKNOWN or value.generation != generation or value.cancelled
                        or any(row.status == UNKNOWN for row in entries.values())
                        or (old is not None and replace(old, status=UNKNOWN,
                                                       generation=generation) != value)):
                    raise OutputAttemptError('clipboard claim conflicts with stored evidence')
            elif (old.status != UNKNOWN or value.status not in (UNKNOWN, SUCCEEDED, FAILED)
                  or replace(old, status=value.status) != value):
                raise OutputAttemptError('clipboard outcome conflicts with stored evidence')
            elif value == old:
                return  # An aborted attempt stays unresolved; it creates no success evidence.
            db.session.add(ClinicParityReceipt(
                scope_key=scope_key((0, 'clipboard-ledger', dictation_id,
                                     f'{key}:{value.generation}:{value.status}')),
                digest=value.payload_hash,
                result={'kind': 'clipboard_attempt', 'dictation_id': dictation_id,
                        'attempt': asdict(value), 'recipient_status': UNKNOWN},
            ))
            db.session.flush()
        except BaseException:
            db.session.rollback()
            raise
