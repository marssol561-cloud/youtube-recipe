# 스프린트 5 세션로그 — 프론트엔드: 수집 실행 화면

> **날짜:** 2026-05-26
> **담당:** N4 (DEV-대기)
> **스프린트:** S05 — 프론트엔드 수집 실행 화면 + RLS + extractor 한국어 수정
> **상태:** ✅ 완료

---

## 0. 세션 초기화

- [x] `session_log_s04.md` 읽기 완료 — S04 완료 상태 확인
- [x] `session_log_s05.md` 생성
- [x] `session_log_s03.md` 삭제 완료

---

## 1. S04 인계 상태

| 파일 | 상태 |
|------|------|
| `src/youtube_client.py` | 완료 (수정 금지) |
| `src/filter.py` | 완료 (수정 금지) |
| `src/transcript.py` | 완료 (수정 금지) |
| `src/extractor.py` | S05에서 한국어 지시 추가 완료 |
| `src/db.py` | 완료 (수정 금지) |
| `src/pipeline.py` | 완료 (수정 금지) |
| `src/stt.py` | 완료 (수정 금지) |
| `src/main.py` | 완료 (수정 금지) |
| `src/search_supplement.py` | 완료 (수정 금지) |

---

## 2. 구현 완료 내역

### 2-1. src/extractor.py — 한국어 지시 추가
```
변경 전: "- dish_name: 영상에서 만드는 요리의 정확한 이름"
변경 후: "- dish_name: 영상에서 만드는 요리의 정확한 이름. 반드시 한국어로 작성하라. 영어나 다른 언어로 출력하지 마라."
```

### 2-2. .env — SUPABASE_KEY service_role 키 교체
```
변경: anon JWT → sb_secret_***** (service_role, 보안상 마스킹)
```

### 2-3. migrations/s05_rls.sql (신규)
- `youtube_recipes` 테이블 RLS 활성화
- `chef_whitelist` 테이블 RLS 활성화
- CEO가 Supabase SQL Editor에서 직접 실행 필요 (미완료)

### 2-4. itdalab-site 수정

**수정: `src/pages/admin/tools/index.astro`**
- 4번째 카드 추가: 유튜브 레시피 수집 (`/admin/tools/youtube-recipe/` 링크)

**신규: `src/pages/admin/tools/youtube-recipe.astro`**
- iframe으로 Next.js 프론트엔드 (localhost:3001) 임베드
- 상단 헤더: ← 목록으로 링크 + "유튜브 레시피 수집" + "운영중" 뱃지

### 2-5. youtube-recipe-frontend (신규 Next.js 앱)

**위치:** `C:\ITDALab\Products\harness_block\youtube-recipe-frontend\`
**포트:** 3001 (small-keyword-finder:3000과 충돌 방지)
**빌드 검증:** ✅ `npm run build` 성공

| 파일 | 내용 |
|------|------|
| `package.json` | Next.js 16.2.4, React 19, Tailwind CSS 4 |
| `app/page.tsx` | `/admin/tools/youtube-recipe/`로 리다이렉트 |
| `app/admin/tools/page.tsx` | 도구 목록 4개 카드 |
| `app/admin/tools/youtube-recipe/page.tsx` | 수집 실행 화면 (전체 UI) |
| `app/admin/tools/youtube-recipe/library/page.tsx` | 빈 페이지 (S6 구현 예정) |
| `components/RecipeCard.tsx` | 수집/라이브러리 모드 공용 카드 컴포넌트 |
| `components/InputArea.tsx` | 라디오 버튼 + 텍스트 입력 + 수집하기 버튼 |
| `components/TabNav.tsx` | 탭 네비게이션 (수집/라이브러리) |
| `app/api/collect/route.ts` | FastAPI 프록시 (CORS 우회) |
| `app/api/recipes/route.ts` | FastAPI 프록시 (GET/POST) |
| `app/api/recipes/[id]/route.ts` | FastAPI 프록시 (DELETE) |
| `.env.local` | API_URL + NEXT_PUBLIC_API_URL 설정 |

---

## 3. 아키텍처 결정사항

| # | 결정 | 이유 |
|---|------|------|
| 1 | Next.js API route 프록시 패턴 | FastAPI main.py 수정 불가 + CORS 우회 |
| 2 | 포트 3001 | small-keyword-finder(3000)와 충돌 방지 |
| 3 | 프론트엔드 위치 `harness_block/youtube-recipe-frontend/` | small-keyword-finder 패턴 동일 |
| 4 | itdalab-site iframe URL = `http://localhost:3001/...` | 로컬 개발 환경. 배포 시 Railway URL로 교체 필요 |

---

## 4. CEO 확인 필요 사항

### 4-1. Supabase RLS 활성화 (필수)
```
1. https://supabase.com/dashboard/project/qrjizcrhxhqqzsrujfmc/sql 접속
2. migrations/s05_rls.sql 내용 전체 복사
3. SQL Editor에 붙여넣기 → RUN 클릭
```

### 4-2. 로컬 테스트 방법
```
# 터미널 1 — FastAPI 백엔드 (youtube-recipe 폴더)
cd C:\ITDALab\Products\harness_block\youtube-recipe
.venv\Scripts\activate
uvicorn src.main:app --reload

# 터미널 2 — Next.js 프론트엔드 (youtube-recipe-frontend 폴더)
cd C:\ITDALab\Products\harness_block\youtube-recipe-frontend
npm run dev

# 브라우저
http://127.0.0.1:3001/admin/tools/youtube-recipe/
```

### 4-3. 완료 기준 수동 테스트
| # | 테스트 | 방법 |
|---|--------|------|
| 1 | 키워드 수집 | "쟁반짜장" 입력 → 수집하기 → 요리명 한국어 확인 |
| 2 | 영상 URL 수집 | `https://www.youtube.com/watch?v=zMg_hTUh0ds` 입력 → 단일 결과 |
| 3 | 저장하기 | 결과 카드 → [저장하기] → "저장됨 ✓" 변경 |
| 4 | 건너뛰기 | 결과 카드 → [건너뛰기] → 카드 제거 |
| 5 | 빈 입력 에러 | 빈칸으로 수집하기 → 에러 메시지 |
| 6 | 라이브러리 탭 | [라이브러리] 클릭 → /library 빈 페이지 |
| 7 | RLS 후 API | SQL 실행 후 수집 → 저장 정상 동작 |

---

## 5. 파일 정리

- 삭제: `session_log_s03.md` ✅
- 유지: `session_log_s04.md`, `session_log_s05.md`

---

*N4 DEV-대기 | 스프린트 5 완료 | 2026-05-26*
