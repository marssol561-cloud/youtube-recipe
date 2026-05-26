"""
유튜브 자막 추출 모듈
스프린트 3 구현 + 스프린트 4 yt-dlp 자막 fallback 추가

경로 A: youtube-transcript-api
경로 B: yt-dlp 자막 다운로드 (경로 A 실패 시)

youtube-transcript-api 1.2.4 기준 — 인스턴스 기반 API 사용:
  api = YouTubeTranscriptApi()
  tl  = api.list(video_id)               → TranscriptList
  t   = tl.find_manually_created_transcript(['ko']) → Transcript
  ft  = t.fetch()                         → FetchedTranscript
  raw = ft.to_raw_data()                  → list[dict]

경로 B (yt-dlp):
  - writesubtitles + writeautomaticsub + skip_download
  - subtitleslangs: ["ko"]
  - subtitlesformat: "json3/vtt"
  - 쿠키 주입 지원 (YOUTUBE_COOKIES_B64)

우선순위:
  1. 수동 자막 (ko)
  2. 자동생성 자막 (ko, a.ko)

needs_stt: True — 스프린트 4 STT fallback 트리거 플래그
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from .cookie_manager import get_cookie_path, get_session

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
    # YOUTUBE_COOKIES_B64 설정 시 쿠키 세션 주입 (Railway 클라우드 IP 차단 우회)
    api = YouTubeTranscriptApi(http_client=get_session())

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
    api = YouTubeTranscriptApi(http_client=get_session())
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
# 경로 B: yt-dlp 자막 다운로드
# ---------------------------------------------------------------------------

def fetch_transcript_via_ytdlp(video_id: str) -> dict[str, Any]:
    """
    yt-dlp extract_info(download=False) 로 자막 URL을 추출한 뒤
    requests.Session(쿠키)으로 직접 다운로드하여 텍스트 반환.

    skip_download=True 방식은 내부 format 검증 단계에서
    Railway IP가 "Requested format is not available" 오류를 내므로
    extract_info(download=False) → URL 직접 다운로드로 우회한다.

    Args:
        video_id: 유튜브 영상 ID (11자)

    Returns:
        성공: {"text": str, "source": "subtitle", "success": True, "needs_stt": False}
        실패: {"text": None, "source": None, "success": False, "needs_stt": True}
    """
    import requests as req_lib

    _fail: dict[str, Any] = {"text": None, "source": None, "success": False, "needs_stt": True}

    url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # format 검증 없이 info만 추출 — format 키 미지정 시 검증 건너뜀
    }

    cookie_path = get_cookie_path()
    if cookie_path:
        ydl_opts["cookiefile"] = cookie_path
        logger.info("[transcript_ytdlp] 쿠키 파일 적용: %s", cookie_path)

    logger.info("[transcript_ytdlp] 영상 정보 추출 시작: video_id='%s'", video_id)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("[transcript_ytdlp] 정보 추출 실패: video_id='%s' | %s", video_id, exc)
        return _fail

    if info is None:
        logger.warning("[transcript_ytdlp] 정보 없음: video_id='%s'", video_id)
        return _fail

    # ── 자막 URL 탐색 (수동 자막 우선 → 자동생성) ───────────────────────
    sub_url: str | None = None
    sub_ext: str | None = None

    def _pick_url(tracks: dict[str, list[dict[str, Any]]]) -> tuple[str | None, str | None]:
        for lang in ["ko"]:
            if lang not in tracks:
                continue
            for ext_pref in ["json3", "vtt"]:
                for entry in tracks[lang]:
                    if entry.get("ext") == ext_pref and entry.get("url"):
                        return entry["url"], ext_pref
        return None, None

    sub_url, sub_ext = _pick_url(info.get("subtitles", {}))
    if sub_url is None:
        sub_url, sub_ext = _pick_url(info.get("automatic_captions", {}))

    if sub_url is None:
        logger.info(
            "[transcript_ytdlp] 한국어 자막 없음: video_id='%s' "
            "(subtitles=%s, auto=%s)",
            video_id,
            list(info.get("subtitles", {}).keys()),
            list(info.get("automatic_captions", {}).keys()),
        )
        return _fail

    logger.info(
        "[transcript_ytdlp] 자막 URL 발견 (ext=%s): video_id='%s'", sub_ext, video_id
    )

    # ── requests.Session 으로 자막 파일 직접 다운로드 ────────────────────
    # get_session() 은 쿠키 미설정 시 None 반환 → 기본 Session 사용
    session = get_session() or req_lib.Session()
    try:
        resp = session.get(sub_url, timeout=30)
        resp.raise_for_status()
        content = resp.text
    except Exception as exc:
        logger.warning(
            "[transcript_ytdlp] 자막 파일 다운로드 실패: video_id='%s' | %s", video_id, exc
        )
        return _fail

    # ── 파싱 ─────────────────────────────────────────────────────────────
    text: str | None = None
    if sub_ext == "json3":
        text = _parse_json3_sub(content)
    else:
        text = _parse_vtt_sub(content)

    if not text:
        logger.info("[transcript_ytdlp] 자막 텍스트 비어 있음: video_id='%s'", video_id)
        return _fail

    logger.info(
        "[transcript_ytdlp] 완료: video_id='%s' (%d자)", video_id, len(text)
    )
    return {"text": text, "source": "subtitle", "success": True, "needs_stt": False}


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _parse_json3_sub(content: str) -> str | None:
    """YouTube json3 자막 포맷 파싱."""
    try:
        data = json.loads(content)
        parts: list[str] = []
        for event in data.get("events", []):
            for seg in event.get("segs", []):
                t = seg.get("utf8", "").strip()
                if t and t != "\n":
                    parts.append(t)
        text = " ".join(parts).strip()
        return text if text else None
    except Exception as exc:
        logger.warning("[transcript_ytdlp] json3 파싱 오류: %s", exc)
        return None


def _parse_vtt_sub(content: str) -> str | None:
    """
    WebVTT 자막 포맷 파싱.
    자동생성 자막의 슬라이딩 윈도우 중복 제거 포함.
    """
    lines = content.split("\n")
    seen: set[str] = set()
    text_parts: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 헤더 및 메타 라인 스킵
        if (
            line.startswith("WEBVTT")
            or line.startswith("NOTE")
            or line.startswith("Kind:")
            or line.startswith("Language:")
        ):
            continue
        # 타이밍 라인 스킵: 00:00:00.000 --> 00:00:03.500
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3} -->", line):
            continue
        # 큐 번호 스킵
        if re.match(r"^\d+$", line):
            continue
        # HTML 태그 및 위치 지정자 제거
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\{[^}]+\}", "", line).strip()
        # 중복 제거
        if line and line not in seen:
            seen.add(line)
            text_parts.append(line)

    text = " ".join(text_parts).strip()
    return text if text else None


def _join_raw_data(raw: list[dict[str, Any]]) -> str:
    """to_raw_data() 결과를 하나의 텍스트 문자열로 합친다."""
    parts = []
    for entry in raw:
        text = entry.get("text", "").strip() if isinstance(entry, dict) else str(entry).strip()
        if text:
            parts.append(text)
    return " ".join(parts)
