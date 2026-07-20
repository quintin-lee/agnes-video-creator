"""Tests for JSON repair logic that handles malformed LLM output."""

from __future__ import annotations

import json

from agnes_video_creator.script_generator import _repair_json


class TestRepairJson:
    """_repair_json() handles literal newlines/tabs inside JSON strings."""

    def test_already_valid_json_unchanged(self) -> None:
        valid = '{"title": "Hello", "scenes": []}'
        repaired = _repair_json(valid)
        assert repaired == valid
        # Must still parse
        json.loads(repaired)

    def test_literal_newlines_in_string(self) -> None:
        bad = '{"desc": "Line1\nLine2\nLine3", "ok": true}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["desc"] == "Line1\nLine2\nLine3"
        assert parsed["ok"] is True

    def test_literal_tab_in_string(self) -> None:
        bad = '{"text": "col1\tcol2\tcol3"}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["text"] == "col1\tcol2\tcol3"

    def test_mixed_newlines_inside_and_outside(self) -> None:
        mixed = '{"a": "hello\nworld"}\n'
        repaired = _repair_json(mixed)
        parsed = json.loads(repaired)
        assert parsed["a"] == "hello\nworld"

    def test_escaped_backslashes_preserved(self) -> None:
        bad = '{"path": "C:\\\\Users\\\\test"}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["path"] == "C:\\Users\\test"

    def test_escaped_quotes_preserved(self) -> None:
        bad = '{"msg": "He said \\"hi\\""}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["msg"] == 'He said "hi"'

    def test_nested_objects_with_newlines(self) -> None:
        bad = '{"outer": {"inner": "multi\nline"}}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["outer"]["inner"] == "multi\nline"

    def test_array_with_newlines(self) -> None:
        bad = '{"items": ["a\nb", "c\nd"]}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["items"] == ["a\nb", "c\nd"]

    def test_real_world_script_output(self) -> None:
        """Simulate the kind of output that caused the original bug."""
        bad = """{
  "title": "测试视频",
  "description": "一段描述",
  "total_duration": 15.0,
  "scenes": [
    {
      "id": 1,
      "narration": "这是一个开场旁白
它换行了",
      "visual_prompt": "A cinematic shot of a character walking",
      "duration_seconds": 5.0
    }
  ]
}"""
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["title"] == "测试视频"
        assert parsed["scenes"][0]["narration"] == "这是一个开场旁白\n它换行了"

    def test_multiple_strings_with_newlines(self) -> None:
        bad = '{"a": "x\ny", "b": "z\nw"}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["a"] == "x\ny"
        assert parsed["b"] == "z\nw"

    def test_carriage_return_in_string(self) -> None:
        bad = '{"text": "line1\r\nline2"}'
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
        assert parsed["text"] == "line1\r\nline2"
