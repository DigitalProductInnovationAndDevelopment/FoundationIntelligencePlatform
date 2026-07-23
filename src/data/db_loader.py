import os
import sys
import sqlite3
import json
import logging
import tempfile
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("db_loader")

SCHEMA_VERSION = "4"
REQUIRED_SCHEMA = {
    "charities": {
        "charity_id", "name", "type", "website", "email", "address", "city",
        "state", "country", "latitude", "longitude", "annual_income",
        "annual_expenditure", "thematic_focus", "geographic_focus", "raw_cc_data",
        "programme_areas_source", "programme_areas_inferred", "programme_area_scores",
        "programme_area_method", "programme_area_evidence", "programme_area_review_required",
        "geographic_focus_source", "geographic_focus_inferred", "headquarters_country",
        "headquarters_region", "geography_method", "geography_confidence",
        "geography_evidence", "geography_review_required", "enrichment_rule_version",
        "enrichment_review_reasons", "insufficient_source_text", "normalized_name",
        "normalized_domain", "organization_type", "primary_source", "source_names",
        "source_record_id", "source_url", "source_records", "ingestion_timestamp",
        "transaction_coverage", "deduplication_status", "deduplication_candidates"
    },
    "grants": {
        "grant_id", "funding_charity_id", "funding_name", "funding_org_source_id",
        "recipient_name", "recipient_charity_id", "recipient_org_source_id", "amount",
        "amount_eur", "currency", "description", "date", "recipient_latitude",
        "recipient_longitude", "recipient_region", "beneficiary_geography",
        "project_geography", "programme_area_source", "tags", "geographic_focus",
        "source", "source_record_id", "source_url", "ingestion_timestamp", "raw_grant_data",
        "programme_area_inferred", "programme_area_scores", "programme_area_method",
        "programme_area_evidence", "programme_area_review_required",
        "beneficiary_geography_normalized", "geographic_focus_inferred",
        "geography_method", "geography_confidence", "geography_evidence",
        "geography_review_required", "enrichment_rule_version", "enrichment_review_reasons",
        "insufficient_source_text"
    },
}

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

def create_tables(conn, reset=False):
    """Create the full schema; optionally reset existing application tables."""
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
        raw_cc_data TEXT,
        programme_areas_source TEXT,
        programme_areas_inferred TEXT,
        programme_area_scores TEXT,
        programme_area_method TEXT,
        programme_area_evidence TEXT,
        programme_area_review_required INTEGER NOT NULL DEFAULT 0,
        geographic_focus_source TEXT,
        geographic_focus_inferred TEXT,
        headquarters_country TEXT,
        headquarters_region TEXT,
        geography_method TEXT,
        geography_confidence REAL,
        geography_evidence TEXT,
        geography_review_required INTEGER NOT NULL DEFAULT 0,
        enrichment_rule_version TEXT,
        enrichment_review_reasons TEXT,
        insufficient_source_text INTEGER NOT NULL DEFAULT 0,
        normalized_name TEXT,
        normalized_domain TEXT,
        organization_type TEXT,
        primary_source TEXT,
        source_names TEXT,
        source_record_id TEXT,
        source_url TEXT,
        source_records TEXT,
        ingestion_timestamp TEXT,
        transaction_coverage TEXT,
        deduplication_status TEXT,
        deduplication_candidates TEXT
    );
    """
    
    grants_sql = """
    CREATE TABLE IF NOT EXISTS grants (
        grant_id TEXT PRIMARY KEY,
        funding_charity_id INTEGER,
        funding_name TEXT,
        funding_org_source_id TEXT,
        recipient_name TEXT NOT NULL,
        recipient_charity_id INTEGER,
        recipient_org_source_id TEXT,
        amount REAL,
        amount_eur REAL,
        currency TEXT,
        description TEXT,
        date TEXT,
        recipient_latitude REAL,
        recipient_longitude REAL,
        recipient_region TEXT,
        beneficiary_geography TEXT,
        project_geography TEXT,
        programme_area_source TEXT,
        tags TEXT,
        geographic_focus TEXT,
        source TEXT,
        source_record_id TEXT,
        source_url TEXT,
        ingestion_timestamp TEXT,
        raw_grant_data TEXT,
        programme_area_inferred TEXT,
        programme_area_scores TEXT,
        programme_area_method TEXT,
        programme_area_evidence TEXT,
        programme_area_review_required INTEGER NOT NULL DEFAULT 0,
        beneficiary_geography_normalized TEXT,
        geographic_focus_inferred TEXT,
        geography_method TEXT,
        geography_confidence REAL,
        geography_evidence TEXT,
        geography_review_required INTEGER NOT NULL DEFAULT 0,
        enrichment_rule_version TEXT,
        enrichment_review_reasons TEXT,
        insufficient_source_text INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (funding_charity_id) REFERENCES charities (charity_id),
        FOREIGN KEY (recipient_charity_id) REFERENCES charities (charity_id)
    );
    """

    try:
        cursor = conn.cursor()
        
        if reset:
            logger.info("Dropping existing tables for a clean reload...")
            cursor.execute("DROP TABLE IF EXISTS grants;")
            cursor.execute("DROP TABLE IF EXISTS charities;")
            cursor.execute("DROP TABLE IF EXISTS metadata;")
        
        logger.info("Creating tables...")
        cursor.execute(charities_sql)
        cursor.execute(grants_sql)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,)
        )
        
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


def validate_database(db_file):
    """Return ``(is_valid, reason)`` without creating or mutating the database."""
    if not db_file or not os.path.exists(db_file):
        return False, "database file does not exist"
    if not os.path.isfile(db_file):
        return False, "database path is not a file"
    if os.path.getsize(db_file) == 0:
        return False, "database file is empty"

    conn = None
    try:
        uri = Path(db_file).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if not quick_check or quick_check[0] != "ok":
            return False, "SQLite integrity check failed"

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing_tables = sorted(set(REQUIRED_SCHEMA) - tables)
        if missing_tables:
            return False, f"missing required tables: {', '.join(missing_tables)}"

        for table, required_columns in REQUIRED_SCHEMA.items():
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing_columns = sorted(required_columns - columns)
            if missing_columns:
                return False, f"table '{table}' is missing columns: {', '.join(missing_columns)}"
        return True, "valid"
    except sqlite3.Error as exc:
        return False, f"cannot open as compatible SQLite: {exc}"
    finally:
        if conn is not None:
            conn.close()


def create_staging_database(db_file, preserve_existing=False):
    """Create a same-directory staging DB, optionally cloning a valid active DB."""
    target_dir = os.path.dirname(os.path.abspath(db_file))
    os.makedirs(target_dir, exist_ok=True)
    fd, staging_path = tempfile.mkstemp(prefix=".charities-", suffix=".tmp.db", dir=target_dir)
    os.close(fd)

    valid_existing, _ = validate_database(db_file)
    try:
        if preserve_existing and valid_existing:
            source_uri = Path(db_file).resolve().as_uri() + "?mode=ro"
            source_conn = sqlite3.connect(source_uri, uri=True)
            staging_conn = sqlite3.connect(staging_path)
            try:
                source_conn.backup(staging_conn)
            finally:
                staging_conn.close()
                source_conn.close()

        conn = create_connection(staging_path)
        create_tables(conn, reset=not (preserve_existing and valid_existing))
        return staging_path, conn
    except Exception:
        if os.path.exists(staging_path):
            os.unlink(staging_path)
        raise


def publish_staging_database(staging_path, db_file):
    """Atomically publish a validated staging database."""
    valid, reason = validate_database(staging_path)
    if not valid:
        raise ValueError(f"Refusing to publish invalid database: {reason}")
    os.replace(staging_path, db_file)
    logger.info(f"Atomically published validated SQLite database at: {db_file}")


def _json_value(value, default):
    """Serialize structured values once while accepting legacy JSON strings."""
    if value is None:
        value = default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)

def insert_charities(conn, charities_list):
    """Inserts or replaces charity profiles in the charities table."""
    cursor = conn.cursor()
    try:
        for c in charities_list:
            cursor.execute(
            """
            INSERT OR REPLACE INTO charities (
                charity_id, name, type, website, email, address, city, state, country,
                latitude, longitude, annual_income, annual_expenditure, thematic_focus,
                geographic_focus, raw_cc_data, programme_areas_source,
                programme_areas_inferred, programme_area_scores, programme_area_method,
                programme_area_evidence, programme_area_review_required,
                geographic_focus_source, geographic_focus_inferred, headquarters_country,
                headquarters_region, geography_method, geography_confidence,
                geography_evidence, geography_review_required, enrichment_rule_version,
                enrichment_review_reasons, insufficient_source_text, normalized_name,
                normalized_domain, organization_type, primary_source, source_names,
                source_record_id, source_url, source_records, ingestion_timestamp,
                transaction_coverage, deduplication_status, deduplication_candidates
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _json_value(c.get("raw_cc_data"), {}),
                _json_value(c.get("programme_areas_source"), []),
                _json_value(c.get("programme_areas_inferred"), []),
                _json_value(c.get("programme_area_scores"), {}),
                c.get("programme_area_method"),
                _json_value(c.get("programme_area_evidence"), []),
                bool(c.get("programme_area_review_required")),
                _json_value(c.get("geographic_focus_source"), []),
                _json_value(c.get("geographic_focus_inferred"), []),
                c.get("headquarters_country"),
                c.get("headquarters_region"),
                c.get("geography_method"),
                c.get("geography_confidence"),
                _json_value(c.get("geography_evidence"), []),
                bool(c.get("geography_review_required")),
                c.get("enrichment_rule_version"),
                _json_value(c.get("enrichment_review_reasons"), []),
                bool(c.get("insufficient_source_text")),
                c.get("normalized_name"),
                c.get("normalized_domain"),
                c.get("organization_type") or c.get("type"),
                c.get("primary_source"),
                _json_value(c.get("source_names"), []),
                c.get("source_record_id"),
                c.get("source_url"),
                _json_value(c.get("source_records"), []),
                c.get("ingestion_timestamp"),
                c.get("transaction_coverage"),
                c.get("deduplication_status"),
                _json_value(c.get("deduplication_candidates"), []),
                )
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

def insert_grants(conn, grants_list):
    """Inserts or replaces grant details in the grants table."""
    cursor = conn.cursor()
    try:
        for g in grants_list:
            cursor.execute(
            """
            INSERT OR REPLACE INTO grants (
                grant_id, funding_charity_id, funding_name, funding_org_source_id,
                recipient_name, recipient_charity_id, recipient_org_source_id,
                amount, amount_eur, currency, description, date, recipient_latitude,
                recipient_longitude, recipient_region, beneficiary_geography,
                project_geography, programme_area_source, tags, geographic_focus,
                source, source_record_id, source_url, ingestion_timestamp, raw_grant_data,
                programme_area_inferred, programme_area_scores, programme_area_method,
                programme_area_evidence, programme_area_review_required,
                beneficiary_geography_normalized, geographic_focus_inferred,
                geography_method, geography_confidence, geography_evidence,
                geography_review_required, enrichment_rule_version, enrichment_review_reasons,
                insufficient_source_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                g["grant_id"],
                g["funding_charity_id"],
                g.get("funding_name"),
                g.get("funding_org_source_id"),
                g["recipient_name"],
                g.get("recipient_charity_id"),
                g.get("recipient_org_source_id"),
                g.get("amount"),
                g.get("amount_eur"),
                g.get("currency"),
                g.get("description"),
                g.get("date"),
                g.get("recipient_latitude"),
                g.get("recipient_longitude"),
                g.get("recipient_region"),
                g.get("beneficiary_geography"),
                g.get("project_geography"),
                g.get("programme_area_source"),
                g.get("tags"),
                g.get("geographic_focus"),
                g.get("source"),
                g.get("source_record_id"),
                g.get("source_url"),
                g.get("ingestion_timestamp"),
                _json_value(g.get("raw_grant_data"), {}),
                _json_value(g.get("programme_area_inferred"), []),
                _json_value(g.get("programme_area_scores"), {}),
                g.get("programme_area_method"),
                _json_value(g.get("programme_area_evidence"), []),
                bool(g.get("programme_area_review_required")),
                _json_value(g.get("beneficiary_geography_normalized"), []),
                _json_value(g.get("geographic_focus_inferred"), []),
                g.get("geography_method"),
                g.get("geography_confidence"),
                _json_value(g.get("geography_evidence"), []),
                bool(g.get("geography_review_required")),
                g.get("enrichment_rule_version"),
                _json_value(g.get("enrichment_review_reasons"), []),
                bool(g.get("insufficient_source_text")),
                )
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

def load_jsonl_to_db(conn, charities_jsonl_path, grants_jsonl_path, strict=False):
    """Load JSON Lines records from raw files into SQLite database."""
    cursor = conn.cursor()
    result = {"charities_loaded": 0, "grants_loaded": 0, "errors": []}

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
                            geographic_focus, raw_cc_data, programme_areas_source,
                            programme_areas_inferred, programme_area_scores, programme_area_method,
                            programme_area_evidence, programme_area_review_required,
                            geographic_focus_source, geographic_focus_inferred, headquarters_country,
                            headquarters_region, geography_method, geography_confidence,
                            geography_evidence, geography_review_required, enrichment_rule_version,
                            enrichment_review_reasons, insufficient_source_text, normalized_name,
                            normalized_domain, organization_type, primary_source, source_names,
                            source_record_id, source_url, source_records, ingestion_timestamp,
                            transaction_coverage, deduplication_status, deduplication_candidates
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            _json_value(c.get("raw_cc_data"), {}),
                            _json_value(c.get("programme_areas_source"), []),
                            _json_value(c.get("programme_areas_inferred"), []),
                            _json_value(c.get("programme_area_scores"), {}),
                            c.get("programme_area_method"),
                            _json_value(c.get("programme_area_evidence"), []),
                            bool(c.get("programme_area_review_required")),
                            _json_value(c.get("geographic_focus_source"), []),
                            _json_value(c.get("geographic_focus_inferred"), []),
                            c.get("headquarters_country"),
                            c.get("headquarters_region"),
                            c.get("geography_method"),
                            c.get("geography_confidence"),
                            _json_value(c.get("geography_evidence"), []),
                            bool(c.get("geography_review_required")),
                            c.get("enrichment_rule_version"),
                            _json_value(c.get("enrichment_review_reasons"), []),
                            bool(c.get("insufficient_source_text")),
                            c.get("normalized_name"),
                            c.get("normalized_domain"),
                            c.get("organization_type") or c.get("type"),
                            c.get("primary_source"),
                            _json_value(c.get("source_names"), []),
                            c.get("source_record_id"),
                            c.get("source_url"),
                            _json_value(c.get("source_records"), []),
                            c.get("ingestion_timestamp"),
                            c.get("transaction_coverage"),
                            c.get("deduplication_status"),
                            _json_value(c.get("deduplication_candidates"), []),
                        )
                    )
                    charity_count += 1
                except Exception as e:
                    logger.error(f"Error parsing/inserting charity line: {line[:100]}... Error: {e}")
                    result["errors"].append(f"charity line: {e}")
                    if strict:
                        conn.rollback()
                        raise ValueError(f"Failed to import charity record: {e}") from e
        logger.info(f"Loaded {charity_count} charities.")
        result["charities_loaded"] = charity_count

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
                            grant_id, funding_charity_id, funding_name, funding_org_source_id,
                            recipient_name, recipient_charity_id, recipient_org_source_id,
                            amount, amount_eur, currency, description, date, recipient_latitude,
                            recipient_longitude, recipient_region, beneficiary_geography,
                            project_geography, programme_area_source, tags, geographic_focus,
                            source, source_record_id, source_url, ingestion_timestamp, raw_grant_data,
                            programme_area_inferred, programme_area_scores, programme_area_method,
                            programme_area_evidence, programme_area_review_required,
                            beneficiary_geography_normalized, geographic_focus_inferred,
                            geography_method, geography_confidence, geography_evidence,
                            geography_review_required, enrichment_rule_version, enrichment_review_reasons,
                            insufficient_source_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            g["grant_id"],
                            g["funding_charity_id"],
                            g.get("funding_name"),
                            g.get("funding_org_source_id"),
                            g["recipient_name"],
                            g.get("recipient_charity_id"),
                            g.get("recipient_org_source_id"),
                            g.get("amount"),
                            g.get("amount_eur"),
                            g.get("currency"),
                            g.get("description"),
                            g.get("date"),
                            g.get("recipient_latitude"),
                            g.get("recipient_longitude"),
                            g.get("recipient_region"),
                            g.get("beneficiary_geography"),
                            g.get("project_geography"),
                            g.get("programme_area_source"),
                            g.get("tags"),
                            g.get("geographic_focus"),
                            g.get("source"),
                            g.get("source_record_id"),
                            g.get("source_url"),
                            g.get("ingestion_timestamp"),
                            _json_value(g.get("raw_grant_data"), {}),
                            _json_value(g.get("programme_area_inferred"), []),
                            _json_value(g.get("programme_area_scores"), {}),
                            g.get("programme_area_method"),
                            _json_value(g.get("programme_area_evidence"), []),
                            bool(g.get("programme_area_review_required")),
                            _json_value(g.get("beneficiary_geography_normalized"), []),
                            _json_value(g.get("geographic_focus_inferred"), []),
                            g.get("geography_method"),
                            g.get("geography_confidence"),
                            _json_value(g.get("geography_evidence"), []),
                            bool(g.get("geography_review_required")),
                            g.get("enrichment_rule_version"),
                            _json_value(g.get("enrichment_review_reasons"), []),
                            bool(g.get("insufficient_source_text")),
                        )
                    )
                    grant_count += 1
                except Exception as e:
                    logger.error(f"Error parsing/inserting grant line: {line[:100]}... Error: {e}")
                    result["errors"].append(f"grant line: {e}")
                    if strict:
                        conn.rollback()
                        raise ValueError(f"Failed to import grant record: {e}") from e
        logger.info(f"Loaded {grant_count} grants.")
        result["grants_loaded"] = grant_count

    conn.commit()
    logger.info("Database load transaction committed successfully.")
    return result


def rebuild_database_atomically(db_file, preprocessed_dir, require_charities=True):
    """Build and validate a replacement DB without risking the active database."""
    charities_jsonl = os.path.join(preprocessed_dir, "charities.jsonl")
    grants_jsonl = os.path.join(preprocessed_dir, "grants.jsonl")
    staging_path, conn = create_staging_database(db_file, preserve_existing=False)
    try:
        result = load_jsonl_to_db(conn, charities_jsonl, grants_jsonl, strict=True)
        conn.close()
        conn = None
        if require_charities and result["charities_loaded"] == 0:
            raise ValueError("Refusing to replace the database because no charity records were loaded")
        publish_staging_database(staging_path, db_file)
        return result
    except Exception:
        if conn is not None:
            conn.close()
        if os.path.exists(staging_path):
            os.unlink(staging_path)
        raise

def main(db_path=None, preprocessed_dir=None):
    # Setup paths relative to current script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(current_dir)
    
    db_file = db_path or os.path.join(src_dir, "data", "charities.db")
    prep_dir = preprocessed_dir or os.path.join(src_dir, "data", "preprocessed")

    result = rebuild_database_atomically(db_file, prep_dir)
    logger.info(
        "Database rebuild complete: %s charities, %s grants",
        result["charities_loaded"],
        result["grants_loaded"],
    )
    return result

if __name__ == "__main__":
    db_arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(db_path=db_arg)
