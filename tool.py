#Alvin Ng, 10/21/2026
#
# SteamIntel agent: Gemini decides which tool to call, we run it, and send the result back.
#
# Setup:
#   pip install -r requirements.txt
#   .env needs: GEMINI_API_KEY, DATABASE_URL, STEAM_API_KEY, STEAM_ID
#   optional:   GEMINI_MODEL

import os, json, time
import psycopg2, psycopg2.extras, requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")  # check AI Studio for current free-tier models
MAX_ROWS = 50          # cap on rows sent back to the LLM per query
MAX_TOOL_CALLS = 10    # stop runaway loops

client = genai.Client()  # reads GEMINI_API_KEY from the environment
db = psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://postgres@localhost:5432/steam"))
db.set_session(readonly=True)  # the agent can only read, never modify the DB
_cache = {}  # key -> (expires_at, value); lives in memory, empties when the program exits

SYSTEM_PROMPT = """You are SteamIntel, an assistant that answers questions about the Steam catalog
by querying a PostgreSQL database. Call get_database_schema before writing SQL if you have not yet.
Notes about the data:
- Only rows with applications.success = true have real metadata; filter on it.
- Use applications.type = 'game' unless the user asks about DLC, software, etc.
- Prices are in cents: mat_final_price / 100.0 is the USD price. is_free marks free games.
- There is no review score column; compute it from reviews:
  100.0 * AVG(voted_up::int) over reviews for an appid.
- Genres join through application_genres -> genres.genre_description.
Write only SELECT queries. Answer concisely and show the numbers you used."""


def _query(sql, params=None):
    # every DB tool goes through here so a failed query can't leave the connection stuck
    # in an aborted transaction (which would make every later query fail too)
    try:
        with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET statement_timeout = 10000")
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchmany(MAX_ROWS)]
    finally:
        db.rollback()  # end the read-only transaction, and clear it if the query failed


def _to_json(rows):
    return json.dumps(rows, default=str)  # default=str handles dates and Decimals


# Returning the db schema so the LLM knows valid tables and columns
def get_database_schema() -> str:
    """Return every table in the database with its columns and data types."""
    cached = fetch_cache("schema")
    if cached != "MISS":
        return cached

    with db.cursor() as cur:
        cur.execute("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)
        tables = {}
        for table, column, dtype in cur.fetchall():
            tables.setdefault(table, []).append(f"{column} {dtype}")
    db.rollback()
    result = "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())
    store_cache("schema", result, ttl_seconds=24 * 3600)  # schema only changes when make.py runs
    return result


# Querying parameterized searches in the database through SQL
def execute_sql(sql_query: str) -> str:
    """Run a read-only SQL SELECT query against the Steam database and return up to 50 rows as JSON."""
    key = "sql:" + " ".join(sql_query.split())  # same query with different whitespace hits the same entry
    cached = fetch_cache(key)
    if cached != "MISS":
        return cached
    result = _to_json(_query(sql_query))
    store_cache(key, result)
    return result


# A data structure used to store cached searches, profiles, preferences, etc
def fetch_cache(key: str) -> str:
    """Look up a previously cached result (e.g. 'userlib', 'profile') by key. Returns 'MISS' if not cached."""
    entry = _cache.get(key)
    if entry is None or entry[0] < time.time():
        _cache.pop(key, None)
        return "MISS"
    return entry[1]

def store_cache(key, value, ttl_seconds=3600):
    # not exposed to the LLM; tools call this to cache their own results
    _cache[key] = (time.time() + ttl_seconds, value)

# pulling metadata about games from the database through SQL
def get_metadata(game_ID: int) -> str:
    """Get name, type, price, release date, genres, developers and review score for one Steam app ID."""
    return _to_json(_query("""
            SELECT a.appid, a.name, a.type, a.is_free, a.release_date,
                   a.mat_final_price / 100.0 AS price_usd, a.mat_discount_percent AS discount_pct,
                   a.short_description,
                   (SELECT array_agg(g.genre_description) FROM application_genres ag
                      JOIN genres g ON g.genre_id = ag.genre_id WHERE ag.appid = a.appid) AS genres,
                   (SELECT array_agg(d.developer_name) FROM application_developers ad
                      JOIN developers d ON d.developer_id = ad.developer_id WHERE ad.appid = a.appid) AS developers,
                   (SELECT ROUND(100.0 * AVG(r.voted_up::int), 1) FROM reviews r
                      WHERE r.appid = a.appid) AS review_score_pct
            FROM applications a
            WHERE a.appid = %s
        """, (int(game_ID),)))  # Gemini sometimes sends numbers as floats (570.0)


# Valve's cloud servers or your local computer files to determine what's in your
    # steam library and information about your recently played games, etc.
def get_userLib() -> str:
    """Get the user's owned Steam games with total playtime, sorted by most played."""
    cached = fetch_cache("userlib")
    if cached != "MISS":
        return cached

    resp = requests.get(
        "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
        params={"key": os.getenv("STEAM_API_KEY"), "steamid": os.getenv("STEAM_ID"),
                "include_appinfo": 1, "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    games = resp.json().get("response", {}).get("games", [])
    games = sorted(
        ({"appid": g["appid"], "name": g.get("name"), "playtime_hours": round(g["playtime_forever"] / 60, 1)}
         for g in games),
        key=lambda g: g["playtime_hours"], reverse=True,
    )
    result = json.dumps(games)
    store_cache("userlib", result)
    return result


#  Calculate matching metadata from probable user recommendations to build a profile of recommended games
def find_reccomendations(limit: int) -> str:
    """Recommend games the user doesn't own that are most similar to their most-played games."""
    owned = json.loads(get_userLib())
    if not owned:
        return "User library is empty or private."
    owned_ids = [g["appid"] for g in owned]
    top_ids = owned_ids[:10]  # the user's taste profile = their 10 most-played games

    # average the description embeddings of the top games, then find the nearest unowned games
    return _to_json(_query("""
            WITH profile AS (
                SELECT AVG(description_embedding) AS v
                FROM applications
                WHERE appid = ANY(%s) AND description_embedding IS NOT NULL
            )
            SELECT a.appid, a.name, a.mat_final_price / 100.0 AS price_usd,
                   ROUND((1 - (a.description_embedding <=> profile.v))::numeric, 3) AS similarity
            FROM applications a, profile
            WHERE a.success AND a.type = 'game'
              AND a.description_embedding IS NOT NULL
              AND NOT (a.appid = ANY(%s))
            ORDER BY a.description_embedding <=> profile.v
            LIMIT %s
        """, (top_ids, owned_ids, min(int(limit), MAX_ROWS))))


TOOLS = [get_database_schema, execute_sql, fetch_cache, get_metadata, get_userLib, find_reccomendations]
TOOL_FUNCTIONS = {f.__name__: f for f in TOOLS}


def run_agent(user_input, history):
    # the inference call lives here, not as a tool: Gemini picks tools, we run them and send results back
    history.append(types.Content(role="user", parts=[types.Part(text=user_input)]))
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=TOOLS,  # the SDK builds tool declarations from the type hints and docstrings
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    for _ in range(MAX_TOOL_CALLS):
        response = client.models.generate_content(model=MODEL, contents=history, config=config)
        if not response.candidates or response.candidates[0].content is None:
            return "Gemini returned no answer (possibly blocked by a safety filter). Try rephrasing."
        history.append(response.candidates[0].content)
        if not response.function_calls:
            return response.text

        results = []
        for call in response.function_calls:
            print(f"  [tool] {call.name}({dict(call.args or {})})")
            try:
                output = TOOL_FUNCTIONS[call.name](**(call.args or {}))
            except Exception as e:
                output = f"ERROR: {e}"  # let the model see the error and retry (e.g. fix bad SQL)
            results.append(types.Part.from_function_response(name=call.name, response={"result": output}))
        history.append(types.Content(role="user", parts=results))

    return "Stopped: too many tool calls without an answer."


def main():
    history = []
    print("SteamIntel - ask about Steam games (type 'quit' to exit)")
    while True:
        user_input = input("\n> ").strip()
        if user_input.lower() in ("quit", "exit"):
            break
        if user_input:
            print(run_agent(user_input, history))


if __name__ == "__main__":
    main()
