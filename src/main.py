"""
유튜브 레시피 수집 도구 — FastAPI 진입점
스프린트 3: API 엔드포인트 추가
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db, pipeline
from .youtube_client import YouTubeClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube Recipe Collector",
    description="유튜브 요리 영상에서 레시피를 자동 수집·추출하는 도구",
    version="0.3.0",
)

# ---------------------------------------------------------------------------
# CORS 미들웨어
# 환경변수 ALLOWED_ORIGINS: 쉼표로 구분된 허용 출처 목록
# 기본값: http://localhost:3001
# ---------------------------------------------------------------------------
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3001")
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 요청 모델
# ---------------------------------------------------------------------------


class CollectRequest(BaseModel):
    """POST /api/collect 요청 바디"""
    input_text: str  # 키워드, 채널명, 채널 URL, 영상 URL 중 하나


class RecipeSaveRequest(BaseModel):
    """POST /api/recipes 요청 바디 — 파이프라인 결과 1건"""
    video_id: str
    video_url: str | None = None
    title: str | None = None
    channel_name: str | None = None
    source_method: str | None = None
    needs_stt: bool = False
    dish_name: str
    ingredients: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    plating: str | None = None
    tips: list[str] = []
    incomplete_ingredients: bool = False


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check() -> dict:
    """서버 상태 확인"""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/collect — 파이프라인 실행 (저장 안 함)
# ---------------------------------------------------------------------------


@app.post("/api/collect")
def collect(req: CollectRequest) -> dict[str, Any]:
    """
    입력(키워드/채널/URL)을 받아 YouTube 영상 검색 → 레시피 추출 결과를 반환한다.
    DB 저장은 하지 않는다. 저장은 POST /api/recipes로 별도 요청한다.

    Returns:
        {
            "input": str,
            "count": int,
            "results": [파이프라인 결과 리스트]
        }
    """
    input_text = req.input_text.strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="input_text가 비어 있습니다.")

    # 영상 목록 확보
    try:
        with YouTubeClient() as yt:
            videos = yt.resolve_input(input_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[/api/collect] YouTube API 오류: %s", exc)
        raise HTTPException(status_code=502, detail=f"YouTube API 오류: {exc}")

    if not videos:
        return {"input": input_text, "count": 0, "results": []}

    video_ids = [v["video_id"] for v in videos]

    # 파이프라인 실행
    try:
        results = pipeline.run_pipeline(video_ids)
    except Exception as exc:
        logger.error("[/api/collect] 파이프라인 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"파이프라인 오류: {exc}")

    return {"input": input_text, "count": len(results), "results": results}


# ---------------------------------------------------------------------------
# POST /api/recipes — 레시피 1건 DB 저장
# ---------------------------------------------------------------------------


@app.post("/api/recipes", status_code=201)
def create_recipe(req: RecipeSaveRequest) -> dict[str, Any]:
    """
    추출된 레시피 1건을 Supabase에 저장한다.
    동일 video_id가 이미 존재하면 에러 없이 기존 레코드를 반환한다.
    """
    data: dict[str, Any] = {
        "video_id": req.video_id,
        "video_url": req.video_url or f"https://www.youtube.com/watch?v={req.video_id}",
        "dish_name": req.dish_name,
        "ingredients": req.ingredients,
        "steps": req.steps,
        "plating": req.plating,
        "tips": req.tips,
        "source_method": req.source_method,
        "incomplete_ingredients": req.incomplete_ingredients,
    }

    saved = db.save_recipe(data)
    if saved is None:
        raise HTTPException(status_code=500, detail="Supabase 저장 실패")
    return saved


# ---------------------------------------------------------------------------
# GET /api/recipes — 목록 조회
# ---------------------------------------------------------------------------


@app.get("/api/recipes")
def list_recipes(
    q: str | None = Query(default=None, description="요리명 검색 키워드"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """
    저장된 레시피 목록을 반환한다.
    q 파라미터가 있으면 dish_name ILIKE 검색.
    """
    recipes = db.list_recipes(search_query=q, limit=limit, offset=offset)
    return {"count": len(recipes), "recipes": recipes}


# ---------------------------------------------------------------------------
# GET /api/recipes/{id} — 단건 조회
# ---------------------------------------------------------------------------


@app.get("/api/recipes/{recipe_id}")
def get_recipe(recipe_id: str) -> dict[str, Any]:
    """UUID로 레시피 단건 조회"""
    recipe = db.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail=f"레시피를 찾을 수 없습니다: {recipe_id}")
    return recipe


# ---------------------------------------------------------------------------
# DELETE /api/recipes/{id} — 삭제
# ---------------------------------------------------------------------------


@app.delete("/api/recipes/{recipe_id}", status_code=200)
def delete_recipe(recipe_id: str) -> dict[str, str]:
    """UUID로 레시피 1건 삭제"""
    success = db.delete_recipe(recipe_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"레시피를 찾을 수 없습니다: {recipe_id}")
    return {"message": f"삭제 완료: {recipe_id}"}
