"""Tests for the Slovak activity-line translator (CR-NS-018 live feed)."""

from backend.services.pipeline_activity import activity_line


def _assistant(*blocks) -> dict:
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _tool(name, **inp) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


def _text(t) -> dict:
    return {"type": "text", "text": t}


def test_read_tool():
    line, kind = activity_line(_assistant(_tool("Read", file_path="docs/specs/BEHAVIOR.md")))
    assert line == "číta specs/BEHAVIOR.md"
    assert kind == "tool"


def test_write_and_edit_tools():
    assert activity_line(_assistant(_tool("Write", file_path="a/b.py")))[0] == "píše a/b.py"
    assert activity_line(_assistant(_tool("Edit", file_path="a/b.py")))[0] == "upravuje a/b.py"


def test_bash_truncates():
    # The command preview is clipped to _MAX_CMD (200) chars — loosened from the original 60 so the live feed
    # shows the meaningful head of a command (live-activity-truncation fix). Use a >200-char command so the
    # truncation actually bites.
    long = "x" * 300
    line, kind = activity_line(_assistant(_tool("Bash", command=long)))
    assert line.startswith("spúšťa: ")
    assert len(line) <= len("spúšťa: ") + 200
    assert kind == "tool"


def test_grep():
    assert activity_line(_assistant(_tool("Grep", pattern="def foo")))[0] == "hľadá def foo"


def test_text_fallback_when_no_tool():
    line, kind = activity_line(_assistant(_text("  Analyzujem   spec   balík  ")))
    assert line == "Analyzujem spec balík"
    assert kind == "text"


def test_tool_use_preferred_over_text():
    line, kind = activity_line(_assistant(_text("uvažujem"), _tool("Read", file_path="x.md")))
    assert kind == "tool"
    assert line == "číta x.md"


def test_noise_events_skipped():
    assert activity_line({"type": "system", "subtype": "init"}) == (None, "")
    assert activity_line({"type": "result", "result": "done"}) == (None, "")
    assert activity_line({"type": "user", "message": {"content": []}}) == (None, "")
    assert activity_line({"type": "assistant", "message": {"content": [_text("   ")]}}) == (None, "")
