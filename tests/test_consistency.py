"""Tests for consistency checking — parsing, report generation, script validation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agnes_video_creator.consistency import (
    ConsistencyIssue,
    ConsistencyReport,
    _extract_content,
    _parse_report,
)


class TestConsistencyIssue:
    def test_creation(self) -> None:
        issue = ConsistencyIssue(
            severity="critical",
            category="character",
            description="Name mismatch",
            location="Scene 2",
            suggestion="Use consistent name",
        )
        assert issue.severity == "critical"
        assert issue.category == "character"
        assert issue.description == "Name mismatch"
        assert issue.location == "Scene 2"
        assert issue.suggestion == "Use consistent name"

    def test_defaults(self) -> None:
        issue = ConsistencyIssue(severity="warning", category="plot", description="Unclear")
        assert issue.location == ""
        assert issue.suggestion == ""


class TestConsistencyReport:
    def test_empty(self) -> None:
        report = ConsistencyReport()
        assert report.critical_count == 0
        assert report.warning_count == 0

    def test_critical_count(self) -> None:
        report = ConsistencyReport(
            issues=[
                ConsistencyIssue("critical", "char", "Mismatch"),
                ConsistencyIssue("warning", "char", "Minor"),
            ]
        )
        assert report.critical_count == 1
        assert report.warning_count == 1

    def test_print_report(self) -> None:
        report = ConsistencyReport(
            issues=[
                ConsistencyIssue("critical", "character", "Name mismatch", "Scene 1"),
                ConsistencyIssue("warning", "plot", "Timeline issue", "Scene 2"),
            ]
        )
        report.print_report()

    def test_print_report_no_issues(self) -> None:
        report = ConsistencyReport()
        report.print_report()

    def test_summary(self) -> None:
        report = ConsistencyReport(summary="All clear")
        assert report.summary == "All clear"


class TestExtractContent:
    def test_valid(self) -> None:
        data = {"choices": [{"message": {"content": "Hello"}}]}
        assert _extract_content(data) == "Hello"

    def test_missing_key(self) -> None:
        assert _extract_content({}) is None

    def test_none(self) -> None:
        assert _extract_content(None) is None  # type: ignore[arg-type]


class TestParseReport:
    def test_valid(self) -> None:
        text = "- critical: Name mismatch [Scene 2]\n- warning: Timeline issue [Scene 5]"
        report = _parse_report(text)
        assert isinstance(report, ConsistencyReport)

    def test_empty(self) -> None:
        report = _parse_report("")
        assert isinstance(report, ConsistencyReport)

    def test_code_block(self) -> None:
        text = "```\n- critical: Problem [Scene 1]\n```"
        report = _parse_report(text)
        assert isinstance(report, ConsistencyReport)



