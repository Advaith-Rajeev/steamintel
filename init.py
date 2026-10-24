"""Set up dependencies, load the midterm database, and normalize genre labels."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
TABLES = ("game", "developer", "publisher", "genre", "tag", "review",
          "game_developer", "game_publisher", "game_genre", "game_tag")


def normalize_genres(conn):
    from data_import_script import canonical_genre, load_genre_aliases

    aliases = load_genre_aliases()
    changed = 0
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT genre_id, name FROM genre ORDER BY genre_id")
            original = cur.fetchall()
            cur.execute("SELECT setval(pg_get_serial_sequence('genre', 'genre_id'), "
                        "GREATEST(COALESCE(MAX(genre_id), 1), 1), MAX(genre_id) IS NOT NULL) FROM genre")
            for old_id, name in original:
                canonical = canonical_genre(name, aliases)
                if not canonical or canonical == name:
                    continue
                cur.execute("INSERT INTO genre (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (canonical,))
                cur.execute("SELECT genre_id FROM genre WHERE name = %s", (canonical,))
                new_id = cur.fetchone()[0]
                cur.execute("INSERT INTO game_genre (app_id, genre_id) "
                            "SELECT app_id, %s FROM game_genre WHERE genre_id = %s "
                            "ON CONFLICT DO NOTHING", (new_id, old_id))
                cur.execute("DELETE FROM game_genre WHERE genre_id = %s", (old_id,))
                cur.execute("DELETE FROM genre WHERE genre_id = %s", (old_id,))
                changed += 1
    return changed


def initialize(args):
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL", "postgresql://postgres@localhost:5432/steamintel")
    required_files = ("applications", "reviews", "developers", "publishers", "genres", "categories",
                      "application_developers", "application_publishers", "application_genres", "application_categories")
    conn = psycopg2.connect(url, connect_timeout=10)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SHOW server_encoding")
                if cur.fetchone()[0] != "UTF8":
                    raise ValueError("This dataset requires a UTF8 PostgreSQL database.")
                cur.execute("SET LOCAL search_path TO public")
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                existing = {row[0] for row in cur.fetchall()} & set(TABLES)
                if existing and existing != set(TABLES):
                    raise ValueError("Incomplete schema. Missing tables: " + ", ".join(sorted(set(TABLES) - existing)))
                if not existing:
                    missing = [name + ".csv" for name in required_files if not (args.data_dir / (name + ".csv")).is_file()]
                    if missing:
                        raise ValueError("Missing CSV files in " + str(args.data_dir) + ": " + ", ".join(missing))
                    cur.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
                cur.execute("SELECT COUNT(*) FROM game")
                populated = cur.fetchone()[0] > 0
                if not populated and not args.reload:
                    for table in TABLES[1:]:
                        cur.execute(f"SELECT EXISTS (SELECT 1 FROM {table})")
                        if cur.fetchone()[0]:
                            raise ValueError("Partial data found with no games. --reload explicitly replaces existing table data.")
        if not populated or args.reload:
            missing = [name + ".csv" for name in required_files if not (args.data_dir / (name + ".csv")).is_file()]
            if missing:
                raise ValueError("Missing CSV files in " + str(args.data_dir) + ": " + ", ".join(missing))
            print("Loading all ten tables through the team's batched SQL INSERT importer.", flush=True)
            subprocess.run([sys.executable, str(ROOT / "data_import_script.py"), str(args.data_dir)], cwd=ROOT, check=True)
        else:
            print("Existing games found; preserving the loaded data. Use --reload only to replace it.", flush=True)
        changed = normalize_genres(conn)
        aliases_path = ROOT / "genre_aliases.json"
        canonical = set(json.loads(aliases_path.read_text(encoding="utf-8")))
        with conn:
            with conn.cursor() as cur:
                counts = {}
                for table in TABLES:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM game WHERE price_usd IS NULL")
                unknown_prices = cur.fetchone()[0]
                cur.execute("SELECT name FROM genre ORDER BY name")
                unmapped = [row[0] for row in cur.fetchall() if row[0] not in canonical]
        print(json.dumps({"table_counts": counts, "normalized_genre_rows": changed,
                          "unknown_usd_prices": unknown_prices, "unmapped_genres": unmapped}, indent=2, ensure_ascii=False))
        print("Ready for the PostgreSQL console demo. Run: psql -d steamintel -f queries.sql")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "steam_dataset_2025_csv")
    parser.add_argument("--setup-only", action="store_true", help="Install dependencies and create .env only")
    parser.add_argument("--skip-install", action="store_true", help="Reuse installed .venv dependencies")
    parser.add_argument("--reload", action="store_true", help="Replace existing table data using the team's importer")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.data_dir = args.data_dir.resolve()
    if args.worker:
        initialize(args)
        return 0
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    for filename in ("genre_aliases.json", "data_import_script.py", "schema.sql", "requirements.txt"):
        if not (ROOT / filename).is_file():
            parser.error("Missing project file: " + filename)
    python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    if not args.skip_install:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    if not (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print("Created .env. Configure DATABASE_URL before importing. The database must already exist.", flush=True)
    if args.setup_only:
        print("Setup complete. Next: python init.py --data-dir path/to/steam_dataset_2025_csv")
        return 0
    return subprocess.run([str(python), str(ROOT / "init.py"), "--worker", "--data-dir", str(args.data_dir),
                           *(["--reload"] if args.reload else [])], cwd=ROOT).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Initialization failed: {exc}", file=sys.stderr)
        sys.exit(1)
