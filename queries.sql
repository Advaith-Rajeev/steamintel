-- SteamIntel demo queries (Phase 2: SQL Queries)
-- Run against a loaded database:  psql -d steamintel -f queries.sql
-- Or run each section by hand during the demo.
--
-- Before the INSERT demo, the setval lines at the bottom of schema.sql must have been
-- run after loading, or the identity columns (developer_id, review_id) will collide.
--
-- The demo game uses app_id 9999991, which is not a real Steam AppID, so the INSERT
-- never collides with loaded data. The DELETE at the end removes it, so the whole file
-- can be re-run.


-- =============================================================
-- (1 pt) INSERT
-- A new indie studio releases an RPG on Steam. Adding a game touches several tables:
-- the game itself, its developer, its genre (through the junction tables), and its
-- one-row review summary.
-- =============================================================

INSERT INTO game (app_id, name, release_date, price_usd, discount_pct, steam_deck_status)
VALUES (9999991, 'Lanterns of Emberfall', DATE '2025-06-15', 14.99, 0, 'Verified');

-- ON CONFLICT DO NOTHING so the demo still works if the studio or genre already exists
INSERT INTO developer (name) VALUES ('SteamIntel Demo Studio')
ON CONFLICT (name) DO NOTHING;

INSERT INTO genre (name) VALUES ('RPG')
ON CONFLICT (name) DO NOTHING;

-- Junction rows: look up the IDs by name instead of hard-coding them
INSERT INTO game_developer (app_id, developer_id)
SELECT 9999991, developer_id FROM developer WHERE name = 'SteamIntel Demo Studio';

INSERT INTO game_genre (app_id, genre_id)
SELECT 9999991, genre_id FROM genre WHERE name = 'RPG';

INSERT INTO review (app_id, review_score_pct, total_reviews)
VALUES (9999991, 92.50, 400);

-- Show the result: the new game with its developer, genre and review score
SELECT g.app_id, g.name, g.price_usd, g.discount_pct, d.name AS developer, ge.name AS genre,
       r.review_score_pct, r.total_reviews
FROM game g
JOIN game_developer gd ON gd.app_id = g.app_id
JOIN developer d       ON d.developer_id = gd.developer_id
JOIN game_genre gg     ON gg.app_id = g.app_id
JOIN genre ge          ON ge.genre_id = gg.genre_id
JOIN review r          ON r.app_id = g.app_id
WHERE g.app_id = 9999991;


-- =============================================================
-- (1 pt) UPDATE
-- The game goes on a 25% off sale. price_usd is the current (after-discount) price,
-- so both columns change together.
-- =============================================================

SELECT app_id, name, price_usd, discount_pct FROM game WHERE app_id = 9999991;  -- before

UPDATE game
SET discount_pct = 25,
    price_usd    = ROUND(price_usd * 0.75, 2)
WHERE app_id = 9999991;

SELECT app_id, name, price_usd, discount_pct FROM game WHERE app_id = 9999991;  -- after: 11.24, 25


-- =============================================================
-- (1.5 pt for both selections) SELECT #1: proposal request 1
-- "Show me the top 5 highest-rated RPGs under $20 that came out this year."
-- The dataset was collected in September 2025, so "this year" means 2025.
-- The minimum review count stops a game with 3 reviews at 100% from topping the list.
-- =============================================================

SELECT g.name, g.release_date, g.price_usd, r.review_score_pct, r.total_reviews
FROM game g
JOIN game_genre gg ON gg.app_id = g.app_id
JOIN genre ge      ON ge.genre_id = gg.genre_id
JOIN review r      ON r.app_id = g.app_id
WHERE ge.name = 'RPG'
  AND g.price_usd < 20
  AND g.release_date >= DATE '2025-01-01'
  AND r.total_reviews >= 50
ORDER BY r.review_score_pct DESC, r.total_reviews DESC
LIMIT 5;


-- =============================================================
-- SELECT #2: proposal request 3
-- "What is the average price of an Indie game compared to an Action game?"
-- Free games are left out so they don't drag the average toward $0.
-- A game tagged both Indie and Action counts in both groups.
-- =============================================================

SELECT ge.name                    AS genre,
       COUNT(*)                   AS paid_games,
       ROUND(AVG(g.price_usd), 2) AS avg_price_usd,
       MIN(g.price_usd)           AS min_price_usd,
       MAX(g.price_usd)           AS max_price_usd
FROM game g
JOIN game_genre gg ON gg.app_id = g.app_id
JOIN genre ge      ON ge.genre_id = gg.genre_id
WHERE ge.name IN ('Indie', 'Action')
  AND g.price_usd > 0
GROUP BY ge.name
ORDER BY avg_price_usd DESC;


-- =============================================================
-- (0.5 pt) DELETE
-- The game is delisted. The foreign keys on review, game_developer and game_genre are
-- ON DELETE CASCADE, so deleting the game removes its child rows automatically.
-- =============================================================

DELETE FROM game WHERE app_id = 9999991;

-- Show the cascade worked: all three should be 0
SELECT (SELECT COUNT(*) FROM review         WHERE app_id = 9999991) AS review_rows,
       (SELECT COUNT(*) FROM game_developer WHERE app_id = 9999991) AS developer_links,
       (SELECT COUNT(*) FROM game_genre     WHERE app_id = 9999991) AS genre_links;

-- The developer is a parent table, so it isn't cascaded; remove the demo studio too
-- (only if it has no other games left)
DELETE FROM developer d
WHERE d.name = 'SteamIntel Demo Studio'
  AND NOT EXISTS (SELECT 1 FROM game_developer gd WHERE gd.developer_id = d.developer_id);
