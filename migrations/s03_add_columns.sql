-- 스프린트 3 마이그레이션
-- youtube_recipes 테이블에 신규 컬럼 추가

ALTER TABLE youtube_recipes
  ADD COLUMN IF NOT EXISTS source_method TEXT,
  ADD COLUMN IF NOT EXISTS incomplete_ingredients BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS video_url TEXT,
  ADD COLUMN IF NOT EXISTS plating TEXT,
  ADD COLUMN IF NOT EXISTS tips JSONB,
  ADD COLUMN IF NOT EXISTS channel_name TEXT;
