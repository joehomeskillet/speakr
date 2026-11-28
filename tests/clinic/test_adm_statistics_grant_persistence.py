"""Durable statistics access across independent database connections."""

from datetime import datetime, timezone

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.clinic import statistics_grants
from src.clinic.models import ClinicDictation
from src.clinic.policy import RecordingFacts
from src.clinic.statistics import StatisticsQuery
from src.clinic.statistics_access import (
    StatisticsAccessError,
    grant_aggregate,
    read_aggregates,
    recording_access,
    revoke_aggregate,
    version_of,
)
from src.database import db
from src.models import Recording, User


def test_grant_survives_reconnect_and_revocation_applies_to_next_read(tmp_path, clinic_on):
    uri = f"sqlite:///{tmp_path / 'statistics.db'}"
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(app)
    with app.app_context():
        db.create_all()
        stats, author, other = [
            User(username=name, email=f"{name}@example.invalid", password="x")
            for name in ("stats", "author", "other")
        ]
        db.session.add_all([stats, author, other])
        db.session.flush()
        for user, seconds in ((author, 10), (other, 99)):
            recording = Recording(user_id=user.id, title="d", status="PENDING", audio_duration_seconds=seconds)
            db.session.add(recording)
            db.session.flush()
            db.session.add(ClinicDictation(
                recording_id=recording.id, author_user_id=user.id,
                created_at=datetime(2026, 3, 2, 8, tzinfo=timezone.utc),
            ))
            if user is author:
                facts = RecordingFacts(recording.id, author.id)
        stats_id, author_id = stats.id, author.id
        db.session.commit()
        grant = grant_aggregate(stats_id, "author", author_ids=[author_id], expected_version=0)
        query = StatisticsQuery(group_by=("author",))
        original = read_aggregates(db.session(), stats_id, query, role="statistics").buckets
        assert [(b.author_user_id, b.count, b.total_seconds) for b in original] == [(author_id, 1, 10.0)]
        db.session.remove()
        db.engine.dispose()

    reader = create_engine(uri)
    writer = create_engine(uri)
    try:
        with Session(reader) as session:
            assert statistics_grants.grants_for(stats_id, session=session) == (grant,)
            assert version_of(stats_id, session=session) == grant.version == 1
            assert read_aggregates(session, stats_id, query, role="statistics", expected_version=1).buckets == original
            for action in ("view", "play", "download", "edit", "export"):
                assert not recording_access(stats_id, action, facts).allowed

        with Session(writer) as session:
            assert revoke_aggregate(stats_id, grant.id, expected_version=1, session=session) == 2

        with Session(reader) as session:
            assert version_of(stats_id, session=session) == 2
            for expected, code in ((1, "stale_version"), (2, "no_grant"), (None, "no_grant")):
                with pytest.raises(StatisticsAccessError) as caught:
                    read_aggregates(session, stats_id, query, role="statistics", expected_version=expected)
                assert caught.value.code == code
    finally:
        reader.dispose()
        writer.dispose()
