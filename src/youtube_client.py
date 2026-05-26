"""
YouTube Data API v3 클라이언트
스프린트 2 구현

쿼터 소모량 (일일 한도: 10,000유닛):
  search.list       100유닛 / 1회 호출
  videos.list         1유닛 / 1회 호출 (최대 50개 ID)
  channels.list       1유닛 / 1회 호출 (최대 50개 ID)
  playlistItems.list  1유닛 / 1회 호출 (최대 50개 결과)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_TIMEOUT = 10.0

# 쿼터 소모량 상수
QUOTA_SEARCH = 100
QUOTA_VIDEOS = 1
QUOTA_CHANNELS = 1
QUOTA_PLAYLIST = 1


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _parse_iso8601_duration(duration: str) -> int:
    """ISO 8601 기간 문자열(PT4M13S) → 초 단위 정수 변환"""
    pattern = re.compile(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?"
        r"T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?"
    )
    m = pattern.match(duration)
    if not m:
        return 0
    years, months, days, hours, minutes, seconds = m.groups(default="0")
    return (
        int(years) * 365 * 86400
        + int(months) * 30 * 86400
        + int(days) * 86400
        + int(hours) * 3600
        + int(minutes) * 60
        + int(float(seconds))
    )


def _extract_video_id(text: str) -> str | None:
    """URL 또는 텍스트에서 YouTube video_id(11자) 추출"""
    # youtube.com/watch?v=VIDEO_ID (&list=... 등 파라미터 앞)
    m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", text)
    if m:
        return m.group(1)
    # youtu.be/VIDEO_ID
    m = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", text)
    if m:
        return m.group(1)
    # youtube.com/shorts/VIDEO_ID
    m = re.search(r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})", text)
    if m:
        return m.group(1)
    return None


def _extract_channel_id_from_url(text: str) -> str | None:
    """
    youtube.com/channel/UCxxxxxx 형식에서 채널 ID 직접 추출.
    /@ 핸들 형식은 None 반환(검색 필요).
    """
    m = re.search(r"youtube\.com/channel/(UC[a-zA-Z0-9_-]+)", text)
    return m.group(1) if m else None


def _extract_handle_from_url(text: str) -> str | None:
    """youtube.com/@handle 형식에서 핸들 추출"""
    m = re.search(r"youtube\.com/@([a-zA-Z0-9_.-]+)", text)
    return m.group(1) if m else None


def _extract_custom_name_from_url(text: str) -> str | None:
    """youtube.com/c/customname 형식에서 이름 추출"""
    m = re.search(r"youtube\.com/c/([a-zA-Z0-9_.-]+)", text)
    return m.group(1) if m else None


def _is_youtube_url(text: str) -> bool:
    return "youtube.com/" in text or "youtu.be/" in text


# ---------------------------------------------------------------------------
# YouTubeClient
# ---------------------------------------------------------------------------

class YouTubeClient:
    """
    YouTube Data API v3 래퍼.

    지원 입력 방식 (resolve_input):
      - 영상 URL  : youtube.com/watch?v= / youtu.be/ / youtube.com/shorts/
      - 채널 URL  : youtube.com/channel/UC... / youtube.com/@handle / youtube.com/c/name
      - 채널명/키워드: 채널 검색 → 없으면 영상 키워드 검색
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ["YOUTUBE_API_KEY"]
        self._http = httpx.Client(timeout=DEFAULT_TIMEOUT)

    # ── 내부 요청 헬퍼 ──────────────────────────────────────────────────────

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """공통 GET 요청. HTTP 오류 시 httpx.HTTPStatusError 발생."""
        params["key"] = self.api_key
        resp = self._http.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    # ── 공개 API ────────────────────────────────────────────────────────────

    def search_by_keyword(
        self, keyword: str, max_results: int = 20
    ) -> list[dict[str, Any]]:
        """
        키워드로 영상 검색 (search.list).

        쿼터 소모: 100유닛 / 1회

        Returns:
            [{"video_id", "title", "channel_id", "channel_name"}, ...]
        """
        logger.info(
            "[쿼터 -100유닛] search.list(video) | 키워드='%s' max=%d", keyword, max_results
        )
        data = self._get("search", {
            "part": "id,snippet",
            "type": "video",
            "q": keyword,
            "maxResults": min(max_results, 50),
            "relevanceLanguage": "ko",
        })

        results = [
            {
                "video_id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel_id": item["snippet"]["channelId"],
                "channel_name": item["snippet"]["channelTitle"],
            }
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        logger.info("  → %d개 반환", len(results))
        return results

    def get_channel_videos(
        self, channel_id: str, max_results: int = 50
    ) -> list[dict[str, Any]]:
        """
        채널 영상 목록 수집.
        channels.list(1유닛) → uploads 플레이리스트 → playlistItems.list(1유닛)

        쿼터 소모: 2유닛 / 1회 (페이지당 +1유닛)

        Returns:
            [{"video_id", "title", "channel_id", "channel_name"}, ...]
        """
        # Step 1: uploads 플레이리스트 ID 확보
        logger.info("[쿼터 -1유닛] channels.list | channel_id='%s'", channel_id)
        ch_data = self._get("channels", {
            "part": "contentDetails,snippet",
            "id": channel_id,
        })
        items = ch_data.get("items", [])
        if not items:
            logger.warning("  → 채널 없음: '%s'", channel_id)
            return []

        uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        channel_name = items[0]["snippet"]["title"]

        # Step 2: 영상 목록 수집
        logger.info("[쿼터 -1유닛] playlistItems.list | playlist='%s'", uploads_id)
        pl_data = self._get("playlistItems", {
            "part": "snippet",
            "playlistId": uploads_id,
            "maxResults": min(max_results, 50),
        })

        results = []
        for item in pl_data.get("items", []):
            snippet = item["snippet"]
            video_id = snippet.get("resourceId", {}).get("videoId")
            if video_id:
                results.append({
                    "video_id": video_id,
                    "title": snippet["title"],
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                })

        logger.info("  → %d개 반환", len(results))
        return results

    def get_video_details(self, video_ids: list[str]) -> list[dict[str, Any]]:
        """
        영상 상세 정보 수집 (videos.list + channels.list).

        쿼터 소모: 2유닛 / 1회 호출 (최대 50개 ID)

        Returns:
            [{
                "video_id": str,
                "title": str,
                "channel_id": str,
                "channel_name": str,
                "view_count": int,
                "subscriber_count": int,
                "upload_date": str,       # YYYY-MM-DD
                "duration_seconds": int,
                "description": str,
                "video_url": str,
            }, ...]
        """
        if not video_ids:
            return []

        batch = video_ids[:50]
        logger.info("[쿼터 -1유닛] videos.list | %d개", len(batch))
        v_data = self._get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
        })

        results: list[dict[str, Any]] = []
        channel_ids: set[str] = set()

        for item in v_data.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            details = item.get("contentDetails", {})
            channel_id = snippet["channelId"]
            channel_ids.add(channel_id)

            published = snippet.get("publishedAt", "")
            upload_date = published[:10] if published else ""

            results.append({
                "video_id": item["id"],
                "title": snippet["title"],
                "channel_id": channel_id,
                "channel_name": snippet["channelTitle"],
                "view_count": int(stats.get("viewCount", 0)),
                "subscriber_count": 0,          # 아래 채널 조회 후 채움
                "upload_date": upload_date,
                "duration_seconds": _parse_iso8601_duration(
                    details.get("duration", "PT0S")
                ),
                "description": snippet.get("description", ""),
                "video_url": f"https://www.youtube.com/watch?v={item['id']}",
            })

        # 구독자 수 보완 (channels.list)
        if channel_ids:
            logger.info("[쿼터 -1유닛] channels.list(구독자수) | %d개 채널", len(channel_ids))
            ch_data = self._get("channels", {
                "part": "statistics",
                "id": ",".join(channel_ids),
            })
            sub_map = {
                ch["id"]: int(ch["statistics"].get("subscriberCount", 0))
                for ch in ch_data.get("items", [])
                if not ch["statistics"].get("hiddenSubscriberCount", False)
            }
            for r in results:
                r["subscriber_count"] = sub_map.get(r["channel_id"], 0)

        logger.info("  → %d개 상세 반환", len(results))
        return results

    def get_channel_id_by_name(self, channel_name: str) -> str | None:
        """
        채널명으로 채널 ID 검색 (search.list type=channel).

        쿼터 소모: 100유닛 / 1회

        Returns:
            채널 ID 또는 None
        """
        logger.info(
            "[쿼터 -100유닛] search.list(channel) | 채널명='%s'", channel_name
        )
        data = self._get("search", {
            "part": "id,snippet",
            "type": "channel",
            "q": channel_name,
            "maxResults": 1,
        })
        items = data.get("items", [])
        if not items:
            logger.info("  → 채널 없음")
            return None
        ch_id = items[0]["id"]["channelId"]
        logger.info("  → 채널 발견: '%s'", ch_id)
        return ch_id

    def resolve_input(self, input_text: str) -> list[dict[str, Any]]:
        """
        입력 텍스트를 자동 판별하여 적절한 메서드를 호출한다.

        판별 순서:
          1. 영상 URL (watch?v= / youtu.be/ / shorts/) → get_video_details
          2. 채널 URL (channel/UC...) → get_channel_videos → get_video_details
          3. 채널 URL (@handle / /c/name) → 채널명 검색 → get_channel_videos → get_video_details
          4. 잘못된 YouTube URL → ValueError
          5. 일반 텍스트 → 채널명 검색 시도 → 없으면 키워드 검색 → get_video_details

        Returns:
            get_video_details 형식의 영상 리스트

        Raises:
            ValueError: 빈 입력 또는 인식 불가 YouTube URL
        """
        text = input_text.strip()
        if not text:
            raise ValueError("입력값이 비어 있습니다. 키워드, 채널명, 또는 URL을 입력하세요.")

        # ── 1. 영상 URL ──────────────────────────────────────────────────────
        video_id = _extract_video_id(text)
        if video_id:
            logger.info("[resolve_input] 영상 URL 감지 → video_id='%s'", video_id)
            return self.get_video_details([video_id])

        # ── 2. 채널 URL (/channel/UC...) ────────────────────────────────────
        ch_id = _extract_channel_id_from_url(text)
        if ch_id:
            logger.info("[resolve_input] 채널 URL(/channel/) 감지 → '%s'", ch_id)
            basic = self.get_channel_videos(ch_id)
            if not basic:
                return []
            return self.get_video_details([v["video_id"] for v in basic])

        # ── 3. 채널 URL (@handle / /c/name) ──────────────────────────────────
        handle = _extract_handle_from_url(text) or _extract_custom_name_from_url(text)
        if handle:
            logger.info("[resolve_input] 채널 핸들/커스텀명 감지 → '%s'", handle)
            ch_id = self.get_channel_id_by_name(handle)
            if ch_id:
                basic = self.get_channel_videos(ch_id)
                return self.get_video_details([v["video_id"] for v in basic])
            return []

        # ── 4. 인식 불가 YouTube URL ─────────────────────────────────────────
        if _is_youtube_url(text):
            raise ValueError(
                f"인식할 수 없는 YouTube URL입니다: '{text}'\n"
                "지원 형식: watch?v=..., youtu.be/..., youtube.com/shorts/..., "
                "youtube.com/@handle, youtube.com/channel/UC..., youtube.com/c/name"
            )

        # ── 5. @handle 텍스트 입력 ────────────────────────────────────────────
        if text.startswith("@"):
            handle = text.lstrip("@")
            logger.info("[resolve_input] @핸들 텍스트 → 채널 검색: '%s'", handle)
            ch_id = self.get_channel_id_by_name(handle)
            if ch_id:
                basic = self.get_channel_videos(ch_id)
                return self.get_video_details([v["video_id"] for v in basic])
            return []

        # ── 6. 채널명 검색 시도 → 없으면 키워드 검색 ─────────────────────────
        logger.info("[resolve_input] 텍스트 입력 → 채널명 검색 시도: '%s'", text)
        ch_id = self.get_channel_id_by_name(text)
        if ch_id:
            basic = self.get_channel_videos(ch_id)
            return self.get_video_details([v["video_id"] for v in basic])

        logger.info("[resolve_input] 채널 없음 → 키워드 검색: '%s'", text)
        basic = self.search_by_keyword(text)
        if not basic:
            return []
        return self.get_video_details([v["video_id"] for v in basic])

    # ── 컨텍스트 매니저 ─────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "YouTubeClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
