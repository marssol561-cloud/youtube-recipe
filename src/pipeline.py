"""
전체 파이프라인 오케스트레이터 — 스프린트 4 확장
5단 순차 보충 파이프라인:
  1차: 영상 설명으로 레시피 추출
  2차: youtube-transcript-api 자막 (자막 있는 경우)
  2차-B: yt-dlp 자막 다운로드 (2차 실패 시, 스프린트 4)
  3차: STT fallback (2차·2차-B 모두 실패 시, 스프린트 4)
  4차: 웹 검색 보충 (재료 분량 불완전 시, 스프린트 4)

DB 저장: 하지 않음 (호출 측 결정)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from . import extractor, search_supplement, stt, transcript
from .youtube_client import YouTubeClient

logger = logging.getLogger(__name__)


def run_pipeline(video_ids: list[str]) -> list[dict[str, Any]]:
    """
    영상 ID 리스트를 받아 레시피 추출 결과를 반환한다.
    DB 저장은 호출하는 쪽(API 엔드포인트)에서 담당한다.

    Args:
        video_ids: 필터링 완료된 유튜브 영상 ID 리스트

    Returns:
        [
            {
                "video_id": str,
                "video_url": str,
                "title": str,
                "channel_name": str,
                "source_method": "subtitle"|"stt"|"description"|"search",
                "needs_stt": bool,
                "recipe": {dish_name, ingredients, steps, plating, tips, incomplete_ingredients},
                "error": str | None,
            },
            ...
        ]
    """
    if not video_ids:
        return []

    results: list[dict[str, Any]] = []

    with YouTubeClient() as yt:
        meta_list = _fetch_metadata_batched(yt, video_ids)

    meta_map: dict[str, dict[str, Any]] = {m["video_id"]: m for m in meta_list}

    for vid in video_ids:
        meta = meta_map.get(vid)
        if not meta:
            logger.warning("[Pipeline] 영상 %s: 메타데이터 조회 실패", vid)
            results.append(_error_result(vid, "메타데이터 조회 실패"))
            continue

        result = _process_video(meta)
        results.append(result)

    logger.info(
        "[Pipeline] 전체 완료: %d건 (성공=%d, 실패=%d)",
        len(video_ids),
        sum(1 for r in results if r.get("recipe")),
        sum(1 for r in results if not r.get("recipe")),
    )
    return results


# ---------------------------------------------------------------------------
# 단일 영상 처리
# ---------------------------------------------------------------------------

def _process_video(meta: dict[str, Any]) -> dict[str, Any]:
    """
    5단 순차 파이프라인으로 단일 영상을 처리한다.

    Step 1: 영상 메타데이터 + 설명 준비 (완료)
    Step 2: youtube-transcript-api 자막 추출 시도
    Step 2-B: yt-dlp 자막 다운로드 fallback (Step 2 실패 시)
    Step 3: STT fallback (Step 2-B 도 실패 시)
    Step 4: incomplete_ingredients → 웹 검색 보충
    """
    video_id = meta["video_id"]
    title = meta.get("title", "")
    description = meta.get("description", "")
    video_url = meta.get("video_url", f"https://www.youtube.com/watch?v={video_id}")
    channel_name = meta.get("channel_name", "")

    base_result: dict[str, Any] = {
        "video_id": video_id,
        "video_url": video_url,
        "title": title,
        "channel_name": channel_name,
        "source_method": None,
        "needs_stt": False,
        "recipe": None,
        "error": None,
    }

    # ── Step 2: 자막 추출 (youtube-transcript-api) ───────────────────────
    logger.info("[Pipeline] 영상 %s: 자막 추출 시도 (youtube-transcript-api)", video_id)
    trans_result = transcript.fetch_transcript(video_id)
    transcript_text: str | None = None
    source_method = "description"

    if trans_result["success"]:
        transcript_text = trans_result["text"]
        source_method = "subtitle"
        logger.info("[Pipeline] 영상 %s: 자막 추출 성공 → 레시피 추출", video_id)
    else:
        # ── Step 2-B: yt-dlp 자막 다운로드 fallback ─────────────────────
        logger.info(
            "[Pipeline] 영상 %s: youtube-transcript-api 실패 → yt-dlp 자막 시도", video_id
        )
        ytdlp_result = transcript.fetch_transcript_via_ytdlp(video_id)

        if ytdlp_result["success"]:
            transcript_text = ytdlp_result["text"]
            source_method = "subtitle"
            logger.info("[Pipeline] 영상 %s: yt-dlp 자막 성공 → 레시피 추출", video_id)
        else:
            # ── Step 3: STT fallback ─────────────────────────────────────
            logger.info(
                "[Pipeline] 영상 %s: yt-dlp 자막 실패 → STT 시도", video_id
            )
            base_result["needs_stt"] = True

            stt_result = _run_stt_sync(video_id)
            if stt_result.get("success"):
                transcript_text = stt_result["text"]
                source_method = "stt"
                logger.info("[Pipeline] 영상 %s: STT 성공 → 레시피 추출", video_id)
            else:
                source_method = "description"
                logger.info(
                    "[Pipeline] 영상 %s: STT 실패 → 영상 설명만으로 추출 시도 | 사유: %s",
                    video_id, stt_result.get("error", "알 수 없음"),
                )

    base_result["source_method"] = source_method

    # ── AI 레시피 추출 (1·2·3차 결과 사용) ──────────────────────────────
    logger.info(
        "[Pipeline] 영상 %s: Claude 레시피 추출 | source=%s", video_id, source_method
    )
    recipe = extractor.extract_recipe(
        title=title,
        description=description,
        transcript_text=transcript_text,
        video_id=video_id,
    )

    if recipe is None:
        logger.error("[Pipeline] 영상 %s: 레시피 추출 실패", video_id)
        base_result["error"] = "레시피 추출 실패"
        return base_result

    # ── Step 4: 웹 검색 보충 ─────────────────────────────────────────────
    if recipe.get("incomplete_ingredients"):
        dish_name = recipe.get("dish_name", title)
        logger.info(
            "[Pipeline] 영상 %s: incomplete_ingredients=True → 웹 검색 보충 시작 | dish='%s'",
            video_id, dish_name,
        )
        recipe = search_supplement.supplement_ingredients(
            recipe=recipe,
            dish_name=dish_name,
            video_id=video_id,
        )
        # 보충 성공 시 source_method 갱신
        if not recipe.get("incomplete_ingredients"):
            base_result["source_method"] = "search"
            logger.info("[Pipeline] 영상 %s: 웹 검색 보충 완료 → source_method=search", video_id)
        else:
            logger.info("[Pipeline] 영상 %s: 웹 검색 보충 후에도 incomplete 유지", video_id)
    else:
        logger.info("[Pipeline] 영상 %s: 재료 분량 완전 → 웹 검색 보충 불필요", video_id)

    base_result["recipe"] = recipe
    return base_result


# ---------------------------------------------------------------------------
# STT async → sync 브리지
# ---------------------------------------------------------------------------

def _run_stt_sync(video_id: str) -> dict[str, Any]:
    """
    stt.extract_audio_and_transcribe (async)를 동기 컨텍스트에서 실행한다.

    FastAPI sync endpoint에서 호출 시:
      - 스레드 풀(ThreadPoolExecutor)에서 asyncio.run()을 실행하여
        이미 실행 중인 이벤트 루프와 충돌을 방지한다.
    """
    try:
        # 현재 이벤트 루프 확인
        loop = asyncio.get_running_loop()
        # 이미 루프가 실행 중 → 별도 스레드에서 asyncio.run()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run, stt.extract_audio_and_transcribe(video_id)
            )
            return future.result(timeout=600)  # 10분 타임아웃
    except RuntimeError:
        # 루프 없음 → 직접 실행
        return asyncio.run(stt.extract_audio_and_transcribe(video_id))


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _fetch_metadata_batched(
    yt: YouTubeClient, video_ids: list[str]
) -> list[dict[str, Any]]:
    """video_ids를 50개씩 나눠 메타데이터를 조회한다."""
    all_meta: list[dict[str, Any]] = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            meta = yt.get_video_details(batch)
            all_meta.extend(meta)
        except Exception as exc:
            logger.error("[Pipeline] 메타데이터 배치 조회 실패: %s", exc)
    return all_meta


def _error_result(video_id: str, error_msg: str) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "title": "",
        "channel_name": "",
        "source_method": None,
        "needs_stt": False,
        "recipe": None,
        "error": error_msg,
    }
