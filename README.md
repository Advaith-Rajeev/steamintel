# SteamIntel

An LLM-powered database application that helps gamers and indie developers explore Steam's catalog. Instead of static search filters, users ask questions in plain English (for example, "Show me the top 5 highest-rated RPGs under $20"), and the agent writes and runs the SQL against a PostgreSQL database to answer them.

## Personas

- **Budget gamer:** finds highly rated games in a genre within a price limit.
- **Indie developer:** analyzes price trends, popular tags, and competition in a genre.

## Example questions

1. Show me the top 5 highest-rated RPGs under $20 that came out this year.
2. Which developers have published the most games with over 90% positive reviews?
3. What is the average price of an Indie game compared to an Action game?
4. Find games similar to Stardew Valley based on tags that are currently discounted.
5. List the top 10 most expensive games on Steam and their review scores.

## Team and file ownership

One owner per file so edits don't collide. If you need a change in someone else's file, message them.

| File | Owner | Purpose |
|---|---|---|
| `schema.sql` | TODO | ER-to-relational DDL (tables, keys, constraints) |
| `insert_data.sql` | AJ | INSERT statements that fill the database |
| `queries.sql` | AJ | Demo SQL queries (SELECT, INSERT, UPDATE, DELETE) |
| `tool.py` | Alvin | LLM agent and its tools |
| `README.md`, repo setup | TODO | Documentation and repo management |
| `report/` | All | Phase report and ER diagram |

## Tech stack

- **Database:** PostgreSQL (local)
- **Agent:** Python, Gemini API (`google-genai`), `psycopg2`
- **Cache:** in-memory in `tool.py` for now (Redis is a possible later upgrade)
- **Frontend:** planned, not started

## Dataset

Source: [steam-dataset-2025](https://github.com/vintagedon/steam-dataset-2025).

- The GitHub repo only contains a 5,000-game sample in its `raw` folder, which is enough for development.
- The full dataset (about 240k applications) is on Zenodo, DOI `10.5281/zenodo.17266922`. The CSV release is about 85 MB.
- Data files are large, so do **not** commit them. Keep them in a local `data/` folder (ignored by Git).

## Setup

### 1. Prerequisites

- Python 3.10+
- PostgreSQL 14+ (or Docker)
- A Gemini API key from Google AI Studio
- Optional: a Steam Web API key and your SteamID64 (for the library and recommendation tools)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` should contain:

```
google-genai
psycopg2-binary
python-dotenv
requests
```

### 3. Environment variables

Copy `.env.example` to `.env` and fill in your values. **Never commit `.env`.**

```
GEMINI_API_KEY=
DATABASE_URL=postgresql://postgres@localhost:5432/steamintel
STEAM_API_KEY=
STEAM_ID=
GEMINI_MODEL=gemini-2.5-flash
```

### 4. Build the database

```bash
createdb steamintel
psql -d steamintel -f schema.sql
psql -d steamintel -f insert_data.sql
```

Check that it worked:

```bash
psql -d steamintel -c "\dt"
psql -d steamintel -c "SELECT COUNT(*) FROM game;"
```

To start over from scratch:

```bash
dropdb steamintel && createdb steamintel
```

### 5. Run the agent

```bash
python tool.py
```

Then type a question at the `>` prompt. Type `quit` to exit.

## Database design

Entities: `game`, `developer`, `publisher`, `genre`, `tag`, `review`.

- Many-to-many relationships use junction tables: `game_developer`, `game_publisher`, `game_genre`, `game_tag`.
- `review` is one-to-one with `game`: `review.app_id` is a NOT NULL foreign key with a UNIQUE constraint.

The ER diagram is in `report/`.

## Agent tools (`tool.py`)

| Tool | What it does |
|---|---|
| `get_database_schema()` | Returns tables and columns so the LLM writes valid SQL |
| `execute_sql(sql_query)` | Runs a read-only SELECT and returns up to 50 rows |
| `fetch_cache(key)` | Looks up a cached result |
| `get_metadata(game_ID)` | Name, price, genres, developers, and review score for one game |
| `get_userLib()` | Pulls the user's owned games from the Steam API |
| `find_reccomendations(limit)` | Recommends games similar to the user's most played |

The agent's database connection is read-only, so it can't modify data.

## Status

- [x] Proposal
- [x] ER diagram (needs rename and Tag cleanup)
- [x] Agent tools (`tool.py`)
- [ ] `schema.sql`
- [ ] `insert_data.sql`
- [ ] `queries.sql`
- [ ] Phase report
- [ ] Demo
- [ ] Frontend

## Open decisions

- `tool.py` currently queries the dataset's raw tables (`applications`, `reviews`, and so on). It needs to be pointed at our own schema (`game`, `review`, and so on) so the report, database, and app agree.
- `find_reccomendations` depends on `description_embedding`, which only exists in the full Postgres dump. Decide whether to keep it or recommend by shared genres and tags instead.
