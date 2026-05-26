"""
웹 검색 보충 모듈 — DuckDuckGo 검색 + Claude API로 불완전 재료 보충
스프린트 4 구현

트리거: extractor.py에서 incomplete_ingredients=True 반환 시

제약:
  - duckduckgo-search DDGS 동기 호출
  - Claude API 호출 1회 (보충 전용)
  - 검색 실패 시 기존 결과 그대로 반환 (에러로 처리하지 않음)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import anthropic
from duckduckgo_search import DDGS
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-5"
_SEARCH_RESULTS = 3   # 상위 N건 수집
_MAX_BODY_CHARS = 800  # 검색 결과 본문 1건당 최대 글자 수

_SUPPLEMENT_SYSTEM = """\
당신은 레시피 데이터 보정 전문가입니다.
기존 추출 결과의 재료 분량이 불완전합니다.
아래 웹 검색 자료를 참고하여 재료 분량(amount, unit)을 최대한 보충하세요.

규칙:
- ingredients 배열의 분량·단위만 보충하세요. 재료 목록 자체를 줄이지 마세요.
- steps, plating, tips는 절대 변경하지 마세요.
- 보충 후 incomplete_ingredients를 재평가하여 true/false를 갱신하세요.
- 동일한 JSON 스키마로만 반환하세요. JSON 외 텍스트는 출력하지 마세요.

JSON 스키마:
{
  "dish_name": "요리명",
  "ingredients": [{"name": "재료명", "amount": "분량", "unit": "단위"}],
  "steps": [{"step_number": 1, "description": "조리 단계"}],
  "plating": "플레이팅 또는 null",
  "tips": ["팁1"],
  "incomplete_ingredients": false
}
"""


def supplement_ingredients(
    recipe: dict[str, Any],
    dish_name: str,
    video_id: str = "",
) -> dict[str, Any]:
    """
    incomplete_ingredients=True인 레시피의 재료 분량을 웹 검색으로 보충한다.

    Args:
        recipe: 기존 추출 레시피 딕셔너리
        dish_name: 검색에 사용할 요리명
        video_id: 로깅용 영상 ID

    Returns:
        보충된 레시피 딕셔너리. 실패 시 기존 recipe 그대로 반환.
    """
    logger.info(
        "[search_supplement] 웹 검색 보충 시작: video_id='%s' dish='%s'",
        video_id, dish_name
    )

    # ── Step 1: DuckDuckGo 검색 ─────────────────────────────────────────
    search_texts = _search_recipe(dish_name, video_id)
    if not search_texts:
        logger.warning(
            "[search_supplement] 검색 결과 없음 — 기존 결과 유지: video_id='%s'", video_id
        )
        return recipe

    # ── Step 2: Claude API로 재료 분량 보충 ─────────────────────────────
    supplemented = _supplement_with_claude(recipe, search_texts, dish_name, video_id)
    if supplemented is None:
        logger.warning(
            "[search_supplement] Claude 보충 실패 — 기존 결과 유지: video_id='%s'", video_id
        )
        return recipe

    logger.info(
        "[search_supplement] 보충 완료: video_id='%s' incomplete=%s",
        video_id, supplemented.get("incomplete_ingredients")
    )
    return supplemented


# ---------------------------------------------------------------------------
# 내부 함수
# ---------------------------------------------------------------------------

def _search_recipe(dish_name: str, video_id: str) -> list[str]:
    """
    DuckDuckGo로 레시피 정보를 검색하여 상위 N건의 본문을 반환한다.
    """
    query = f"{dish_name} 레시피 재료 분량"
    logger.info("[search_supplement] DDG 검색: '%s'", query)

    texts: list[str] = []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region="kr-kr", max_results=_SEARCH_RESULTS))

        for r in results:
            body = r.get("body", "") or ""
            if body:
                texts.append(body[:_MAX_BODY_CHARS])
                logger.debug("[search_supplement] 검색 결과: %s", body[:80])

    except Exception as exc:
        logger.warning("[search_supplement] DDG 검색 실패: %s", exc)

    logger.info("[search_supplement] 검색 결과 %d건 수집", len(texts))
    return texts


def _supplement_with_claude(
    recipe: dict[str, Any],
    search_texts: list[str],
    dish_name: str,
    video_id: str,
) -> dict[str, Any] | None:
    """
    Claude API에 기존 레시피 + 검색 결과를 전달하여 재료 분량을 보충한다.
    최대 1회 호출.
    """
    client = _get_client()
    user_content = _build_supplement_content(recipe, search_texts, dish_name)

    try:
        logger.info(
            "[search_supplement] Claude API 호출 (1회): video_id='%s'", video_id
        )
        message = client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            system=_SUPPLEMENT_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = message.content[0].text.strip()
        result = _parse_json(raw)
        return result

    except anthropic.APIError as exc:
        logger.error("[search_supplement] Claude API 오류: %s", exc)
        return None
    except Exception as exc:
        logger.error("[search_supplement] 예외: %s", exc)
        return None


def _build_supplement_content(
    recipe: dict[str, Any],
    search_texts: list[str],
    dish_name: str,
) -> str:
    """Claude에게 전달할 보충 요청 메시지를 조립한다."""
    existing_json = json.dumps(recipe, ensure_ascii=False, indent=2)
    search_block = "\n\n".join(
        f"[검색 결과 {i+1}]\n{text}" for i, text in enumerate(search_texts)
    )

    return (
        f"[기존 추출 결과 — {dish_name}]\n"
        f"```json\n{existing_json}\n```\n\n"
        f"[웹 검색 참고 자료]\n{search_block}\n\n"
        "위 검색 자료를 참고하여 재료 분량을 보충하고, 동일 JSON 스키마로 반환하세요."
    )


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다.")
    return anthropic.Anthropic(api_key=api_key)


def _parse_json(text: str) -> dict[str, Any] | None:
    """텍스트에서 JSON을 파싱한다. 코드블록 제거 후 파싱."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# 레거시 시그니처 (스켈레톤 호환)
# ---------------------------------------------------------------------------

def expand_keywords(keyword: str) -> list[str]:
    """레거시 시그니처 — 키워드 확장 (미사용)."""
    return [keyword]


def deduplicate_videos(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """레거시 시그니처 — video_id 기준 중복 제거."""
    seen: set[str] = set()
    result = []
    for v in videos:
        vid = v.get("video_id", "")
        if vid not in seen:
            seen.add(vid)
            result.append(v)
    return result
