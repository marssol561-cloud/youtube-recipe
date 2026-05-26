"""
유튜브 자막 추출 모듈 (경로 A — youtube-transcript-api)
스프린트 3 구현

youtube-transcript-api 1.2.4 기준 — 인스턴스 기반 API 사용:
  api = YouTubeTranscriptApi()
  tl  = api.list(video_id)               → TranscriptList
  t   = tl.find_manually_created_transcript(['ko']) → Transcript
  ft  = t.fetch()                         → FetchedTranscript
  raw = ft.to_raw_data()                  → list[dict]

우선순위:
  1. 수동 자막 (ko)
  2. 자동생성 자막 (ko, a.ko)

needs_stt: True — 스프린트 4 STT fallback 트리거 플래그
"""

from __future__ import annotations

import logging
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

logger = logging.getLogger(__name__)

# 자동생성 자막 시도 언어 우선순위
_AUTO_LANG_PRIORITY = ["ko", "a.ko"]


def fetch_transcript(video_id: str) -> dict[str, Any]:
    """
    youtube-transcript-api로 한국어 자막을 가져온다.

    Args:
        video_id: 유튜브 영상 ID (11자)

    Returns:
        성공: {"text": str, "source": "subtitle", "success": True, "needs_stt": False}
        실패: {"text": None, "source": None, "success": False, "needs_stt": True}
    """
    _fail = {"text": None, "source": None, "success": False, "needs_stt": True}
    api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)
    except TranscriptsDisabled:
        logger.warning("[transcript] 자막 비활성화: video_id='%s'", video_id)
        return _fail
    except VideoUnavailable:
        logger.warning("[transcript] 영상 없음 또는 접근 불가: video_id='%s'", video_id)
        return _fail
    except Exception as exc:
        logger.warning("[transcript] 자막 목록 조회 실패: video_id='%s' | %s", video_id, exc)
        return _fail

    # ── 수동 자막(ko) 우선 시도 ───────────────────────────────────────────
    try:
        transcript = transcript_list.find_manually_created_transcript(["ko"])
        fetched = transcript.fetch()
        text = _join_raw_data(fetched.to_raw_data())
        logger.info(
            "[transcript] 수동 자막(ko) 추출 성공: video_id='%s' (%d자)", video_id, len(text)
        )
        return {"text": text, "source": "subtitle", "success": True, "needs_stt": False}
    except NoTranscriptFound:
        pass
    except Exception as exc:
        logger.warning("[transcript] 수동 자막 fetch 실패: %s", exc)

    # ── 자동생성 자막(ko, a.ko) 시도 ─────────────────────────────────────
    try:
        transcript = transcript_list.find_generated_transcript(_AUTO_LANG_PRIORITY)
        fetched = transcript.fetch()
        text = _join_raw_data(fetched.to_raw_data())
        logger.info(
            "[transcript] 자동생성 자막(%s) 추출 성공: video_id='%s' (%d자)",
            getattr(transcript, "language_code", "?"), video_id, len(text)
        )
        return {"text": text, "source": "subtitle", "success": True, "needs_stt": False}
    except NoTranscriptFound:
        logger.info("[transcript] 한국어 자막 없음 (수동+자동 모두): video_id='%s'", video_id)
    except Exception as exc:
        logger.warning("[transcript] 자동생성 자막 fetch 실패: video_id='%s' | %s", video_id, exc)

    return _fail


def fetch_transcript_with_timestamps(
    video_id: str, language: str = "ko"
) -> list[dict[str, Any]] | None:
    """
    타임스탬프 포함 자막 리스트를 반환한다. (레거시 시그니처 유지)

    Args:
        video_id: 유튜브 영상 ID
        language: 자막 언어 코드 (기본 "ko")

    Returns:
        [{"text": str, "start": float, "duration": float}, ...] 또는 None
    """
    api = YouTubeTranscriptApi()
    try:
        tl = api.list(video_id)
        try:
            transcript = tl.find_manually_created_transcript([language])
        except NoTranscriptFound:
            transcript = tl.find_generated_transcript([language, f"a.{language}"])

        fetched = transcript.fetch()
        return fetched.to_raw_data()
    except Exception as exc:
        logger.warning("[transcript_ts] 실패: video_id='%s' | %s", video_id, exc)
        return None


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _join_raw_data(raw: list[dict[str, Any]]) -> str:
    """to_raw_data() 결과를 하나의 텍스트 문자열로 합친다."""
    parts = []
    for entry in raw:
        text = entry.get("text", "").strip() if isinstance(entry, dict) else str(entry).strip()
        if text:
            parts.append(text)
    return " ".join(parts)
