-- 所有时间都是北京时间（UTC+8）的 ISO 字符串，带 +08:00 偏移；纯日期为 YYYY-MM-DD。
-- 停滞等级不落库：读取时由 last_commit_at 和 stage 现算，永远不会过期。

CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,            -- = 仓库名（小写）
    title           TEXT NOT NULL,
    owner           TEXT NOT NULL,
    stage           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    members_json    TEXT NOT NULL DEFAULT '[]',
    tags_json       TEXT NOT NULL DEFAULT '[]',
    milestones_json TEXT NOT NULL DEFAULT '[]',
    blockers_json   TEXT NOT NULL DEFAULT '[]',
    commits_json    TEXT NOT NULL DEFAULT '[]',
    repo            TEXT NOT NULL,
    last_sha        TEXT NOT NULL,
    last_commit_at  TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY,
    project_id   TEXT NOT NULL,
    sha          TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    source       TEXT NOT NULL,
    UNIQUE (project_id, sha)                     -- 幂等
);

-- 项目的投稿目标。会议: ref = deadlines.id；期刊: ref = 期刊名，planned = 预计投稿日期。
CREATE TABLE IF NOT EXISTS project_targets (
    project_id TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('venue', 'journal')),
    ref        TEXT NOT NULL,
    planned    TEXT,
    role       TEXT NOT NULL,
    pos        INTEGER NOT NULL,
    PRIMARY KEY (project_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_targets_ref ON project_targets (kind, ref);

-- 会议实体，全组共享一份。source: ccfddl（自动同步）| manual（手工，优先）。
CREATE TABLE IF NOT EXISTS deadlines (
    id         TEXT PRIMARY KEY,                 -- ccfddl 的会议 id，如 eurocrypt27
    kind       TEXT NOT NULL DEFAULT 'conference',
    name       TEXT NOT NULL,
    url        TEXT NOT NULL DEFAULT '',
    rank       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 一届会议可以有多轮截稿，也可以有单独的摘要截稿。due 为空表示日期未公布。
CREATE TABLE IF NOT EXISTS deadline_rounds (
    deadline_id  TEXT NOT NULL,
    idx          INTEGER NOT NULL,
    due          TEXT,
    abstract_due TEXT,
    comment      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (deadline_id, idx)
);

-- 以下三张表本期不使用，为后续功能预留。
CREATE TABLE IF NOT EXISTS members (
    id    INTEGER PRIMARY KEY,
    login TEXT NOT NULL UNIQUE,                  -- GitHub 用户名
    name  TEXT,
    role  TEXT                                   -- pi / phd / ms
);
CREATE TABLE IF NOT EXISTS resources (
    id        INTEGER PRIMARY KEY,
    type      TEXT NOT NULL,                     -- doc / skill / dataset / bib
    title     TEXT NOT NULL,
    url       TEXT,
    owner     TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    scope      TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE
);
