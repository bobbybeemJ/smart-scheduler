"""Tests app.persistence in isolation, using pytest's tmp_path fixture so it never touches the
real data/usual_meeting_defaults.json file - keeps this fully isolated from any real runtime
state (or other tests running concurrently)."""

from app.persistence import load_usual_meeting_defaults, save_usual_meeting_defaults


def test_load_returns_empty_dict_when_file_does_not_exist(tmp_path):
    result = load_usual_meeting_defaults(tmp_path / "does_not_exist.json")
    assert result == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "defaults.json"
    save_usual_meeting_defaults({"usual sync-up": 30, "usual 1:1": 45}, path)

    result = load_usual_meeting_defaults(path)

    assert result == {"usual sync-up": 30, "usual 1:1": 45}


def test_load_returns_empty_dict_on_corrupt_json(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")

    result = load_usual_meeting_defaults(path)

    assert result == {}


def test_save_creates_parent_directory_if_missing(tmp_path):
    path = tmp_path / "nested" / "dir" / "defaults.json"
    save_usual_meeting_defaults({"x": 1}, path)
    assert path.exists()
    assert load_usual_meeting_defaults(path) == {"x": 1}
