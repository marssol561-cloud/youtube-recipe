"""
Supabase 클라이언트 초기화 + CRUD 본문 구현
스프린트 3: 스켈레톤 → 본문

테이블: youtube_recipes
  id           uuid (PK, default gen_random_uuid())
  video_id     text UNIQUE NOT NULL
  video_url    text
  dish_name    text
  ingredients  jsonb
  steps        jsonb
  plating      text
  tips         jsonb
  source_method text
  incomplete_ingredients boolean
  created_at   timestamptz (default now())
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logger = logging.getLogger(__name__)

_TABLE = "youtube_recipes"
_client: Client | None = None


def get_client() -> Client:
    """Supabase 클라이언트 싱글턴 반환"""
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# CRUD 본문
# ---------------------------------------------------------------------------


def save_recipe(data: dict[str, Any]) -> dict[str, Any] | None:
    """
    레시피 1건을 youtube_recipes 테이블에 저장한다.

    video_id 중복 시 에러 없이 기존 레코드를 반환한다.

    Args:
        data: youtube_recipes 스키마에 맞는 딕셔너리
              (dish_name, ingredients, steps, video_id, video_url 필수)

    Returns:
        저장(또는 기존) 레코드 딕셔너리, 실패 시 None
    """
    client = get_client()
    video_id = data.get("video_id")

    # 중복 확인
    if video_id:
        try:
            existing = (
                client.table(_TABLE)
                .select("*")
                .eq("video_id", video_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                logger.info(
                    "[db] video_id 중복 — 기존 레코드 반환: video_id='%s'", video_id
                )
                return existing.data[0]
        except Exception as exc:
            logger.warning("[db] 중복 확인 조회 실패: %s", exc)

    # INSERT
    try:
        resp = client.table(_TABLE).insert(data).execute()
        if resp.data:
            logger.info("[db] 레시피 저장 완료: video_id='%s'", video_id)
            return resp.data[0]
        logger.warning("[db] INSERT 응답 비어 있음: video_id='%s'", video_id)
        return None
    except Exception as exc:
        logger.error("[db] 레시피 저장 실패: %s", exc)
        return None


def get_recipe(recipe_id: str) -> dict[str, Any] | None:
    """
    UUID로 레시피 단건 조회.

    Args:
        recipe_id: youtube_recipes.id (UUID 문자열)

    Returns:
        레코드 딕셔너리 또는 None
    """
    client = get_client()
    try:
        resp = (
            client.table(_TABLE)
            .select("*")
            .eq("id", recipe_id)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
        return None
    except Exception as exc:
        logger.error("[db] get_recipe 실패: %s", exc)
        return None


def list_recipes(
    search_query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    레시피 목록 조회.
    search_query가 있으면 dish_name ILIKE 검색.

    Args:
        search_query: 검색 키워드 (None이면 전체 조회)
        limit: 최대 반환 건수 (기본 50)
        offset: 페이지 오프셋 (기본 0)

    Returns:
        레코드 리스트 (없으면 빈 리스트)
    """
    client = get_client()
    try:
        query = client.table(_TABLE).select("*")
        if search_query:
            # dish_name ILIKE 검색
            query = query.ilike("dish_name", f"%{search_query}%")
        resp = query.range(offset, offset + limit - 1).execute()
        return resp.data or []
    except Exception as exc:
        logger.error("[db] list_recipes 실패: %s", exc)
        return []


def delete_recipe(recipe_id: str) -> bool:
    """
    UUID로 레시피 1건 삭제.

    Args:
        recipe_id: youtube_recipes.id (UUID 문자열)

    Returns:
        삭제 성공 시 True, 실패 또는 대상 없음 시 False
    """
    client = get_client()
    try:
        resp = (
            client.table(_TABLE)
            .delete()
            .eq("id", recipe_id)
            .execute()
        )
        deleted_count = len(resp.data) if resp.data else 0
        if deleted_count > 0:
            logger.info("[db] 레시피 삭제 완료: id='%s'", recipe_id)
            return True
        logger.warning("[db] 삭제 대상 없음: id='%s'", recipe_id)
        return False
    except Exception as exc:
        logger.error("[db] delete_recipe 실패: %s", exc)
        return False


def upsert_chef(data: dict[str, Any]) -> dict[str, Any] | None:
    """
    셰프 채널을 chef_whitelist 테이블에 upsert한다.

    Args:
        data: chef_whitelist 스키마에 맞는 딕셔너리
              (channel_id, channel_name 필수)

    Returns:
        저장된 레코드 딕셔너리, 실패 시 None
    """
    client = get_client()
    try:
        resp = (
            client.table("chef_whitelist")
            .upsert(data, on_conflict="channel_id")
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        logger.error("[db] upsert_chef 실패: %s", exc)
        return None
