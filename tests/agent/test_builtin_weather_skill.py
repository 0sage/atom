from atom.agent.skills import BUILTIN_SKILLS_DIR


def test_weather_skill_uses_single_today_request() -> None:
    content = (BUILTIN_SKILLS_DIR / "weather" / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(content.split())

    assert "https://wttr.in/London?1&m" in content
    assert 'curl -s "https://wttr.in/Berlin.png" -o weather.png' in content
    assert "/tmp/weather.png" not in content
    assert (
        "Do not fetch current conditions separately when a today or forecast "
        "request already includes them."
    ) in normalized
