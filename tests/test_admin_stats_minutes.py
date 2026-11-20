"""Exact tenth-minute rounding of the reported daily and model totals."""
from datetime import date
from decimal import Decimal

import pytest

from test_admin_stats_overview import app, db, world
from src.models import TranscriptionUsage
from src.services import admin_stats


@pytest.mark.parametrize("seconds, minutes", [
    (441, 7.4), (1641, 27.4), (3, 0.0), (9, 0.2),
    (440.99, 7.3), (Decimal("441.01"), 7.4),
])
def test_tenth_minutes_rounds_to_nearest_even(seconds, minutes):
    assert admin_stats._minutes(seconds) == minutes


def test_model_rounds_once_across_connectors_with_the_same_display_name(world):
    # Both rows have the same displayed model, including the connector-name fallback.
    day = date(1998, 8, 27)
    with app.app_context():
        for connector, model in (("shared", None), ("other", "shared")):
            db.session.add(TranscriptionUsage(user_id=world["admin"], date=day, connector_type=connector,
                                             model_name=model, audio_duration_seconds=4, request_count=1))
        db.session.commit()
        usage = admin_stats.build_overview(7, day)["usage"]
        daily = next(d for d in usage["daily"] if d["date"] == day.isoformat())
        model = next(m for m in usage["by_model"] if m["kind"] == "transcription" and m["model"] == "shared")
        assert model["requests"] == 2
        assert model["minutes"] == daily["minutes"] == 0.1
