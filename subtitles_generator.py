#!/usr/bin/env python3
"""
Subtitles Generator — single-file portable app.
Transcribes videos with OpenAI Whisper and writes SRT files next to each source video.
"""
from __future__ import annotations

import io
import ipaddress
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, send_file
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))
LAN_ONLY = os.getenv("LAN_ONLY", "true").lower() in ("1", "true", "yes")
# Listen on all interfaces; runtime LAN toggle + network_guard control remote access.
BIND_HOST = os.getenv("BIND_HOST", "0.0.0.0")
DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
USE_CUDA_DEFAULT = os.getenv("USE_CUDA", "true").lower() in ("1", "true", "yes")
# Word-level SRT output is opt-in via UI toggle (always off on app start).

# Cinema-style phrase timing (used by default; word-level SRT is optional)
SUBTITLE_LEAD_IN_SEC = 0.08
SUBTITLE_TRAIL_OUT_SEC = 0.18
SUBTITLE_MIN_GAP_SEC = 0.12
SUBTITLE_MIN_DURATION_SEC = 0.55
SUBTITLE_MAX_DURATION_SEC = 8.0
MODELS_DIR = ROOT / os.getenv("MODELS_DIR", "models")
LOG_DIR = ROOT / os.getenv("LOG_DIR", "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "1000"))
TRANSLATION_BATCH_SIZE = int(os.getenv("TRANSLATION_BATCH_SIZE", "20"))
TRANSLATION_RETRY_COUNT = int(os.getenv("TRANSLATION_RETRY_COUNT", "3"))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "").strip()

LOG_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR = ROOT / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
USER_SETTINGS_FILE = ROOT / "user_settings.json"

# Whisper language map (code -> English name)
WHISPER_LANGUAGES: dict[str, str] = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "as": "Assamese",
    "az": "Azerbaijani", "ba": "Bashkir", "be": "Belarusian", "bg": "Bulgarian",
    "bn": "Bengali", "bo": "Tibetan", "br": "Breton", "bs": "Bosnian",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish",
    "fo": "Faroese", "fr": "French", "gl": "Galician", "gu": "Gujarati",
    "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew", "hi": "Hindi",
    "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian", "hy": "Armenian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese",
    "jw": "Javanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer",
    "kn": "Kannada", "ko": "Korean", "la": "Latin", "lb": "Luxembourgish",
    "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian", "lv": "Latvian",
    "mg": "Malagasy", "mi": "Maori", "mk": "Macedonian", "ml": "Malayalam",
    "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese",
    "my": "Myanmar", "ne": "Nepali", "nl": "Dutch", "nn": "Norsk Nynorsk",
    "no": "Norwegian", "oc": "Occitan", "pa": "Punjabi", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sa": "Sanskrit", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak",
    "sl": "Slovenian", "sn": "Shona", "so": "Somali", "sq": "Albanian",
    "sr": "Serbian", "su": "Sundanese", "sv": "Swedish", "sw": "Swahili",
    "ta": "Tamil", "te": "Telugu", "tg": "Tajik", "th": "Thai",
    "tk": "Turkmen", "tl": "Tagalog", "tr": "Turkish", "tt": "Tatar",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese",
    "yi": "Yiddish", "yo": "Yoruba", "zh": "Chinese", "auto": "Auto-detect",
}

DEEP_TRANSLATOR_MAP = {
    "af": "afrikaans", "ar": "arabic", "bg": "bulgarian", "bn": "bengali",
    "ca": "catalan", "cs": "czech", "da": "danish", "de": "german",
    "el": "greek", "en": "english", "es": "spanish", "et": "estonian",
    "fa": "persian", "fi": "finnish", "fr": "french", "gu": "gujarati",
    "he": "hebrew", "hi": "hindi", "hr": "croatian", "hu": "hungarian",
    "id": "indonesian", "it": "italian", "ja": "japanese", "kn": "kannada",
    "ko": "korean", "lt": "lithuanian", "lv": "latvian", "mk": "macedonian",
    "ml": "malayalam", "mr": "marathi", "ms": "malay", "nl": "dutch",
    "no": "norwegian", "pl": "polish", "pt": "portuguese", "ro": "romanian",
    "ru": "russian", "sk": "slovak", "sl": "slovenian", "sq": "albanian",
    "sv": "swedish", "sw": "swahili", "ta": "tamil", "te": "telugu",
    "th": "thai", "tl": "tagalog", "tr": "turkish", "uk": "ukrainian",
    "ur": "urdu", "vi": "vietnamese", "zh": "chinese (simplified)",
    "zh-cn": "chinese (simplified)", "zh-tw": "chinese (traditional)",
}

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp"}

WHISPER_MODELS: list[dict[str, str]] = [
    {"id": "tiny", "name": "Tiny — fastest, lowest quality"},
    {"id": "tiny.en", "name": "Tiny (English only) — fastest"},
    {"id": "base", "name": "Base — balanced (recommended)"},
    {"id": "base.en", "name": "Base (English only) — balanced"},
    {"id": "small", "name": "Small — better accuracy"},
    {"id": "small.en", "name": "Small (English only)"},
    {"id": "medium", "name": "Medium — high accuracy"},
    {"id": "medium.en", "name": "Medium (English only)"},
    {"id": "large-v3", "name": "Large v3 — best quality (recommended for GPU)"},
    {"id": "large-v2", "name": "Large v2 — excellent quality"},
    {"id": "large", "name": "Large v1 — high quality, slowest"},
    {"id": "turbo", "name": "Turbo — fast (English-focused)"},
]


def detect_cuda() -> tuple[bool, str]:
    try:
        import torch
        if torch.cuda.is_available():
            return True, torch.cuda.get_device_name(0)
    except Exception as exc:
        log.debug("CUDA detection failed: %s", exc)
    return False, ""


CUDA_AVAILABLE, GPU_NAME = detect_cuda()


def get_lan_ips() -> list[str]:
    """All private IPv4 addresses on this machine (real LAN NICs)."""
    import socket
    import psutil

    found: list[str] = []
    seen: set[str] = set()
    for _iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            if ip in seen or ip.startswith("127."):
                continue
            if is_private_client_ip(ip):
                seen.add(ip)
                found.append(ip)
    return found


def get_lan_ip() -> str:
    """Pick the best LAN IP — prefer 192.168.x.x (typical home router)."""
    ips = get_lan_ips()
    if not ips:
        return ""
    for prefix in ("192.168.", "10.", "172."):
        for ip in ips:
            if ip.startswith(prefix):
                return ip
    return ips[0]


def local_app_url() -> str:
    return f"http://127.0.0.1:{PORT}"


def lan_app_url() -> str:
    ip = get_lan_ip()
    return f"http://{ip}:{PORT}" if ip else ""


def is_private_client_ip(addr: str) -> bool:
    """True for localhost and RFC1918 LAN addresses only."""
    if not addr:
        return True
    try:
        ip = ipaddress.ip_address(addr.split("%")[0].strip())
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Logging — intrusive, separated
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("subtitles")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.DEBUG))
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=10_000_000, backupCount=10, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    eh = RotatingFileHandler(
        LOG_DIR / "errors.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    eh.setFormatter(fmt)
    eh.setLevel(logging.ERROR)
    logger.addHandler(eh)

    jh = RotatingFileHandler(
        LOG_DIR / "events.jsonl", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
    )
    jh.setLevel(logging.INFO)
    jh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(jh)

    if DEBUG:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        ch.setLevel(logging.DEBUG)
        logger.addHandler(ch)

    return logger


log = setup_logging()


def log_event(event: str, **data: Any) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data,
    }
    log.info(json.dumps(payload, default=str))


class _SafeStream:
    """Writable stream for tqdm/whisper when pywebview breaks stderr."""

    def write(self, *_: Any) -> int:
        return 0

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


@contextmanager
def safe_stdio():
    """Prevent [Errno 22] when tqdm flushes invalid stderr in worker threads."""
    safe = _SafeStream()
    old_err, old_out = sys.stderr, sys.stdout
    sys.stderr = safe
    sys.stdout = safe
    try:
        yield
    finally:
        sys.stderr = old_err
        sys.stdout = old_out


_main_window: Any = None
_tray_started = False
_last_drag_preview = ""


def set_main_window(window: Any) -> None:
    global _main_window
    _main_window = window


def notify_ui(
    added_count: int,
    *,
    message: str | None = None,
    preview: str | None = None,
    animate: bool = True,
) -> None:
    if not _main_window:
        return
    msg = message or (f"Added {added_count} video(s)" if added_count else "No videos found")
    try:
        js = (
            f"showToast({json.dumps(msg)});"
            f"{'dropZoneSuccess();' if animate and added_count else ''}"
            f"hideDragPreview();"
            "if(typeof refresh==='function') refresh();"
        )
        _main_window.evaluate_js(js)
    except Exception as exc:
        log.debug("UI notify failed: %s", exc)


def collect_video_paths(paths: list[str]) -> list[str]:
    """Expand folders recursively and return unique video file paths."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            continue
        p = Path(raw)
        if p.is_file():
            if p.suffix.lower() in VIDEO_EXTENSIONS:
                resolved = str(p.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    found.append(resolved)
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS:
                    resolved = str(child.resolve())
                    if resolved not in seen:
                        seen.add(resolved)
                        found.append(resolved)
    return found


def extract_drag_preview(event: dict) -> str:
    """Build a short label for files being dragged over the window."""
    dt = event.get("dataTransfer") or event.get("domTransfer") or {}
    files = dt.get("files") or []
    names: list[str] = []
    for f in files:
        name = f.get("name") or f.get("pywebviewFullPath") or f.get("path") or ""
        if name:
            names.append(Path(str(name)).name)
    if not names:
        paths = extract_drop_paths(event)
        names = [Path(p).name for p in paths[:6]]
    if not names:
        return "Drop videos or folders here"
    if len(names) == 1:
        return names[0]
    extra = f" +{len(names) - 3}" if len(names) > 3 else ""
    shown = ", ".join(names[:3])
    return f"{shown}{extra}"


# ---------------------------------------------------------------------------
# FFmpeg helper
# ---------------------------------------------------------------------------
def resolve_ffmpeg() -> str:
    if FFMPEG_PATH and Path(FFMPEG_PATH).exists():
        return FFMPEG_PATH
    local = ROOT / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.exists():
        return str(local)
    local_unix = ROOT / "ffmpeg" / "bin" / "ffmpeg"
    if local_unix.exists():
        return str(local_unix)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError(
        "FFmpeg not found. Run run.bat or install FFmpeg and add to PATH."
    )


def extract_audio(video_path: Path, out_wav: Path) -> None:
    ffmpeg = resolve_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(out_wav),
    ]
    log_event("ffmpeg_start", video=str(video_path), cmd=" ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg stderr: %s", proc.stderr[-2000:])
        raise RuntimeError(f"FFmpeg failed for {video_path.name}")
    log_event("ffmpeg_done", video=str(video_path), wav=str(out_wav))


# ---------------------------------------------------------------------------
# SRT utilities
# ---------------------------------------------------------------------------
def format_srt_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def refine_segments_for_display(segments: list[dict]) -> list[dict]:
    """Tighten phrase cues to speech using word timings — movie-style, not word-by-word."""
    if not segments:
        return segments

    refined: list[dict] = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue

        words = seg.get("words") or []
        valid_words = [
            w for w in words
            if w.get("start") is not None and w.get("end") is not None
        ]
        if valid_words:
            speech_start = min(float(w["start"]) for w in valid_words)
            speech_end = max(float(w["end"]) for w in valid_words)
        else:
            speech_start = float(seg["start"])
            speech_end = float(seg["end"])

        start = max(0.0, speech_start - SUBTITLE_LEAD_IN_SEC)
        end = speech_end + SUBTITLE_TRAIL_OUT_SEC
        duration = end - start
        if duration < SUBTITLE_MIN_DURATION_SEC:
            end = start + SUBTITLE_MIN_DURATION_SEC
        elif duration > SUBTITLE_MAX_DURATION_SEC:
            end = start + SUBTITLE_MAX_DURATION_SEC

        refined.append({"start": start, "end": end, "text": text})

    for i in range(len(refined) - 1):
        cur = refined[i]
        nxt = refined[i + 1]
        latest_end = nxt["start"] - SUBTITLE_MIN_GAP_SEC
        if cur["end"] > latest_end:
            cur["end"] = max(cur["start"] + SUBTITLE_MIN_DURATION_SEC, latest_end)
        if cur["end"] <= cur["start"]:
            cur["end"] = cur["start"] + SUBTITLE_MIN_DURATION_SEC

    return refined


def segments_to_srt(segments: list[dict], word_level: bool = False) -> str:
    if word_level:
        return _segments_to_srt_word_level(segments)
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        start = format_srt_timestamp(float(seg["start"]))
        end = format_srt_timestamp(float(seg["end"]))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _segments_to_srt_word_level(segments: list[dict]) -> str:
    """One SRT cue per word using Whisper word timestamps."""
    lines: list[str] = []
    idx = 1
    for seg in segments:
        words = seg.get("words") or []
        if words:
            for w in words:
                text = str(w.get("word", "")).strip()
                if not text:
                    continue
                start = format_srt_timestamp(float(w["start"]))
                end = format_srt_timestamp(float(w["end"]))
                lines.append(str(idx))
                lines.append(f"{start} --> {end}")
                lines.append(text)
                lines.append("")
                idx += 1
        else:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            start = format_srt_timestamp(float(seg["start"]))
            end = format_srt_timestamp(float(seg["end"]))
            lines.append(str(idx))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")
            idx += 1
    return "\n".join(lines).strip() + "\n"


def output_srt_path(video_path: Path, lang_code: str) -> Path:
    stem = video_path.stem
    return video_path.parent / f"{stem} - ({lang_code}).srt"


def srt_exists_for(video_path: str, lang: str) -> bool:
    p = output_srt_path(Path(video_path), lang)
    try:
        return p.exists() and p.stat().st_size > 5
    except OSError:
        return False


def reconcile_completed_langs(item: VideoItem) -> None:
    """Keep completed_langs in sync with SRT files that actually exist."""
    item.completed_langs = [
        lang for lang in item.target_langs if srt_exists_for(item.path, lang)
    ]


def apply_language_settings(
    source_lang: str | None = None,
    target_langs: list[str] | None = None,
    *,
    video_id: str | None = None,
    all_videos: bool = False,
) -> list[str]:
    """Apply language settings atomically before processing."""
    with state.lock:
        if source_lang is not None:
            state.global_source_lang = source_lang
        if target_langs is not None:
            # Deduplicate while preserving order
            seen: set[str] = set()
            clean: list[str] = []
            for lang in target_langs:
                if lang and lang not in seen:
                    seen.add(lang)
                    clean.append(lang)
            state.global_target_langs = clean
            target_langs = clean

        if all_videos:
            for v in state.videos.values():
                if target_langs is not None:
                    v.target_langs = list(target_langs)
                if source_lang is not None:
                    v.source_lang = source_lang
                reconcile_completed_langs(v)
        elif video_id:
            item = state.videos.get(video_id)
            if item:
                if target_langs is not None:
                    item.target_langs = list(target_langs)
                if source_lang is not None:
                    item.source_lang = source_lang
                reconcile_completed_langs(item)

        return list(state.global_target_langs)


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------
def translate_segments(
    segments: list[dict], source_lang: str, target_lang: str
) -> list[dict]:
    if source_lang == target_lang or (
        source_lang == "auto" and target_lang == "auto"
    ):
        return [dict(s) for s in segments]

    src = DEEP_TRANSLATOR_MAP.get(source_lang, source_lang)
    tgt = DEEP_TRANSLATOR_MAP.get(target_lang, target_lang)
    if src == tgt:
        return [dict(s) for s in segments]

    from deep_translator import GoogleTranslator

    translator = GoogleTranslator(source=src if source_lang != "auto" else "auto", target=tgt)
    out: list[dict] = []
    texts = [str(s.get("text", "")).strip() for s in segments]

    for batch_start in range(0, len(texts), TRANSLATION_BATCH_SIZE):
        batch = texts[batch_start : batch_start + TRANSLATION_BATCH_SIZE]
        translated_batch = _translate_batch(translator, batch)
        for j, seg in enumerate(segments[batch_start : batch_start + len(batch)]):
            new_seg = dict(seg)
            new_seg["text"] = translated_batch[j] if j < len(translated_batch) else seg["text"]
            out.append(new_seg)

    log_event("translation_done", source=source_lang, target=target_lang, segments=len(out))
    return out


def _translate_batch(translator: Any, texts: list[str]) -> list[str]:
    joined = "\n|||SPLIT|||\n".join(texts)
    for attempt in range(TRANSLATION_RETRY_COUNT):
        try:
            result = translator.translate(joined)
            parts = result.split("|||SPLIT|||")
            if len(parts) == len(texts):
                return [p.strip() for p in parts]
            # fallback per-line
            return [translator.translate(t) if t else "" for t in texts]
        except Exception as exc:
            log.warning("Translation attempt %d failed: %s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    return texts


# ---------------------------------------------------------------------------
# Whisper engine (lazy-loaded, reloads on model/device change)
# ---------------------------------------------------------------------------
_whisper_model = None
_whisper_lock = threading.Lock()
_loaded_model_key: tuple[str, str] | None = None


def resolve_device(use_cuda: bool) -> str:
    if use_cuda:
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
    return "cpu"


def unload_whisper_model() -> None:
    global _whisper_model, _loaded_model_key
    if _whisper_model is not None:
        try:
            import torch
            del _whisper_model
            _whisper_model = None
            _loaded_model_key = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception as exc:
            log.warning("Model unload failed: %s", exc)


def get_whisper_model(force_reload: bool = False):
    global _whisper_model, _loaded_model_key
    with _whisper_lock:
        import whisper

        model_name = state.whisper_model
        device = resolve_device(state.use_cuda)
        key = (model_name, device)

        if force_reload or _whisper_model is None or _loaded_model_key != key:
            unload_whisper_model()
            log_event("whisper_load_start", model=model_name, device=device)
            t0 = time.perf_counter()
            _whisper_model = whisper.load_model(
                model_name, device=device, download_root=str(MODELS_DIR)
            )
            _loaded_model_key = key
            state.active_device = device
            log_event(
                "whisper_load_done",
                model=model_name,
                device=device,
                seconds=round(time.perf_counter() - t0, 2),
            )
        return _whisper_model


def _set_progress(
    item: VideoItem | None,
    pct: float,
    msg: str,
) -> None:
    if not item:
        return
    with state.lock:
        item.progress_pct = max(0.0, min(100.0, pct))
        item.progress = msg


def transcribe_video(
    video_path: Path,
    source_lang: str,
    item: VideoItem | None = None,
    *,
    task: str = "transcribe",
    language: str | None = None,
) -> tuple[list[dict], str]:
    cache_dir = LOG_DIR / "audio_cache"
    cache_dir.mkdir(exist_ok=True)
    wav_path = cache_dir / f"{video_path.stem}_{uuid.uuid4().hex[:8]}.wav"

    try:
        _set_progress(item, 5, "Extracting audio…")
        extract_audio(video_path, wav_path)

        _set_progress(item, 12, "Loading Whisper model…")
        model = get_whisper_model()

        lang_arg = language
        if lang_arg is None:
            lang_arg = None if source_lang == "auto" else source_lang

        _set_progress(item, 18, "Transcribing…")

        import tqdm as tqdm_mod

        original_tqdm = tqdm_mod.tqdm

        class _ProgressTqdm(original_tqdm):
            def __init__(self, *args, **kwargs):
                kwargs.setdefault("file", io.StringIO())
                kwargs.setdefault("disable", False)
                super().__init__(*args, **kwargs)

            def update(self, n=1):
                result = super().update(n)
                if item and self.total:
                    pct = 18 + int(62 * min(self.n, self.total) / self.total)
                    _set_progress(item, pct, f"Transcribing… {int(100 * self.n / self.total)}%")
                return result

        tqdm_mod.tqdm = _ProgressTqdm
        try:
            log_event(
                "whisper_transcribe_start",
                video=str(video_path),
                lang=source_lang,
                task=task,
                word_timestamps=True,
            )
            t0 = time.perf_counter()
            with safe_stdio():
                result = model.transcribe(
                    str(wav_path),
                    language=lang_arg,
                    task=task,
                    verbose=False,
                    word_timestamps=True,
                )
        finally:
            tqdm_mod.tqdm = original_tqdm

        detected = result.get("language", source_lang if source_lang != "auto" else "en")
        segments = result.get("segments", [])
        log_event(
            "whisper_transcribe_done",
            video=str(video_path),
            detected_lang=detected,
            segments=len(segments),
            seconds=round(time.perf_counter() - t0, 2),
        )
        _set_progress(item, 82, "Transcription complete")
        return segments, detected
    finally:
        if wav_path.exists():
            try:
                wav_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Queue & state
# ---------------------------------------------------------------------------
@dataclass
class VideoItem:
    id: str
    path: str
    name: str
    source_lang: str = "auto"
    target_langs: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | queued | processing | done | error
    progress: str = ""
    progress_pct: float = 0.0
    error: str = ""
    completed_langs: list[str] = field(default_factory=list)
    process_started_at: float = 0.0
    process_elapsed_sec: float = 0.0


@dataclass
class QueueJob:
    job_id: str
    video_id: str
    video_path: str
    source_lang: str
    target_lang: str


class AppState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.videos: dict[str, VideoItem] = {}
        self.job_queue: queue.Queue[QueueJob | None] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self.processing = False
        self.current_job: QueueJob | None = None
        self._transcription_cache: dict[str, tuple[list[dict], str]] = {}
        self.worker_started = False
        self.global_source_lang: str = "auto"
        self.global_target_langs: list[str] = []
        self.whisper_model: str = WHISPER_MODEL
        self.use_cuda: bool = USE_CUDA_DEFAULT if CUDA_AVAILABLE else False
        self.word_timestamps: bool = False
        self.active_device: str = "cpu"
        self.cuda_available: bool = CUDA_AVAILABLE
        self.gpu_name: str = GPU_NAME
        self.lan_access_enabled: bool = False
        self.favorite_langs: list[str] = []
        self._load_user_settings()

    def to_dict(self) -> dict:
        with self.lock:
            return {
                "videos": [asdict(v) for v in self.videos.values()],
                "processing": self.processing,
                "current_job": asdict(self.current_job) if self.current_job else None,
                "queue_size": self.job_queue.qsize(),
                "global_source_lang": self.global_source_lang,
                "global_target_langs": self.global_target_langs,
                "whisper_model": self.whisper_model,
                "use_cuda": self.use_cuda,
                "word_timestamps": self.word_timestamps,
                "active_device": self.active_device,
                "cuda_available": self.cuda_available,
                "gpu_name": self.gpu_name,
                "lan_access_enabled": self.lan_access_enabled,
                "favorite_langs": list(self.favorite_langs),
            }

    def _load_user_settings(self) -> None:
        if not USER_SETTINGS_FILE.is_file():
            return
        try:
            data = json.loads(USER_SETTINGS_FILE.read_text(encoding="utf-8"))
            self.favorite_langs = list(data.get("favorite_langs") or [])
        except Exception as exc:
            log.debug("user settings load failed: %s", exc)

    def save_user_settings(self) -> None:
        try:
            USER_SETTINGS_FILE.write_text(
                json.dumps({"favorite_langs": self.favorite_langs}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("user settings save failed: %s", exc)


state = AppState()


def enqueue_video_jobs(video_id: str) -> int:
    with state.lock:
        item = state.videos.get(video_id)
        if not item:
            return 0
        if not item.target_langs:
            return 0

        reconcile_completed_langs(item)
        added = 0
        for lang in item.target_langs:
            if srt_exists_for(item.path, lang):
                if lang not in item.completed_langs:
                    item.completed_langs.append(lang)
                continue
            job = QueueJob(
                job_id=str(uuid.uuid4()),
                video_id=video_id,
                video_path=item.path,
                source_lang=item.source_lang,
                target_lang=lang,
            )
            try:
                state.job_queue.put_nowait(job)
                added += 1
            except queue.Full:
                log.error("Queue full, dropped job for %s -> %s", item.name, lang)

        missing = [l for l in item.target_langs if not srt_exists_for(item.path, l)]
        if not missing:
            item.status = "done"
            item.progress = "Complete"
            item.progress_pct = 100.0
        elif added:
            item.status = "queued"
            item.process_started_at = 0.0
            item.process_elapsed_sec = 0.0
        return added


def process_job(job: QueueJob) -> None:
    video_path = Path(job.video_path)
    item = state.videos.get(job.video_id)

    if item:
        with state.lock:
            item.status = "processing"
            item.error = ""
            item.progress_pct = 0.0
            if not item.process_started_at:
                item.process_started_at = time.time()

    cache_key = f"{job.video_path}|{job.source_lang}|{state.whisper_model}"
    whisper_native_output = False

    if cache_key not in state._transcription_cache:
        segments, detected = transcribe_video(
            video_path, job.source_lang, item=item, task="transcribe"
        )
        state._transcription_cache[cache_key] = (segments, detected)
    else:
        segments, detected = state._transcription_cache[cache_key]
        _set_progress(item, 82, "Using cached transcription")

    effective_source = detected if job.source_lang == "auto" else job.source_lang

    _set_progress(item, 85, f"Writing {job.target_lang}…")

    if job.target_lang == effective_source:
        final_segments = segments
        whisper_native_output = True
    elif job.target_lang == "en" and effective_source != "en":
        try:
            segments_en, _ = transcribe_video(
                video_path,
                job.source_lang,
                item=item,
                task="translate",
                language=effective_source,
            )
            final_segments = segments_en
            whisper_native_output = True
        except Exception as exc:
            log.warning("Whisper translate fallback to deep-translator: %s", exc)
            _set_progress(item, 88, f"Translating to {job.target_lang}…")
            final_segments = translate_segments(segments, effective_source, job.target_lang)
    else:
        _set_progress(item, 88, f"Translating to {job.target_lang}…")
        final_segments = translate_segments(segments, effective_source, job.target_lang)

    use_word_level = (
        state.word_timestamps
        and whisper_native_output
        and any(seg.get("words") for seg in final_segments)
    )
    display_segments = final_segments if use_word_level else refine_segments_for_display(final_segments)
    srt_content = segments_to_srt(display_segments, word_level=use_word_level)
    if not srt_content.strip():
        raise RuntimeError(f"Empty subtitles generated for {job.target_lang}")

    out_path = output_srt_path(video_path, job.target_lang)
    out_path.write_text(srt_content, encoding="utf-8")

    if not srt_exists_for(str(video_path), job.target_lang):
        raise RuntimeError(f"SRT file was not written for {job.target_lang}: {out_path}")

    log_event(
        "srt_written",
        video=str(video_path),
        lang=job.target_lang,
        path=str(out_path),
        word_level=use_word_level,
        bytes=len(srt_content.encode("utf-8")),
    )

    if item:
        with state.lock:
            reconcile_completed_langs(item)
            if job.target_lang not in item.completed_langs:
                item.completed_langs.append(job.target_lang)
            missing = [l for l in item.target_langs if not srt_exists_for(item.path, l)]
            if not missing:
                item.status = "done"
                item.progress = "Complete"
                item.progress_pct = 100.0
                if item.process_started_at:
                    item.process_elapsed_sec = max(0.0, time.time() - item.process_started_at)
            else:
                item.status = "queued"
                item.progress_pct = 0.0
                item.progress = f"Waiting ({len(missing)} languages left)"


def worker_loop() -> None:
    log_event("worker_start")
    while True:
        job = state.job_queue.get()
        if job is None:
            break
        try:
            with state.lock:
                state.processing = True
                state.current_job = job
            log_event("job_start", job_id=job.job_id, video=job.video_path, target=job.target_lang)
            with safe_stdio():
                process_job(job)
            log_event("job_done", job_id=job.job_id)
        except Exception as exc:
            log.error("Job failed: %s\n%s", exc, traceback.format_exc())
            item = state.videos.get(job.video_id)
            if item:
                with state.lock:
                    item.status = "error"
                    item.error = str(exc)
                    item.progress = "Failed"
                    item.progress_pct = 0.0
            log_event("job_error", job_id=job.job_id, error=str(exc))
        finally:
            with state.lock:
                state.processing = False
                state.current_job = None
            state.job_queue.task_done()


def ensure_worker() -> None:
    if not state.worker_started:
        state.worker_started = True
        t = threading.Thread(target=worker_loop, name="QueueWorker", daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.before_request
def network_guard() -> Any:
    """LAN toggle + block public internet IPs."""
    client = request.remote_addr or ""
    if client in ("127.0.0.1", "::1"):
        return None
    if not state.lan_access_enabled:
        log_event("blocked_lan_disabled", client=client, path=request.path)
        return jsonify({"error": "LAN access is turned off in the app."}), 403
    if LAN_ONLY and not is_private_client_ip(client):
        log_event("blocked_remote_ip", client=client, path=request.path)
        return jsonify({
            "error": "LAN only — this app accepts connections from your local network only.",
        }), 403
    return None


@app.route("/")
def index() -> str:
    return HTML_PAGE


@app.route("/api/languages")
def api_languages():
    langs = [{"code": k, "name": v} for k, v in sorted(WHISPER_LANGUAGES.items(), key=lambda x: x[1])]
    return jsonify(langs)


@app.route("/api/network")
def api_network():
    ips = get_lan_ips()
    primary = get_lan_ip()
    return jsonify({
        "local_url": local_app_url(),
        "lan_url": lan_app_url() if state.lan_access_enabled and primary else "",
        "lan_ips": ips,
        "lan_ip": primary,
        "lan_access": state.lan_access_enabled,
        "lan_only": LAN_ONLY,
        "port": PORT,
        "is_local_client": request.remote_addr in ("127.0.0.1", "::1"),
        "note": "192.168.x.x is your home LAN — private, not on the public internet.",
    })


@app.route("/api/settings/network", methods=["GET", "PATCH"])
def api_network_settings():
    if request.method == "GET":
        return jsonify({
            "lan_access_enabled": state.lan_access_enabled,
            "lan_url": lan_app_url() if state.lan_access_enabled else "",
            "lan_ips": get_lan_ips(),
        })
    data = request.get_json(force=True) or {}
    if "lan_access_enabled" in data:
        with state.lock:
            state.lan_access_enabled = bool(data["lan_access_enabled"])
        log_event("lan_access_toggled", enabled=state.lan_access_enabled)
    return jsonify({
        "ok": True,
        "lan_access_enabled": state.lan_access_enabled,
        "lan_url": lan_app_url() if state.lan_access_enabled else "",
    })


@app.route("/api/state")
def api_state():
    return jsonify(state.to_dict())


def _add_video_paths(paths: list[str]) -> list[dict]:
    added = []
    expanded = collect_video_paths(paths)
    with state.lock:
        for resolved in expanded:
            p = Path(resolved)
            if any(v.path == resolved for v in state.videos.values()):
                log.info("Skipped duplicate: %s", resolved)
                continue
            vid = str(uuid.uuid4())
            item = VideoItem(id=vid, path=resolved, name=p.name)
            if state.global_source_lang:
                item.source_lang = state.global_source_lang
            if state.global_target_langs:
                item.target_langs = list(state.global_target_langs)
            state.videos[vid] = item
            added.append(asdict(item))
            log_event("video_added", id=vid, path=resolved)
    return added


@app.route("/api/videos", methods=["POST"])
def api_add_videos():
    data = request.get_json(force=True) or {}
    paths = data.get("paths", [])
    added = _add_video_paths(paths)
    return jsonify({"added": added})


@app.route("/api/pick-files", methods=["POST"])
def api_pick_files():
    """Native file dialog on the PC only — remote devices must upload."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({
            "error": "Remote device — use Browse to upload from this device.",
            "added": [],
            "count": 0,
        }), 400
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    paths = filedialog.askopenfilenames(
        title="Select video files",
        filetypes=[
            ("Video files", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm *.m4v *.mpg *.mpeg *.3gp"),
            ("All files", "*.*"),
        ],
    )
    root.destroy()
    added = _add_video_paths(list(paths))
    return jsonify({"added": added, "paths": list(paths), "count": len(added)})


@app.route("/api/pick-folder", methods=["POST"])
def api_pick_folder():
    """Native folder dialog on the PC only."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "Remote device — upload files instead.", "added": [], "count": 0}), 400
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title="Select folder with videos")
    root.destroy()
    if not folder:
        return jsonify({"added": [], "count": 0})
    added = _add_video_paths([folder])
    return jsonify({"added": added, "count": len(added), "folder": folder})


@app.route("/api/upload-videos", methods=["POST"])
def api_upload_videos():
    """Browser upload from phone/remote device — files land in uploads/."""
    if not request.files:
        return jsonify({"added": [], "count": 0, "error": "No files"}), 400
    saved_paths: list[str] = []
    for key in request.files:
        f = request.files[key]
        if not f or not f.filename:
            continue
        safe = secure_filename(Path(f.filename).name)
        if not safe:
            continue
        ext = Path(safe).suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            continue
        dest = UPLOADS_DIR / safe
        if dest.exists():
            dest = UPLOADS_DIR / f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}"
        f.save(dest)
        saved_paths.append(str(dest.resolve()))
    added = _add_video_paths(saved_paths)
    log_event("upload_videos", count=len(added), client=request.remote_addr)
    return jsonify({"added": added, "count": len(added)})


@app.route("/api/download-srt")
def api_download_srt():
    video = request.args.get("video", "")
    lang = request.args.get("lang", "")
    if not video or not lang:
        return jsonify({"error": "missing params"}), 400
    with state.lock:
        allowed = any(v.path == video for v in state.videos.values())
    if not allowed:
        return jsonify({"error": "not found"}), 404
    srt = output_srt_path(Path(video), lang)
    if not srt.is_file():
        return jsonify({"error": "srt not ready"}), 404
    return send_file(srt, as_attachment=True, download_name=srt.name)


@app.route("/api/models")
def api_models():
    return jsonify(WHISPER_MODELS)


@app.route("/api/settings/engine", methods=["GET", "PATCH"])
def api_engine_settings():
    if request.method == "GET":
        with state.lock:
            return jsonify({
                "whisper_model": state.whisper_model,
                "use_cuda": state.use_cuda,
                "word_timestamps": state.word_timestamps,
                "active_device": state.active_device,
                "cuda_available": state.cuda_available,
                "gpu_name": state.gpu_name,
            })
    data = request.get_json(force=True) or {}
    reload_needed = False
    with state.lock:
        if "whisper_model" in data and data["whisper_model"] != state.whisper_model:
            state.whisper_model = data["whisper_model"]
            reload_needed = True
            state._transcription_cache.clear()
        if "use_cuda" in data:
            new_cuda = bool(data["use_cuda"])
            if new_cuda != state.use_cuda:
                state.use_cuda = new_cuda
                reload_needed = True
        if "word_timestamps" in data:
            state.word_timestamps = bool(data["word_timestamps"])
    if reload_needed:
        unload_whisper_model()
    log_event(
        "engine_settings_updated",
        model=state.whisper_model,
        use_cuda=state.use_cuda,
        device=state.active_device,
    )
    return jsonify({"ok": True, "reload_needed": reload_needed})


@app.route("/api/settings/global", methods=["GET", "PATCH"])
def api_global_settings():
    if request.method == "GET":
        with state.lock:
            return jsonify({
                "source_lang": state.global_source_lang,
                "target_langs": state.global_target_langs,
                "favorite_langs": list(state.favorite_langs),
            })
    data = request.get_json(force=True) or {}
    with state.lock:
        if "source_lang" in data:
            state.global_source_lang = data["source_lang"]
        if "target_langs" in data:
            state.global_target_langs = list(data["target_langs"])
            for v in state.videos.values():
                v.target_langs = list(state.global_target_langs)
                reconcile_completed_langs(v)
        if "source_lang" in data:
            for v in state.videos.values():
                v.source_lang = state.global_source_lang
        if "favorite_langs" in data:
            state.favorite_langs = list(dict.fromkeys(data["favorite_langs"]))
            state.save_user_settings()
    log_event("global_settings_updated", source=state.global_source_lang, targets=state.global_target_langs)
    return jsonify({"ok": True, "favorite_langs": state.favorite_langs})


@app.route("/api/videos/<video_id>/open-folder", methods=["POST"])
def api_open_folder(video_id: str):
    """Open the video's folder in the system file manager (PC only)."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "Open folder is only available on the PC app."}), 400
    with state.lock:
        item = state.videos.get(video_id)
    if not item:
        return jsonify({"error": "not found"}), 404
    folder = str(Path(item.path).resolve().parent)
    if not Path(folder).is_dir():
        return jsonify({"error": "folder not found"}), 404
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["open", folder], check=False)
        else:
            import subprocess
            subprocess.run(["xdg-open", folder], check=False)
        log_event("open_folder", video_id=video_id, folder=folder)
        return jsonify({"ok": True, "folder": folder})
    except Exception as exc:
        log.error("open_folder failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/videos/<video_id>", methods=["DELETE"])
def api_remove_video(video_id: str):
    with state.lock:
        state.videos.pop(video_id, None)
    log_event("video_removed", id=video_id)
    return jsonify({"ok": True})


@app.route("/api/videos/<video_id>/settings", methods=["PATCH"])
def api_update_settings(video_id: str):
    data = request.get_json(force=True) or {}
    with state.lock:
        item = state.videos.get(video_id)
        if not item:
            return jsonify({"error": "not found"}), 404
        if "source_lang" in data:
            item.source_lang = data["source_lang"]
        if "target_langs" in data:
            item.target_langs = list(data["target_langs"])
    return jsonify({"ok": True})


@app.route("/api/process-all", methods=["POST"])
def api_process_all():
    ensure_worker()
    data = request.get_json(force=True) or {}
    targets = apply_language_settings(
        data.get("source_lang"),
        data.get("target_langs"),
        all_videos=True,
    )
    if not targets:
        return jsonify({"jobs_added": 0, "error": "No target languages selected"}), 400
    total = 0
    with state.lock:
        ids = list(state.videos.keys())
    for vid in ids:
        total += enqueue_video_jobs(vid)
    log_event("process_all", jobs_added=total, targets=targets)
    return jsonify({"jobs_added": total, "targets": targets})


@app.route("/api/videos/<video_id>/process", methods=["POST"])
def api_process_one(video_id: str):
    ensure_worker()
    data = request.get_json(force=True) or {}
    apply_language_settings(
        data.get("source_lang"),
        data.get("target_langs"),
        video_id=video_id,
    )
    with state.lock:
        item = state.videos.get(video_id)
        if not item:
            return jsonify({"error": "not found"}), 404
        targets = list(item.target_langs)
    if not targets:
        return jsonify({"jobs_added": 0, "error": "No target languages selected"}), 400
    added = enqueue_video_jobs(video_id)
    log_event("process_one", video_id=video_id, jobs_added=added, targets=targets)
    return jsonify({"jobs_added": added, "targets": targets})


@app.route("/logs/<path:filename>")
def serve_logs(filename: str):
    return send_from_directory(LOG_DIR, filename)


# ---------------------------------------------------------------------------
# Embedded UI (glassmorphism, responsive, driver.js tour)
# ---------------------------------------------------------------------------
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Subtitles Generator</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/driver.js@1.3.1/dist/driver.css"/>
<style>
:root {
  --bg: #0f1117;
  --glass: rgba(255,255,255,0.06);
  --glass-border: rgba(255,255,255,0.12);
  --accent: #e91e8c;
  --accent-soft: #f8b4d9;
  --text: #e8e6e3;
  --muted: #9a98a4;
  --success: #4ade80;
  --warn: #fbbf24;
  --error: #f87171;
  --radius: 16px;
  --font: "Segoe UI", system-ui, sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font);
  background: linear-gradient(145deg, #0d0d12 0%, #1a1625 50%, #12101a 100%);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 1rem 1.5rem; flex-wrap: wrap; gap: .75rem;
  border-bottom: 1px solid var(--glass-border);
  background: var(--glass); backdrop-filter: blur(20px);
}
header h1 { font-size: 1.35rem; font-weight: 700; letter-spacing: -.02em; }
header h1 span { color: var(--accent); }
.toolbar { display: flex; gap: .5rem; flex-wrap: wrap; }
.btn {
  padding: .5rem 1rem; border-radius: 10px; border: 1px solid var(--glass-border);
  background: var(--glass); color: var(--text); cursor: pointer; font-size: .85rem;
  transition: all .2s; backdrop-filter: blur(8px);
}
.btn:hover { border-color: var(--accent); background: rgba(233,30,140,.15); }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
.btn-primary:hover { background: #c4187a; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-howto { position: relative; }
.btn-howto.pulse::after {
  content: ""; position: absolute; inset: -3px; border-radius: 12px;
  border: 2px solid var(--accent); animation: pulse 2s ease infinite; pointer-events: none;
}
@keyframes pulse { 0%,100%{opacity:.3;transform:scale(1)} 50%{opacity:1;transform:scale(1.04)} }
main {
  display: grid; grid-template-columns: 1fr 320px; gap: 1rem;
  padding: 1rem 1.5rem 2rem; max-width: 1400px; margin: 0 auto;
}
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
.panel {
  background: var(--glass); border: 1px solid var(--glass-border);
  border-radius: var(--radius); backdrop-filter: blur(24px); padding: 1rem;
}
.drop-zone {
  border: 2px dashed var(--glass-border); border-radius: var(--radius);
  padding: 2.5rem 1rem; text-align: center; cursor: pointer;
  transition: border-color .2s, background .2s, transform .25s; margin-bottom: 1rem;
  position: relative; overflow: hidden;
}
.drop-zone.dragover {
  border-color: var(--accent); background: rgba(233,30,140,.08);
  animation: dropHover 1.2s ease infinite; transform: scale(1.01);
}
.drop-zone.success-pulse { animation: dropSuccess .7s ease; }
.drop-zone .drag-preview {
  display: none; margin-top: .75rem; padding: .5rem .75rem;
  background: rgba(233,30,140,.15); border-radius: 10px;
  font-size: .82rem; color: var(--accent-soft); word-break: break-all;
}
.drop-zone.dragover .drag-preview { display: block; }
@keyframes dropHover {
  0%,100% { box-shadow: 0 0 0 0 rgba(233,30,140,.25); }
  50% { box-shadow: 0 0 24px 4px rgba(233,30,140,.18); }
}
@keyframes dropSuccess {
  0% { transform: scale(1); background: rgba(233,30,140,.08); }
  40% { transform: scale(1.03); background: rgba(74,222,128,.15); border-color: var(--success); }
  100% { transform: scale(1); }
}
.drop-actions { display: flex; gap: .5rem; justify-content: center; margin-top: .75rem; flex-wrap: wrap; }
.drop-actions .btn { font-size: .78rem; padding: .35rem .7rem; }
#toastHost {
  position: fixed; top: 1rem; right: 1rem; z-index: 9999;
  display: flex; flex-direction: column; gap: .5rem; pointer-events: none;
}
.toast {
  padding: .7rem 1rem; border-radius: 12px; font-size: .85rem; font-weight: 600;
  background: rgba(30,30,35,.92); border: 1px solid var(--glass-border);
  color: var(--text); box-shadow: 0 8px 32px rgba(0,0,0,.35);
  animation: toastIn .35s ease, toastOut .35s ease 2.6s forwards;
  backdrop-filter: blur(12px);
}
.toast.success { border-color: rgba(74,222,128,.45); color: var(--success); }
.toast.warn { border-color: rgba(251,191,36,.45); color: var(--warn); }
@keyframes toastIn { from { opacity:0; transform:translateY(-8px); } to { opacity:1; transform:none; } }
@keyframes toastOut { to { opacity:0; transform:translateY(-8px); } }
.drop-zone p { color: var(--muted); font-size: .9rem; margin-top: .5rem; }
.drop-zone strong { color: var(--accent-soft); }
.video-list { display: flex; flex-direction: column; gap: .6rem; max-height: 60vh; overflow-y: auto; }
.video-card {
  background: rgba(0,0,0,.25); border: 1px solid var(--glass-border);
  border-radius: 12px; padding: .75rem; display: grid; gap: .4rem;
}
.video-card .name { font-weight: 600; font-size: .9rem; word-break: break-all; }
.video-card .meta { font-size: .75rem; color: var(--muted); }
.status-badge {
  display: inline-block; padding: .15rem .5rem; border-radius: 6px;
  font-size: .7rem; font-weight: 600; text-transform: uppercase;
}
.status-pending { background: rgba(154,152,164,.2); color: var(--muted); }
.status-queued { background: rgba(251,191,36,.2); color: var(--warn); }
.status-processing { background: rgba(233,30,140,.2); color: var(--accent-soft); }
.status-done { background: rgba(74,222,128,.2); color: var(--success); }
.status-error { background: rgba(248,113,113,.2); color: var(--error); }
.card-actions { display: flex; gap: .4rem; flex-wrap: wrap; margin-top: .3rem; }
.card-actions .btn { padding: .3rem .6rem; font-size: .75rem; }
.sidebar h2 { font-size: .95rem; margin-bottom: .75rem; color: var(--accent-soft); }
.field { margin-bottom: 1rem; }
.field label { display: block; font-size: .8rem; color: var(--muted); margin-bottom: .35rem; }
select {
  width: 100%; padding: .55rem; border-radius: 10px;
  border: 1px solid var(--glass-border); background: rgba(0,0,0,.3);
  color: var(--text); font-size: .85rem;
}
.lang-checklist {
  max-height: 220px; overflow-y: auto; border: 1px solid var(--glass-border);
  border-radius: 10px; padding: .5rem; display: none;
}
.lang-checklist.visible { display: block; }
.lang-checklist label {
  display: flex; align-items: center; gap: .5rem; padding: .3rem .4rem;
  font-size: .8rem; cursor: pointer; border-radius: 6px;
}
.lang-checklist label:hover { background: rgba(255,255,255,.05); }
.lang-row {
  display: flex; align-items: center; gap: .35rem; padding: .3rem .4rem;
  font-size: .8rem; cursor: pointer; border-radius: 6px;
}
.lang-row:hover { background: rgba(255,255,255,.05); }
.lang-row input[type=checkbox] { flex-shrink: 0; }
.lang-row span { flex: 1; }
.lang-star {
  background: none; border: none; cursor: pointer; font-size: .95rem;
  color: var(--muted); padding: 0 .1rem; line-height: 1; flex-shrink: 0;
}
.lang-star:hover, .lang-star.starred { color: #fbbf24; }
.lang-star.starred { text-shadow: 0 0 8px rgba(251,191,36,.4); }
.lan-panel { margin-top: .35rem; }
.lan-panel-head { display: flex; align-items: center; gap: .35rem; }
.lan-toggle-btn {
  flex: 1; text-align: left; font-size: .75rem; padding: .4rem .55rem;
  color: var(--muted);
}
.lan-toggle-btn:hover { color: var(--accent-soft); }
.btn-icon {
  padding: .35rem .5rem; border-radius: 8px; border: 1px solid var(--glass-border);
  background: var(--glass); color: var(--text); cursor: pointer; font-size: .85rem;
  line-height: 1;
}
.btn-icon:hover { border-color: var(--accent); color: var(--accent-soft); }
.lan-links-body { margin-top: .35rem; padding: .45rem .5rem; border-radius: 8px; background: rgba(0,0,0,.2); }
.lan-links-body a { color: var(--accent-soft); font-size: .75rem; word-break: break-all; }
.lan-privacy { font-size: .7rem; color: var(--success); margin-top: .35rem; }
.modal {
  position: fixed; inset: 0; z-index: 10000; background: rgba(0,0,0,.55);
  display: flex; align-items: center; justify-content: center; padding: 1rem;
}
.modal[hidden] { display: none !important; }
.modal-card {
  background: rgba(22, 18, 32, 0.97); border: 1px solid var(--glass-border);
  border-radius: 14px; padding: 1.25rem; text-align: center; max-width: 280px; position: relative;
}
.modal-close {
  position: absolute; top: .5rem; right: .65rem; background: none; border: none;
  color: var(--muted); font-size: 1.4rem; cursor: pointer;
}
.modal-card img { width: 200px; height: 200px; border-radius: 8px; margin: .75rem 0; }
.queue-info { font-size: .75rem; color: var(--muted); margin-top: .75rem; }
.device-badge {
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .25rem .6rem; border-radius: 8px; font-size: .72rem; font-weight: 600;
  background: rgba(74,222,128,.15); color: var(--success); border: 1px solid rgba(74,222,128,.3);
}
.device-badge.cpu { background: rgba(251,191,36,.12); color: var(--warn); border-color: rgba(251,191,36,.3); }
.toggle-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: .45rem 0; font-size: .82rem;
}
.toggle-row input[type=checkbox] { width: 1rem; height: 1rem; accent-color: var(--accent); cursor: pointer; }
.progress-wrap { margin-top: .35rem; }
.progress-bar {
  height: 6px; border-radius: 4px; background: rgba(255,255,255,.08); overflow: hidden;
}
.progress-fill {
  height: 100%; border-radius: 4px; background: linear-gradient(90deg, var(--accent), var(--accent-soft));
  transition: width .4s ease;
}
.progress-label { font-size: .7rem; color: var(--muted); margin-top: .2rem; }
.sidebar h3 { font-size: .82rem; color: var(--muted); margin: 1rem 0 .5rem; text-transform: uppercase; letter-spacing: .06em; }
footer {
  text-align: center; padding: 1rem; font-size: .75rem; color: var(--muted);
}
.driver-popover.driverjs-theme {
  background: rgba(22, 18, 32, 0.95);
  border: 1px solid rgba(233, 30, 140, 0.35);
  color: var(--text);
  border-radius: 14px;
  box-shadow: 0 12px 40px rgba(0,0,0,.45);
  max-width: min(92vw, 380px);
}
.driver-popover.driverjs-theme .driver-popover-title {
  font-size: 1rem; font-weight: 700; color: var(--accent-soft);
}
.driver-popover.driverjs-theme .driver-popover-description {
  font-size: .82rem; line-height: 1.55; color: var(--muted);
}
.driver-popover.driverjs-theme .driver-popover-description code {
  background: rgba(255,255,255,.08); padding: .1em .35em; border-radius: 4px; font-size: .78rem;
}
.driver-popover.driverjs-theme .driver-popover-progress-text {
  font-size: .72rem; color: var(--accent-soft);
}
.driver-popover.driverjs-theme button {
  background: rgba(233, 30, 140, 0.2); border: 1px solid rgba(233, 30, 140, 0.4);
  color: var(--accent-soft); border-radius: 8px; text-shadow: none;
}
.driver-popover.driverjs-theme button:hover { background: rgba(233, 30, 140, 0.35); }
@media (max-width: 640px) {
  .driver-popover.driverjs-theme { max-width: 94vw; }
  .main { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div id="toastHost"></div>
<header>
  <h1><span>Subtitles</span> Generator</h1>
  <div class="toolbar">
    <button class="btn btn-howto" id="btnHowTo" type="button">How to Use</button>
  </div>
</header>
<main>
  <section class="panel" id="panelVideos">
    <div class="drop-zone" id="dropZone">
      <div style="font-size:2rem">🎬</div>
      <strong>Drag & drop videos or folders</strong>
      <p>Drop anywhere on this window · MP4, MKV, AVI, MOV, WebM…</p>
      <div class="drag-preview" id="dragPreview">Drop videos or folders here</div>
      <div class="drop-actions">
        <button class="btn" type="button" id="btnBrowse">Browse files</button>
        <button class="btn" type="button" id="btnBrowseFolder">Browse folder</button>
      </div>
      <input type="file" id="fileUploadInput" accept="video/*" multiple hidden>
    </div>
    <div class="video-list" id="videoList"></div>
  </section>
  <aside class="panel sidebar" id="panelSettings">
    <h2>Settings</h2>
    <div id="deviceBadge" class="device-badge cpu">Device: CPU</div>

    <h3>Languages</h3>
    <p style="font-size:.8rem;color:var(--muted);margin-bottom:1rem">
      Defaults for all videos. Select a video to override. ☆ = favorite (pinned to top).
    </p>
    <div class="field">
      <label for="sourceLang">Source language (spoken in video)</label>
      <select id="sourceLang"></select>
    </div>
    <div class="field" id="targetField" style="display:none">
      <label>Subtitle languages (check all you need) <span id="targetCount" style="color:var(--accent-soft)"></span></label>
      <div class="lang-checklist" id="targetLangs"></div>
    </div>
    <button class="btn btn-primary" id="btnProcessAll" style="width:100%;margin-top:.5rem" type="button">
      Process All
    </button>
    <div class="queue-info" id="queueInfo">Queue idle</div>

    <h3>Engine</h3>
    <div class="field">
      <label for="whisperModel">Whisper model</label>
      <select id="whisperModel"></select>
    </div>
    <div class="toggle-row">
      <label for="useCuda">Use CUDA / GPU (faster)</label>
      <input type="checkbox" id="useCuda" checked>
    </div>
    <div class="toggle-row">
      <label for="wordTimestamps">Word-level timestamps</label>
      <input type="checkbox" id="wordTimestamps">
    </div>

    <h3>Network</h3>
    <div class="toggle-row">
      <label for="lanAccess">Allow LAN access (phone/tablet)</label>
      <input type="checkbox" id="lanAccess">
    </div>
    <div id="lanPanel" class="lan-panel" style="display:none">
      <div class="lan-panel-head">
        <button type="button" class="btn lan-toggle-btn" id="btnLanToggle">
          📱 Same Wi‑Fi — open on phone/tablet <span id="lanChevron">▸</span>
        </button>
        <button type="button" class="btn-icon" id="btnLanQr" title="Show QR code" aria-label="Show QR code">▣</button>
      </div>
      <div id="lanLinksBody" class="lan-links-body" hidden>
        <div id="lanLinksList"></div>
        <div class="lan-privacy">🔒 Private LAN only — not on the public internet</div>
      </div>
    </div>
    <div class="queue-info" id="lanOffMsg" style="display:none;margin-top:.35rem"></div>
  </aside>
</main>
<div id="qrModal" class="modal" hidden>
  <div class="modal-card">
    <button type="button" class="modal-close" id="qrClose" aria-label="Close">×</button>
    <strong style="font-size:.9rem">Scan to open on phone</strong>
    <img id="qrImage" alt="QR code" width="200" height="200">
    <p id="qrUrl" class="meta" style="font-size:.72rem;word-break:break-all"></p>
  </div>
</div>
<footer>Powered by OpenAI Whisper · SRT saved next to each video as <code>Name - (lang).srt</code></footer>

<script src="https://cdn.jsdelivr.net/npm/driver.js@1.3.1/dist/driver.js.iife.js"></script>
<script>
const $ = id => document.getElementById(id);
let languages = [];
let selectedVideoId = null;
let pollTimer = null;
let isLocalClient = true;
let useServerPathDrop = false;
let favoriteLangs = [];
let primaryLanUrl = '';

async function api(path, opts={}) {
  const r = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts});
  return r.json();
}

async function uploadFiles(fileList) {
  const files = [...(fileList || [])].filter(f => f && f.size >= 0);
  if (!files.length) return;
  showToast(`Uploading ${files.length} file${files.length === 1 ? '' : 's'}…`);
  const fd = new FormData();
  files.forEach((f, i) => fd.append('file' + i, f, f.name));
  const r = await fetch('/api/upload-videos', {method:'POST', body: fd});
  const data = await r.json();
  if (!r.ok) {
    showToast(data.error || 'Upload failed', 'warn');
    return;
  }
  await handleAddedResponse(data, 'No valid video files in upload');
}

function showToast(message, type='success') {
  const host = $('toastHost');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function showDragPreview(text) {
  const el = $('dragPreview');
  if (!el) return;
  el.textContent = text || 'Drop videos or folders here';
  $('dropZone').classList.add('dragover');
}

function hideDragPreview() {
  $('dropZone')?.classList.remove('dragover');
  const el = $('dragPreview');
  if (el) el.textContent = 'Drop videos or folders here';
}

function dropZoneSuccess() {
  const dz = $('dropZone');
  dz.classList.add('success-pulse');
  setTimeout(() => dz.classList.remove('success-pulse'), 800);
}

async function handleAddedResponse(data, fallbackMsg) {
  const n = data?.count ?? (data?.added?.length || 0);
  if (n > 0) {
    showToast(`Added ${n} video${n === 1 ? '' : 's'}`);
    dropZoneSuccess();
  } else if (fallbackMsg) {
    showToast(fallbackMsg, 'warn');
  }
  await refresh();
}

async function loadLanguages() {
  languages = await api('/api/languages');
  const src = $('sourceLang');
  src.innerHTML = languages.map(l => `<option value="${l.code}">${l.name}</option>`).join('');
  buildTargetChecklist();
  const [g, engine] = await Promise.all([api('/api/settings/global'), api('/api/settings/engine')]);
  favoriteLangs = g.favorite_langs || [];
  buildTargetChecklist();
  $('sourceLang').value = g.source_lang || 'auto';
  $('targetField').style.display = 'block';
  $('targetLangs').classList.add('visible');
  document.querySelectorAll('#targetLangs input').forEach(cb => {
    cb.checked = (g.target_langs || []).includes(cb.value);
  });
  updateTargetCount();
  const models = await api('/api/models');
  $('whisperModel').innerHTML = models.map(m => `<option value="${m.id}">${m.name}</option>`).join('');
  $('whisperModel').value = engine.whisper_model || 'base';
  $('useCuda').checked = engine.use_cuda;
  $('useCuda').disabled = !engine.cuda_available;
  $('wordTimestamps').checked = engine.word_timestamps;
  updateDeviceBadge(engine);
}

function updateDeviceBadge(engine) {
  const el = $('deviceBadge');
  if (engine.cuda_available && engine.use_cuda) {
    el.className = 'device-badge';
    el.textContent = `GPU: ${engine.gpu_name || 'CUDA'} (${engine.active_device})`;
  } else if (engine.cuda_available) {
    el.className = 'device-badge cpu';
    el.textContent = `GPU available (${engine.gpu_name}) — CPU mode`;
  } else {
    el.className = 'device-badge cpu';
    el.textContent = 'CPU mode (no CUDA detected)';
  }
}

async function saveEngineSettings() {
  const engine = await api('/api/settings/engine', {
    method:'PATCH',
    body: JSON.stringify({
      whisper_model: $('whisperModel').value,
      use_cuda: $('useCuda').checked,
      word_timestamps: $('wordTimestamps').checked,
    })
  });
  updateDeviceBadge(await api('/api/settings/engine'));
  return engine;
}

$('whisperModel').addEventListener('change', saveEngineSettings);
$('useCuda').addEventListener('change', saveEngineSettings);
$('wordTimestamps').addEventListener('change', saveEngineSettings);

function buildTargetChecklist() {
  const box = $('targetLangs');
  const checked = new Set(getCheckedTargets());
  const favSet = new Set(favoriteLangs);
  const sorted = languages.filter(l => l.code !== 'auto').sort((a, b) => {
    const af = favSet.has(a.code), bf = favSet.has(b.code);
    if (af && !bf) return -1;
    if (!af && bf) return 1;
    return a.name.localeCompare(b.name);
  });
  box.innerHTML = sorted.map(l => {
    const starred = favSet.has(l.code);
    const on = checked.has(l.code);
    return `<label class="lang-row" data-code="${l.code}">
      <button type="button" class="lang-star${starred ? ' starred' : ''}" data-code="${l.code}" aria-label="Favorite">${starred ? '★' : '☆'}</button>
      <input type="checkbox" value="${l.code}"${on ? ' checked' : ''}>
      <span>${esc(l.name)} (${l.code})</span>
    </label>`;
  }).join('');
  box.querySelectorAll('.lang-star').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      toggleFavorite(btn.dataset.code);
    });
  });
}

async function toggleFavorite(code) {
  const set = new Set(favoriteLangs);
  if (set.has(code)) set.delete(code);
  else set.add(code);
  favoriteLangs = [...set];
  const res = await api('/api/settings/global', {
    method: 'PATCH',
    body: JSON.stringify({ favorite_langs: favoriteLangs }),
  });
  favoriteLangs = res.favorite_langs || favoriteLangs;
  buildTargetChecklist();
}

function formatDuration(sec) {
  if (!sec || sec <= 0) return '';
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

function renderVideos(videos) {
  const list = $('videoList');
  if (!videos.length) {
    list.innerHTML = '<p style="color:var(--muted);text-align:center;padding:2rem">No videos yet. Drop files above.</p>';
    return;
  }
  list.innerHTML = videos.map(v => {
    const elapsed = v.status === 'done' && v.process_elapsed_sec
      ? ` · ${formatDuration(v.process_elapsed_sec)}`
      : (v.status === 'processing' && v.process_started_at
        ? ` · ${formatDuration(Date.now() / 1000 - v.process_started_at)}…`
        : '');
    const progressLine = v.progress
      ? `${esc(v.progress)}${elapsed}`
      : (elapsed ? elapsed.slice(3) : '');
    return `
    <div class="video-card" data-id="${v.id}" style="outline:${v.id===selectedVideoId?'2px solid var(--accent)':'none'}">
      <div class="name">${esc(v.name)}</div>
      <div class="meta">${esc(v.path)}</div>
      <div>
        <span class="status-badge status-${v.status}">${v.status}</span>
        ${progressLine ? `<span class="meta"> · ${progressLine}</span>` : ''}
      </div>
      ${v.error ? `<div style="color:var(--error);font-size:.75rem">${esc(v.error)}</div>` : ''}
      <div class="meta">Targets: ${v.target_langs.length ? v.target_langs.join(', ') : 'none'} · Done: ${v.completed_langs.join(', ') || '—'}</div>
      <div class="progress-wrap">
        <div class="progress-bar"><div class="progress-fill" style="width:${v.progress_pct||0}%"></div></div>
        <div class="progress-label">${v.progress_pct ? Math.round(v.progress_pct)+'%' : ''} ${v.progress ? '· '+esc(v.progress) : ''}</div>
      </div>
      <div class="card-actions">
        <button class="btn" onclick="selectVideo('${v.id}')">Select</button>
        <button class="btn btn-primary" onclick="processOne('${v.id}')">Process</button>
        ${isLocalClient ? `<button class="btn" onclick="openFolder('${v.id}')">Open folder</button>` : ''}
        <button class="btn" onclick="removeVideo('${v.id}')">Remove</button>
        ${v.completed_langs.length ? v.completed_langs.map(l =>
          `<a class="btn" href="/api/download-srt?video=${encodeURIComponent(v.path)}&lang=${encodeURIComponent(l)}" download>↓ ${l}.srt</a>`
        ).join('') : ''}
      </div>
    </div>`;
  }).join('');
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function refresh() {
  const data = await api('/api/state');
  renderVideos(data.videos);
  updateDeviceBadge(data);
  const q = data.queue_size;
  const proc = data.processing;
  $('queueInfo').textContent = proc
    ? `Processing… (${q} in queue)`
    : (q ? `${q} job(s) queued` : 'Queue idle');
  $('btnProcessAll').disabled = !data.videos.length;
}

async function addPaths(paths) {
  if (!paths.length) return;
  await api('/api/videos', {method:'POST', body: JSON.stringify({paths})});
  await refresh();
}

window.selectVideo = async function(id) {
  selectedVideoId = id;
  const data = await api('/api/state');
  const v = data.videos.find(x => x.id === id);
  if (!v) return;
  $('sourceLang').value = v.source_lang || 'auto';
  $('targetField').style.display = 'block';
  $('targetLangs').classList.add('visible');
  document.querySelectorAll('#targetLangs input').forEach(cb => {
    cb.checked = v.target_langs.includes(cb.value);
  });
  updateTargetCount();
  renderVideos(data.videos);
};

function getCheckedTargets() {
  return [...document.querySelectorAll('#targetLangs input:checked')].map(cb => cb.value);
}

function updateTargetCount() {
  const n = getCheckedTargets().length;
  const el = $('targetCount');
  if (el) el.textContent = n ? `(${n} selected)` : '(none — check languages below)';
}

async function getProcessPayload() {
  const target_langs = getCheckedTargets();
  if (!target_langs.length) {
    showToast('Select at least one target language', 'warn');
    return null;
  }
  return { source_lang: $('sourceLang').value, target_langs };
}

async function saveGlobalSettings() {
  const target_langs = getCheckedTargets();
  await api('/api/settings/global', {
    method:'PATCH',
    body: JSON.stringify({source_lang: $('sourceLang').value, target_langs})
  });
  updateTargetCount();
}

async function saveSettings() {
  await saveGlobalSettings();
  if (!selectedVideoId) return;
  const target_langs = getCheckedTargets();
  await api(`/api/videos/${selectedVideoId}/settings`, {
    method:'PATCH',
    body: JSON.stringify({source_lang: $('sourceLang').value, target_langs})
  });
}

$('sourceLang').addEventListener('change', async () => {
  $('targetField').style.display = 'block';
  $('targetLangs').classList.add('visible');
  await saveGlobalSettings();
  if (selectedVideoId) await saveSettings();
});

document.getElementById('targetLangs').addEventListener('change', async (e) => {
  if (!e.target.matches('input[type=checkbox]')) return;
  updateTargetCount();
  await saveGlobalSettings();
  if (selectedVideoId) await saveSettings();
});

window.processOne = async function(id) {
  selectedVideoId = id;
  const payload = await getProcessPayload();
  if (!payload) return;
  // Highlight selected card without resetting checkboxes from server
  const data = await api('/api/state');
  renderVideos(data.videos);
  const res = await api(`/api/videos/${id}/process`, {
    method:'POST',
    body: JSON.stringify(payload)
  });
  if (res.error) showToast(res.error, 'warn');
  else showToast(`Queued ${res.jobs_added} job(s): ${res.targets.join(', ')}`);
  await refresh();
};

window.removeVideo = async function(id) {
  await api(`/api/videos/${id}`, {method:'DELETE'});
  if (selectedVideoId === id) selectedVideoId = null;
  await refresh();
};

window.openFolder = async function(id) {
  const res = await api(`/api/videos/${id}/open-folder`, {method:'POST', body:'{}'});
  if (res.error) showToast(res.error, 'warn');
};

$('btnProcessAll').addEventListener('click', async () => {
  const payload = await getProcessPayload();
  if (!payload) return;
  const res = await api('/api/process-all', {method:'POST', body: JSON.stringify(payload)});
  if (res.error) showToast(res.error, 'warn');
  else showToast(`Queued ${res.jobs_added} job(s) for ${res.targets.length} language(s)`);
  await refresh();
});

// Drag & drop — pywebview DOM handlers on desktop; browser upload on phone/remote
const dz = $('dropZone');

function updateDropZoneHint() {
  const hint = dz.querySelector('p');
  if (!hint) return;
  if (useServerPathDrop) {
    hint.textContent = 'Drop anywhere on this window · MP4, MKV, AVI, MOV, WebM…';
  } else if (isLocalClient) {
    hint.textContent = 'Drop video files here or use Browse · uploads copy to the PC';
  } else {
    hint.textContent = 'Upload videos from this device · processing runs on the PC';
  }
  $('btnBrowseFolder').style.display = isLocalClient ? '' : 'none';
}

$('btnBrowse').addEventListener('click', async (e) => {
  e.stopPropagation();
  if (!isLocalClient) {
    $('fileUploadInput').click();
    return;
  }
  try {
    const data = await api('/api/pick-files', {method:'POST', body: '{}'});
    if (data.error) showToast(data.error, 'warn');
    else await handleAddedResponse(data, 'No videos selected');
  } catch (err) {
    showToast('Could not open file picker', 'warn');
  }
});

$('btnBrowseFolder').addEventListener('click', async (e) => {
  e.stopPropagation();
  if (!isLocalClient) return;
  try {
    const data = await api('/api/pick-folder', {method:'POST', body: '{}'});
    if (data.error) showToast(data.error, 'warn');
    else await handleAddedResponse(data, 'No videos found in folder');
  } catch (err) {
    showToast('Could not open folder picker', 'warn');
  }
});

$('fileUploadInput').addEventListener('change', async e => {
  await uploadFiles(e.target.files);
  e.target.value = '';
});

dz.addEventListener('click', async (e) => {
  if (e.target.closest('.drop-actions')) return;
  if (!isLocalClient) {
    $('fileUploadInput').click();
    return;
  }
  try {
    const data = await api('/api/pick-files', {method:'POST', body: '{}'});
    if (data.error) showToast(data.error, 'warn');
    else await handleAddedResponse(data, 'No videos selected');
  } catch (err) {
    showToast('Could not open file picker', 'warn');
  }
});

function onDragOver(e) {
  e.preventDefault();
  e.stopPropagation();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  dz.classList.add('dragover');
  const n = e.dataTransfer?.files?.length;
  if (n) showDragPreview(`${n} file${n === 1 ? '' : 's'} ready to drop`);
}

function onDragLeave(e) {
  e.preventDefault();
  if (!dz.contains(e.relatedTarget)) hideDragPreview();
}

async function onDropUpload(e) {
  e.preventDefault();
  e.stopPropagation();
  hideDragPreview();
  const files = [...(e.dataTransfer?.files || [])];
  if (files.length) await uploadFiles(files);
  else showToast('Drop video files here', 'warn');
}

function bindDragDrop() {
  dz.addEventListener('dragover', onDragOver);
  dz.addEventListener('dragleave', onDragLeave);
  if (!useServerPathDrop) {
    dz.addEventListener('drop', onDropUpload);
    document.body.addEventListener('dragover', onDragOver);
    document.body.addEventListener('drop', onDropUpload);
  }
}
bindDragDrop();

function updateLanInfo(net) {
  const panel = $('lanPanel');
  const offMsg = $('lanOffMsg');
  const enabled = net.lan_access_enabled ?? net.lan_access;
  $('lanAccess').checked = !!enabled;
  if (!enabled) {
    panel.style.display = 'none';
    offMsg.style.display = 'block';
    offMsg.innerHTML = '<span style="font-size:.75rem;color:var(--muted)">LAN access is <b>off</b>. Only this PC can use the app.</span>';
    return;
  }
  offMsg.style.display = 'none';
  const urls = (net.lan_ips && net.lan_ips.length)
    ? net.lan_ips.map(ip => `http://${ip}:${net.port || 8765}`)
    : (net.lan_url ? [net.lan_url] : []);
  primaryLanUrl = net.lan_url || urls[0] || '';
  if (!urls.length) {
    panel.style.display = 'block';
    $('lanLinksList').innerHTML = '<span style="font-size:.75rem;color:var(--muted)">No LAN IP detected.</span>';
    return;
  }
  panel.style.display = 'block';
  $('lanLinksList').innerHTML = urls.map(u =>
    `<div><a href="${u}" target="_blank" rel="noopener">${u}</a></div>`
  ).join('');
}

let lanLinksOpen = false;
$('btnLanToggle').addEventListener('click', () => {
  lanLinksOpen = !lanLinksOpen;
  $('lanLinksBody').hidden = !lanLinksOpen;
  $('lanChevron').textContent = lanLinksOpen ? '▾' : '▸';
});

function showQrModal(url) {
  if (!url) {
    showToast('No LAN URL available', 'warn');
    return;
  }
  $('qrUrl').textContent = url;
  $('qrImage').src = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(url)}`;
  $('qrModal').hidden = false;
}

$('btnLanQr').addEventListener('click', () => showQrModal(primaryLanUrl));
$('qrClose').addEventListener('click', () => { $('qrModal').hidden = true; });
$('qrModal').addEventListener('click', e => {
  if (e.target === $('qrModal')) $('qrModal').hidden = true;
});

$('lanAccess').addEventListener('change', async () => {
  const res = await api('/api/settings/network', {
    method:'PATCH',
    body: JSON.stringify({lan_access_enabled: $('lanAccess').checked})
  });
  const net = await api('/api/network');
  updateLanInfo({...net, ...res});
  showToast($('lanAccess').checked ? 'LAN access enabled' : 'LAN access disabled');
});

// How to Use — driver.js tour (manual only, never auto-runs)
function stopHowToPulse() {
  $('btnHowTo').classList.remove('pulse');
}

function setupHowToPulse() {
  if (localStorage.getItem('sg_tour_opened')) return;
  const btn = $('btnHowTo');
  btn.classList.add('pulse');
  setTimeout(stopHowToPulse, 60000);
}

function startTour() {
  localStorage.setItem('sg_tour_opened', '1');
  stopHowToPulse();
  const driver = window.driver.js.driver;
  const d = driver({
    showProgress: true,
    animate: true,
    smoothScroll: true,
    allowClose: true,
    overlayOpacity: 0.65,
    popoverClass: 'driverjs-theme',
    steps: [
      { element: '#dropZone', popover: {
        title: '1 · Add videos',
        description: '<b>On this PC:</b> drag files or folders anywhere on the window, or use Browse.<br><br><b>On phone/tablet:</b> open the LAN link in the sidebar, then Browse to upload videos from that device. Processing always runs on this computer.',
        side: 'bottom', align: 'start'
      }},
      { element: '#videoList', popover: {
        title: '2 · Video queue',
        description: 'Each card shows status, progress %, and target languages. Click <b>Select</b> to override languages for one file. Use <b>Process</b> for a single video or <b>Process All</b> for the whole queue.',
        side: 'top'
      }},
      { element: '#sourceLang', popover: {
        title: '3 · Source language',
        description: 'Language spoken in the video. <b>Auto</b> works for most content. Set explicitly if detection is wrong.',
        side: 'left'
      }},
      { element: '#targetLangs', popover: {
        title: '4 · Target languages',
        description: 'Check every subtitle language you want. Tap ☆ to pin favorites to the top. One SRT per language is saved as <code>VideoName - (lang).srt</code>.',
        side: 'left'
      }},
      { element: '#btnProcessAll', popover: {
        title: '5 · Process All',
        description: 'Queues every video with the selected target languages. Jobs run one at a time.',
        side: 'left'
      }},
      { element: '#deviceBadge', popover: {
        title: '6 · GPU / CPU',
        description: 'Shows whether Whisper is using your NVIDIA GPU (CUDA) or CPU. GPU is much faster when available.',
        side: 'left'
      }},
      { element: '#whisperModel', popover: {
        title: '7 · Whisper model',
        description: '<b>tiny/base</b> = fast · <b>small/medium</b> = better · <b>large-v3</b> = best (needs GPU VRAM) · <b>turbo</b> = fast English. English-only variants (.en) skip multilingual detection.',
        side: 'left'
      }},
      { element: '#useCuda', popover: {
        title: '8 · CUDA / GPU',
        description: 'Leave ON if you have an NVIDIA GPU. Turn OFF to force CPU.',
        side: 'left'
      }},
      { element: '#wordTimestamps', popover: {
        title: '9 · Word timestamps',
        description: 'When ON, subtitles get precise per-word timing (best when output language matches spoken language).',
        side: 'left'
      }},
      { element: '#lanAccess', popover: {
        title: '10 · LAN access toggle',
        description: 'Turn OFF to block phones/tablets on your Wi‑Fi. When ON, expand the link row or scan the QR code.',
        side: 'left'
      }},
      { element: '#lanPanel', popover: {
        title: '11 · Phone / tablet link',
        description: 'Click to expand LAN URLs, or tap ▣ for a QR code. Private network only — not on the public internet.',
        side: 'left'
      }},
      { element: '#queueInfo', popover: {
        title: '12 · Queue status',
        description: 'Shows how many jobs are waiting or transcribing. Done cards show total time taken.',
        side: 'left'
      }},
      { element: '#btnHowTo', popover: {
        title: 'Replay anytime',
        description: 'This tour never auto-runs on startup. Click <b>How to Use</b> whenever you need a refresher.',
        side: 'bottom'
      }},
    ]
  });
  d.drive();
}
$('btnHowTo').addEventListener('click', startTour);
setupHowToPulse();

loadLanguages().then(async () => {
  await refresh();
  const [net, netSettings] = await Promise.all([
    api('/api/network'),
    api('/api/settings/network'),
  ]);
  isLocalClient = !!net.is_local_client;
  useServerPathDrop = !!(window.pywebview);
  updateDropZoneHint();
  updateLanInfo({...net, ...netSettings});
});
pollTimer = setInterval(refresh, 2000);
</script>
</body>
</html>
"""


def start_flask() -> None:
    app.run(host=BIND_HOST, port=PORT, debug=DEBUG, threaded=True, use_reloader=False)


def extract_drop_paths(event: dict) -> list[str]:
    """Pull absolute file paths from a pywebview DOM drop event."""
    dt = event.get("dataTransfer") or event.get("domTransfer") or {}
    files = dt.get("files") or []
    paths: list[str] = []
    for f in files:
        path = f.get("pywebviewFullPath") or f.get("path")
        if path:
            paths.append(path)
    return paths


def handle_paths_dropped(paths: list[str], source: str = "drop") -> int:
    added = _add_video_paths(paths)
    log_event(f"{source}", count=len(added), paths=paths[:10])
    notify_ui(len(added))
    return len(added)


_tray_drop_window: Any = None


def _show_tray_drop_target() -> None:
    """Small floating drop chip near system tray."""
    global _tray_drop_window
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        import tkinter as tk
    except ImportError:
        return

    if _tray_drop_window is not None:
        try:
            _tray_drop_window.deiconify()
            _tray_drop_window.lift()
            return
        except Exception:
            pass

    root = TkinterDnD.Tk()
    root.title("Drop videos")
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    w, h = 120, 72
    root.geometry(f"{w}x{h}+{sw - w - 16}+{sh - h - 80}")
    root.attributes("-topmost", True)
    root.configure(bg="#1a1625")
    lbl = tk.Label(
        root,
        text="⬇ Drop\nhere",
        fg="#f8b4d9",
        bg="#1a1625",
        font=("Segoe UI", 10, "bold"),
    )
    lbl.pack(expand=True, fill="both")

    def on_drop(event: Any) -> None:
        paths = list(root.tk.splitlist(event.data))
        n = handle_paths_dropped(paths, source="tray_drop")
        root.title(f"Added {n}")

    root.drop_target_register(DND_FILES)
    root.dnd_bind("<<Drop>>", on_drop)
    _tray_drop_window = root
    log_event("tray_drop_target_shown")

    def _loop() -> None:
        try:
            root.mainloop()
        except Exception:
            pass

    threading.Thread(target=_loop, name="TrayDrop", daemon=True).start()


def start_system_tray() -> None:
    global _tray_started
    if _tray_started:
        return
    _tray_started = True

    def _run() -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw

            img = Image.new("RGB", (64, 64), (26, 22, 37))
            draw = ImageDraw.Draw(img)
            draw.ellipse((8, 8, 56, 56), fill=(233, 30, 140))
            draw.text((22, 20), "S", fill=(255, 255, 255))

            def on_open(_icon: Any, _item: Any) -> None:
                if _main_window:
                    try:
                        _main_window.show()
                    except Exception:
                        pass

            def on_drop_target(_icon: Any, _item: Any) -> None:
                _show_tray_drop_target()

            def on_add_folder(_icon: Any, _item: Any) -> None:
                import tkinter as tk
                from tkinter import filedialog
                r = tk.Tk()
                r.withdraw()
                folder = filedialog.askdirectory(title="Select folder with videos")
                r.destroy()
                if folder:
                    handle_paths_dropped([folder], source="tray_folder")

            def on_quit(icon: Any, _item: Any) -> None:
                icon.stop()
                os._exit(0)

            menu = pystray.Menu(
                pystray.MenuItem("Open app", on_open, default=True),
                pystray.MenuItem("Show tray drop target", on_drop_target),
                pystray.MenuItem("Import folder…", on_add_folder),
                pystray.MenuItem("Quit", on_quit),
            )
            icon = pystray.Icon("subtitles_generator", img, "Subtitles Generator", menu)
            log_event("system_tray_started")
            icon.run()
        except Exception as exc:
            log.warning("System tray unavailable: %s", exc)

    threading.Thread(target=_run, name="SystemTray", daemon=True).start()


_drop_handlers_bound = False


def bind_pywebview_drop(window: Any) -> None:
    """Handle drag-and-drop on the pywebview window (full Windows paths)."""
    global _drop_handlers_bound
    from webview.dom import DOMEventHandler

    set_main_window(window)
    start_system_tray()

    def _register_handlers() -> None:
        global _drop_handlers_bound, _last_drag_preview
        if _drop_handlers_bound:
            return

        def on_dragover(e: Any) -> None:
            global _last_drag_preview
            try:
                preview = extract_drag_preview(e if isinstance(e, dict) else {})
                if preview != _last_drag_preview:
                    _last_drag_preview = preview
                    window.evaluate_js(
                        f"showDragPreview({json.dumps(preview)});"
                    )
            except Exception:
                pass

        def on_drop(e: Any) -> None:
            global _last_drag_preview
            _last_drag_preview = ""
            try:
                paths = extract_drop_paths(e if isinstance(e, dict) else {})
                if not paths:
                    log.warning("Drop event had no file paths: %s", e)
                    notify_ui(0, message="No videos found in drop", animate=False)
                    return
                handle_paths_dropped(paths, source="pywebview_drop")
            except Exception as exc:
                log.error("Drop handler failed: %s\n%s", exc, traceback.format_exc())

        window.dom.document.events.dragover += DOMEventHandler(
            on_dragover, prevent_default=True, stop_propagation=True
        )
        window.dom.document.events.drop += DOMEventHandler(
            on_drop, prevent_default=True, stop_propagation=True
        )
        _drop_handlers_bound = True
        log_event("pywebview_drop_bound")

    def _on_loaded() -> None:
        _register_handlers()

    window.events.loaded += _on_loaded
    if window.events.loaded.is_set():
        _on_loaded()


def open_ui() -> None:
    url = local_app_url()
    try:
        import webview

        flask_thread = threading.Thread(target=start_flask, name="Flask", daemon=True)
        flask_thread.start()
        time.sleep(0.8)

        window = webview.create_window(
            "Subtitles Generator",
            url,
            width=1200,
            height=800,
            min_size=(640, 480),
            text_select=True,
            easy_drag=True,
        )
        webview.start(bind_pywebview_drop, args=window, gui="edgechromium")
    except Exception as exc:
        log.warning("pywebview unavailable (%s), falling back to browser", exc)
        threading.Thread(target=start_flask, name="Flask", daemon=True).start()
        time.sleep(1.0)
        webbrowser.open(url)
        while flask_thread_alive():
            time.sleep(1)


def flask_thread_alive() -> bool:
    return any(t.name == "Flask" and t.is_alive() for t in threading.enumerate())


def main() -> int:
    log_event("app_start", host=BIND_HOST, port=PORT, lan_access=False, model=WHISPER_MODEL, root=str(ROOT))
    try:
        resolve_ffmpeg()
    except FileNotFoundError as exc:
        log.error(str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    ensure_worker()
    open_ui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
