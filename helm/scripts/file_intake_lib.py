#!/usr/bin/env python3
from __future__ import annotations

import mimetypes
from pathlib import Path


TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_EXACT = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/x-yaml",
}


def _claimed_type(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _detect_magic_mime(data: bytes, *, suffix: str) -> tuple[str | None, str | None]:
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf_document"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image_ocr"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image_ocr"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "image_ocr"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav", "speech_audio"
    if data.startswith(b"FORM") and data[8:12] in {b"AIFF", b"AIFC"}:
        return "audio/aiff", "speech_audio"
    if data.startswith(b"OggS"):
        return "audio/ogg", "speech_audio"
    if data.startswith(b"fLaC"):
        return "audio/flac", "speech_audio"
    if data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "audio/mpeg", "speech_audio"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        office_zip_mimes = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        if suffix in office_zip_mimes:
            return office_zip_mimes[suffix], "office_document"
        return "application/zip", "archive_inspection"
    if data.startswith(b"\x1f\x8b"):
        return "application/gzip", "archive_inspection"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {b"M4A ", b"M4B ", b"M4P ", b"F4A "}:
            return "audio/mp4", "speech_audio"
        if suffix in {".m4a", ".m4b", ".aac"}:
            return "audio/mp4", "speech_audio"
        return "video/mp4", "media_review"
    if _looks_like_text(data):
        return "text/plain", "text_loader"
    return None, None


def _looks_like_text(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    decoded = sample.decode("utf-8", errors="ignore")
    if not decoded:
        return False
    printable = sum(1 for char in decoded if char.isprintable() or char in "\r\n\t")
    return printable / max(len(decoded), 1) >= 0.9


def _detect_with_magika(path: Path) -> tuple[str | None, float | None]:
    try:
        from magika import Magika  # type: ignore
    except Exception:
        return None, None

    try:
        result = Magika().identify_path(path)
    except Exception:
        return None, None

    candidate = getattr(result, "output", result)
    mime = getattr(candidate, "mime_type", None) or getattr(candidate, "mimeType", None)
    score = getattr(candidate, "score", None) or getattr(candidate, "confidence", None)
    if not isinstance(mime, str) or not mime.strip():
        return None, None
    numeric_score = None
    if isinstance(score, (float, int)):
        numeric_score = round(float(score), 4)
    return mime.strip(), numeric_score


def _route_for_mime(mime_type: str) -> tuple[str, str, bool]:
    normalized = (mime_type or "application/octet-stream").casefold()
    if normalized.startswith("audio/"):
        return "speech_audio", "audio_transcription", True
    if normalized.startswith("image/"):
        return "image_ocr", "vision_ocr", True
    if normalized == "application/pdf":
        return "pdf_document", "pdf_parser", True
    if normalized in TEXT_MIME_EXACT or normalized.startswith(TEXT_MIME_PREFIXES):
        return "text_loader", "plain_text_loader", True
    if normalized in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        return "office_document", "office_parser", True
    if normalized in {"application/zip", "application/gzip", "application/x-tar"}:
        return "archive_inspection", "archive_inspector", False
    if normalized.startswith("video/"):
        return "media_review", "vision_or_media_review", True
    return "manual_review", "manual_review", False


def _types_match(claimed_type: str, detected_type: str) -> bool:
    if not claimed_type or not detected_type:
        return True
    if claimed_type == detected_type:
        return True
    claimed_major = claimed_type.split("/", 1)[0]
    detected_major = detected_type.split("/", 1)[0]
    if claimed_major == detected_major and claimed_major in {"audio", "image", "text"}:
        return True
    audio_aliases = {"audio/mp4", "audio/x-m4a"}
    if {claimed_type, detected_type} <= audio_aliases:
        return True
    return False


def probe_file_intake(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    if not path.is_file():
        raise SystemExit(f"Not a file: {path}")

    claimed_type = _claimed_type(path)
    with path.open("rb") as handle:
        sample = handle.read(4096)
    magika_mime, magika_score = _detect_with_magika(path)
    detector = "magika" if magika_mime else "magic_bytes"
    detected_type, route_hint = _detect_magic_mime(sample, suffix=path.suffix.casefold())
    confidence = 0.75 if detected_type else None

    if magika_mime:
        detected_type = magika_mime
        confidence = magika_score
    elif not detected_type:
        detected_type = claimed_type
        detector = "mimetypes"

    route_decision, parser_family, safe_to_parse = _route_for_mime(detected_type)
    mismatch = not _types_match(claimed_type, detected_type)
    warnings: list[str] = []
    if mismatch:
        warnings.append("claimed type and detected type differ")
    if route_decision == "manual_review":
        warnings.append("unknown or unsupported file type; review before parsing")
    if route_hint and route_hint != route_decision:
        warnings.append("magic-byte route hint differs from final route decision")

    return {
        "path": str(path),
        "file_name": path.name,
        "extension": path.suffix.casefold(),
        "size_bytes": path.stat().st_size,
        "claimed_type": claimed_type,
        "detected_type": detected_type,
        "detected_mime_type": detected_type,
        "detector": detector,
        "confidence": confidence,
        "mismatch": mismatch,
        "route_decision": route_decision,
        "parser_family": parser_family,
        "safe_to_parse": safe_to_parse,
        "warnings": warnings,
    }
