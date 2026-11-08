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

SCHEMA_VERSION = 4

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
    note TEXT,
    related_claim INTEGER              -- the other claim an event refers to (privacy filtering)
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
    subject_norm TEXT NOT NULL DEFAULT '',
    positive INTEGER NOT NULL DEFAULT 1   -- 0 = negated statement ("is not", "no está")
);

-- B5 knowledge model ----------------------------------------------------------------
-- relations are DATA: B3's five are built in; more can be added without code
CREATE TABLE IF NOT EXISTS relations (
    key TEXT PRIMARY KEY,
    value_type TEXT NOT NULL,          -- place | person | number | text
    cardinality TEXT NOT NULL,         -- one | many (used for conflicts from B9 on)
    changes INTEGER NOT NULL DEFAULT 0,   -- 1 = changes over time
    labels TEXT NOT NULL DEFAULT '{}',    -- {"en": "who looks after it", "es": ...}
    sentence_forms TEXT NOT NULL DEFAULT '[]',   -- added relations: "{value} opens the {place}"
    question_forms TEXT NOT NULL DEFAULT '[]',   -- added relations: "when does {place} open"
    builtin INTEGER NOT NULL DEFAULT 0,
    added_by TEXT,
    added_at TEXT
);

-- the structured fact each claim states: DERIVED data (like claim_index), rebuilt
-- from claims; a fact has no status, evidence or owner of its own - its claim's
CREATE TABLE IF NOT EXISTS claim_facts (
    claim_id INTEGER PRIMARY KEY REFERENCES claims(id),
    claim_version INTEGER NOT NULL,
    text_sha TEXT NOT NULL,            -- the wording the fact was read from
    relation TEXT,                     -- NULL = this claim states no fact
    place TEXT,
    value TEXT,
    absent INTEGER NOT NULL DEFAULT 0, -- 1 = documented absence
    qualifier TEXT NOT NULL DEFAULT '',
    display TEXT NOT NULL DEFAULT '',
    as_of TEXT,                        -- reserved for dates (later milestones)
    valid_until TEXT,
    method TEXT NOT NULL               -- how it was read, e.g. 'b3-forms-v1'
);

-- people / places / things, created by a person (never merged automatically)
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    key TEXT NOT NULL,                 -- normalised name (as facts compare names)
    kind TEXT NOT NULL DEFAULT '',     -- free label: "room", "person", "city"...
    status TEXT NOT NULL,              -- approved | proposed (automatic contributors)
    scope TEXT NOT NULL DEFAULT 'personal',
    owner_id TEXT REFERENCES contributors(id),
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_key ON entities(key);
CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    alias TEXT NOT NULL,
    key TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,              -- approved | proposed - only approved ones are used
    scope TEXT NOT NULL DEFAULT 'personal',
    owner_id TEXT REFERENCES contributors(id),
    added_by TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aliases_key ON aliases(key);
-- "same as" between two entities: only added by a person, reversible (kept as history)
CREATE TABLE IF NOT EXISTS entity_links (
    id INTEGER PRIMARY KEY,
    a INTEGER NOT NULL REFERENCES entities(id),
    b INTEGER NOT NULL REFERENCES entities(id),
    scope TEXT NOT NULL DEFAULT 'personal',
    owner_id TEXT REFERENCES contributors(id),
    active INTEGER NOT NULL DEFAULT 1,
    added_by TEXT NOT NULL,
    added_at TEXT NOT NULL,
    removed_by TEXT,
    removed_at TEXT
);
"""

# v1 -> v2: owner of each claim (= who created it) and the status of old versions
# v2 -> v3: the search index (claim_index above, created by SCHEMA; filled by
#           the store) and claim_events.related_claim - additive, no existing
#           row is changed
# v3 -> v4: the B5 tables above (created by SCHEMA, additive). An older file is
#           NEVER upgraded in place: brain/migrate.py upgrades a verified copy.
MIGRATIONS = {
    4: [],
    3: ["ALTER TABLE claim_events ADD COLUMN related_claim INTEGER"],
    2: [
        "ALTER TABLE claims ADD COLUMN owner_id TEXT REFERENCES contributors(id)",
        "UPDATE claims SET owner_id = created_by WHERE owner_id IS NULL",
        "ALTER TABLE claim_versions ADD COLUMN old_status TEXT",
    ],
}

# B5.2 storage separation ------------------------------------------------------------
# Every library file (personal / shared) - its meta also records role, namespace
# and, for a personal file, its owner.
LIBRARY_SCHEMA = """
-- personal file: a row here points at a row of ANOTHER file (the shared one):
-- the reference is (namespace, id), never a bare number
CREATE TABLE IF NOT EXISTS xrefs (
    tbl TEXT NOT NULL,
    row_id INTEGER NOT NULL,
    col TEXT NOT NULL,
    target_ns TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    PRIMARY KEY (tbl, row_id, col)
);
-- personal file: what the owner allowed of one of their sources (B5.2 consent)
CREATE TABLE IF NOT EXISTS source_grants (
    source_id INTEGER PRIMARY KEY,
    opaque_id TEXT NOT NULL UNIQUE,     -- the only name shared storage ever sees
    token TEXT NOT NULL,                -- withdrawal token (secret, only here)
    levels TEXT NOT NULL DEFAULT '[]',  -- [] (stub) / quote / details / full_text
    granted_at TEXT,
    withdrawn_at TEXT,
    withdraw_state TEXT                 -- NULL | blocked | outbox_done | complete
);
"""

# Only in shared.db: withdrawals and moderator decisions (no owner identities,
# no content - opaque ids, keyed token hashes, random proposal tokens).
SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_opaque (
    source_id INTEGER PRIMARY KEY,      -- a source stub in this file
    opaque_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS source_tokens (
    opaque_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL            -- keyed hash of the owner's withdrawal token
);
CREATE TABLE IF NOT EXISTS withdrawals (
    opaque_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    state TEXT NOT NULL,                -- blocked | complete
    blocked_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS evidence_withdrawn (
    evidence_id INTEGER PRIMARY KEY,
    at TEXT NOT NULL
);
-- rows of shared.db that are a redacted copy (same id) of an owner's original row
-- kept in that owner's personal file (B5.2 migration: quotes withheld, no consent)
CREATE TABLE IF NOT EXISTS projections (
    tbl TEXT NOT NULL,
    id INTEGER NOT NULL,
    PRIMARY KEY (tbl, id)
);
CREATE TABLE IF NOT EXISTS proposal_decisions (
    token TEXT PRIMARY KEY,             -- random; means nothing outside the owner's outbox
    decision TEXT NOT NULL,             -- approved | rejected
    note TEXT,
    claim_id INTEGER,                   -- the NEW shared claim (approved)
    by TEXT NOT NULL,
    at TEXT NOT NULL
);
"""
