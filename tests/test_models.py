"""Tests for data model classes — Script, Scene, Character, FaceFeatures."""

from __future__ import annotations

from pathlib import Path

from agnes_video_creator.models import Character, FaceFeatures, Scene, Script


class TestScene:
    """Scene dataclass — construction, properties, serialization."""

    def test_default_values(self) -> None:
        scene = Scene(id=1, narration="test", visual_prompt="a cat")
        assert scene.duration_seconds == 5.0
        assert scene.camera == "static"
        assert scene.style == "cinematic"
        assert scene.image_url == ""
        assert scene.video_path == ""
        assert scene.character_appearances == []
        assert scene.dialogues == []
        assert scene.trim_in == 0.0
        assert scene.trim_out == 0.0

    def test_is_image_ready_false_by_default(self) -> None:
        scene = Scene(id=1, narration="test", visual_prompt="a dog")
        assert not scene.is_image_ready

    def test_is_image_ready_with_url(self) -> None:
        scene = Scene(id=1, narration="test", visual_prompt="a dog")
        scene.image_url = "http://example.com/img.jpg"
        assert scene.is_image_ready

    def test_is_image_ready_with_path(self, tmp_path: Path) -> None:
        img = tmp_path / "img.jpg"
        img.write_text("fake")
        scene = Scene(id=1, narration="test", visual_prompt="a dog")
        scene.image_path = str(img)
        assert scene.is_image_ready

    def test_is_video_ready_false_by_default(self) -> None:
        scene = Scene(id=1, narration="test", visual_prompt="a dog")
        assert not scene.is_video_ready

    def test_is_video_ready_with_url(self) -> None:
        scene = Scene(id=1, narration="test", visual_prompt="a dog")
        scene.video_url = "http://example.com/vid.mp4"
        assert scene.is_video_ready

    def test_to_dict_roundtrip(self) -> None:
        original = Scene(
            id=3,
            narration="旁白",
            visual_prompt="a cat on a mat",
            duration_seconds=6.0,
            camera="推近",
            style="noir",
            character_appearances=["猫"],
            dialogues=[{"character": "猫", "line": "喵"}],
            sfx="cat meow",
            trim_in=0.5,
            trim_out=1.0,
        )
        d = original.to_dict()
        restored = Scene.from_dict(d)
        assert restored.id == 3
        assert restored.narration == "旁白"
        assert restored.duration_seconds == 6.0
        assert restored.camera == "推近"
        assert restored.character_appearances == ["猫"]
        assert restored.dialogues == [{"character": "猫", "line": "喵"}]
        assert restored.sfx == "cat meow"
        assert restored.trim_in == 0.5
        assert restored.trim_out == 1.0

    def test_from_dict_ignores_unknown_keys(self) -> None:
        d = {"id": 1, "narration": "hi", "visual_prompt": "img", "invalid_key": True}
        scene = Scene.from_dict(d)
        assert scene.id == 1


class TestCharacter:
    """Character dataclass — defaults and construction."""

    def test_default_values(self) -> None:
        c = Character(name="测试角色")
        assert c.appearance == ""
        assert c.voice == ""
        assert c.role == ""
        assert c.personality == ""
        assert c.age == ""
        assert c.sample_dialogue == ""
        assert c.backstory == ""
        assert c.portrait_path == ""
        assert c.portrait_url == ""
        assert c.seed == 0
        assert c.face_features is None

    def test_full_construction(self) -> None:
        c = Character(
            name="林黛玉",
            appearance="a delicate young woman in traditional dress",
            voice="zh-CN-XiaoxiaoNeural",
            role="主角",
            personality="多愁善感",
            age="teen",
            sample_dialogue="花谢花飞花满天",
            backstory="苏州人氏，自幼丧母",
            seed=42,
        )
        assert c.name == "林黛玉"
        assert c.seed == 42
        assert c.voice == "zh-CN-XiaoxiaoNeural"


class TestFaceFeatures:
    """FaceFeatures — prompt generation and population checks."""

    def test_empty_is_not_populated(self) -> None:
        ff = FaceFeatures()
        assert not ff.is_populated()

    def test_populated_with_face_shape(self) -> None:
        ff = FaceFeatures(face_shape="oval")
        assert ff.is_populated()

    def test_populated_with_skin_tone(self) -> None:
        ff = FaceFeatures(skin_tone="fair")
        assert ff.is_populated()

    def test_to_prompt_snippet_empty(self) -> None:
        ff = FaceFeatures()
        assert ff.to_prompt_snippet() == ""

    def test_to_prompt_snippet_full(self) -> None:
        ff = FaceFeatures(
            face_shape="oval",
            eye_shape="almond",
            eye_color="dark brown",
            nose="straight",
            mouth_lips="full",
            skin_tone="fair",
            skin_texture="smooth",
            hair_style="long",
            hair_color="black",
            age_range="young adult",
        )
        snippet = ff.to_prompt_snippet()
        assert "Face:" in snippet
        assert "oval" in snippet
        assert "S:" in snippet
        assert "Hair:" in snippet
        assert "Age:" in snippet

    def test_to_prompt_snippet_includes_distinctive_features(self) -> None:
        ff = FaceFeatures(
            distinctive_features=["scar on left cheek", "glasses"],
        )
        snippet = ff.to_prompt_snippet()
        assert "scar" in snippet
        assert "glasses" in snippet


class TestScript:
    """Script dataclass — serialization, save/load, helpers."""

    def test_default_scenes_empty(self) -> None:
        script = Script(title="t", description="d", total_duration=10.0)
        assert script.scenes == []
        assert script.characters == []

    def test_to_dict_keys(self, sample_script: Script) -> None:
        d = sample_script.to_dict()
        assert "title" in d
        assert "scenes" in d
        assert "characters" in d
        assert "episode" in d
        assert "visual_updates" in d
        assert d["title"] == "测试视频"

    def test_to_dict_scene_count(self, sample_script: Script) -> None:
        d = sample_script.to_dict()
        assert len(d["scenes"]) == 2

    def test_to_dict_character_count(self, sample_script: Script) -> None:
        d = sample_script.to_dict()
        assert len(d["characters"]) == 2

    def test_save_and_load(self, tmp_path: Path, sample_script: Script) -> None:
        path = tmp_path / "script.json"
        sample_script.save(path)
        assert path.exists()
        loaded = Script.load(path)
        assert loaded.title == sample_script.title
        assert len(loaded.scenes) == len(sample_script.scenes)
        assert loaded.characters[0].name == "红狐"

    def test_inject_characters_no_match(self, sample_script: Script) -> None:
        prompt = "a beautiful landscape"
        result = sample_script.inject_characters(prompt, ["未知角色"])
        assert result == prompt

    def test_inject_characters_with_match(self, sample_script: Script) -> None:
        prompt = "running through forest"
        result = sample_script.inject_characters(prompt, ["红狐"])
        assert "红狐" in result
        assert "red fox" in result
        assert prompt in result

    def test_inject_characters_empty_list(self, sample_script: Script) -> None:
        prompt = "a scene"
        result = sample_script.inject_characters(prompt, [])
        assert result == prompt

    def test_generate_system_prompt_contains_keywords(self) -> None:
        prompt = Script.generate_system_prompt()
        assert "json.loads()" in prompt
        assert "total_duration" in prompt
        assert "visual_prompt" in prompt

    def test_generate_system_prompt_with_characters(self) -> None:
        prompt = Script.generate_system_prompt(character_info="林黛玉: 女主角")
        assert "character_appearances" in prompt
        assert "dialogues" in prompt

    def test_generate_system_prompt_with_continuity(self) -> None:
        prompt = Script.generate_system_prompt(need_continuity_updates=True)
        assert "visual_updates" in prompt
        assert "Previous episode continuity" in prompt

    def test_visual_updates_roundtrip(self) -> None:
        script = Script(
            title="t", description="d", total_duration=10.0,
            visual_updates={"environment:花园": "spring garden with cherry blossoms"},
        )
        d = script.to_dict()
        assert d["visual_updates"]["environment:花园"] == "spring garden with cherry blossoms"
