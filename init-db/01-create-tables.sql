-- Crawl data table for Bach Hoa Xanh SEO audit
CREATE TABLE IF NOT EXISTS crawl_data (
    id              SERIAL PRIMARY KEY,
    url             TEXT NOT NULL,
    status_code     INTEGER,

    -- Title
    title           TEXT,
    title_length    INTEGER DEFAULT 0,

    -- Meta tags
    meta_description        TEXT,
    meta_description_length INTEGER DEFAULT 0,
    meta_keywords           TEXT,

    -- Open Graph
    og_title        TEXT,
    og_description  TEXT,
    og_image        TEXT,
    og_url          TEXT,

    -- Technical
    canonical_url   TEXT,
    favicon         TEXT,
    viewport        TEXT,
    charset         VARCHAR(20),
    robots_meta     TEXT,

    -- Headings
    h1_tags         JSONB DEFAULT '[]',
    h1_count        INTEGER DEFAULT 0,
    h2_tags         JSONB DEFAULT '[]',
    h2_count        INTEGER DEFAULT 0,
    h3_tags         JSONB DEFAULT '[]',
    h3_count        INTEGER DEFAULT 0,

    -- Links
    total_links     INTEGER DEFAULT 0,
    internal_links  INTEGER DEFAULT 0,
    external_links  INTEGER DEFAULT 0,

    -- Images
    total_images        INTEGER DEFAULT 0,
    images_with_alt     INTEGER DEFAULT 0,
    images_without_alt  INTEGER DEFAULT 0,

    -- Content
    word_count      INTEGER DEFAULT 0,

    -- Raw JSON data
    links_json      JSONB DEFAULT '[]',
    images_json     JSONB DEFAULT '[]',

    -- Timestamps
    crawled_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for fast URL lookups
CREATE INDEX IF NOT EXISTS idx_crawl_data_url ON crawl_data (url);
CREATE INDEX IF NOT EXISTS idx_crawl_data_crawled_at ON crawl_data (crawled_at DESC);
