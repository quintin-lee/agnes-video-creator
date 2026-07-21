"""Tests for novel processing — chunking, analysis, dialogue extraction, episode scripts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agnes_video_creator.novel import (
    NovelChunk,
    _CHAPTER_RE,
    _infer_chunk_title,
    _split_by_chapters,
    analyze_novel,
    chunk_novel,
    extract_dialogues,
    generate_episode_script,
    novel_to_episodes,
)
from agnes_video_creator.models import Character


EN_NOVEL = """# Chapter 1

The sun rose over the mountains. John walked through the forest path, his boots crunching on fallen leaves.

"The air is fresh today," he said to himself. "I should come here more often."

A bird chirped in the distance. John smiled and continued walking.

# Chapter 2

The village market was bustling with activity. Merchants called out their wares to passersby.

"Fresh bread! Get your fresh bread here!" shouted the baker.

John purchased a loaf and continued his journey south.
"""

CHARACTERS = [
    Character(name="John", role="protagonist", appearance="A traveler"),
    Character(name="Baker", role="supporting", appearance="Market baker"),
]


class TestNovelChunk:
    def test_default_fields(self) -> None:
        c = NovelChunk(index=0, text="Hello")
        assert c.index == 0
        assert c.text == "Hello"
        assert c.title == ""
        assert c.summary == ""
        assert c.characters == []

    def test_all_fields(self) -> None:
        c = NovelChunk(index=1, text="Content", title="Chapter 1", summary="A summary", characters=["John"])
        assert c.title == "Chapter 1"
        assert c.summary == "A summary"
        assert c.characters == ["John"]


class TestChapterRE:
    def test_matches_chinese_chapter(self) -> None:
        assert _CHAPTER_RE.search("第一章")

    def test_matches_chinese_chapter_number(self) -> None:
        assert _CHAPTER_RE.search("第1章")

    def test_matches_english_chapter(self) -> None:
        assert _CHAPTER_RE.search("Chapter 5")

    def test_matches_markdown_heading(self) -> None:
        assert _CHAPTER_RE.search("### My Heading")

    def test_no_match_plain_text(self) -> None:
        assert _CHAPTER_RE.search("Plain text") is None


class TestSplitByChapters:
    def test_splits_chapters(self) -> None:
        chunks = _split_by_chapters(EN_NOVEL)
        assert len(chunks) >= 2
        assert chunks[0][0] == "# Chapter"

    def test_no_chapters_returns_single(self) -> None:
        chunks = _split_by_chapters("Plain text without chapters.")
        assert len(chunks) == 1
        assert chunks[0][0] == ""


class TestChunkNovel:
    def test_chunk_by_chapter(self) -> None:
        chunks = chunk_novel(EN_NOVEL, verbose=False)
        assert len(chunks) >= 1
        assert "sun" in chunks[0].text

    def test_chunk_content_field(self) -> None:
        chunks = chunk_novel(EN_NOVEL, verbose=False)
        assert "sun" in chunks[0].text

    def test_chunk_empty(self) -> None:
        assert chunk_novel("", verbose=False) == []

    def test_chunk_whitespace(self) -> None:
        chunks = chunk_novel("   \n  \n  ", verbose=False)
        assert len(chunks) == 1

    def test_chunk_sets_index(self) -> None:
        chunks = chunk_novel(EN_NOVEL, verbose=False)
        assert chunks[0].index == 0

    def test_chunk_no_marker(self) -> None:
        text = "Once upon a time.\nThe end."
        chunks = chunk_novel(text, verbose=False)
        assert len(chunks) >= 1


class TestInferChunkTitle:
    def test_uses_chapter_heading(self) -> None:
        title = _infer_chunk_title("# Chapter 1\nContent", [("# Chapter 1", "Content")])
        assert title == "# Chapter 1"

    def test_fallback_to_first_line(self) -> None:
        title = _infer_chunk_title("First meaningful line\n\nmore text", [])
        assert title == "First meaningful line"

    def test_empty_fallback(self) -> None:
        assert _infer_chunk_title("", []) == ""


class TestExtractDialogues:
    def test_extract_dialogues(self) -> None:
        cn_text = '张三笑道："你好世界"'
        chars = [Character(name="张三", role="protagonist")]
        dialogues = extract_dialogues(cn_text, chars)
        assert len(dialogues) >= 1
        assert all("character" in d and "line" in d for d in dialogues)

    def test_extract_no_known_characters(self) -> None:
        dialogues = extract_dialogues(EN_NOVEL, [])
        assert dialogues == []

    def test_extract_no_dialogues(self) -> None:
        dialogues = extract_dialogues("Narrative only.", CHARACTERS)
        assert dialogues == []


class TestAnalyzeNovel:
    def test_analyze_basic(self) -> None:
        from agnes_video_creator.config import AgnesConfig

        cfg = AgnesConfig(api_key="test-key")
        with patch("agnes_video_creator.novel.request_json") as mock_req:
            mock_req.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "title": "Test",
                            "characters": [],
                            "episodes": [],
                            "remaining_text": "",
                        })
                    }
                }]
            }
            result = analyze_novel(EN_NOVEL, cfg, verbose=False)
            assert isinstance(result, tuple)
            assert len(result) == 4

    def test_analyze_empty(self) -> None:
        import json
        from agnes_video_creator.config import AgnesConfig

        cfg = AgnesConfig(api_key="test-key")
        with patch("agnes_video_creator.novel.request_json") as mock_req:
            mock_req.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "title": "Untitled",
                            "characters": [],
                            "episodes": [],
                            "remaining_text": "",
                        })
                    }
                }]
            }
            title, chars, episodes, remaining = analyze_novel("", cfg, verbose=False)
            assert isinstance(title, str)


class TestGenerateEpisodeScript:
    def test_generate_calls_api(self) -> None:
        from agnes_video_creator.config import AgnesConfig

        cfg = AgnesConfig(api_key="test-key")
        with patch("agnes_video_creator.novel.generate_script") as mock_gen:
            mock_script = MagicMock()
            mock_script.to_dict.return_value = {"title": "Test"}
            mock_gen.return_value = mock_script
            result = generate_episode_script("Test", {"summary": "Test ep"}, CHARACTERS, EN_NOVEL, cfg, verbose=False)
            assert result is not None


class TestNovelToEpisodes:
    def test_novel_to_episodes(self) -> None:
        from agnes_video_creator.config import AgnesConfig

        cfg = AgnesConfig(api_key="test-key")
        with (
            patch("agnes_video_creator.novel.request_json") as mock_req,
            patch("agnes_video_creator.novel.generate_episode_script") as mock_gen,
        ):
            mock_req.return_value = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "title": "T",
                            "characters": [],
                            "episodes": [],
                            "remaining_text": "",
                        })
                    }
                }]
            }
            mock_gen.return_value = MagicMock()
            result = novel_to_episodes(EN_NOVEL, cfg, verbose=False)
            assert isinstance(result, tuple) or isinstance(result, list)
