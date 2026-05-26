"""
YouTube 쿠키 관리 모듈

YOUTUBE_COOKIES_B64 환경변수에서 base64 인코딩된 Netscape cookies.txt를
디코딩하여 /tmp/yt_cookies.txt에 기록한다.

사용처:
  - transcript.py : YouTubeTranscriptApi(http_client=get_session())
  - stt.py        : ydl_opts['cookiefile'] = get_cookie_path()

로컬 환경: 환경변수 미설정 → None 반환 → 기존 방식 그대로 동작
Railway  : YOUTUBE_COOKIES_B64 설정 → 쿠키 파일 생성 → YouTube IP 차단 우회
"""

from __future__ import annotations

import base64
import logging
import os
import pathlib
from http.cookiejar import MozillaCookieJar

import requests

logger = logging.getLogger(__name__)

# Railway 컨테이너 /tmp는 메모리 기반 임시 스토리지 (재시작 시 초기화)
_COOKIE_FILE = pathlib.Path("/tmp/yt_cookies.txt")

# 모듈 로드 시 1회만 초기화
_ready: bool | None = None   # None=미초기화, True=준비완료, False=쿠키없음


def _initialize() -> bool:
    """환경변수를 읽어 쿠키 파일을 생성한다. 모듈 당 1회 실행."""
    b64 = os.environ.get("YOUTUBE_COOKIES_B64", "").strip()
    if not b64:
        logger.info("[cookie_manager] YOUTUBE_COOKIES_B64 미설정 — 쿠키 없이 동작")
        return False

    try:
        cookie_bytes = base64.b64decode(b64)
        _COOKIE_FILE.write_bytes(cookie_bytes)
        logger.info(
            "[cookie_manager] 쿠키 파일 준비: %s (%d bytes)",
            _COOKIE_FILE, len(cookie_bytes),
        )
        return True
    except Exception as exc:
        logger.error("[cookie_manager] 쿠키 초기화 실패: %s", exc)
        return False


def _ensure_ready() -> bool:
    global _ready
    if _ready is None:
        _ready = _initialize()
    return _ready


def get_cookie_path() -> str | None:
    """
    쿠키 파일 경로를 반환한다.

    Returns:
        str: /tmp/yt_cookies.txt  (yt-dlp cookiefile 옵션에 사용)
        None: 쿠키 미설정 (로컬 환경)
    """
    return str(_COOKIE_FILE) if _ensure_ready() else None


def get_session() -> requests.Session | None:
    """
    YouTube 쿠키가 로드된 requests.Session을 반환한다.
    YouTubeTranscriptApi(http_client=get_session()) 에 전달한다.

    Returns:
        requests.Session: 쿠키 적재 완료
        None: 쿠키 미설정 → 기본 Session 사용 (http_client=None)
    """
    if not _ensure_ready():
        return None

    try:
        jar = MozillaCookieJar()
        jar.load(str(_COOKIE_FILE), ignore_discard=True, ignore_expires=True)
        session = requests.Session()
        # requests.Session.cookies는 RequestsCookieJar여야 쿠키가 실제로 전송됨
        # MozillaCookieJar를 직접 대입하면 쿠키 전송이 안 됨 → update()로 복사
        session.cookies.update(jar)
        cookie_count = len(list(jar))
        logger.info("[cookie_manager] requests.Session 쿠키 로드 완료: %d개", cookie_count)
        return session
    except Exception as exc:
        logger.error("[cookie_manager] Session 생성 실패: %s", exc)
        return None
