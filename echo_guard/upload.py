"""Feedback upload — prepares and sends anonymized feedback to the collection endpoint.

Respects the user's consent tier:
- 'none': nothing uploaded
- 'private': anonymized structural features only (no code, paths, or names)
- 'public': structural features + code pairs (paths/names stripped)

Upload is fire-and-forget: if the endpoint is unreachable, silently skip.
Rows stay un-uploaded and will be retried next session.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

from echo_guard.config import EchoGuardConfig

logger = logging.getLogger(__name__)

_upload_lock = threading.Lock()

FEEDBACK_ENDPOINT = "https://echo-guard-feedback.echo-guard.workers.dev/v1/upload"
SCHEMA_VERSION = "1"

# Fields to exclude from private-tier feedback records (these are internal/DB-only)
_FEEDBACK_EXCLUDE = {"id", "uploaded_at", "recorded_at"}

# Fields to strip from public-tier training pairs
_TRAINING_PAIR_STRIP = {
    "filepath_a", "filepath_b",
    "function_name_a", "function_name_b",
    "id", "uploaded_at", "recorded_at",
}


def _strip_feedback_record(record: dict) -> dict:
    """Strip internal fields from a feedback record for upload."""
    return {k: v for k, v in record.items() if k not in _FEEDBACK_EXCLUDE}


def _strip_training_pair(pair: dict) -> dict:
    """Strip identifying fields from a training pair for public-tier upload.

    Keeps source code, language, embedding score, clone type, verdict.
    Removes filepath_a, filepath_b, function_name_a, function_name_b.
    """
    return {k: v for k, v in pair.items() if k not in _TRAINING_PAIR_STRIP}


def _get_language_distribution(feedback_records: list[dict]) -> dict[str, int]:
    """Count languages from feedback records."""
    counts: dict[str, int] = {}
    for r in feedback_records:
        lang = r.get("source_language", "")
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def prepare_payload(
    config: EchoGuardConfig,
    feedback_records: list[dict],
    training_pairs: list[dict],
) -> dict | None:
    """Build upload payload from un-uploaded feedback rows.

    Returns None if nothing to upload or consent='none'.
    """
    if config.feedback_consent == "none":
        return None

    if not feedback_records and not training_pairs:
        return None

    from echo_guard import __version__

    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "echo_guard_version": __version__,
        "model_name": config.model,
        "consent_tier": config.feedback_consent,
        "language_distribution": _get_language_distribution(feedback_records),
        "upload_timestamp": datetime.now(timezone.utc).isoformat(),
        "records": [],
    }

    # Always include anonymized feedback records (both private and public tiers)
    for record in feedback_records:
        payload["records"].append({
            "type": "feedback",
            **_strip_feedback_record(record),
        })

    # Only include training pairs for public tier
    if config.feedback_consent == "public":
        for pair in training_pairs:
            payload["records"].append({
                "type": "training_pair",
                **_strip_training_pair(pair),
            })

    if not payload["records"]:
        return None

    return payload


def upload_payload(payload: dict, endpoint: str = FEEDBACK_ENDPOINT) -> bool:
    """POST JSONL to the collection endpoint. Returns success.

    Each record is a separate JSON line. The first line contains metadata.
    """
    # Build JSONL body: metadata line + one line per record
    records = payload.pop("records", [])
    lines = [json.dumps(payload)]
    for record in records:
        lines.append(json.dumps(record, default=str))
    body = "\n".join(lines) + "\n"

    req = Request(
        endpoint,
        data=body.encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/jsonl")
    req.add_header("User-Agent", "echo-guard")

    try:
        response = urlopen(req, timeout=10)  # noqa: S310
        return response.status in (200, 201)
    except URLError as exc:
        logger.debug("Upload failed (network): %s", exc)
        return False
    except Exception as exc:
        logger.debug("Upload failed: %s", exc)
        return False


def _is_upload_disabled() -> bool:
    """Check if uploads are disabled via environment variables.

    Respects DO_NOT_TRACK (https://consoledonottrack.com/) and
    ECHO_GUARD_NO_UPLOAD for explicit opt-out.
    """
    import os

    if os.environ.get("DO_NOT_TRACK", "").strip() == "1":
        return True
    if os.environ.get("ECHO_GUARD_NO_UPLOAD", "").strip() == "1":
        return True
    return False


def _maybe_upload(
    config: EchoGuardConfig,
    repo_root,
    scan_event: dict | None = None,
) -> None:
    """Fire-and-forget upload of any un-uploaded feedback + optional scan event.

    Called at the end of scan, review, check, and acknowledge commands.
    Combines all pending data into a single request to avoid rate limiting.
    Silently skips if consent is 'none', DO_NOT_TRACK=1 is set,
    or the endpoint is unreachable.
    """
    if config.feedback_consent == "none":
        return
    if _is_upload_disabled():
        return

    from pathlib import Path

    from echo_guard.index import FunctionIndex

    with _upload_lock:
        try:
            idx = FunctionIndex(Path(repo_root))
        except Exception as exc:
            logger.debug("Upload attempt failed (index open): %s", exc)
            return
        try:
            feedback_records = idx.get_unuploaded_feedback()
            training_pairs = (
                idx.get_unuploaded_training_pairs()
                if config.feedback_consent == "public"
                else []
            )

            payload = prepare_payload(config, feedback_records, training_pairs)

            # If no feedback/training data but we have a scan event, build a minimal payload
            if payload is None and scan_event:
                from echo_guard import __version__

                payload = {
                    "schema_version": SCHEMA_VERSION,
                    "echo_guard_version": __version__,
                    "model_name": config.model,
                    "consent_tier": config.feedback_consent,
                    "language_distribution": scan_event.get("language_counts", {}),
                    "upload_timestamp": datetime.now(timezone.utc).isoformat(),
                    "records": [],
                }

            if payload is None:
                return

            # Append scan event to the same payload
            if scan_event:
                payload["records"].append({
                    "type": "scan_event",
                    **{k: v for k, v in scan_event.items() if k != "language_counts"},
                })

            feedback_ids = [r["id"] for r in feedback_records if "id" in r]
            pair_ids = [p["id"] for p in training_pairs if "id" in p]

            success = upload_payload(payload)
            if success:
                idx.mark_feedback_uploaded(feedback_ids)
                idx.mark_training_pairs_uploaded(pair_ids)
                if feedback_ids or pair_ids:
                    uploaded_count = len(feedback_ids) + len(pair_ids)
                    logger.debug(
                        "↑ %d feedback record%s uploaded",
                        uploaded_count,
                        "s" if uploaded_count != 1 else "",
                    )
        except Exception as exc:
            logger.debug("Upload attempt failed: %s", exc)
        finally:
            idx.close()
