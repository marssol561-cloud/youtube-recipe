-- 스프린트 5: RLS 활성화
-- 실행 위치: Supabase SQL Editor (https://supabase.com/dashboard/project/qrjizcrhxhqqzsrujfmc/sql)
-- 실행 방법: 아래 SQL 전체 복사 → SQL Editor 붙여넣기 → RUN

-- youtube_recipes 테이블 RLS 활성화
ALTER TABLE youtube_recipes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for service role" ON youtube_recipes
  FOR ALL USING (true) WITH CHECK (true);

-- chef_whitelist 테이블 RLS 활성화
ALTER TABLE chef_whitelist ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for service role" ON chef_whitelist
  FOR ALL USING (true) WITH CHECK (true);
