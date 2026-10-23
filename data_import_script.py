import pandas as pd
import psycopg2
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

# load env vars
load_dotenv()

# db setup
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres@localhost:5432/steamintel")
engine = create_engine(DB_URL)

def main():
    print("Loading raw CSVs...")
    # load csvs
    try:
        apps = pd.read_csv('applications.csv', low_memory=False)
        reviews = pd.read_csv('reviews.csv', low_memory=False)
    except FileNotFoundError as e:
        print(f"Error: {e}. Please ensure the CSV files are in the same directory.")
        return

    print("Transforming Games...")
    # filter by game type
    if 'type' in apps.columns:
        games = apps[apps['type'] == 'game'].copy()
    else:
        games = apps.copy()

    # convert price to usd
    if 'mat_final_price' in games.columns:
        games['price_usd'] = games['mat_final_price'] / 100.0
    else:
        games['price_usd'] = 0.00

    # rename columns
    rename_map = {
        'appid': 'app_id',
        'mat_discount_percent': 'discount_pct',
        # map extras
    }
    games = games.rename(columns=rename_map)

    # default deck status
    if 'steam_deck_status' not in games.columns:
        games['steam_deck_status'] = 'Unknown'

    # valid schema columns
    valid_game_columns = ['app_id', 'name', 'release_date', 'price_usd', 'discount_pct', 'steam_deck_status']
    
    # clean columns
    games_clean = games[[col for col in valid_game_columns if col in games.columns]]

    print("Transforming Reviews...")
    # aggregate reviews
    if 'voted_up' in reviews.columns and 'appid' in reviews.columns:
        reviews['voted_up'] = reviews['voted_up'].astype(int)
        review_agg = reviews.groupby('appid').agg(
            review_score_pct=('voted_up', lambda x: round(x.mean() * 100, 2)),
            total_reviews=('voted_up', 'count')
        ).reset_index().rename(columns={'appid': 'app_id'})
    else:
        print("Warning: 'voted_up' or 'appid' not found in reviews.csv. Skipping reviews.")
        review_agg = pd.DataFrame(columns=['app_id', 'review_score_pct', 'total_reviews'])

    print("Pushing to PostgreSQL...")
    # insert games
    print(" -> Inserting games...")
    games_clean.to_sql('game', engine, if_exists='append', index=False, chunksize=5000)
    
    # insert reviews
    print(" -> Inserting reviews...")
    review_agg.to_sql('review', engine, if_exists='append', index=False, chunksize=5000)

    # handle other tables here

    print("Resetting Sequences...")
    # sync db sequences
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