import os
import sys
import sqlite3
import json
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("db_loader")

def create_connection(db_file):
    """Create a database connection to the SQLite database specified by db_file."""
    conn = None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(db_file)), exist_ok=True)
        conn = sqlite3.connect(db_file)
        logger.info(f"Successfully connected to SQLite: {db_file}")
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        raise e

def create_tables(conn):
    """Create charities and grants tables with proper relational structure and indexes."""
    charities_sql = """
    CREATE TABLE IF NOT EXISTS charities (
        charity_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT,
        website TEXT,
        email TEXT,
        address TEXT,
        city TEXT,
        state TEXT,
        country TEXT,
        latitude REAL,
        longitude REAL,
        annual_income REAL,
        annual_expenditure REAL,
        thematic_focus TEXT,
        geographic_focus TEXT,
        raw_cc_data TEXT
    );
    """
    
    grants_sql = """
    CREATE TABLE IF NOT EXISTS grants (
        grant_id TEXT PRIMARY KEY,
        funding_charity_id INTEGER,
        recipient_name TEXT NOT NULL,
        recipient_charity_id INTEGER,
        amount_eur REAL,
        currency TEXT,
        description TEXT,
        date TEXT,
        recipient_latitude REAL,
        recipient_longitude REAL,
        recipient_region TEXT,
        tags TEXT,
        geographic_focus TEXT,
        FOREIGN KEY (funding_charity_id) REFERENCES charities (charity_id),
        FOREIGN KEY (recipient_charity_id) REFERENCES charities (charity_id)
    );
    """

    try:
        cursor = conn.cursor()
        
        # Drop existing tables to ensure clean reload
        logger.info("Dropping existing tables...")
        cursor.execute("DROP TABLE IF EXISTS grants;")
        cursor.execute("DROP TABLE IF EXISTS charities;")
        
        logger.info("Creating tables...")
        cursor.execute(charities_sql)
        cursor.execute(grants_sql)
        
        # Create indexes to speed up name searches, filtering by tag/region, and joins
        logger.info("Creating database indexes...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_charities_name ON charities (name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_grants_funding ON grants (funding_charity_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_grants_recipient ON grants (recipient_charity_id);")
        
        conn.commit()
        logger.info("Tables and indexes successfully created.")
    except Exception as e:
        logger.error(f"Failed to create tables/indexes: {e}")
        conn.rollback()
        raise e

def insert_charities(conn, charities_list):
    """Inserts or replaces charity profiles in the charities table."""
    cursor = conn.cursor()
    for c in charities_list:
        cursor.execute(
            """
            INSERT OR REPLACE INTO charities (
                charity_id, name, type, website, email, address, city, state, country,
                latitude, longitude, annual_income, annual_expenditure, thematic_focus,
                geographic_focus, raw_cc_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                c["charity_id"],
                c["name"],
                c.get("type"),
                c.get("website"),
                c.get("email"),
                c.get("address"),
                c.get("city"),
                c.get("state"),
                c.get("country"),
                c.get("latitude"),
                c.get("longitude"),
                c.get("annual_income"),
                c.get("annual_expenditure"),
                c.get("thematic_focus"),
                c.get("geographic_focus"),
                json.dumps(c.get("raw_cc_data", {}))
            )
        )
    conn.commit()

def insert_grants(conn, grants_list):
    """Inserts or replaces grant details in the grants table."""
    cursor = conn.cursor()
    for g in grants_list:
        cursor.execute(
            """
            INSERT OR REPLACE INTO grants (
                grant_id, funding_charity_id, recipient_name, recipient_charity_id,
                amount_eur, currency, description, date, recipient_latitude,
                recipient_longitude, recipient_region, tags, geographic_focus
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g["grant_id"],
                g["funding_charity_id"],
                g["recipient_name"],
                g.get("recipient_charity_id"),
                g.get("amount_eur"),
                g.get("currency"),
                g.get("description"),
                g.get("date"),
                g.get("recipient_latitude"),
                g.get("recipient_longitude"),
                g.get("recipient_region"),
                g.get("tags"),
                g.get("geographic_focus")
            )
        )
    conn.commit()

def load_jsonl_to_db(conn, charities_jsonl_path, grants_jsonl_path):
    """Load JSON Lines records from raw files into SQLite database."""
    cursor = conn.cursor()

    # Load Charities
    if not os.path.exists(charities_jsonl_path):
        logger.warning(f"Charities JSONL file not found at: {charities_jsonl_path}")
    else:
        logger.info(f"Loading charities from {charities_jsonl_path}...")
        charity_count = 0
        with open(charities_jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO charities (
                            charity_id, name, type, website, email, address, city, state, country,
                            latitude, longitude, annual_income, annual_expenditure, thematic_focus,
                            geographic_focus, raw_cc_data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c["charity_id"],
                            c["name"],
                            c.get("type"),
                            c.get("website"),
                            c.get("email"),
                            c.get("address"),
                            c.get("city"),
                            c.get("state"),
                            c.get("country"),
                            c.get("latitude"),
                            c.get("longitude"),
                            c.get("annual_income"),
                            c.get("annual_expenditure"),
                            c.get("thematic_focus"),
                            c.get("geographic_focus"),
                            json.dumps(c.get("raw_cc_data", {}))
                        )
                    )
                    charity_count += 1
                except Exception as e:
                    logger.error(f"Error parsing/inserting charity line: {line[:100]}... Error: {e}")
        logger.info(f"Loaded {charity_count} charities.")

    # Load Grants
    if not os.path.exists(grants_jsonl_path):
        logger.warning(f"Grants JSONL file not found at: {grants_jsonl_path}")
    else:
        logger.info(f"Loading grants from {grants_jsonl_path}...")
        grant_count = 0
        with open(grants_jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    g = json.loads(line)
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO grants (
                            grant_id, funding_charity_id, recipient_name, recipient_charity_id,
                            amount_eur, currency, description, date, recipient_latitude,
                            recipient_longitude, recipient_region, tags, geographic_focus
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            g["grant_id"],
                            g["funding_charity_id"],
                            g["recipient_name"],
                            g.get("recipient_charity_id"),
                            g.get("amount_eur"),
                            g.get("currency"),
                            g.get("description"),
                            g.get("date"),
                            g.get("recipient_latitude"),
                            g.get("recipient_longitude"),
                            g.get("recipient_region"),
                            g.get("tags"),
                            g.get("geographic_focus")
                        )
                    )
                    grant_count += 1
                except Exception as e:
                    logger.error(f"Error parsing/inserting grant line: {line[:100]}... Error: {e}")
        logger.info(f"Loaded {grant_count} grants.")

    conn.commit()
    logger.info("Database load transaction committed successfully.")

def main(db_path=None, preprocessed_dir=None):
    # Setup paths relative to current script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(current_dir)
    
    db_file = db_path or os.path.join(src_dir, "data", "charities.db")
    prep_dir = preprocessed_dir or os.path.join(src_dir, "data", "preprocessed")

    charities_jsonl = os.path.join(prep_dir, "charities.jsonl")
    grants_jsonl = os.path.join(prep_dir, "grants.jsonl")

    conn = create_connection(db_file)
    if conn:
        try:
            create_tables(conn)
            load_jsonl_to_db(conn, charities_jsonl, grants_jsonl)
        finally:
            conn.close()
            logger.info("Database connection closed.")

if __name__ == "__main__":
    db_arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(db_path=db_arg)
