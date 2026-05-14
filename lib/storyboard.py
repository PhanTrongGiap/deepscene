#!/usr/bin/env python3
"""deepscene-detail implementation.

Creates a reconstruction-oriented storyboard from a local video or URL:
scene/interval frames -> Gemini Vision observations -> audio chunk cues ->
merged Markdown + JSON artifacts.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def gemini_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def gemini_model(kind: str) -> str:
    specific = os.environ.get(f"DEEPSCENE_GEMINI_{kind.upper()}_MODEL")
    if specific:
        return specific
    return os.environ.get("DEEPSCENE_GEMINI_MODEL") or "gemini-2.5-flash"


def run(cmd: list[str], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=check,
    )


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"[deepscene-detail] missing required tool: {name}")


def retry_delay_hint(error_text: str) -> float | None:
    retry_after = re.search(r"retry in ([0-9.]+)s", error_text, re.IGNORECASE)
    if retry_after:
        return float(retry_after.group(1))
    return None


def cache_dir() -> Path:
    base = os.environ.get("DEEPSCENE_CACHE_DIR")
    if not base:
        xdg = os.environ.get("XDG_CACHE_HOME")
        home = os.environ.get("HOME", str(Path.home()))
        base = str(Path(xdg or Path(home) / ".cache") / "deepscene")
    path = Path(base)
    if path.is_symlink():
        raise SystemExit(f"[deepscene-detail] refusing symlink cache dir: {path}")
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def video_duration(video: Path) -> float:
    proc = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(video),
    ])
    try:
        return float(proc.stdout.strip() or "0")
    except ValueError:
        return 0.0


def resolve_video(input_value: str, cookies: str | None) -> Path:
    candidate = Path(input_value)
    if candidate.is_file():
        return candidate.resolve()

    cmd = [str(BIN / "deepscene-download"), input_value]
    if cookies:
        cmd.extend(["--cookies", cookies])
    print(f"[deepscene-detail] downloading {input_value} ...", file=sys.stderr)
    proc = run(cmd)
    video = Path(proc.stdout.strip().splitlines()[-1])
    if not video.is_file():
        raise SystemExit(f"[deepscene-detail] downloader did not return a video file: {video}")
    return video


def make_out_dir(video: Path, out: str | None) -> Path:
    if out:
        out_dir = Path(out)
    else:
        digest = hashlib.sha1(str(video).encode()).hexdigest()[:8]
        subdir = "summaries" if os.environ.get("DEEPSCENE_MODE") == "summary" else "details"
        out_dir = cache_dir() / subdir / f"{video.stem}_{digest}"
    if out_dir.is_symlink():
        raise SystemExit(f"[deepscene-detail] refusing symlink out dir: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    return out_dir


def parse_showinfo_timestamps(stderr: str) -> list[float]:
    timestamps: list[float] = []
    for line in stderr.splitlines():
        if "Parsed_showinfo" not in line or "pts_time:" not in line:
            continue
        match = re.search(r"pts_time:([0-9.]+)", line)
        if match:
            timestamps.append(float(match.group(1)))
    return timestamps


def extract_frame(video: Path, timestamp: float, out_file: Path) -> bool:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(out_file),
        ],
        check=False,
    )
    return proc.returncode == 0 and out_file.is_file() and out_file.stat().st_size > 0


def sample_frames(video: Path, out_dir: Path, target_count: int, threshold: float) -> list[dict[str, Any]]:
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.chmod(0o700)

    duration = video_duration(video)
    scene_pattern = frames_dir / "scene_%03d.jpg"
    scene_budget = max(2, target_count // 2)
    scene_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"select='gt(scene,{threshold})',showinfo",
        "-vsync",
        "vfr",
        "-frames:v",
        str(scene_budget * 2),
        "-q:v",
        "4",
        str(scene_pattern),
    ]
    proc = run(scene_cmd, check=False)
    scene_times = parse_showinfo_timestamps(proc.stderr or "")
    scene_files = sorted(frames_dir.glob("scene_*.jpg"))

    frames: list[dict[str, Any]] = []
    for idx, path in enumerate(scene_files[:scene_budget]):
        timestamp = scene_times[idx] if idx < len(scene_times) else 0.0
        frames.append({
            "frame_id": f"scene_{idx + 1:03d}",
            "timestamp_sec": round(timestamp, 3),
            "path": str(path),
            "source": "scene",
        })

    # Always add interval frames for coverage across the full video. Pure
    # scene-change sampling can cluster around fast title sequences.
    if duration > 0:
        existing = {round(f["timestamp_sec"], 1) for f in frames}
        interval_needed = max(0, target_count - len(frames))
        for i in range(1, interval_needed + 1):
            if len(frames) >= target_count:
                break
            ts = (i / (interval_needed + 1)) * duration
            if round(ts, 1) in existing:
                continue
            out_file = frames_dir / f"interval_{i:03d}.jpg"
            if extract_frame(video, ts, out_file):
                frames.append({
                    "frame_id": f"interval_{i:03d}",
                    "timestamp_sec": round(ts, 3),
                    "path": str(out_file),
                    "source": "interval",
                })
                existing.add(round(ts, 1))

    frames.sort(key=lambda item: item["timestamp_sec"])
    frames = frames[:target_count]
    for idx, item in enumerate(frames, 1):
        item["index"] = idx

    (out_dir / "frames.json").write_text(json.dumps(frames, ensure_ascii=False, indent=2), encoding="utf-8")
    return frames


def gemini_generate(
    parts: list[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.2,
) -> str:
    api_key = os.environ.get("GOOGLE_AI_KEY")
    if not api_key:
        raise SystemExit("[deepscene-detail] GOOGLE_AI_KEY is required")

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        gemini_url(model or gemini_model("default")),
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "User-Agent": os.environ.get("DEEPSCENE_USER_AGENT", "deepscene/storyboard"),
        },
    )
    retries = int(os.environ.get("DEEPSCENE_GEMINI_RETRIES", "3"))
    delay = float(os.environ.get("DEEPSCENE_GEMINI_RETRY_DELAY", "2"))
    last_error = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {body_text[:1000]}"
            retryable = exc.code in RETRYABLE_HTTP_CODES
        except urllib.error.URLError as exc:
            last_error = str(exc)
            retryable = True

        if not retryable or attempt >= retries:
            raise SystemExit(f"[deepscene-detail] Gemini call failed: {last_error}")
        sleep_for = retry_delay_hint(last_error) or delay * (2 ** attempt)
        sleep_for = min(sleep_for, float(os.environ.get("DEEPSCENE_GEMINI_MAX_RETRY_DELAY", "45")))
        print(
            f"[deepscene-detail] Gemini temporary failure ({last_error.splitlines()[0]}), "
            f"retry {attempt + 1}/{retries} in {sleep_for:.1f}s ...",
            file=sys.stderr,
        )
        time.sleep(sleep_for)

    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(f"[deepscene-detail] Gemini response missing text: {json.dumps(payload)[:1000]}") from exc


def openai_chat_generate(prompt: str, *, max_tokens: int = 8192, temperature: float = 0.2) -> str:
    api_key = os.environ.get("DEEPSCENE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("[deepscene-detail] DEEPSCENE_OPENAI_API_KEY or OPENAI_API_KEY is required for OpenAI synthesis")

    base_url = (
        os.environ.get("DEEPSCENE_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or OPENAI_DEFAULT_BASE_URL
    ).rstrip("/")
    model = os.environ.get("DEEPSCENE_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-5.4"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": os.environ.get("DEEPSCENE_USER_AGENT", "deepscene/storyboard"),
        },
    )

    retries = int(os.environ.get("DEEPSCENE_OPENAI_RETRIES", os.environ.get("DEEPSCENE_GEMINI_RETRIES", "3")))
    delay = float(os.environ.get("DEEPSCENE_OPENAI_RETRY_DELAY", os.environ.get("DEEPSCENE_GEMINI_RETRY_DELAY", "2")))
    last_error = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {body_text[:1000]}"
            retryable = exc.code in RETRYABLE_HTTP_CODES
        except urllib.error.URLError as exc:
            last_error = str(exc)
            retryable = True

        if not retryable or attempt >= retries:
            raise SystemExit(f"[deepscene-detail] OpenAI synthesis failed: {last_error}")
        sleep_for = retry_delay_hint(last_error) or delay * (2 ** attempt)
        sleep_for = min(sleep_for, float(os.environ.get("DEEPSCENE_OPENAI_MAX_RETRY_DELAY", "45")))
        print(
            f"[deepscene-detail] OpenAI temporary failure ({last_error.splitlines()[0]}), "
            f"retry {attempt + 1}/{retries} in {sleep_for:.1f}s ...",
            file=sys.stderr,
        )
        time.sleep(sleep_for)

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(f"[deepscene-detail] OpenAI response missing content: {json.dumps(payload)[:1000]}") from exc
    if not content:
        raise SystemExit(f"[deepscene-detail] OpenAI response returned empty content: {json.dumps(payload)[:1000]}")
    return content


def text_generate(prompt: str, provider: str, *, max_tokens: int = 8192, temperature: float = 0.2) -> str:
    if provider == "openai":
        return openai_chat_generate(prompt, max_tokens=max_tokens, temperature=temperature)
    return gemini_generate(
        [{"text": prompt}],
        model=gemini_model("synthesis"),
        max_tokens=max_tokens,
        temperature=temperature,
    )


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_json_with_repair(raw_text: str, repair_context: str, provider: str) -> tuple[dict[str, Any], str | None]:
    cleaned = strip_json_fence(raw_text)
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError as first_error:
        repair_prompt = f"""
Fix this response into valid JSON only. Do not add Markdown fences or explanations.

Required JSON shape:
{repair_context}

Broken response:
{raw_text}

Parse error: {first_error}
""".strip()
        repaired = text_generate(repair_prompt, provider, max_tokens=8192, temperature=0.0)
        repaired_clean = strip_json_fence(repaired)
        try:
            return json.loads(repaired_clean), "json_repaired"
        except json.JSONDecodeError as second_error:
            return {
                "metadata": {
                    "purpose": "reconstruction_storyboard",
                    "json_parse_warning": f"Gemini did not return valid JSON after repair: {second_error}",
                },
                "characters": [],
                "shots": [],
                "scenes": [],
                "audio_cues": [],
                "reconstruction_notes": [],
                "raw_storyboard_text": raw_text,
                "raw_repair_text": repaired,
            }, "json_repair_failed"


def ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def normalize_storyboard(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("metadata", {})
    data.setdefault("characters", [])
    data.setdefault("shots", [])
    data.setdefault("scenes", [])
    data.setdefault("audio_cues", [])
    data.setdefault("reconstruction_notes", [])

    for i, char in enumerate(ensure_list(data.get("characters")), 1):
        if not isinstance(char, dict):
            char = {"name": str(char), "description": "", "confidence": "low"}
        char.setdefault("name", f"Character {i}")
        char.setdefault("description", "")
        char.setdefault("confidence", "medium")
        data["characters"][i - 1] = char

    normalized_shots = []
    for i, shot in enumerate(ensure_list(data.get("shots")), 1):
        if not isinstance(shot, dict):
            continue
        shot.setdefault("shot_id", f"SH{i:03d}")
        shot.setdefault("start_sec", 0)
        shot.setdefault("end_sec", shot.get("start_sec", 0))
        shot.setdefault("source_frames", [])
        shot.setdefault("characters", [])
        shot.setdefault("visual_description", "")
        shot.setdefault("action", "")
        shot.setdefault("camera_framing", "")
        shot.setdefault("audio_cues", "")
        shot.setdefault("reconstruction_prompt", "")
        shot.setdefault("uncertainties", [])
        normalized_shots.append(shot)
    data["shots"] = normalized_shots

    normalized_scenes = []
    for i, scene in enumerate(ensure_list(data.get("scenes")), 1):
        if not isinstance(scene, dict):
            continue
        scene.setdefault("scene_id", f"S{i:02d}")
        scene.setdefault("start_sec", 0)
        scene.setdefault("end_sec", scene.get("start_sec", 0))
        scene.setdefault("source_frames", [])
        scene.setdefault("setting", "")
        scene.setdefault("characters", [])
        scene.setdefault("visual_description", "")
        scene.setdefault("actions", [])
        scene.setdefault("camera_framing", "")
        scene.setdefault("props_and_text", [])
        scene.setdefault("audio_cues", "")
        scene.setdefault("reconstruction_prompt", "")
        scene.setdefault("uncertainties", [])
        normalized_scenes.append(scene)
    data["scenes"] = normalized_scenes

    normalized_audio = []
    for i, cue in enumerate(ensure_list(data.get("audio_cues")), 1):
        if not isinstance(cue, dict):
            cue = {"cue_id": f"A{i:03d}", "summary": str(cue)}
        cue.setdefault("cue_id", f"A{i:03d}")
        cue.setdefault("start_sec", 0)
        cue.setdefault("end_sec", cue.get("start_sec", 0))
        cue.setdefault("summary", "")
        cue.setdefault("speaker_or_source", "")
        normalized_audio.append(cue)
    data["audio_cues"] = normalized_audio

    return data


def read_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def extract_markdown_field(section: str, label: str) -> str:
    pattern = rf"- \*\*{re.escape(label)}:\*\*\s*(.*?)(?=\n- \*\*|\n###|\Z)"
    match = re.search(pattern, section, re.DOTALL)
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def parse_vision_observations(vision_md: str) -> dict[int, dict[str, str]]:
    observations: dict[int, dict[str, str]] = {}
    matches = list(re.finditer(r"^### Frame\s+(\d+)\s*$", vision_md, re.MULTILINE))
    for idx, match in enumerate(matches):
        frame_no = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(vision_md)
        section = vision_md[start:end]
        observations[frame_no] = {
            "timestamp": extract_markdown_field(section, "timestamp"),
            "characters": extract_markdown_field(section, "characters/entities visible"),
            "setting": extract_markdown_field(section, "setting and visual style"),
            "actions": extract_markdown_field(section, "actions, body language, expression"),
            "camera": extract_markdown_field(section, "camera/framing/composition"),
            "props": extract_markdown_field(section, "props, objects, and on-screen text"),
            "continuity": extract_markdown_field(section, "continuity notes for reconstructing the shot"),
            "uncertainty": extract_markdown_field(section, "uncertainty if any"),
        }
    return observations


def split_character_names(text: str) -> list[str]:
    if not text or "Không có" in text:
        return []
    cleaned = re.sub(r"\([^)]*\)", "", text)
    parts = re.split(r",|\bvà\b|\band\b", cleaned)
    names = []
    for part in parts:
        name = part.strip(" .;:")
        if name and len(name) <= 80:
            names.append(name)
    return names


def image_part(path: Path) -> dict[str, Any]:
    return {
        "inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    }


def analyze_frames(frames: list[dict[str, Any]], out_dir: Path) -> str:
    prompt = """
You are producing a reconstruction storyboard from sampled video frames.
Reply in Vietnamese Markdown.

For each frame, include:
- timestamp
- characters/entities visible
- setting and visual style
- actions, body language, expression
- camera/framing/composition
- props, objects, and on-screen text
- continuity notes for reconstructing the shot
- uncertainty if any

Do not invent dialogue. Focus on details useful for recreating a storyboard.
""".strip()
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for frame in frames:
        parts.append({
            "text": (
                f"Frame {frame['index']:02d} "
                f"({frame['source']}), timestamp {frame['timestamp_sec']:.1f}s, "
                f"path {frame['path']}"
            )
        })
        parts.append(image_part(Path(frame["path"])))

    text = gemini_generate(parts, model=gemini_model("vision"), max_tokens=8192, temperature=0.2)
    (out_dir / "vision.md").write_text(text, encoding="utf-8")
    return text


def has_audio(video: Path) -> bool:
    proc = run([
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video),
    ], check=False)
    return bool(proc.stdout.strip())


def extract_audio_chunk(video: Path, start: float, duration: float, out_file: Path) -> bool:
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            "-f",
            "mp3",
            str(out_file),
        ],
        check=False,
    )
    return proc.returncode == 0 and out_file.is_file() and out_file.stat().st_size > 0


def analyze_audio_chunks(video: Path, out_dir: Path, chunk_sec: int) -> list[dict[str, Any]]:
    duration = video_duration(video)
    chunks_dir = out_dir / "audio_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.chmod(0o700)

    if duration <= 0 or not has_audio(video):
        return []

    results: list[dict[str, Any]] = []
    starts = [float(s) for s in range(0, int(duration), chunk_sec)]
    for idx, start in enumerate(starts, 1):
        dur = min(chunk_sec, max(0.0, duration - start))
        if dur <= 0:
            continue
        audio_file = chunks_dir / f"chunk_{idx:03d}.mp3"
        if not extract_audio_chunk(video, start, dur, audio_file):
            continue
        if audio_file.stat().st_size > 19 * 1024 * 1024:
            results.append({
                "index": idx,
                "start_sec": round(start, 3),
                "end_sec": round(start + dur, 3),
                "summary": "Skipped: audio chunk exceeds Gemini inline size limit.",
            })
            continue
        prompt = (
            "Tra loi tieng Viet that ngan, toi da 5 gach dau dong, moi gach dau dong mot cau hoan chinh. "
            f"Audio chunk {idx}: {start:.1f}s den {start + dur:.1f}s. "
            "Noi ro: loi noi neu nghe ro, ai co ve dang noi, nhac/SFX, mood, cue cau chuyen de dung storyboard. "
            "Khong mo dau dai. Khong bia quote neu khong chac."
        )
        parts = [
            {"text": prompt},
            {
                "inline_data": {
                    "mime_type": "audio/mp3",
                    "data": base64.b64encode(audio_file.read_bytes()).decode("ascii"),
                }
            },
        ]
        try:
            summary = gemini_generate(parts, model=gemini_model("audio"), max_tokens=2048, temperature=0.2).strip()
        except SystemExit as exc:
            summary = f"Skipped: Gemini audio analysis failed for this chunk. {exc}"
        if summary.endswith(("*", ":", "**")):
            summary += " [possibly truncated by provider]"
        results.append({
            "index": idx,
            "start_sec": round(start, 3),
            "end_sec": round(start + dur, 3),
            "path": str(audio_file),
            "model": gemini_model("audio"),
            "summary": summary,
        })

    (out_dir / "audio_chunks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def build_storyboard_json(
    input_value: str,
    video: Path,
    duration: float,
    frames: list[dict[str, Any]],
    vision_md: str,
    audio_chunks: list[dict[str, Any]],
    synthesis_provider: str,
) -> dict[str, Any]:
    audio_brief = "\n\n".join(
        f"{c['start_sec']:.1f}-{c['end_sec']:.1f}s: {c['summary']}" for c in audio_chunks
    ) or "No audio cues available."
    frame_brief = json.dumps(
        [{"index": f["index"], "timestamp_sec": f["timestamp_sec"], "source": f["source"], "path": f["path"]} for f in frames],
        ensure_ascii=False,
        indent=2,
    )
    required_shape = """
{
  "metadata": {"source": "...", "duration_sec": 0, "purpose": "reconstruction_storyboard"},
  "characters": [{"name": "...", "description": "...", "confidence": "high|medium|low"}],
  "shots": [
    {
      "shot_id": "SH001",
      "start_sec": 0,
      "end_sec": 0,
      "source_frames": ["frame ids or indexes"],
      "setting": "...",
      "characters": ["..."],
      "visual_description": "...",
      "action": "...",
      "camera_framing": "...",
      "props_and_text": ["..."],
      "audio_cues": "...",
      "reconstruction_prompt": "...",
      "uncertainties": ["..."]
    }
  ],
  "scenes": [
    {
      "scene_id": "S01",
      "start_sec": 0,
      "end_sec": 0,
      "source_frames": ["frame ids or indexes"],
      "setting": "...",
      "characters": ["..."],
      "visual_description": "...",
      "actions": ["..."],
      "camera_framing": "...",
      "props_and_text": ["..."],
      "audio_cues": "...",
      "reconstruction_prompt": "...",
      "uncertainties": ["..."]
    }
  ],
  "audio_cues": [{"cue_id": "A001", "start_sec": 0, "end_sec": 0, "summary": "...", "speaker_or_source": "..."}],
  "reconstruction_notes": ["..."]
}
""".strip()
    prompt = f"""
Create a detailed reconstruction storyboard JSON in Vietnamese.

Input video: {input_value}
Duration seconds: {duration:.3f}
Frame metadata:
{frame_brief}

Vision observations:
{vision_md}

Audio chunk observations:
{audio_brief}

Return ONLY valid JSON with this exact shape:
{required_shape}

Use scene boundaries inferred from frame timestamps and audio chunks.
Shots should be more granular than scenes when multiple sampled frames belong to one scene.
Do not invent exact dialogue.
""".strip()
    text = text_generate(prompt, synthesis_provider, max_tokens=8192, temperature=0.2)
    data, repair_status = parse_json_with_repair(text, required_shape, synthesis_provider)
    data = normalize_storyboard(data)
    if repair_status:
        data.setdefault("metadata", {})["repair_status"] = repair_status
    return data


def fallback_storyboard_json(
    input_value: str,
    video: Path,
    duration: float,
    frames: list[dict[str, Any]],
    vision_md: str,
    audio_chunks: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    observations = parse_vision_observations(vision_md)
    character_descriptions: dict[str, str] = {}
    shots: list[dict[str, Any]] = []
    for idx, frame in enumerate(frames, 1):
        start = float(frame["timestamp_sec"])
        if idx < len(frames):
            end = float(frames[idx]["timestamp_sec"])
        else:
            end = duration
        obs = observations.get(idx, {})
        characters = split_character_names(obs.get("characters", ""))
        for character in characters:
            character_descriptions.setdefault(character, obs.get("characters", ""))
        audio_for_shot = [
            chunk.get("summary", "")
            for chunk in audio_chunks
            if float(chunk.get("start_sec", 0)) <= start < float(chunk.get("end_sec", 0))
        ]
        shots.append({
            "shot_id": f"SH{idx:03d}",
            "start_sec": round(start, 3),
            "end_sec": round(max(start, end), 3),
            "source_frames": [frame.get("frame_id", str(idx))],
            "setting": obs.get("setting", ""),
            "characters": characters,
            "visual_description": obs.get("setting", "") or f"See sampled frame {frame.get('frame_id', idx)} at {start:.1f}s: {frame.get('path', '')}",
            "action": obs.get("actions", ""),
            "camera_framing": obs.get("camera", ""),
            "props_and_text": [obs.get("props", "")] if obs.get("props") else [],
            "audio_cues": " ".join(audio_for_shot),
            "reconstruction_prompt": (
                f"Tái dựng khoảnh khắc quanh {start:.1f}s từ frame {frame.get('path', '')}. "
                f"Bối cảnh: {obs.get('setting', '')} Hành động: {obs.get('actions', '')} "
                f"Góc máy: {obs.get('camera', '')} Đồ vật/chữ: {obs.get('props', '')} "
                f"Ghi chú continuity: {obs.get('continuity', '')}"
            ),
            "uncertainties": [obs.get("uncertainty", "")] if obs.get("uncertainty") else [
                "Structured synthesis was unavailable; this shot is parsed from raw visual observations."
            ],
        })

    characters = [
        {"name": name, "description": description, "confidence": "medium"}
        for name, description in character_descriptions.items()
    ]
    audio_cues = []
    for chunk in audio_chunks:
        audio_cues.append({
            "cue_id": f"A{int(chunk.get('index', len(audio_cues) + 1)):03d}",
            "start_sec": chunk.get("start_sec", 0),
            "end_sec": chunk.get("end_sec", chunk.get("start_sec", 0)),
            "summary": chunk.get("summary", ""),
            "speaker_or_source": "",
        })

    return normalize_storyboard({
        "metadata": {
            "source": input_value,
            "duration_sec": round(duration, 3),
            "purpose": "reconstruction_storyboard",
            "fallback": True,
            "fallback_reason": reason,
        },
        "characters": characters,
        "shots": shots,
        "scenes": [{
            "scene_id": "S01",
            "start_sec": 0,
            "end_sec": round(duration, 3),
            "source_frames": [frame.get("frame_id", str(frame.get("index", ""))) for frame in frames],
            "setting": shots[0].get("setting", "") if shots else "",
            "characters": [character["name"] for character in characters],
            "visual_description": "Scene assembled from parsed Gemini Vision frame observations.",
            "actions": [shot.get("action", "") for shot in shots if shot.get("action")],
            "camera_framing": "",
            "props_and_text": [],
            "audio_cues": "See Structured Audio Cues and Audio Chunks.",
            "reconstruction_prompt": "Use every sampled frame in chronological order plus the raw visual observations to reconstruct the sequence.",
            "uncertainties": ["LLM synthesis did not complete; structured fields were parsed from the raw visual report."],
        }],
        "audio_cues": audio_cues,
        "reconstruction_notes": [
            "This is a fallback structured storyboard. The raw vision report is still included below in Markdown.",
            "Run again later or increase provider quota for richer character, scene, and shot synthesis.",
        ],
    })


def markdown_from_storyboard(data: dict[str, Any], out_dir: Path, vision_md: str, audio_chunks: list[dict[str, Any]]) -> str:
    meta = data.get("metadata", {})
    lines = [
        "# Reconstruction Storyboard",
        "",
        f"- Source: {meta.get('source', '')}",
        f"- Duration: {meta.get('duration_sec', '')}s",
        f"- Artifacts: {out_dir}",
        "",
        "## Characters",
        "",
    ]
    for character in data.get("characters", []):
        lines.append(f"- **{character.get('name', 'Unknown')}** ({character.get('confidence', 'unknown')}): {character.get('description', '')}")
    if not data.get("characters"):
        lines.append("- No structured character list returned.")

    lines.extend(["", "## Shots", ""])
    shots = data.get("shots", [])
    if shots:
        for shot in shots:
            lines.extend([
                f"### {shot.get('shot_id', 'Shot')} ({shot.get('start_sec', '?')}s-{shot.get('end_sec', '?')}s)",
                "",
                f"- Setting: {shot.get('setting', '')}",
                f"- Characters: {', '.join(shot.get('characters', [])) if isinstance(shot.get('characters'), list) else shot.get('characters', '')}",
                f"- Visual: {shot.get('visual_description', '')}",
                f"- Action: {shot.get('action', '')}",
                f"- Camera/framing: {shot.get('camera_framing', '')}",
                f"- Props/text: {', '.join(shot.get('props_and_text', [])) if isinstance(shot.get('props_and_text'), list) else shot.get('props_and_text', '')}",
                f"- Audio: {shot.get('audio_cues', '')}",
                "",
                "Reconstruction prompt:",
                "",
                shot.get("reconstruction_prompt", ""),
                "",
            ])
            uncertainties = shot.get("uncertainties", [])
            if uncertainties:
                lines.append("Uncertainties:")
                for item in uncertainties:
                    lines.append(f"- {item}")
                lines.append("")
    else:
        lines.append("- No structured shots returned.")

    lines.extend(["", "## Scenes", ""])
    for scene in data.get("scenes", []):
        lines.extend([
            f"### {scene.get('scene_id', 'Scene')} ({scene.get('start_sec', '?')}s-{scene.get('end_sec', '?')}s)",
            "",
            f"- Setting: {scene.get('setting', '')}",
            f"- Characters: {', '.join(scene.get('characters', [])) if isinstance(scene.get('characters'), list) else scene.get('characters', '')}",
            f"- Visual: {scene.get('visual_description', '')}",
            f"- Camera/framing: {scene.get('camera_framing', '')}",
            f"- Props/text: {', '.join(scene.get('props_and_text', [])) if isinstance(scene.get('props_and_text'), list) else scene.get('props_and_text', '')}",
            f"- Audio: {scene.get('audio_cues', '')}",
            "",
            "Reconstruction prompt:",
            "",
            scene.get("reconstruction_prompt", ""),
            "",
        ])
        actions = scene.get("actions", [])
        if actions:
            lines.append("Actions:")
            for action in actions:
                lines.append(f"- {action}")
            lines.append("")
        uncertainties = scene.get("uncertainties", [])
        if uncertainties:
            lines.append("Uncertainties:")
            for item in uncertainties:
                lines.append(f"- {item}")
            lines.append("")

    notes = data.get("reconstruction_notes", [])
    lines.extend(["## Reconstruction Notes", ""])
    if notes:
        lines.extend(f"- {note}" for note in notes)
    else:
        lines.append("- No structured notes returned.")

    lines.extend(["", "## Structured Audio Cues", ""])
    cues = data.get("audio_cues", [])
    if cues:
        for cue in cues:
            lines.append(
                f"- {cue.get('cue_id', 'Audio')} "
                f"({cue.get('start_sec', '?')}s-{cue.get('end_sec', '?')}s): "
                f"{cue.get('summary', '')} "
                f"[{cue.get('speaker_or_source', '')}]"
            )
    else:
        lines.append("- No structured audio cues returned.")

    lines.extend(["", "## Audio Chunks", ""])
    if audio_chunks:
        for chunk in audio_chunks:
            lines.append(f"- {chunk['start_sec']:.1f}-{chunk['end_sec']:.1f}s: {chunk['summary']}")
    else:
        lines.append("- No audio chunks available.")

    lines.extend(["", "## Raw Visual Observations", "", vision_md])
    return "\n".join(lines).rstrip() + "\n"


def build_summary_json(
    input_value: str,
    video: Path,
    duration: float,
    frames: list[dict[str, Any]],
    vision_md: str,
    audio_chunks: list[dict[str, Any]],
    synthesis_provider: str,
) -> dict[str, Any]:
    audio_brief = "\n\n".join(
        f"{c['start_sec']:.1f}-{c['end_sec']:.1f}s: {c['summary']}" for c in audio_chunks
    ) or "No audio cues available."
    frame_brief = json.dumps(
        [{"index": f["index"], "timestamp_sec": f["timestamp_sec"], "source": f["source"]} for f in frames],
        ensure_ascii=False,
        indent=2,
    )
    required_shape = """
{
  "metadata": {"source": "...", "duration_sec": 0, "purpose": "summary"},
  "summary": "...",
  "characters": ["..."],
  "key_scenes": [{"start_sec": 0, "end_sec": 0, "title": "...", "description": "..."}],
  "audio_mood": "...",
  "uncertainties": ["..."]
}
""".strip()
    prompt = f"""
Create a concise Vietnamese video-understanding summary JSON.

Input video: {input_value}
Duration seconds: {duration:.3f}
Frame metadata:
{frame_brief}

Visual observations:
{vision_md}

Audio observations:
{audio_brief}

Return ONLY valid JSON with this exact shape:
{required_shape}

Do not invent exact dialogue. Prefer visual evidence over uncertain audio.
""".strip()
    text = text_generate(prompt, synthesis_provider, max_tokens=4096, temperature=0.2)
    data, repair_status = parse_json_with_repair(text, required_shape, synthesis_provider)
    data.setdefault("metadata", {})
    data.setdefault("summary", "")
    data.setdefault("characters", [])
    data.setdefault("key_scenes", [])
    data.setdefault("audio_mood", "")
    data.setdefault("uncertainties", [])
    if repair_status:
        data["metadata"]["repair_status"] = repair_status
    return data


def fallback_summary_json(
    input_value: str,
    video: Path,
    duration: float,
    frames: list[dict[str, Any]],
    vision_md: str,
    audio_chunks: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    observations = parse_vision_observations(vision_md)
    characters: list[str] = []
    key_scenes = []
    for idx, frame in enumerate(frames, 1):
        obs = observations.get(idx, {})
        for name in split_character_names(obs.get("characters", "")):
            if name not in characters:
                characters.append(name)
        if obs:
            key_scenes.append({
                "start_sec": frame.get("timestamp_sec", 0),
                "end_sec": frame.get("timestamp_sec", 0),
                "title": obs.get("setting", "")[:80] or f"Frame {idx}",
                "description": obs.get("actions", "") or obs.get("setting", ""),
            })
    audio_mood = " ".join(c.get("summary", "") for c in audio_chunks[:2])
    summary = " ".join(
        scene.get("description", "") for scene in key_scenes[:4] if scene.get("description")
    ) or "Summary synthesis was unavailable; see vision.md for raw observations."
    return {
        "metadata": {
            "source": input_value,
            "video": str(video),
            "duration_sec": round(duration, 3),
            "purpose": "summary",
            "fallback": True,
            "fallback_reason": reason,
        },
        "summary": summary,
        "characters": characters,
        "key_scenes": key_scenes,
        "audio_mood": audio_mood,
        "uncertainties": ["LLM summary synthesis did not complete; fields were parsed from raw observations."],
    }


def markdown_from_summary(data: dict[str, Any], out_dir: Path) -> str:
    meta = data.get("metadata", {})
    lines = [
        "# DeepScene Summary",
        "",
        f"- Source: {meta.get('source', '')}",
        f"- Duration: {meta.get('duration_sec', '')}s",
        f"- Artifacts: {out_dir}",
        "",
        "## What Happens",
        "",
        data.get("summary", "") or "No summary returned.",
        "",
        "## Characters / Entities",
        "",
    ]
    chars = data.get("characters", [])
    if chars:
        lines.extend(f"- {item}" for item in chars)
    else:
        lines.append("- No characters/entities returned.")

    lines.extend(["", "## Key Scenes", ""])
    scenes = data.get("key_scenes", [])
    if scenes:
        for scene in scenes:
            lines.append(
                f"- {scene.get('start_sec', '?')}s-{scene.get('end_sec', '?')}s: "
                f"{scene.get('title', '')} - {scene.get('description', '')}"
            )
    else:
        lines.append("- No key scenes returned.")

    lines.extend(["", "## Audio / Mood", "", data.get("audio_mood", "") or "No audio/mood returned."])
    uncertainties = data.get("uncertainties", [])
    if uncertainties:
        lines.extend(["", "## Uncertainties", ""])
        lines.extend(f"- {item}" for item in uncertainties)
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=os.environ.get("DEEPSCENE_PROG", "deepscene-detail"),
        description="Build a DeepScene summary or detailed reconstruction storyboard from a video.",
    )
    parser.add_argument("input", help="URL or local video path")
    parser.add_argument("--mode", choices=("summary", "detail"), default=os.environ.get("DEEPSCENE_MODE", "detail"))
    parser.add_argument("--frames", type=int, help="target frame count")
    parser.add_argument("--out", help="artifact output directory")
    parser.add_argument("--cookies", help="Netscape cookies file for URL downloads")
    parser.add_argument("--scene-threshold", type=float, default=0.25, help="ffmpeg scene threshold (default: 0.25)")
    parser.add_argument("--audio-chunk-sec", type=int, default=120, help="audio chunk seconds (default: 120)")
    parser.add_argument("--vision-model", help="Gemini model for frame vision analysis")
    parser.add_argument("--audio-model", help="Gemini model for audio chunk analysis")
    parser.add_argument("--gemini-synthesis-model", "--synthesis-model", dest="gemini_synthesis_model", help="Gemini model for final text synthesis")
    parser.add_argument("--format", choices=("md", "json"), default="md", help="summary stdout format (default: md)")
    parser.add_argument("--json-only", action="store_true", help="write only storyboard.json, not storyboard.md")
    parser.add_argument("--md-only", action="store_true", help="write only storyboard.md, not storyboard.json")
    parser.add_argument("--no-audio", action="store_true", help="skip audio chunk analysis")
    parser.add_argument(
        "--synthesis-provider",
        choices=("auto", "gemini", "openai"),
        default="auto",
        help="text synthesis backend for storyboard JSON (default: auto)",
    )
    args = parser.parse_args()

    if args.frames is None:
        args.frames = 8 if args.mode == "summary" else 24
    if args.json_only and args.md_only:
        raise SystemExit("[deepscene-detail] choose only one of --json-only or --md-only")
    if args.frames < 4:
        raise SystemExit("[deepscene-detail] --frames must be >= 4")
    if not os.environ.get("GOOGLE_AI_KEY"):
        raise SystemExit("[deepscene-detail] GOOGLE_AI_KEY is required")
    for tool in ("ffmpeg", "ffprobe"):
        require_tool(tool)

    if args.vision_model:
        os.environ["DEEPSCENE_GEMINI_VISION_MODEL"] = args.vision_model
    if args.audio_model:
        os.environ["DEEPSCENE_GEMINI_AUDIO_MODEL"] = args.audio_model
    if args.gemini_synthesis_model:
        os.environ["DEEPSCENE_GEMINI_SYNTHESIS_MODEL"] = args.gemini_synthesis_model

    video = resolve_video(args.input, args.cookies)
    duration = video_duration(video)
    if duration <= 0:
        raise SystemExit(f"[deepscene-detail] could not read duration: {video}")
    max_video = os.environ.get("DEEPSCENE_MAX_VIDEO_SECONDS")
    if max_video and max_video.isdigit() and duration > int(max_video):
        raise SystemExit(f"[deepscene-detail] video duration {duration:.0f}s exceeds DEEPSCENE_MAX_VIDEO_SECONDS={max_video}")

    out_dir = make_out_dir(video, args.out)
    label = "deepscene-summary" if args.mode == "summary" else "deepscene-detail"
    print(f"[{label}] artifacts: {out_dir}", file=sys.stderr)
    print(f"[{label}] sampling up to {args.frames} frames ...", file=sys.stderr)
    frames = sample_frames(video, out_dir, args.frames, args.scene_threshold)
    print(f"[{label}] sampled {len(frames)} frames", file=sys.stderr)

    vision_path = out_dir / "vision.md"
    if vision_path.is_file() and vision_path.stat().st_size > 0:
        print(f"[{label}] reusing visual artifact ...", file=sys.stderr)
        vision_md = vision_path.read_text(encoding="utf-8")
    else:
        print(f"[{label}] analyzing visual content ...", file=sys.stderr)
        vision_md = analyze_frames(frames, out_dir)

    if args.no_audio:
        print(f"[{label}] skipping audio chunks (--no-audio)", file=sys.stderr)
        audio_chunks = []
    elif (out_dir / "audio_chunks.json").is_file():
        print(f"[{label}] reusing audio chunk artifact ...", file=sys.stderr)
        audio_chunks = read_json_file(out_dir / "audio_chunks.json", [])
    else:
        print(f"[{label}] analyzing audio chunks ...", file=sys.stderr)
        audio_chunks = analyze_audio_chunks(video, out_dir, args.audio_chunk_sec)

    synthesis_provider = args.synthesis_provider
    if synthesis_provider == "auto":
        synthesis_provider = (
            "openai"
            if os.environ.get("DEEPSCENE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
            else "gemini"
        )
    if args.mode == "summary":
        print(f"[{label}] synthesizing summary ({synthesis_provider}) ...", file=sys.stderr)
        try:
            summary = build_summary_json(args.input, video, duration, frames, vision_md, audio_chunks, synthesis_provider)
        except SystemExit as exc:
            print(f"[{label}] summary synthesis failed, writing fallback summary: {exc}", file=sys.stderr)
            summary = fallback_summary_json(args.input, video, duration, frames, vision_md, audio_chunks, str(exc))
        summary.setdefault("metadata", {})
        summary["metadata"].setdefault("source", args.input)
        summary["metadata"]["video"] = str(video)
        summary["metadata"]["duration_sec"] = round(duration, 3)
        summary["metadata"]["artifact_dir"] = str(out_dir)
        summary["frames"] = frames
        summary["audio_chunks"] = audio_chunks
        json_path = out_dir / "summary.json"
        md_path = out_dir / "summary.md"
        json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        md = markdown_from_summary(summary, out_dir)
        md_path.write_text(md, encoding="utf-8")
        if args.format == "json":
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(md, end="")
        print(f"SUMMARY_MD: {md_path}", file=sys.stderr)
        print(f"SUMMARY_JSON: {json_path}", file=sys.stderr)
        print(f"FRAMES: {out_dir / 'frames'}", file=sys.stderr)
        return 0

    print(f"[deepscene-detail] synthesizing reconstruction storyboard ({synthesis_provider}) ...", file=sys.stderr)
    try:
        storyboard = build_storyboard_json(
            args.input,
            video,
            duration,
            frames,
            vision_md,
            audio_chunks,
            synthesis_provider,
        )
    except SystemExit as exc:
        print(f"[deepscene-detail] synthesis failed, writing fallback storyboard: {exc}", file=sys.stderr)
        storyboard = fallback_storyboard_json(args.input, video, duration, frames, vision_md, audio_chunks, str(exc))
    storyboard.setdefault("metadata", {})
    storyboard["metadata"].setdefault("source", args.input)
    storyboard["metadata"]["video"] = str(video)
    storyboard["metadata"]["duration_sec"] = round(duration, 3)
    storyboard["metadata"]["frames_dir"] = str(out_dir / "frames")
    storyboard["metadata"]["artifact_dir"] = str(out_dir)
    storyboard["frames"] = frames
    storyboard["audio_chunks"] = audio_chunks

    json_path = out_dir / "storyboard.json"
    md_path = out_dir / "storyboard.md"
    if not args.md_only:
        json_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.json_only:
        md_path.write_text(markdown_from_storyboard(storyboard, out_dir, vision_md, audio_chunks), encoding="utf-8")

    if not args.json_only:
        print(f"STORYBOARD_MD: {md_path}")
    if not args.md_only:
        print(f"STORYBOARD_JSON: {json_path}")
    print(f"FRAMES: {out_dir / 'frames'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
