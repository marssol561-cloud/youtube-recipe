"""
영상 선정 필터
스프린트 2 구현

트랙 1 (화이트리스트 셰프 채널): 길이 기준만 통과하면 무조건 선정
트랙 2 (일반): 구독자 10만 이상 OR 조회수 100만 이상 (6개월 이내 신작은 30만으로 완화)
공통: 영상 길이 5분(300초) 미만 제외
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# config/whitelist.json 경로 (이 파일 기준 상대 경로)
_WHITELIST_PATH = Path(__file__).parent.parent / "config" / "whitelist.json"


# ---------------------------------------------------------------------------
# 화이트리스트 로드
# ---------------------------------------------------------------------------

def load_whitelist(path: Path | str | None = None) -> list[str]:
    """
    config/whitelist.json에서 채널 ID 목록을 로드한다.

    Args:
        path: whitelist.json 경로 (기본: config/whitelist.json)

    Returns:
        채널 ID 문자열 리스트. 파일 없거나 파싱 오류 시 빈 리스트.
    """
    target = Path(path) if path else _WHITELIST_PATH
    if not target.exists():
        logger.warning("whitelist.json 없음: '%s' → 빈 목록 사용", target)
        return []

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        channels = data.get("channels", [])
        ids = [ch["channel_id"] for ch in channels if ch.get("channel_id")]
        logger.info("화이트리스트 로드: %d개 채널", len(ids))
        return ids
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error("whitelist.json 파싱 오류: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 필터 적용
# ---------------------------------------------------------------------------

def apply_filter(
    videos: list[dict[str, Any]],
    whitelist: list[str],
    min_duration: int = 300,
    general_min_subscribers: int = 100_000,
    general_min_views: int = 1_000_000,
    recent_months: int = 6,
    recent_min_views: int = 300_000,
) -> list[dict[str, Any]]:
    """
    영상 선정 필터를 적용한다.

    Args:
        videos: get_video_details() 형식의 영상 리스트
        whitelist: 화이트리스트 채널 ID 목록 (load_whitelist() 결과)
        min_duration: 최소 영상 길이(초). 미만이면 제외 (기본 300초 = 5분)
        general_min_subscribers: 트랙 2 구독자 기준 (기본 100,000)
        general_min_views: 트랙 2 조회수 기준 (기본 1,000,000)
        recent_months: 신작 기준 개월 수 (기본 6개월)
        recent_min_views: 신작 완화 조회수 기준 (기본 300,000)

    Returns:
        선정된 영상 리스트 (track_type 필드 추가됨)
    """
    whitelist_set = set(whitelist)
    today = date.today()
    recent_cutoff = today - timedelta(days=recent_months * 30)

    passed: list[dict[str, Any]] = []
    stats = {"total": len(videos), "short": 0, "track1": 0, "track2": 0, "rejected": 0}

    for v in videos:
        vid = v.get("video_id", "?")
        duration = v.get("duration_seconds", 0)
        channel_id = v.get("channel_id", "")
        view_count = v.get("view_count", 0)
        subscriber_count = v.get("subscriber_count", 0)
        upload_date_str = v.get("upload_date", "")

        # ── 공통: 길이 기준 ──────────────────────────────────────────────
        if duration < min_duration:
            logger.debug("[제외] 길이 미달 %ds < %ds | %s", duration, min_duration, vid)
            stats["short"] += 1
            continue

        # ── 트랙 1: 화이트리스트 ─────────────────────────────────────────
        if channel_id in whitelist_set:
            result = {**v, "track_type": "chef"}
            passed.append(result)
            stats["track1"] += 1
            logger.debug("[트랙1 통과] %s | %s", v.get("channel_name", ""), vid)
            continue

        # ── 트랙 2: 일반 기준 ────────────────────────────────────────────
        # 업로드 날짜 파싱
        upload_date: date | None = None
        if upload_date_str:
            try:
                upload_date = date.fromisoformat(upload_date_str)
            except ValueError:
                pass

        is_recent = upload_date is not None and upload_date >= recent_cutoff

        # 기준 충족 여부
        meets_subscribers = subscriber_count >= general_min_subscribers
        meets_views = view_count >= general_min_views
        meets_recent_views = is_recent and view_count >= recent_min_views

        if meets_subscribers or meets_views or meets_recent_views:
            result = {**v, "track_type": "general"}
            passed.append(result)
            stats["track2"] += 1
            reason = []
            if meets_subscribers:
                reason.append(f"구독자 {subscriber_count:,}")
            if meets_views:
                reason.append(f"조회수 {view_count:,}")
            if meets_recent_views:
                reason.append(f"신작({upload_date}) 조회수 {view_count:,}")
            logger.debug("[트랙2 통과] %s | %s", " / ".join(reason), vid)
        else:
            stats["rejected"] += 1
            logger.debug(
                "[제외] 트랙2 미달 | 구독자=%d / 조회수=%d | %s",
                subscriber_count, view_count, vid,
            )

    logger.info(
        "필터 결과: 전체 %d → 통과 %d (트랙1=%d, 트랙2=%d) | 제외: 길이부족=%d, 기준미달=%d",
        stats["total"],
        stats["track1"] + stats["track2"],
        stats["track1"],
        stats["track2"],
        stats["short"],
        stats["rejected"],
    )
    return passed
