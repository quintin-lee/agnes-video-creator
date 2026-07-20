"""Plot consistency checker — uses Agnes AI to analyze scripts for plot holes,
character inconsistencies, timeline/logical errors, and visual contradictions."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script
from agnes_video_creator.utils import request_json


@dataclass
class ConsistencyIssue:
    """A single plot or continuity issue found during analysis."""

    severity: str  # "critical" / "warning" / "info"
    category: str  # "character" / "timeline" / "plot" / "visual" / "dialogue"
    description: str
    location: str = ""  # e.g. "Episode 2, Scene 3"
    suggestion: str = ""


@dataclass
class ConsistencyReport:
    """Full consistency analysis result."""

    issues: list[ConsistencyIssue] = field(default_factory=list)
    summary: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def print_report(self, *, file=sys.stderr) -> None:
        if not self.issues:
            print("  ✓ No consistency issues found.", file=file)
            return
        print(f"  Found {len(self.issues)} consistency issue(s):", file=file)
        for _i, issue in enumerate(self.issues, 1):
            tag = {"critical": "✗", "warning": "⚠", "info": "ℹ"}.get(issue.severity, "•")
            print(f"  {tag} [{issue.category}] {issue.description}", file=file)
            if issue.location:
                print(f"      Location: {issue.location}", file=file)
            if issue.suggestion:
                print(f"      Suggestion: {issue.suggestion}", file=file)
        print(f"\n  Summary: {self.summary}", file=file)


_SYSTEM_PROMPT = """You are a professional short-drama script editor.
Analyze the provided scripts for plot consistency issues and output **only** valid JSON.

Focus on:
- Character consistency (name spelling, personality, relationships, voices)
- Timeline / chronology errors
- Plot holes or unexplained events
- Visual continuity (contradicting environment descriptions)
- Dialogue consistency (character voice, tone, information known)

Output format:
{
  "summary": "One-sentence overall assessment",
  "issues": [
    {
      "severity": "critical|warning|info",
      "category": "character|timeline|plot|visual|dialogue",
      "description": "Clear description of the issue",
      "location": "Where it occurs (e.g. Episode X, Scene Y)",
      "suggestion": "How to fix it"
    }
  ]
}

If no issues found, return {"summary": "No issues found.", "issues": []}.
No markdown fences, no extra text."""


def check_consistency(
    scripts: list[Script],
    *,
    cfg: AgnesConfig | None = None,
    verbose: bool = True,
) -> ConsistencyReport:
    """Analyze one or more episode scripts for plot consistency issues.

    Parameters
    ----------
    scripts : list[Script]
        Scripts to analyze. Pass multiple for cross-episode checking.
    """
    if cfg is None:
        cfg = AgnesConfig.from_env()
    if not cfg.has_api_key:
        raise SystemExit("AGNES_API_KEY not set.")

    # Build a compact script summary for the model
    summary_lines: list[str] = []
    for script in scripts:
        ep = script.episode or 0
        summary_lines.append(f"Episode {ep}: {script.title}")
        summary_lines.append(
            f"  Characters: {', '.join(c.name for c in script.characters) or 'none'}"
        )
        for scene in script.scenes:
            chars = ", ".join(scene.character_appearances) or "none"
            dialogues = "; ".join(
                f"{d.get('character', '?')}: {d.get('line', '')[:60]}" for d in scene.dialogues[:3]
            )
            summary_lines.append(
                f"  Scene {scene.id}: [{scene.camera}] {scene.narration[:100]} | chars: {chars}"
            )
            if dialogues:
                summary_lines.append(f"    Dialogues: {dialogues}")
        summary_lines.append("")

    user_content = "\n".join(summary_lines)
    if verbose:
        title_info = "; ".join(s.title for s in scripts)
        print(f"  Checking consistency for: {title_info}", file=sys.stderr)

    payload = {
        "model": cfg.text_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
    }

    raw = request_json("POST", "/v1/chat/completions", payload, cfg=cfg)
    content = _extract_content(raw)
    if not content:
        if verbose:
            print(
                "  ⚠ Consistency check returned empty response.",
                file=sys.stderr,
            )
        return ConsistencyReport(summary="Analysis failed — empty response.")

    return _parse_report(content)


def check_script_file(
    script_paths: str | Path | list[str | Path],
    *,
    cfg: AgnesConfig | None = None,
    verbose: bool = True,
) -> ConsistencyReport:
    """Load scripts from JSON files and run consistency check."""
    if isinstance(script_paths, (str, Path)):
        script_paths = [script_paths]
    scripts = []
    for path in script_paths:
        p = Path(path)
        if not p.exists():
            print(f"  ⚠ Script not found: {path}", file=sys.stderr)
            continue
        scripts.append(Script.load(p))
    if not scripts:
        raise SystemExit("No valid scripts to check.")
    return check_consistency(scripts, cfg=cfg, verbose=verbose)


def _extract_content(data: dict[str, Any]) -> str | None:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def _parse_report(raw: str) -> ConsistencyReport:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
        elif "```" in cleaned:
            cleaned = cleaned[: cleaned.rindex("```")].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return ConsistencyReport(
            summary="Failed to parse analysis result.",
            issues=[],
        )

    issues_raw = data.get("issues", [])
    issues = [
        ConsistencyIssue(
            severity=i.get("severity", "info"),
            category=i.get("category", "plot"),
            description=i.get("description", ""),
            location=i.get("location", ""),
            suggestion=i.get("suggestion", ""),
        )
        for i in issues_raw
    ]
    return ConsistencyReport(
        issues=issues,
        summary=data.get("summary", ""),
    )
