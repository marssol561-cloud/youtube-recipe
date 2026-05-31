-- 스프린트 9 (v2.2 보완): 분류 메타 4개 컬럼 추가 + CHECK + 인덱스 (멱등)

-- 컬럼
ALTER TABLE youtube_recipes
  ADD COLUMN IF NOT EXISTS cuisine_type          TEXT,
  ADD COLUMN IF NOT EXISTS estimated_cost        INT,
  ADD COLUMN IF NOT EXISTS difficulty            TEXT,
  ADD COLUMN IF NOT EXISTS classification_status TEXT;

-- CHECK 제약 (pg_constraint 조회 후 멱등 추가)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_cuisine_type') THEN
    ALTER TABLE youtube_recipes ADD CONSTRAINT chk_cuisine_type
    CHECK (cuisine_type IS NULL OR cuisine_type IN
      ('한식','중식','일식','양식','분식','카페','베이킹','디저트','주류','기타'));
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_difficulty') THEN
    ALTER TABLE youtube_recipes ADD CONSTRAINT chk_difficulty
    CHECK (difficulty IS NULL OR difficulty IN ('상','중','하'));
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_classification_status') THEN
    ALTER TABLE youtube_recipes ADD CONSTRAINT chk_classification_status
    CHECK (classification_status IS NULL OR classification_status IN ('success','failed'));
  END IF;
END $$;

-- 인덱스 (멱등)
CREATE INDEX IF NOT EXISTS idx_youtube_recipes_cuisine_type
  ON youtube_recipes(cuisine_type);

-- 확인 쿼리
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'youtube_recipes'
  AND column_name IN ('cuisine_type','estimated_cost','difficulty','classification_status');
