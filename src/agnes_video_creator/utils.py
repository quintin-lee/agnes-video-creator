"""Shared utilities — HTTP calls, prompt translation, file helpers."""

from __future__ import annotations

import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agnes_video_creator.config import AgnesConfig

# ── HTTP helpers (with retry) ─────────────────────────────────────────


def _headers(cfg: AgnesConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }


def _is_retryable(err: Exception) -> bool:
    """True if the error is worth retrying: 5xx, timeout, connection reset."""
    if isinstance(err, urllib.error.HTTPError):
        return 500 <= err.code < 600
    if isinstance(err, urllib.error.URLError):
        reason = str(err.reason).lower()
        for keyword in (
            "timeout",
            "timed out",
            "connection",
            "reset",
            "refused",
            "eof",
            "broken",
            "name resolution",
        ):
            if keyword in reason:
                return True
    return False


def _do_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    cfg: AgnesConfig,
    timeout: int,
    *,
    raw: bool = False,
) -> bytes:
    """Single HTTP request attempt. Returns response body bytes."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        cfg.base_url + path,
        data=body,
        method=method,
        headers=_headers(cfg),
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _request_with_retry(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    cfg: AgnesConfig | None = None,
    timeout: int | None = None,
    *,
    raw: bool = False,
) -> bytes:
    """Make an HTTP request with exponential-backoff retry on transient errors."""
    if cfg is None:
        cfg = AgnesConfig.from_env()
    if timeout is None:
        timeout = cfg.request_timeout

    last_err: Exception | None = None
    delay = cfg.request_base_delay
    for attempt in range(1, cfg.request_retries + 1):
        try:
            return _do_request(method, path, payload, cfg, timeout, raw=raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if _is_retryable(exc) and attempt < cfg.request_retries:
                print(
                    f"  HTTP {exc.code} on attempt {attempt}/{cfg.request_retries} "
                    f"for {path}. Retrying in {delay:.0f}s…",
                    file=sys.stderr,
                )
                last_err = exc
            else:
                raise SystemExit(
                    f"HTTP {exc.code} {path}{' after retries' if attempt > 1 else ''}: {detail}"
                ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if _is_retryable(exc) and attempt < cfg.request_retries:
                print(
                    f"  {type(exc).__name__} on attempt {attempt}/{cfg.request_retries} "
                    f"for {path}: {exc}. Retrying in {delay:.0f}s…",
                    file=sys.stderr,
                )
                last_err = exc
            else:
                raise SystemExit(
                    f"Request failed for {path}{' after retries' if attempt > 1 else ''}: {exc}"
                ) from exc

        # Exponential backoff + jitter
        jitter = random.uniform(0.5, 1.5)
        time.sleep(min(delay * jitter, cfg.request_max_delay))
        delay *= 2

    # Should not be reached, but keeps type-checkers happy
    raise SystemExit(f"Request failed for {path} after {cfg.request_retries} attempts: {last_err}")


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    cfg: AgnesConfig | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Make an HTTP request and return parsed JSON. Retries on transient errors."""
    body = _request_with_retry(method, path, payload, cfg, timeout=timeout)
    return json.loads(body) if body else {}


def request_raw(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    cfg: AgnesConfig | None = None,
    timeout: int | None = None,
) -> str:
    """Make an HTTP request and return raw text. Retries on transient errors."""
    body = _request_with_retry(method, path, payload, cfg, timeout=timeout, raw=True)
    return body.decode("utf-8", errors="replace")


# ── Prompt translation ─────────────────────────────────────────────────


def needs_translation(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text)


def translate_prompt(prompt: str, cfg: AgnesConfig) -> str:
    """Translate a non-English prompt to English using the text model."""
    payload = {
        "model": cfg.text_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate the user's generation prompt into fluent English. "
                    "Preserve all visual details, style words, camera motion, lighting, "
                    "composition constraints, negative instructions. Return only the English text."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 800,
    }
    data = request_json("POST", "/v1/chat/completions", payload)
    try:
        translated = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(
            f"Prompt translation failed: {json.dumps(data, ensure_ascii=False)}"
        ) from exc
    if not translated:
        raise SystemExit("Prompt translation failed: empty response")
    return translated


def prepare_prompt(prompt: str, cfg: AgnesConfig) -> tuple[str, str | None]:
    """Translate if needed. Returns (final_prompt, original_or_None)."""
    if cfg.translate_prompts and needs_translation(prompt):
        translated = translate_prompt(prompt, cfg)
        return translated, prompt
    return prompt, None


# ── File helpers ───────────────────────────────────────────────────────


def download_file(url: str, dest: str | Path, timeout: int = 120) -> Path:
    """Download a URL to a local file. Returns the destination path."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as exc:
        raise SystemExit(f"Failed to download {url}: {exc}") from exc
    dest.write_bytes(data)
    return dest


def slugify(text: str) -> str:
    """Turn a string into a safe filesystem name."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def json_pretty(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ── Video polling ──────────────────────────────────────────────────────


def poll_video_task(
    task_id: str,
    cfg: AgnesConfig,
    *,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Poll for video completion. Returns the completed response."""
    deadline = time.time() + cfg.poll_timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = request_json("GET", f"/v1/videos/{task_id}", cfg=cfg)
        if last.get("error"):
            raise SystemExit(
                f"Video task {task_id} returned error: {json.dumps(last, ensure_ascii=False)}"
            )
        status = str(last.get("status", "")).lower()
        progress = last.get("progress")
        if progress_callback:
            progress_callback(task_id, status, progress)
        else:
            print(
                f"  video {task_id}: status={status} progress={progress}",
                file=sys.stderr,
            )
        if status in {"completed", "failed"}:
            return last
        time.sleep(cfg.poll_interval)
    raise SystemExit(f"Timed out waiting for video task {task_id}. Last: {json.dumps(last)}")
