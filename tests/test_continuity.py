"""Tests for continuity module — CharacterContinuity, VisualRegistry, ContinuityState."""

from __future__ import annotations

from agnes_video_creator.continuity import CharacterContinuity, ContinuityState, VisualRegistry


class TestCharacterContinuity:
    def test_defaults(self) -> None:
        cc = CharacterContinuity()
        assert cc.outfit == ""
        assert cc.location == ""
        assert cc.emotional_state == ""
        assert cc.notes == ""

    def test_is_populated_false_by_default(self) -> None:
        assert not CharacterContinuity().is_populated()

    def test_is_populated_with_outfit(self) -> None:
        assert CharacterContinuity(outfit="red dress").is_populated()

    def test_is_populated_with_location(self) -> None:
        assert CharacterContinuity(location="garden").is_populated()

    def test_is_populated_with_emotional_state(self) -> None:
        assert CharacterContinuity(emotional_state="angry").is_populated()

    def test_to_prompt_snippet_empty(self) -> None:
        assert CharacterContinuity().to_prompt_snippet() == ""

    def test_to_prompt_snippet_full(self) -> None:
        cc = CharacterContinuity(
            outfit="armor",
            location="battlefield",
            emotional_state="determined",
            notes="wounded left arm",
        )
        snippet = cc.to_prompt_snippet()
        assert "armor" in snippet
        assert "battlefield" in snippet
        assert "determined" in snippet
        assert "wounded" in snippet


class TestVisualRegistry:
    def test_is_populated_false_by_default(self) -> None:
        assert not VisualRegistry().is_populated()

    def test_is_populated_with_environment(self) -> None:
        vr = VisualRegistry(environments={"garden": "cherry blossoms"})
        assert vr.is_populated()

    def test_is_populated_with_prop(self) -> None:
        vr = VisualRegistry(props={"sword": "golden blade"})
        assert vr.is_populated()

    def test_is_populated_with_outfit(self) -> None:
        vr = VisualRegistry(outfits={"hero": "red cloak"})
        assert vr.is_populated()

    def test_to_prompt_snippet_empty(self) -> None:
        assert VisualRegistry().to_prompt_snippet() == ""

    def test_to_prompt_snippet_all_sections(self) -> None:
        vr = VisualRegistry(
            environments={"garden": "spring park"},
            props={"sword": "iron sword"},
            outfits={"hero": "leather armor"},
        )
        snippet = vr.to_prompt_snippet()
        assert "场景场所" in snippet or "garden" in snippet
        assert "道具物品" in snippet
        assert "角色服装" in snippet


class TestContinuityState:
    def test_defaults(self) -> None:
        cs = ContinuityState()
        assert cs.episode == 0
        assert cs.characters == {}
        assert cs.plot_threads == []
        assert cs.prev_summary == ""

    def test_ensure_character_creates_new(self) -> None:
        cs = ContinuityState()
        cc = cs.ensure_character("林黛玉")
        assert isinstance(cc, CharacterContinuity)
        assert "林黛玉" in cs.characters

    def test_ensure_character_reuses_existing(self) -> None:
        cs = ContinuityState()
        cc1 = cs.ensure_character("test")
        cc2 = cs.ensure_character("test")
        assert cc1 is cc2

    def test_to_prompt_snippet_empty(self) -> None:
        cs = ContinuityState()
        assert cs.to_prompt_snippet() == ""

    def test_to_prompt_snippet_with_summary(self) -> None:
        cs = ContinuityState(prev_summary="林黛玉进了大观园")
        snippet = cs.to_prompt_snippet()
        assert "前情提要" in snippet or "林黛玉" in snippet

    def test_to_prompt_snippet_with_plot_threads(self) -> None:
        cs = ContinuityState(plot_threads=["宝玉摔玉"])
        snippet = cs.to_prompt_snippet()
        assert "宝玉摔玉" in snippet

    def test_to_prompt_snippet_with_character(self) -> None:
        cs = ContinuityState()
        cs.ensure_character("林黛玉").emotional_state = "sad"
        snippet = cs.to_prompt_snippet()
        assert "林黛玉" in snippet
        assert "sad" in snippet

    def test_to_dict_roundtrip(self) -> None:
        cs = ContinuityState(episode=3)
        cs.ensure_character("林黛玉").outfit = "汉服"
        cs.visual.environments["garden"] = "大观园"
        cs.plot_threads.append("宝玉摔玉")
        cs.prev_summary = "前情概要"

        d = cs.to_dict()
        restored = ContinuityState.from_dict(d)
        assert restored.episode == 3
        assert restored.characters["林黛玉"].outfit == "汉服"
        assert restored.visual.environments["garden"] == "大观园"
        assert "宝玉摔玉" in restored.plot_threads
        assert restored.prev_summary == "前情概要"

    def test_apply_visual_updates_summary(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"summary": "new summary"})
        assert cs.prev_summary == "new summary"

    def test_apply_visual_updates_environment(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"environment:garden": "cherry blossom garden"})
        assert cs.visual.environments["garden"] == "cherry blossom garden"

    def test_apply_visual_updates_env_short(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"env:room": "dimly lit chamber"})
        assert cs.visual.environments["room"] == "dimly lit chamber"

    def test_apply_visual_updates_outfit(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"outfit:林黛玉": "pink hanfu"})
        assert cs.visual.outfits["林黛玉"] == "pink hanfu"

    def test_apply_visual_updates_prop(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"prop:sword": "golden blade"})
        assert cs.visual.props["sword"] == "golden blade"

    def test_apply_visual_updates_plot(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"plot:marriage": "宝玉娶亲"})
        assert "marriage: 宝玉娶亲" in cs.plot_threads

    def test_apply_visual_updates_plot_duplicate_not_blocked(self) -> None:
        """The dedup check compares thread key against full plot strings.

        Since self.plot_threads contains \"marriage: 宝玉娶亲\" and the
        check is ``\"marriage\" not in self.plot_threads``, a duplicate with
        the same key but different value is appended.  This is a known
        limitation of the simple prefix check.
        """
        cs = ContinuityState()
        cs.apply_visual_updates({"plot:marriage": "宝玉娶亲"})
        cs.apply_visual_updates({"plot:marriage": "different description"})
        assert len(cs.plot_threads) == 2

    def test_apply_visual_updates_emotion(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"emotion:林黛玉": "heartbroken"})
        assert cs.characters["林黛玉"].emotional_state == "heartbroken"

    def test_apply_visual_updates_location(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"location:林黛玉": "xiaoxiang"})
        assert cs.characters["林黛玉"].location == "xiaoxiang"

    def test_apply_visual_updates_notes(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"notes:林黛玉": "coughing blood"})
        assert cs.characters["林黛玉"].notes == "coughing blood"

    def test_apply_visual_updates_unknown_falls_through(self) -> None:
        cs = ContinuityState()
        cs.apply_visual_updates({"unknown:key": "val"})
        # unknown keys with colon go to environments as a fallback
        assert cs.visual.environments.get("unknown:key") == "val"

    def test_to_prompt_snippet_character_continuity_empty_not_shown(self) -> None:
        """Characters with no populated continuity should not appear in the snippet."""
        cs = ContinuityState()
        cs.ensure_character("新角色")  # no fields set
        snippet = cs.to_prompt_snippet()
        assert "新角色" not in snippet
