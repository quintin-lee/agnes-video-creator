"""Storyboard preview — generate an HTML visual summary of scene images.

After images are generated but before video creation, the user can
review the storyboard to catch issues early, avoiding wasted API
credits on video generation for problematic scenes.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from agnes_video_creator.config import AgnesConfig
from agnes_video_creator.models import Script


def generate_storyboard_html(script: Script, output_path: str | Path) -> Path:
    """Create a standalone HTML storyboard showing each scene's image
    and narration in a grid layout.

    Returns the path to the generated HTML file.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    scenes_html = ""
    for i, scene in enumerate(script.scenes):
        img_src = _resolve_image_src(scene.image_url or scene.image_path)
        cam = scene.camera or "static"
        style = scene.style or "cinematic"

        # Character tags
        chars_html = ""
        if scene.character_appearances:
            tags = "".join(
                f'<span class="tag">{c}</span>'
                for c in scene.character_appearances
            )
            chars_html = f'<div class="tags">{tags}</div>'

        # Narration
        narration = scene.narration or ""

        # Dialogues if any
        dialogues_html = ""
        if scene.dialogues:
            for d in scene.dialogues:
                char_name = d.get("character", "")
                line = d.get("line", "")
                dialogues_html += (
                    f'<div class="dialogue">'
                    f'<span class="char">{char_name}</span>: {line}'
                    f"</div>\n"
                )

        # Camera motion badge
        cam_badge = _camera_badge(cam)

        scenes_html += f"""
        <div class="scene">
            <div class="scene-num">Scene {scene.id}</div>
            <div class="scene-img-container">
                <img class="scene-img" src="{img_src}" alt="Scene {scene.id}"
                     onerror="this.alt='(image not available)';this.style.display='none'">
            </div>
            <div class="scene-info">
                <div class="scene-meta">
                    <span class="badge">{scene.duration_seconds:.1f}s</span>
                    {cam_badge}
                    <span class="badge">{style}</span>
                </div>
                {chars_html}
                <div class="narration">{narration}</div>
                {dialogues_html}
            </div>
        </div>
        """

    if not scenes_html:
        scenes_html = "<p>No scenes in this script.</p>"

    char_list = ""
    if script.characters:
        items = "".join(
            f"<li>{c.name} — {c.role or '角色'}</li>" for c in script.characters
        )
        char_list = f"<h3>角色 Characters</h3><ul>{items}</ul>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(script.title)} — 分镜预览</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, "Noto Sans CJK SC", "Microsoft YaHei",
                 "PingFang SC", sans-serif;
    background: #0f0f0f; color: #e0e0e0; padding: 20px;
}}
h1 {{ font-size: 1.6em; margin-bottom: 4px; }}
h2 {{ font-size: 1em; color: #999; font-weight: 400; margin-bottom: 16px; }}
.sub {{ color: #888; font-size: 0.85em; margin-bottom: 8px; }}
.grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 16px;
}}
.scene {{
    background: #1a1a1a; border-radius: 10px; overflow: hidden;
    border: 1px solid #2a2a2a;
}}
.scene-num {{
    background: #2a2a2a; padding: 6px 12px; font-size: 0.8em;
    font-weight: 600; color: #aaa;
}}
.scene-img-container {{
    width: 100%; aspect-ratio: 16/9; background: #222;
    display: flex; align-items: center; justify-content: center;
}}
.scene-img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
.scene-info {{ padding: 10px 12px; }}
.scene-meta {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }}
.badge {{
    font-size: 0.75em; padding: 2px 8px; border-radius: 4px;
    background: #333; color: #ccc;
}}
.badge-cam {{ background: #1a3a2a; color: #6c6; }}
.narration {{
    font-size: 0.9em; line-height: 1.5; color: #ccc;
    margin-top: 6px;
}}
.dialogue {{ font-size: 0.85em; color: #b0b0ff; margin-top: 4px; }}
.char {{ font-weight: 600; color: #8af; }}
.tags {{ display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 4px; }}
.tag {{
    font-size: 0.7em; padding: 1px 6px; border-radius: 3px;
    background: #2a2a4a; color: #aac;
}}
.status {{ margin-top: 12px; font-size: 0.85em; color: #888; }}
footer {{ margin-top: 20px; font-size: 0.75em; color: #555; text-align: center; }}
</style>
</head>
<body>
<h1>{_escape(script.title)}</h1>
<h2>{_escape(script.description)}</h2>
<div class="sub">
    {len(script.scenes)} 个场景 · {script.total_duration:.0f}s 总时长
    · {script.style_guide or ''}
</div>
{char_list}
<div class="grid">
{scenes_html}
</div>
<div class="status">
    ✅ 图像已生成 · 可在此检查角色面部一致性后继续视频生成
</div>
<footer>Agnes Video Creator — Storyboard Preview</footer>
</body>
</html>"""

    output.write_text(html, encoding="utf-8")
    return output


def preview_storyboard(
    script: Script,
    cfg: AgnesConfig,
    *,
    verbose: bool = True,
) -> bool:
    """Generate an HTML storyboard and prompt the user.

    Returns True if the user says to continue, False to abort.
    """
    output_dir = cfg.resolved_output
    html_path = output_dir / "storyboard.html"
    generate_storyboard_html(script, html_path)

    if verbose:
        print(f"\n  📋 Storyboard: {html_path}", file=sys.stderr)
        _try_open(html_path)

    print(
        "\n  检查分镜中角色的面部一致性。\n"
        "  继续生成视频? [Y/n]: ",
        end="",
        file=sys.stderr,
    )
    try:
        answer = input().strip().lower()
        if answer in ("n", "no"):
            print(
                "  已暂停。编辑场景图像后可用 --skip-images 跳过图像步骤继续。",
                file=sys.stderr,
            )
            return False
    except (EOFError, KeyboardInterrupt):
        return False
    return True


# ── Helpers ─────────────────────────────────────────────────────────────


def _resolve_image_src(src: str | None) -> str:
    if not src:
        return ""
    if src.startswith(("http://", "https://", "data:")):
        return src
    return Path(src).resolve().as_uri()


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _camera_badge(cam: str) -> str:
    cam_lower = cam.lower()
    if any(k in cam_lower for k in ("zoom", "dolly", "close")):
        return f'<span class="badge badge-cam">🎥 {cam}</span>'
    if any(k in cam_lower for k in ("pan", "tilt")):
        return f'<span class="badge badge-cam">🔄 {cam}</span>'
    if any(k in cam_lower for k in ("track", "follow", "dolly")):
        return f'<span class="badge badge-cam">🎯 {cam}</span>'
    if any(k in cam_lower for k in ("handheld", "shake")):
        return f'<span class="badge badge-cam">📱 {cam}</span>'
    if any(k in cam_lower for k in ("aerial", "bird", "crane")):
        return f'<span class="badge badge-cam">🚁 {cam}</span>'
    return f'<span class="badge badge-cam">{cam}</span>'


def _try_open(path: Path) -> None:
    """Try to open the HTML in a browser; fail silently."""
    try:
        webbrowser.open(str(path.resolve()))
    except Exception:
        pass
