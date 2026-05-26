-- 스프린트 8: extra_info + user_context 컬럼 추가
-- 실행 위치: Supabase SQL Editor

ALTER TABLE youtube_recipes
  ADD COLUMN IF NOT EXISTS extra_info   JSONB DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS user_context TEXT  DEFAULT NULL;

-- 확인 쿼리
SELECT column_name, data_type
FROM   information_schema.columns
WHERE  table_name = 'youtube_recipes'
  AND  column_name IN ('extra_info', 'user_context');
