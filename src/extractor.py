"""
AI 레시피 추출 모듈 — Claude API(claude-sonnet-4-20250514)로 레시피 구조화
스프린트 3 구현

제약:
- 모델: claude-sonnet-4-20250514 고정
- Claude API 호출: 최대 2회 (초회 + JSON 파싱 실패 시 재시도)
- response_format 미사용 — 프롬프트에서 JSON 지시
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-5"
# 주의: 스프린트 3 지시서에 "claude-sonnet-4-20250514" 명시됐으나
# 해당 모델 ID는 API에 존재하지 않음(404). 가장 근접한 유효 ID인
# "claude-sonnet-4-5"(Sonnet 4.5)로 대체. CEO 보고 후 확정 필요.
_MAX_RETRIES = 2  # 총 호출 횟수 한도

_SYSTEM_PROMPT = """\
당신은 유튜브 요리 영상에서 레시피를 추출하는 전문가입니다.
영상 제목, 설명, 자막(또는 설명만)을 분석하여 정확한 레시피 정보를 JSON으로 반환합니다.

기본 추출 우선순위: 재료(분량 포함) > 조리법 > 팁·비법 > 플레이팅.
사용자 맥락이 제공되면 맥락에 따라 우선순위를 조정하고, 맥락에서 요구하는 부가 정보를 extra_info 객체에 담아 반환하라.

반드시 아래 JSON 스키마를 정확히 따르세요. JSON 외 다른 텍스트는 출력하지 마세요:

{
  "dish_name": "요리명 (문자열)",
  "ingredients": [
    {"name": "재료명", "amount": "분량 (없으면 빈 문자열)", "unit": "단위 (없으면 빈 문자열)"}
  ],
  "steps": [
    {"step_number": 1, "description": "조리 단계 설명"}
  ],
  "plating": "플레이팅 설명 또는 null (언급이 없으면 반드시 null)",
  "tips": ["팁1", "팁2"],
  "incomplete_ingredients": false,
  "extra_info": null
}

규칙:
- dish_name: 영상에서 만드는 요리의 정확한 이름. 반드시 한국어로 작성하라. 영어나 다른 언어로 출력하지 마라.
- ingredients: 모든 재료와 분량을 최대한 추출. 분량 정보가 없으면 amount를 빈 문자열("")로 설정
- steps: 조리 순서대로 번호 부여. 설명은 명확하고 구체적으로
- plating: 자막/설명에 플레이팅(담기, 모양, 장식) 언급이 있을 때만 채움. 없으면 반드시 null
- tips: 비법, 포인트, 주의사항 등. 없으면 빈 배열 []
- incomplete_ingredients: 재료 분량이 전반적으로 불완전(누락 다수, "적당량"만 반복)이면 true, 아니면 false
- extra_info: 사용자 맥락이 없으면 반드시 null. 사용자 맥락이 있으면 맥락에서 요구하는 부가 정보를 key-value 객체로 반환. key 이름은 맥락에 맞게 자율 생성 (예: {"중식_기법": "...", "대체_불가_재료": ["..."]})
"""


def extract_recipe(
    title: str,
    description: str,
    transcript_text: str | None,
    video_id: str = "",
    context: str | None = None,
) -> dict[str, Any] | None:
    """
    Claude API를 사용해 영상 정보에서 레시피를 추출한다.

    Args:
        title: 영상 제목
        description: 영상 설명 (YouTube description)
        transcript_text: 자막 텍스트 (None이면 description만 사용)
        video_id: 로깅용 영상 ID
        context: 사용자 특이사항 (채널 성격, 추출 방향 등). None이면 기본 추출

    Returns:
        레시피 딕셔너리 또는 None (추출 실패)
    """
    user_content = _build_user_content(title, description, transcript_text, context)
    client = _get_client()

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info(
                "[extractor] Claude API 호출 (시도 %d/%d): video_id='%s'",
                attempt, _MAX_RETRIES, video_id
            )
            message = client.messages.create(
                model=_MODEL,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            raw = message.content[0].text.strip()
            recipe = _parse_json(raw)
            if recipe is not None:
                logger.info(
                    "[extractor] 추출 성공: video_id='%s' dish='%s'",
                    video_id, recipe.get("dish_name", "?")
                )
                return recipe
            else:
                logger.warning(
                    "[extractor] JSON 파싱 실패 (시도 %d): video_id='%s' | raw[:200]=%s",
                    attempt, video_id, raw[:200]
                )
        except anthropic.APIError as exc:
            logger.error("[extractor] API 오류 (시도 %d): %s", attempt, exc)
            if attempt == _MAX_RETRIES:
                return None

    logger.error("[extractor] 최대 재시도 초과: video_id='%s'", video_id)
    return None


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    """Anthropic 클라이언트 반환."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다.")
    return anthropic.Anthropic(api_key=api_key)


def _build_user_content(
    title: str,
    description: str,
    transcript_text: str | None,
    context: str | None = None,
) -> str:
    """Claude에게 전달할 사용자 메시지를 조립한다."""
    parts = [f"[영상 제목]\n{title}"]

    if description:
        # 설명이 너무 길면 앞 3000자만 사용
        desc = description[:3000] if len(description) > 3000 else description
        parts.append(f"[영상 설명]\n{desc}")

    if transcript_text:
        # 자막이 너무 길면 앞 5000자만 사용
        trans = transcript_text[:5000] if len(transcript_text) > 5000 else transcript_text
        parts.append(f"[자막 텍스트]\n{trans}")
    else:
        parts.append("[자막 텍스트]\n(자막 없음 — 영상 설명에서만 추출하세요)")

    if context:
        parts.append(f"[사용자 맥락]\n{context}")

    parts.append("\n위 정보를 분석하여 레시피 JSON을 반환하세요.")
    return "\n\n".join(parts)


def _parse_json(text: str) -> dict[str, Any] | None:
    """
    텍스트에서 JSON을 파싱한다.
    코드 블록(```json ... ```) 감싸여 있으면 제거 후 파싱.
    """
    # 코드 블록 제거
    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        # JSON 블록만 추출 시도
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return None
