import json
from pathlib import Path


def test_persona_panel_personas_have_required_fields():
    personas = Path(__file__).resolve().parents[2] / "scripts/persona_panel/personas"
    paths = sorted(personas.glob("*.agent.json"))
    assert len(paths) == 12

    required = {
        "name": str, "age": int, "gender": str, "nationality": str,
        "residence": str, "education": str, "long_term_goals": list,
        "occupation": dict, "style": str, "personality": dict, "preferences": dict,
        "skills": list, "beliefs": list, "behaviors": dict, "health": str,
        "relationships": list,
    }
    for path in paths:
        specification = json.loads(path.read_text(encoding="utf-8"))
        assert specification.get("type") == "TinyPerson", path.name
        profile = specification.get("persona")
        assert isinstance(profile, dict), path.name
        for field, field_type in required.items():
            assert type(profile.get(field)) is field_type, f"{path.name}: {field}"
        assert profile["name"].strip() and profile["occupation"], path.name
