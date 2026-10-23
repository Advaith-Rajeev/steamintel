-- SteamIntel: ER-to-relational schema (PostgreSQL)
-- Run: psql -d steamintel -f schema.sql

DROP TABLE IF EXISTS game_tag, game_genre, game_publisher, game_developer,
                     review, tag, genre, publisher, developer, game CASCADE;

-- ---------- Entities ----------

CREATE TABLE game (
    app_id            INTEGER       PRIMARY KEY,               -- Steam's appid
    name              VARCHAR(500)  NOT NULL,
    type              VARCHAR(20)   NOT NULL DEFAULT 'game'
                      CHECK (type IN ('game', 'dlc', 'demo', 'music', 'video', 'series', 'episode', 'mod', 'hardware', 'advertising')),
    is_free           BOOLEAN       NOT NULL DEFAULT FALSE,
    release_date      DATE,
    required_age      SMALLINT      NOT NULL DEFAULT 0 CHECK (required_age BETWEEN 0 AND 21),
    short_description TEXT,
    header_image      TEXT,                                    -- URL
    metacritic_score  SMALLINT      CHECK (metacritic_score BETWEEN 0 AND 100),
    recommendations   INTEGER       CHECK (recommendations >= 0),
    initial_price     NUMERIC(10,2) CHECK (initial_price >= 0),    -- USD, e.g. 9.99
    final_price       NUMERIC(10,2) CHECK (final_price >= 0),      -- price after discount
    discount_percent  SMALLINT      NOT NULL DEFAULT 0 CHECK (discount_percent BETWEEN 0 AND 100),
    supports_windows  BOOLEAN       NOT NULL DEFAULT FALSE,
    supports_mac      BOOLEAN       NOT NULL DEFAULT FALSE,
    supports_linux    BOOLEAN       NOT NULL DEFAULT FALSE
);

CREATE TABLE developer (
    developer_id  INTEGER      PRIMARY KEY,
    name          VARCHAR(255) NOT NULL
);

CREATE TABLE publisher (
    publisher_id  INTEGER      PRIMARY KEY,
    name          VARCHAR(255) NOT NULL
);

CREATE TABLE genre (
    genre_id  INTEGER      PRIMARY KEY,
    name      VARCHAR(100) NOT NULL UNIQUE
);

-- Steam store "categories" (Single-player, Multi-player, Steam Achievements, ...)
CREATE TABLE tag (
    tag_id  INTEGER      PRIMARY KEY,
    name    VARCHAR(100) NOT NULL UNIQUE
);

-- One row per user review; a game has many reviews (1:N)
CREATE TABLE review (
    review_id          BIGINT      PRIMARY KEY,                -- Steam's recommendationid
    app_id             INTEGER     NOT NULL REFERENCES game(app_id) ON DELETE CASCADE,
    author_steam_id    BIGINT      NOT NULL,
    voted_up           BOOLEAN     NOT NULL,                   -- TRUE = positive review
    review_text        TEXT,
    language           VARCHAR(50),
    playtime_at_review INTEGER     CHECK (playtime_at_review >= 0),   -- minutes
    votes_up           INTEGER     NOT NULL DEFAULT 0 CHECK (votes_up >= 0),
    votes_funny        INTEGER     NOT NULL DEFAULT 0 CHECK (votes_funny >= 0),
    steam_purchase     BOOLEAN,
    received_for_free  BOOLEAN,
    created_at         TIMESTAMP   NOT NULL
);

-- ---------- Many-to-many relationships ----------

CREATE TABLE game_developer (
    app_id        INTEGER REFERENCES game(app_id)            ON DELETE CASCADE,
    developer_id  INTEGER REFERENCES developer(developer_id) ON DELETE CASCADE,
    PRIMARY KEY (app_id, developer_id)
);

CREATE TABLE game_publisher (
    app_id        INTEGER REFERENCES game(app_id)            ON DELETE CASCADE,
    publisher_id  INTEGER REFERENCES publisher(publisher_id) ON DELETE CASCADE,
    PRIMARY KEY (app_id, publisher_id)
);

CREATE TABLE game_genre (
    app_id    INTEGER REFERENCES game(app_id)    ON DELETE CASCADE,
    genre_id  INTEGER REFERENCES genre(genre_id) ON DELETE CASCADE,
    PRIMARY KEY (app_id, genre_id)
);

CREATE TABLE game_tag (
    app_id  INTEGER REFERENCES game(app_id) ON DELETE CASCADE,
    tag_id  INTEGER REFERENCES tag(tag_id)  ON DELETE CASCADE,
    PRIMARY KEY (app_id, tag_id)
);

-- ---------- Indexes for common lookups ----------

CREATE INDEX idx_review_app_id  ON review(app_id);
CREATE INDEX idx_game_price     ON game(final_price);
CREATE INDEX idx_game_release   ON game(release_date);
