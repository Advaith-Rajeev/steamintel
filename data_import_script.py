import os
import sys
import json
import html
import unicodedata
from pathlib import Path

import pandas as pd
import psycopg2
from sqlalchemy import create_engine
from dotenv import load_dotenv

# load env vars
load_dotenv()

# db setup
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres@localhost:5432/steamintel")
# CHANGED: tell SQLAlchemy to use psycopg2.
# Why: new SQLAlchemy versions read a plain postgresql:// URL as psycopg (version 3), which
# isn't installed, so the script crashed before loading anything.
engine = create_engine(DB_URL.replace("postgresql://", "postgresql+psycopg2://", 1))

# CHANGED: read the CSVs from a folder instead of the current directory.
# Why: the Zenodo zip unpacks to a steam_dataset_2025_csv/ folder, and the README says data
# files live in data/ (git-ignored) so nobody commits 1 GB of CSVs by accident.
# Usage: python data_import_script.py [path/to/steam_dataset_2025_csv]
DATA_DIR = Path(sys.argv[1] if len(sys.argv) > 1
                else os.getenv("DATA_DIR", "data/steam_dataset_2025_csv"))


def read_csv(name, **kwargs):
    return pd.read_csv(DATA_DIR / f"{name}.csv", low_memory=False, **kwargs)


def push(df, table):
    # CHANGED: method='multi' sends 5,000 rows per INSERT instead of one INSERT per row.
    # Why: the junction tables have hundreds of thousands of rows; row-by-row takes far longer.
    print(f" -> {table}: {len(df):,} rows")
    df.to_sql(table, engine, if_exists='append', index=False, chunksize=5000, method='multi')


def load_genre_aliases():
    path = Path(__file__).resolve().parent / "genre_aliases.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    return {label.casefold(): canonical for canonical, labels in mapping.items()
            for label in (canonical, *labels)}


def canonical_genre(name, aliases):
    cleaned = " ".join(unicodedata.normalize("NFC", html.unescape(str(name))).split())
    return aliases.get(cleaned.casefold(), cleaned)


def load_lookup(csv_name, table, id_col, used_ids):
    """Load a reference table (developers, genres, ...) and return {dataset id -> our id}.

    The dataset's reference CSVs have columns (id, name). Our schema calls the key
    developer_id / genre_id / etc., and has UNIQUE(name).
    """
    # Names are literal text: preserve studios named 'null' or '[Ad Space Reserved]'.
    ref = read_csv(csv_name, keep_default_na=False)

    # CHANGED: only keep entries that at least one loaded game uses.
    # Why: the CSVs also cover DLC, soundtracks and demos; their developers/publishers would
    # just be clutter that no game links to.
    ref = ref[ref['id'].isin(used_ids)]

    # CHANGED: drop blank names and collapse duplicate names onto one ID.
    # Why: name is NOT NULL UNIQUE in our schema, so one blank or repeated name (e.g. trailing
    # spaces) would abort the whole insert. Junction rows pointing at a dropped duplicate are
    # remapped to the surviving ID instead of being lost.
    ref = ref.assign(name=ref['name'].astype(str).str.strip())
    ref = ref[ref['name'].ne('') & ref['name'].ne('nan')]
    if table == 'genre':
        aliases = load_genre_aliases()
        ref['name'] = ref['name'].map(lambda name: canonical_genre(name, aliases))
    keep_id = ref.groupby('name')['id'].transform('min')
    id_map = dict(zip(ref['id'], keep_id))

    rows = ref[ref['id'] == keep_id].rename(columns={'id': id_col})[[id_col, 'name']]
    push(rows, table)
    return id_map


def load_junction(links, src_col, table, id_col, id_map, game_ids):
    links = links.rename(columns={'appid': 'app_id'})
    # CHANGED: keep only links for games we loaded, and remap to the de-duplicated IDs.
    # Why: a link to a DLC app_id (not in game) or a dropped ID would violate the foreign key.
    links = links[links['app_id'].isin(game_ids)].copy()
    links[id_col] = links[src_col].map(id_map)
    links = links.dropna(subset=[id_col]).astype({id_col: int})
    push(links[['app_id', id_col]].drop_duplicates(), table)


def main():
    if not (DATA_DIR / "applications.csv").exists():
        print(f"Error: applications.csv not found in {DATA_DIR.resolve()}. "
              "Unzip steam_dataset_2025_csv_package_v1.zip and pass its folder as an argument.")
        return

    with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
        # CHANGED: stop early if the database can't store non-English text.
        # Why: Postgres on Windows defaults to WIN1252, which can't hold the Japanese, Chinese
        # and Korean game names in the dataset; the import would crash partway through.
        cur.execute("SHOW server_encoding")
        if cur.fetchone()[0] != "UTF8":
            print("Error: the database must use UTF8. Recreate it with:\n"
                  "  dropdb steamintel\n"
                  "  createdb -E UTF8 -T template0 --locale=C steamintel\n"
                  "  psql -d steamintel -f schema.sql")
            return

        # CHANGED: empty every table first.
        # Why: to_sql appends, so running the script a second time used to crash on duplicate
        # primary keys. RESTART IDENTITY also resets the ID sequences.
        print("Clearing existing data...")
        cur.execute("TRUNCATE game, developer, publisher, genre, tag RESTART IDENTITY CASCADE")

    print("Transforming Games...")
    # CHANGED: only read the columns we use.
    # Why: applications.csv is ~180 MB; skipping descriptions and requirements saves memory.
    apps = read_csv('applications', usecols=['appid', 'name', 'type', 'is_free', 'release_date',
                                             'mat_final_price', 'mat_discount_percent', 'mat_currency'])

    # filter by game type (feedback: only load type = 'game' rows)
    games = apps[apps['type'] == 'game'].copy()

    # CHANGED: drop games without a name.
    # Why: game.name is NOT NULL; apps whose store page failed to load have no name.
    games = games[games['name'].notna()]

    # CHANGED: price conversion now respects currency and free games.
    # Why: about 40% of prices in the dataset are in EUR, GBP, etc. (depends on the region the
    # API answered from). Dividing those by 100 and calling them USD gives wrong prices, so
    # non-USD prices become NULL (unknown) instead. Free games have no price data at all, so
    # without is_free they'd be NULL too; they're set to 0.00.
    is_usd = games['mat_currency'] == 'USD'
    games['price_usd'] = (games['mat_final_price'] / 100.0).where(is_usd)
    games.loc[games['is_free'] == True, 'price_usd'] = 0.0

    # CHANGED: discount defaults to 0 when missing.
    # Why: discount_pct is NOT NULL DEFAULT 0, but pandas sends missing values as an explicit
    # NULL, which skips the default and fails the constraint.
    games['discount_pct'] = games['mat_discount_percent'].fillna(0).clip(0, 100).astype(int)

    # CHANGED: parse release dates; bad values become NULL instead of failing the insert.
    games['release_date'] = pd.to_datetime(games['release_date'], errors='coerce').dt.date

    # default deck status (the dataset has no Steam Deck data)
    games['steam_deck_status'] = 'Unknown'

    games_clean = games.rename(columns={'appid': 'app_id'})[
        ['app_id', 'name', 'release_date', 'price_usd', 'discount_pct', 'steam_deck_status']
    ].drop_duplicates('app_id')
    game_ids = set(games_clean['app_id'])

    print("Transforming Reviews...")
    # CHANGED: read reviews.csv in chunks, only appid and voted_up.
    # Why: the file is ~670 MB, mostly review text. Loading it whole can run out of memory.
    totals = pd.DataFrame(columns=['sum', 'count'])
    for chunk in pd.read_csv(DATA_DIR / "reviews.csv", usecols=['appid', 'voted_up'], chunksize=200_000):
        # CHANGED: keep only reviews for loaded games.
        # Why: reviews for DLC/demos would create review rows whose app_id isn't in game,
        # which violates the foreign key and aborts the insert.
        chunk = chunk[chunk['appid'].isin(game_ids)]
        chunk['voted_up'] = chunk['voted_up'].astype(str).str.lower().isin(['true', 't', '1'])
        part = chunk.groupby('appid')['voted_up'].agg(['sum', 'count'])
        totals = part if totals.empty else totals.add(part, fill_value=0)

    # one row per game: average score and count (feedback: review is one row per game)
    review_agg = pd.DataFrame({
        'app_id': totals.index,
        'review_score_pct': (totals['sum'] / totals['count'] * 100).round(2).values,
        'total_reviews': totals['count'].astype(int).values,
    })

    print("Pushing to PostgreSQL...")
    push(games_clean, 'game')
    push(review_agg, 'review')

    # CHANGED: load developers, publishers, genres and tags with their junction tables.
    # Why: the old script stopped at game and review ("handle other tables here"), so every
    # query that joins a genre/developer (including both demo SELECTs) returned nothing.
    for name, table, id_col in [('developer', 'developer', 'developer_id'),
                                ('publisher', 'publisher', 'publisher_id'),
                                ('genre', 'genre', 'genre_id')]:
        links = read_csv(f'application_{name}s')
        used = links.loc[links['appid'].isin(game_ids), id_col]
        id_map = load_lookup(f'{name}s', table, id_col, set(used))
        load_junction(links, id_col, f'game_{name}', id_col, id_map, game_ids)

    # CHANGED: Steam categories go into the tag table.
    # Why: the dataset has no user tags (Steam's store API doesn't return them), but it does
    # have categories like "Single-player", "Co-op" and "Steam Achievements". They're the
    # closest substitute, so tag-based queries and recommendations have data.
    links = read_csv('application_categories')
    used = links.loc[links['appid'].isin(game_ids), 'category_id']
    id_map = load_lookup('categories', 'tag', 'tag_id', set(used))
    load_junction(links, 'category_id', 'game_tag', 'tag_id', id_map, game_ids)

    print("Resetting Sequences...")
    # sync db sequences (schema.sql: supplied IDs don't advance them, so the demo INSERT would collide)
    sequence_sql = """
    SELECT setval(pg_get_serial_sequence('developer','developer_id'), COALESCE((SELECT MAX(developer_id) FROM developer), 1));
    SELECT setval(pg_get_serial_sequence('publisher','publisher_id'), COALESCE((SELECT MAX(publisher_id) FROM publisher), 1));
    SELECT setval(pg_get_serial_sequence('genre','genre_id'), COALESCE((SELECT MAX(genre_id) FROM genre), 1));
    SELECT setval(pg_get_serial_sequence('tag','tag_id'), COALESCE((SELECT MAX(tag_id) FROM tag), 1));
    SELECT setval(pg_get_serial_sequence('review','review_id'), COALESCE((SELECT MAX(review_id) FROM review), 1));
    """
    try:
        with psycopg2.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(sequence_sql)
            conn.commit()
    except Exception as e:
        print(f"Error resetting sequences: {e}")

    print("Data import complete!")

if __name__ == "__main__":
    main()
