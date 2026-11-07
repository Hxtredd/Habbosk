"""knowledge.db - the SK Brain's own SQLite database (separate from the quiz
memory habbosk.db and the World Atlas atlas.db, which it only reads).

Design rules (owner's requirements):
  * every claim keeps where it came from (sources + evidence), who added it
    (contributors), and its whole history (versions + events) - nothing is
    silently overwritten or deleted;
  * verification is an explicit, recorded review backed by evidence
    (direct or independent confirmation); repeating a claim never verifies it;
  * personal vs shared scope exists from the start so a community library
    can be added later without redesign (no accounts / sync yet).
"""

SCHEMA_VERSION = 3

SCHEMA = """
-- who did something: the local user, the importer, SK itself, later other users
CREATE TABLE IF NOT EXISTS contributors (
    id TEXT PRIMARY KEY,               -- stable id (uuid for people)
    display_name TEXT,
    kind TEXT NOT NULL,                -- local_user | external_user | moderator | system | importer
    created TEXT NOT NULL
);

-- where information comes from: a guide page, screenshot, video, game observation...
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                -- see model.SOURCE_KINDS
    title TEXT,
    reference TEXT,                    -- URL, file name, 'video.mp4 @ 00:12', ...
    content_hash TEXT,                 -- same material => same hash (counts once as evidence)
    captured_at TEXT,                  -- when the material was made / taken
    added_at TEXT NOT NULL,
    added_by TEXT NOT NULL REFERENCES contributors(id),
    private INTEGER NOT NULL DEFAULT 0,  -- 1 = may contain personal info: never shareable
    raw_text TEXT,                     -- original text if any (pages are kept whole)
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sources_hash ON sources(content_hash);

-- parts of a source that keep their context (page headings)
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    heading_path TEXT,                 -- 'Trabajos > Armería > Montar un arma'
    position INTEGER,
    text TEXT NOT NULL
);

-- one statement SK can retrieve and reason over
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,                -- current wording (older ones in claim_versions)
    subject TEXT,                      -- what it is about ('Gran Casino', 'Montar un arma')
    category TEXT,                     -- searchable category ('lugares', 'trabajos', ...)
    origin TEXT NOT NULL,              -- see model.ORIGINS
    status TEXT NOT NULL,              -- see model.STATUSES
    scope TEXT NOT NULL DEFAULT 'personal',   -- see model.SCOPES
    owner_id TEXT REFERENCES contributors(id),  -- whose knowledge it is (personal = only theirs)
    section_id INTEGER REFERENCES sections(id),   -- context it was taken from
    version INTEGER NOT NULL DEFAULT 1,
    created TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES contributors(id),
    updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

-- why a claim might be true: a source + what kind of support it gives
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    source_id INTEGER REFERENCES sources(id),
    kind TEXT NOT NULL,                -- see model.EVIDENCE_KINDS
    locator TEXT,                      -- quote / region / timestamp inside the source
    added_by TEXT NOT NULL REFERENCES contributors(id),
    added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);

-- relations between claims
CREATE TABLE IF NOT EXISTS claim_links (
    id INTEGER PRIMARY KEY,
    from_claim INTEGER NOT NULL REFERENCES claims(id),
    to_claim INTEGER NOT NULL REFERENCES claims(id),
    kind TEXT NOT NULL,                -- supports | contradicts | derived_from | supersedes | duplicate_of
    added_by TEXT NOT NULL REFERENCES contributors(id),
    added_at TEXT NOT NULL,
    UNIQUE (from_claim, to_claim, kind)
);

-- every earlier wording of a claim (corrections never destroy the original)
CREATE TABLE IF NOT EXISTS claim_versions (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    version INTEGER NOT NULL,
    text TEXT NOT NULL,
    subject TEXT,
    category TEXT,
    old_status TEXT,                   -- status the replaced wording had
    replaced_by TEXT REFERENCES contributors(id),
    replaced_at TEXT NOT NULL,
    reason TEXT,
    UNIQUE (claim_id, version)
);

-- the claim's history: created, reviewed, corrected, evidence added, share proposed...
CREATE TABLE IF NOT EXISTS claim_events (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    event TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT,
    by TEXT NOT NULL REFERENCES contributors(id),
    at TEXT NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_claim ON claim_events(claim_id);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- B2 search index: DERIVED data (pre-processed words of each claim), rebuilt
-- from `claims` whenever needed; it never changes a claim. An FTS5 copy
-- (claim_fts, created when SQLite supports it) only speeds up finding
-- candidates - scoring always uses the viewer's own visible claims.
CREATE TABLE IF NOT EXISTS claim_index (
    claim_id INTEGER PRIMARY KEY REFERENCES claims(id),
    terms TEXT NOT NULL,               -- text + subject + category, normalised / stemmed
    subject_terms TEXT NOT NULL,
    norm TEXT NOT NULL DEFAULT '',     -- subject + text, normalised (whole-name matching)
    subject_norm TEXT NOT NULL DEFAULT ''
);
"""

# v1 -> v2: owner of each claim (= who created it) and the status of old versions
# v2 -> v3: the search index (claim_index above, created by SCHEMA; filled by
#           the store) - additive, no existing row is changed
MIGRATIONS = {
    3: [],
    2: [
        "ALTER TABLE claims ADD COLUMN owner_id TEXT REFERENCES contributors(id)",
        "UPDATE claims SET owner_id = created_by WHERE owner_id IS NULL",
        "ALTER TABLE claim_versions ADD COLUMN old_status TEXT",
    ],
}
