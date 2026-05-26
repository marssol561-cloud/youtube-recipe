"""
STT fallback 모듈 - yt-dlp로 오디오 추출 + Groq Whisper API로 한국어 STT
스프린트 4 수정 (faster-whisper -> Groq API)

트리거: transcript.py에서 needs_stt=True 반환 시

환경변수:
  GROQ_API_KEY: Groq API 키 (.env에서 로드)

제약:
  - 임시 파일: tmp/ 디렉토리. finally 블록에서 반드시 삭제
  - 언어: ko 고정
  - Groq API 파일 크기 제한: 25MB
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import pathlib
from typing import Any

import yt_dlp
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

# tmp 디렉토리 경로 (프로젝트 루트 기준)
_TMP_DIR = pathlib.Path(__file__).parent.parent / "tmp"

# Groq Whisper 모델
_GROQ_MODEL = "whisper-large-v3-turbo"

# Groq API 파일 크기 제한 (MB)
_GROQ_MAX_MB = 25


async def extract_audio_and_transcribe(video_id: str) -> dict[str, Any]:
    """
    yt-dlp로 오디오를 추출하고 Groq Whisper API로 STT를 실행한다.

    Args:
        video_id: 유튜브 영상 ID (11자)

    Returns:
        성공: {"text": str, "source": "stt", "success": True}
        실패: {"text": None, "source": None, "success": False, "error": str}
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _do_stt, video_id)
    return result


def _do_stt(video_id: str) -> dict[str, Any]:
    """동기 STT 실행 본체. extract_audio_and_transcribe에서 executor를 통해 호출된다."""
    _fail: dict[str, Any] = {"text": None, "source": None, "success": False}
    _TMP_DIR.mkdir(exist_ok=True)

    try:
        # Step 1: 오디오 다운로드
        audio_path = _download_audio(video_id)
        if audio_path is None:
            return {**_fail, "error": "오디오 다운로드 실패"}

        # Step 2: Groq Whisper STT
        text = _transcribe(audio_path, video_id)
        if text is None:
            return {**_fail, "error": "STT 변환 실패"}

        logger.info("[stt] 완료: video_id='%s' (%d자)", video_id, len(text))
        return {"text": text, "source": "stt", "success": True}

    except Exception as exc:
        logger.error("[stt] 예외: video_id='%s' | %s", video_id, exc)
        return {**_fail, "error": str(exc)}

    finally:
        # 임시 파일 반드시 삭제 (예외 발생 시에도)
        _cleanup(video_id)


# ---------------------------------------------------------------------------
# 내부 함수
# ---------------------------------------------------------------------------

def _download_audio(video_id: str) -> str | None:
    """
    yt-dlp로 오디오를 다운로드한다.
    ffmpeg 없이도 동작하도록 native 포맷(webm/m4a/opus)으로 저장.
    Groq API는 flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm을 모두 지원.
    파일 크기 25MB 초과 시 None 반환.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl = str(_TMP_DIR / f"{video_id}.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }

    logger.info("[stt] 오디오 다운로드 시작: video_id='%s'", video_id)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        logger.error("[stt] 다운로드 실패: %s", exc)
        return None

    # 다운로드된 파일 탐색 (확장자 불확실)
    pattern = str(_TMP_DIR / f"{video_id}.*")
    files = glob.glob(pattern)
    if not files:
        logger.error("[stt] 다운로드 파일 없음: pattern='%s'", pattern)
        return None

    audio_path = files[0]
    size_mb = pathlib.Path(audio_path).stat().st_size / 1024 / 1024
    logger.info("[stt] 다운로드 완료: %s (%.1fMB)", audio_path, size_mb)

    # Groq 파일 크기 제한 확인
    if size_mb > _GROQ_MAX_MB:
        logger.error(
            "[stt] 파일 크기 초과 (%.1fMB > %dMB Groq 제한): %s",
            size_mb, _GROQ_MAX_MB, audio_path,
        )
        return None

    return audio_path


def _transcribe(audio_path: str, video_id: str) -> str | None:
    """
    Groq Whisper API로 오디오 파일을 한국어 텍스트로 변환한다.
    모델: whisper-large-v3-turbo
    처리 시간: 60초 이내 (Groq 클라우드)
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.error("[stt] GROQ_API_KEY 미설정 - .env 확인 필요")
        return None

    client = Groq(api_key=api_key)

    logger.info("[stt] Groq STT 시작: video_id='%s' model='%s'", video_id, _GROQ_MODEL)
    try:
        with open(audio_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model=_GROQ_MODEL,
                file=audio_file,
                language="ko",
                response_format="text",
            )

        # response_format="text" → str 직접 반환
        if isinstance(transcription, str):
            text = transcription.strip()
        else:
            text = getattr(transcription, "text", "").strip()

        if not text:
            logger.warning("[stt] Groq STT 결과 비어 있음: video_id='%s'", video_id)
            return None

        logger.info("[stt] Groq STT 성공: video_id='%s' (%d자)", video_id, len(text))
        return text

    except Exception as exc:
        logger.error("[stt] Groq STT 실패: video_id='%s' | %s", video_id, exc)
        return None


def _cleanup(video_id: str) -> None:
    """tmp/ 디렉토리의 해당 영상 임시 파일을 삭제한다."""
    pattern = str(_TMP_DIR / f"{video_id}.*")
    files = glob.glob(pattern)
    for f in files:
        try:
            os.remove(f)
            logger.debug("[stt] 임시 파일 삭제: %s", f)
        except OSError as exc:
            logger.warning("[stt] 임시 파일 삭제 실패: %s | %s", f, exc)
    if files:
        logger.info("[stt] 정리 완료: video_id='%s' (%d파일)", video_id, len(files))


# ---------------------------------------------------------------------------
# 레거시 동기 시그니처 (스켈레톤 호환)
# ---------------------------------------------------------------------------

def download_audio(video_url: str, output_path: str) -> str | None:
    """레거시 시그니처 - 내부적으로 _download_audio 호출."""
    import re
    m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", video_url)
    if not m:
        m = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", video_url)
    if not m:
        return None
    video_id = m.group(1)
    return _download_audio(video_id)


def transcribe_audio(audio_path: str, language: str = "ko") -> str | None:
    """레거시 시그니처 - 내부적으로 _transcribe 호출."""
    return _transcribe(audio_path, "unknown")
