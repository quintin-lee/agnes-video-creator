"""Tests for JSON repair and script JSON parsing — _repair_json, _parse_script_json."""

from __future__ import annotations

import json

from agnes_video_creator.script_generator import _parse_script_json, _repair_json


class TestRepairJson:
    """_repair_json() handles literal newlines/tabs inside JSON strings."""

    def test_already_valid_json_unchanged(self) -> None:
        valid = '{"title": "Hello", "scenes": []}'
        repaired = _repair_json(valid)
        assert repaired == valid
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
        bad = """{
  "title": "测试视频",
  "description": "一段描述",
  "total_duration": 15.0,
  "scenes": [
    {
      "id": 1,
      "narration": "这是一个开场旁白
它换行了",
      "visual_prompt": "A cinematic shot",
      "duration_seconds": 5.0
    }
  ]
}"""
        repaired = _repair_json(bad)
        parsed = json.loads(repaired)
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


class TestParseScriptJson:
    """_parse_script_json() — fence stripping, repair, brace fallback."""

    def test_valid_json(self) -> None:
        raw = '{"title": "Test", "scenes": [{"id": 1, "visual_prompt": "a cat", "duration_seconds": 5.0}]}'  # noqa: E501
        script = _parse_script_json(raw, "fallback")
        assert script.title == "Test"
        assert len(script.scenes) == 1

    def test_markdown_fence_json(self) -> None:
        raw = """```json
{"title": "Fenced", "scenes": [{"id": 1, "visual_prompt": "a dog", "duration_seconds": 5.0}]}
```"""
        script = _parse_script_json(raw, "fallback")
        assert script.title == "Fenced"
        assert len(script.scenes) == 1

    def test_markdown_fence_no_lang(self) -> None:
        raw = """```
{"title": "NoLang", "scenes": [{"id": 1, "visual_prompt": "a bird", "duration_seconds": 5.0}]}
```"""
        script = _parse_script_json(raw, "fallback")
        assert script.title == "NoLang"

    def test_repair_literal_newlines(self) -> None:
        raw = '{"title": "Repaired", "scenes": [{"id": 1, "narration": "line1\nline2", "visual_prompt": "a fox", "duration_seconds": 5.0}]}'  # noqa: E501
        script = _parse_script_json(raw, "fallback")
        assert script.title == "Repaired"
        assert script.scenes[0].narration == "line1\nline2"

    def test_brace_fallback_extra_text(self) -> None:
        raw = 'Some text before {"title": "Braces", "scenes": []} and some after'
        script = _parse_script_json(raw, "fallback")
        assert script.title == "Braces"

    def test_brace_fallback_with_newlines(self) -> None:
        raw = """前言
{
  "title": "BraceRepair",
  "description": "hello\nworld",
  "scenes": []
}
后语"""
        script = _parse_script_json(raw, "fallback")
        assert script.title == "BraceRepair"
        assert script.description == "hello\nworld"

    def test_fallback_title_used(self) -> None:
        raw = '{"title": "", "scenes": []}'
        script = _parse_script_json(raw, "我的回退标题")
        assert script.title == "我的回退标题"

    def test_scene_defaults_applied(self) -> None:
        raw = '{"title": "Defaults", "scenes": [{"id": 1, "visual_prompt": "a fish"}]}'
        script = _parse_script_json(raw, "fallback")
        assert len(script.scenes) == 1
        assert script.scenes[0].duration_seconds == 5.0
        assert script.scenes[0].camera == "static"

    def test_output_dir_not_set_by_parser(self) -> None:
        raw = '{"title": "NoDir", "scenes": []}'
        script = _parse_script_json(raw, "fallback")
        assert script.output_dir == ""

    def test_multiple_scenes(self) -> None:
        raw = """{
  "title": "Multi",
  "scenes": [
    {"id": 1, "visual_prompt": "first", "duration_seconds": 5.0},
    {"id": 2, "visual_prompt": "second", "duration_seconds": 6.0}
  ]
}"""
        script = _parse_script_json(raw, "fallback")
        assert len(script.scenes) == 2
        assert script.scenes[0].visual_prompt == "first"
        assert script.scenes[1].duration_seconds == 6.0
