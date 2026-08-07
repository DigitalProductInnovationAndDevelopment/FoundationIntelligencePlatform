"""Add versioned dashboard materializations and refresh function.

Revision ID: 0004_versioned_analytics
Revises: 0003_grant_award_timestamp
"""

from alembic import op


revision = "0004_versioned_analytics"
down_revision = "0003_grant_award_timestamp"
branch_labels = None
depends_on = None


TABLES = (
    "analytics_scope_totals",
    "analytics_country_aggregates",
    "analytics_country_connections",
    "analytics_period_aggregates",
    "analytics_programme_aggregates",
    "analytics_entity_rankings",
    "analytics_country_funder_rankings",
    "analytics_funder_relationships",
    "analytics_filter_values",
)


def upgrade() -> None:
    statements = (
        """
        CREATE TABLE analytics_scope_totals (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            total_grants BIGINT NOT NULL,
            known_geography_grants BIGINT NOT NULL,
            multi_country_grants BIGINT NOT NULL,
            invalid_amount_grants BIGINT NOT NULL,
            missing_date_grants BIGINT NOT NULL,
            negative_amount_grants BIGINT NOT NULL,
            zero_amount_grants BIGINT NOT NULL,
            classified_grants BIGINT NOT NULL,
            unclassified_grants BIGINT NOT NULL,
            source_classified_grants BIGINT NOT NULL,
            inferred_classified_grants BIGINT NOT NULL,
            multiple_programme_grants BIGINT NOT NULL,
            invalid_source_label_grants BIGINT NOT NULL,
            low_confidence_grants BIGINT NOT NULL,
            total_amount_minor NUMERIC(30, 4) NOT NULL,
            maximum_amount_minor NUMERIC(30, 4),
            first_award_date DATE,
            latest_award_date DATE,
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (dataset_version, amount_basis, currency),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original')),
            CHECK (currency ~ '^[A-Z]{3}$')
        )
        """,
        """
        CREATE TABLE analytics_country_aggregates (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            country_code CHAR(2) NOT NULL,
            country_name TEXT NOT NULL,
            grant_count BIGINT NOT NULL,
            total_amount_minor NUMERIC(30, 4) NOT NULL,
            distinct_funders BIGINT NOT NULL,
            distinct_recipients BIGINT NOT NULL,
            association_count BIGINT NOT NULL,
            multi_country_count BIGINT NOT NULL,
            invalid_amount_count BIGINT NOT NULL,
            PRIMARY KEY (
                dataset_version, amount_basis, currency, country_code
            ),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original')),
            CHECK (currency ~ '^[A-Z]{3}$'),
            CHECK (country_code ~ '^[A-Z]{2}$')
        )
        """,
        """
        CREATE INDEX ix_analytics_country_amount
        ON analytics_country_aggregates
            (dataset_version, amount_basis, currency, total_amount_minor DESC, country_code)
        """,
        """
        CREATE TABLE analytics_country_connections (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            origin_country_code CHAR(2) NOT NULL,
            origin_country_name TEXT NOT NULL,
            destination_country_code CHAR(2) NOT NULL,
            destination_country_name TEXT NOT NULL,
            grant_count BIGINT NOT NULL,
            origin_sources TEXT[] NOT NULL,
            PRIMARY KEY (
                dataset_version, amount_basis, currency,
                origin_country_code, destination_country_code
            ),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original')),
            CHECK (origin_country_code ~ '^[A-Z]{2}$'),
            CHECK (destination_country_code ~ '^[A-Z]{2}$')
        )
        """,
        """
        CREATE INDEX ix_analytics_country_connection_rank
        ON analytics_country_connections
            (dataset_version, amount_basis, currency, grant_count DESC,
             origin_country_code, destination_country_code)
        """,
        """
        CREATE TABLE analytics_period_aggregates (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            granularity TEXT NOT NULL,
            period_start DATE NOT NULL,
            source_record_count BIGINT NOT NULL,
            grant_count BIGINT NOT NULL,
            total_amount_minor NUMERIC(30, 4) NOT NULL,
            mapped_grant_count BIGINT NOT NULL,
            unmapped_grant_count BIGINT NOT NULL,
            PRIMARY KEY (
                dataset_version, amount_basis, currency, granularity, period_start
            ),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original')),
            CHECK (granularity IN ('monthly', 'yearly'))
        )
        """,
        """
        CREATE TABLE analytics_programme_aggregates (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            programme_area TEXT NOT NULL,
            distinct_grant_count BIGINT NOT NULL,
            weighted_grant_count NUMERIC(30, 8) NOT NULL,
            allocated_amount_minor NUMERIC(30, 4) NOT NULL,
            source_classified_count BIGINT NOT NULL,
            inferred_classified_count BIGINT NOT NULL,
            PRIMARY KEY (
                dataset_version, amount_basis, currency, programme_area
            ),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original'))
        )
        """,
        """
        CREATE TABLE analytics_entity_rankings (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            entity_role TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            organization_id BIGINT,
            organization_name TEXT NOT NULL,
            total_amount_minor NUMERIC(30, 4) NOT NULL,
            grant_count BIGINT NOT NULL,
            PRIMARY KEY (
                dataset_version, amount_basis, currency, entity_role, entity_key
            ),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original')),
            CHECK (entity_role IN ('funder', 'recipient'))
        )
        """,
        """
        CREATE INDEX ix_analytics_entity_rank
        ON analytics_entity_rankings
            (dataset_version, amount_basis, currency, entity_role,
             total_amount_minor DESC, entity_key)
        """,
        """
        CREATE TABLE analytics_country_funder_rankings (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            country_code CHAR(2) NOT NULL,
            country_name TEXT NOT NULL,
            source_funder_key TEXT NOT NULL,
            source_namespace TEXT NOT NULL,
            source_organization_id TEXT,
            display_name TEXT NOT NULL,
            identity_method TEXT NOT NULL,
            source_ids TEXT[] NOT NULL,
            sources TEXT[] NOT NULL,
            observed_profile_id BIGINT,
            grant_count BIGINT NOT NULL,
            recipient_count BIGINT NOT NULL,
            first_award_date DATE,
            latest_award_date DATE,
            selected_amount_minor NUMERIC(30, 4) NOT NULL,
            included_count BIGINT NOT NULL,
            multi_country_count BIGINT NOT NULL,
            conversion_excluded BIGINT NOT NULL,
            missing_count BIGINT NOT NULL,
            invalid_count BIGINT NOT NULL,
            negative_count BIGINT NOT NULL,
            fallback_amount_minor NUMERIC(30, 4),
            fallback_currency CHAR(3),
            fallback_count BIGINT NOT NULL,
            publisher_source_url TEXT,
            override_identifier TEXT NOT NULL,
            PRIMARY KEY (
                dataset_version, amount_basis, currency, country_code,
                source_funder_key
            ),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original'))
        )
        """,
        """
        CREATE INDEX ix_analytics_country_funder_amount
        ON analytics_country_funder_rankings
            (dataset_version, amount_basis, currency, country_code,
             selected_amount_minor DESC, source_funder_key)
        """,
        """
        CREATE INDEX ix_analytics_country_funder_activity
        ON analytics_country_funder_rankings
            (dataset_version, amount_basis, currency, country_code,
             grant_count DESC, latest_award_date DESC, source_funder_key)
        """,
        """
        CREATE TABLE analytics_funder_relationships (
            dataset_version TEXT NOT NULL,
            amount_basis TEXT NOT NULL,
            currency CHAR(3) NOT NULL,
            country_code CHAR(2) NOT NULL,
            source_funder_key TEXT NOT NULL,
            recipient_key TEXT NOT NULL,
            recipient_name TEXT NOT NULL,
            total_amount_minor NUMERIC(30, 4) NOT NULL,
            grant_count BIGINT NOT NULL,
            rank_within_funder INTEGER NOT NULL,
            PRIMARY KEY (
                dataset_version, amount_basis, currency, country_code,
                source_funder_key, recipient_key
            ),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (amount_basis IN ('eur_converted', 'original')),
            CHECK (rank_within_funder BETWEEN 1 AND 50)
        )
        """,
        """
        CREATE INDEX ix_analytics_funder_relationship_rank
        ON analytics_funder_relationships
            (dataset_version, amount_basis, currency, country_code,
             source_funder_key, rank_within_funder)
        """,
        """
        CREATE TABLE analytics_filter_values (
            dataset_version TEXT NOT NULL,
            dimension TEXT NOT NULL,
            value TEXT NOT NULL,
            usage_count BIGINT NOT NULL,
            PRIMARY KEY (dataset_version, dimension, value),
            FOREIGN KEY (dataset_version) REFERENCES dataset_versions(dataset_version)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CHECK (dimension IN (
                'beneficiary_country', 'currency', 'programme_area', 'source'
            ))
        )
        """,
        """
        CREATE INDEX ix_registry_current_normalized_name
        ON charity_registry_organizations
            (dataset_version, normalized_name, registry_id)
        WHERE is_current_source_record
        """,
        """
        CREATE INDEX ix_grants_funding_source_id
        ON grants (dataset_version, funding_org_source_id, grant_id)
        WHERE funding_org_source_id IS NOT NULL
        """,
        """
        CREATE INDEX ix_grants_recipient_source_id
        ON grants (dataset_version, recipient_org_source_id, grant_id)
        WHERE recipient_org_source_id IS NOT NULL
        """,
    )
    for statement in statements:
        op.execute(statement)
    op.execute(_refresh_function_sql())


def _refresh_function_sql() -> str:
    return r"""
    CREATE FUNCTION refresh_analytics_materializations(p_dataset_version TEXT)
    RETURNS BIGINT
    LANGUAGE plpgsql
    AS $$
    DECLARE
        inserted_rows BIGINT := 0;
        current_rows BIGINT := 0;
        version_uuid UUID;
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM dataset_versions WHERE dataset_version=p_dataset_version
        ) THEN
            RAISE EXCEPTION 'unknown dataset version %', p_dataset_version;
        END IF;

        DELETE FROM analytics_filter_values WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_funder_relationships WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_country_funder_rankings WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_entity_rankings WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_programme_aggregates WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_period_aggregates WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_country_aggregates WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_country_connections WHERE dataset_version=p_dataset_version;
        DELETE FROM analytics_scope_totals WHERE dataset_version=p_dataset_version;

        INSERT INTO analytics_scope_totals
        WITH scoped AS (
            SELECT dataset_version, 'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency, award_date,
                   country_count, programme_category_count, programme_provenance,
                   invalid_source_label, low_confidence_inference,
                   eur_amount_minor AS amount_minor,
                   eur_amount_status AS amount_status
            FROM grant_overview_facts WHERE dataset_version=p_dataset_version
            UNION ALL
            SELECT dataset_version, 'original', currency, award_date,
                   country_count, programme_category_count, programme_provenance,
                   invalid_source_label, low_confidence_inference,
                   original_amount_minor, original_amount_status
            FROM grant_overview_facts
            WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        )
        SELECT dataset_version, amount_basis, selected_currency,
               COUNT(*), COUNT(*) FILTER (WHERE country_count>0),
               COUNT(*) FILTER (WHERE country_count>1),
               COUNT(*) FILTER (WHERE amount_status IN ('missing','invalid')),
               COUNT(*) FILTER (WHERE award_date IS NULL),
               COUNT(*) FILTER (WHERE amount_status='negative'),
               COUNT(*) FILTER (WHERE amount_status='zero'),
               COUNT(*) FILTER (WHERE programme_category_count>0),
               COUNT(*) FILTER (WHERE programme_category_count=0),
               COUNT(*) FILTER (WHERE programme_provenance='source'),
               COUNT(*) FILTER (WHERE programme_provenance='inferred'),
               COUNT(*) FILTER (WHERE programme_category_count>1),
               COUNT(*) FILTER (WHERE invalid_source_label),
               COUNT(*) FILTER (WHERE low_confidence_inference),
               COALESCE(SUM(amount_minor) FILTER (
                   WHERE amount_status NOT IN ('missing','invalid','negative')
               ), 0),
               MAX(amount_minor) FILTER (
                   WHERE amount_status NOT IN ('missing','invalid','negative')
               ),
               MIN(award_date), MAX(award_date), CURRENT_TIMESTAMP
        FROM scoped
        GROUP BY dataset_version, amount_basis, selected_currency;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_country_connections
        WITH scoped AS (
            SELECT dataset_version, grant_id, source_namespace,
                   origin_country_code, origin_country_name,
                   'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency
            FROM grant_overview_facts
            WHERE dataset_version=p_dataset_version
              AND origin_country_code IS NOT NULL
            UNION ALL
            SELECT dataset_version, grant_id, source_namespace,
                   origin_country_code, origin_country_name,
                   'original', currency
            FROM grant_overview_facts
            WHERE dataset_version=p_dataset_version
              AND origin_country_code IS NOT NULL AND currency IS NOT NULL
        )
        SELECT fact.dataset_version, fact.amount_basis, fact.selected_currency,
               fact.origin_country_code, MIN(fact.origin_country_name),
               country.country_code, MIN(country.country_name),
               COUNT(DISTINCT fact.grant_id),
               array_agg(DISTINCT fact.source_namespace)
        FROM scoped AS fact
        JOIN grant_beneficiary_countries AS country
          ON country.dataset_version=fact.dataset_version
         AND country.grant_id=fact.grant_id
        WHERE country.country_code<>fact.origin_country_code
        GROUP BY fact.dataset_version, fact.amount_basis, fact.selected_currency,
                 fact.origin_country_code, country.country_code;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_country_aggregates
        WITH scoped AS (
            SELECT fact.*, 'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency,
                   eur_amount_minor AS amount_minor,
                   eur_amount_status AS amount_status
            FROM grant_overview_facts AS fact
            WHERE dataset_version=p_dataset_version
            UNION ALL
            SELECT fact.*, 'original', currency, original_amount_minor,
                   original_amount_status
            FROM grant_overview_facts AS fact
            WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        )
        SELECT fact.dataset_version, fact.amount_basis, fact.selected_currency,
               country.country_code, MIN(country.country_name),
               COUNT(DISTINCT fact.grant_id),
               COALESCE(SUM(
                   CASE WHEN fact.amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.amount_minor::numeric / NULLIF(fact.country_count, 0)
                        ELSE 0 END
               ), 0),
               COUNT(DISTINCT fact.funding_name),
               COUNT(DISTINCT fact.recipient_name), COUNT(*),
               COUNT(*) FILTER (WHERE fact.country_count>1),
               COUNT(*) FILTER (WHERE fact.amount_status IN ('missing','invalid','negative'))
        FROM scoped AS fact
        JOIN grant_beneficiary_countries AS country
          ON country.dataset_version=fact.dataset_version
         AND country.grant_id=fact.grant_id
        GROUP BY fact.dataset_version, fact.amount_basis, fact.selected_currency,
                 country.country_code;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_period_aggregates
        WITH scoped AS (
            SELECT dataset_version, 'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency, award_date, country_count,
                   eur_amount_minor AS amount_minor, eur_amount_status AS amount_status
            FROM grant_overview_facts WHERE dataset_version=p_dataset_version
            UNION ALL
            SELECT dataset_version, 'original', currency, award_date, country_count,
                   original_amount_minor, original_amount_status
            FROM grant_overview_facts
            WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        ), granular AS (
            SELECT scoped.*, granularity,
                   date_trunc(granularity, award_date)::date AS period_start
            FROM scoped CROSS JOIN (VALUES ('month'), ('year')) AS levels(granularity)
            WHERE award_date IS NOT NULL
        )
        SELECT dataset_version, amount_basis, selected_currency,
               CASE granularity WHEN 'month' THEN 'monthly' ELSE 'yearly' END,
               period_start, COUNT(*),
               COUNT(*) FILTER (
                   WHERE amount_status NOT IN ('missing','invalid','negative')
               ),
               COALESCE(SUM(amount_minor) FILTER (
                   WHERE amount_status NOT IN ('missing','invalid','negative')
               ), 0),
               COUNT(*) FILTER (WHERE country_count>0),
               COUNT(*) FILTER (WHERE country_count=0)
        FROM granular
        GROUP BY dataset_version, amount_basis, selected_currency,
                 granularity, period_start;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_programme_aggregates
        WITH scoped AS (
            SELECT fact.*, 'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency,
                   eur_amount_minor AS amount_minor,
                   eur_amount_status AS amount_status
            FROM grant_overview_facts AS fact
            WHERE dataset_version=p_dataset_version
            UNION ALL
            SELECT fact.*, 'original', currency, original_amount_minor,
                   original_amount_status
            FROM grant_overview_facts AS fact
            WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        )
        SELECT fact.dataset_version, fact.amount_basis, fact.selected_currency,
               category.programme_area, COUNT(DISTINCT fact.grant_id),
               SUM(1.0 / NULLIF(fact.programme_category_count, 0)),
               COALESCE(SUM(
                   CASE WHEN fact.amount_status NOT IN ('missing','invalid','negative')
                        THEN fact.amount_minor::numeric
                             / NULLIF(fact.programme_category_count, 0)
                        ELSE 0 END
               ), 0),
               COUNT(DISTINCT fact.grant_id) FILTER (
                   WHERE fact.programme_provenance='source'
               ),
               COUNT(DISTINCT fact.grant_id) FILTER (
                   WHERE fact.programme_provenance='inferred'
               )
        FROM scoped AS fact
        JOIN grant_programme_categories AS category
          ON category.dataset_version=fact.dataset_version
         AND category.grant_id=fact.grant_id
        GROUP BY fact.dataset_version, fact.amount_basis, fact.selected_currency,
                 category.programme_area;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_entity_rankings
        WITH scoped AS (
            SELECT fact.*, 'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency,
                   eur_amount_minor AS amount_minor, eur_amount_status AS amount_status
            FROM grant_overview_facts AS fact
            WHERE dataset_version=p_dataset_version
            UNION ALL
            SELECT fact.*, 'original', currency, original_amount_minor,
                   original_amount_status
            FROM grant_overview_facts AS fact
            WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        ), entities AS (
            SELECT scoped.dataset_version, scoped.amount_basis,
                   scoped.selected_currency, 'funder' AS role,
                   COALESCE(grant_row.funding_charity_id::text,
                            'name:' || scoped.funding_name_normalized) AS entity_key,
                   grant_row.funding_charity_id AS organization_id,
                   scoped.funding_name AS organization_name,
                   scoped.amount_minor, scoped.amount_status, scoped.grant_id
            FROM scoped
            JOIN grants AS grant_row USING (dataset_version, grant_id)
            WHERE scoped.funding_name IS NOT NULL
            UNION ALL
            SELECT scoped.dataset_version, scoped.amount_basis,
                   scoped.selected_currency, 'recipient',
                   COALESCE(grant_row.recipient_charity_id::text,
                            'name:' || scoped.recipient_name_normalized),
                   grant_row.recipient_charity_id, scoped.recipient_name,
                   scoped.amount_minor, scoped.amount_status, scoped.grant_id
            FROM scoped
            JOIN grants AS grant_row USING (dataset_version, grant_id)
            WHERE scoped.recipient_name IS NOT NULL
        )
        SELECT dataset_version, amount_basis, selected_currency, role, entity_key,
               MAX(organization_id), MIN(organization_name),
               COALESCE(SUM(amount_minor) FILTER (
                   WHERE amount_status NOT IN ('missing','invalid','negative')
               ), 0), COUNT(DISTINCT grant_id)
        FROM entities
        GROUP BY dataset_version, amount_basis, selected_currency, role, entity_key;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_country_funder_rankings
        WITH scoped AS (
            SELECT fact.*, 'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency,
                   eur_amount_minor AS amount_minor, eur_amount_status AS amount_status
            FROM grant_source_funder_facts AS fact
            WHERE dataset_version=p_dataset_version
            UNION ALL
            SELECT fact.*, 'original', currency, original_amount_minor,
                   original_amount_status
            FROM grant_source_funder_facts AS fact
            WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        )
        SELECT dataset_version, amount_basis, selected_currency, country_code,
               MIN(country_name), source_funder_key, MIN(source_namespace),
               MIN(source_organization_id), MIN(display_name), MIN(identity_method),
               COALESCE(array_agg(DISTINCT source_organization_id)
                   FILTER (WHERE source_organization_id IS NOT NULL), ARRAY[]::text[]),
               array_agg(DISTINCT source_namespace), MAX(linked_profile_id),
               COUNT(DISTINCT grant_id), COUNT(DISTINCT recipient_key),
               MIN(award_date), MAX(award_date),
               COALESCE(SUM(amount_minor) FILTER (
                   WHERE amount_status NOT IN ('missing','invalid','negative')
               ), 0),
               COUNT(*) FILTER (
                   WHERE amount_status NOT IN ('missing','invalid','negative')
               ),
               COUNT(*) FILTER (WHERE country_count>1),
               COUNT(*) FILTER (
                   WHERE conversion_status IS NULL OR conversion_status='unavailable_missing_rate'
               ),
               COUNT(*) FILTER (WHERE original_amount_status='missing'),
               COUNT(*) FILTER (WHERE original_amount_status='invalid'),
               COUNT(*) FILTER (WHERE original_amount_status='negative'),
               SUM(original_amount_minor) FILTER (
                   WHERE original_amount_status NOT IN ('negative','invalid','missing')
               ),
               MIN(currency),
               COUNT(*) FILTER (
                   WHERE original_amount_status NOT IN ('negative','invalid','missing')
               ),
               MIN(publisher_source_url),
               MIN(COALESCE(NULLIF(source_organization_id, ''),
                   'source-funder-key:' || source_funder_key))
        FROM scoped
        GROUP BY dataset_version, amount_basis, selected_currency,
                 country_code, source_funder_key;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_funder_relationships
        WITH scoped AS (
            SELECT fact.*, 'eur_converted'::text AS amount_basis,
                   'EUR'::char(3) AS selected_currency,
                   eur_amount_minor AS amount_minor, eur_amount_status AS amount_status
            FROM grant_source_funder_facts AS fact
            WHERE dataset_version=p_dataset_version
            UNION ALL
            SELECT fact.*, 'original', currency, original_amount_minor,
                   original_amount_status
            FROM grant_source_funder_facts AS fact
            WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        ), aggregated AS (
            SELECT dataset_version, amount_basis, selected_currency, country_code,
                   source_funder_key, recipient_key, MIN(recipient_name) AS recipient_name,
                   COALESCE(SUM(amount_minor) FILTER (
                       WHERE amount_status NOT IN ('missing','invalid','negative')
                   ), 0) AS total_amount_minor,
                   COUNT(DISTINCT grant_id) AS grant_count
            FROM scoped
            GROUP BY dataset_version, amount_basis, selected_currency, country_code,
                     source_funder_key, recipient_key
        ), ranked AS (
            SELECT aggregated.*,
                   row_number() OVER (
                       PARTITION BY dataset_version, amount_basis, selected_currency,
                                    country_code, source_funder_key
                       ORDER BY total_amount_minor DESC, recipient_key
                   ) AS relationship_rank
            FROM aggregated
        )
        SELECT dataset_version, amount_basis, selected_currency, country_code,
               source_funder_key, recipient_key, recipient_name,
               total_amount_minor, grant_count, relationship_rank
        FROM ranked WHERE relationship_rank<=50;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        INSERT INTO analytics_filter_values
        SELECT p_dataset_version, 'beneficiary_country', country_name, COUNT(*)
        FROM grant_beneficiary_countries WHERE dataset_version=p_dataset_version
        GROUP BY country_name
        UNION ALL
        SELECT p_dataset_version, 'currency', currency, COUNT(*)
        FROM grant_overview_facts
        WHERE dataset_version=p_dataset_version AND currency IS NOT NULL
        GROUP BY currency
        UNION ALL
        SELECT p_dataset_version, 'programme_area', programme_area, COUNT(*)
        FROM grant_programme_categories WHERE dataset_version=p_dataset_version
        GROUP BY programme_area
        UNION ALL
        SELECT p_dataset_version, 'source', source_namespace, COUNT(*)
        FROM grant_overview_facts WHERE dataset_version=p_dataset_version
        GROUP BY source_namespace;
        GET DIAGNOSTICS current_rows = ROW_COUNT;
        inserted_rows := inserted_rows + current_rows;

        version_uuid := (
            substring(md5(p_dataset_version || '-dashboard-analytics-v1'), 1, 8)
            || '-' || substring(md5(p_dataset_version || '-dashboard-analytics-v1'), 9, 4)
            || '-' || substring(md5(p_dataset_version || '-dashboard-analytics-v1'), 13, 4)
            || '-' || substring(md5(p_dataset_version || '-dashboard-analytics-v1'), 17, 4)
            || '-' || substring(md5(p_dataset_version || '-dashboard-analytics-v1'), 21, 12)
        )::uuid;
        INSERT INTO materialization_versions (
            materialization_version_id, dataset_version, materialization_name,
            revision, status, is_active, row_count, activated_at, metadata
        ) VALUES (
            version_uuid, p_dataset_version, 'dashboard_analytics', 1,
            'active', TRUE, inserted_rows, CURRENT_TIMESTAMP,
            jsonb_build_object('function', 'refresh_analytics_materializations',
                               'relationship_limit', 50)
        )
        ON CONFLICT (dataset_version, materialization_name, revision)
        DO UPDATE SET status='active', is_active=TRUE, row_count=inserted_rows,
                      activated_at=CURRENT_TIMESTAMP,
                      metadata=EXCLUDED.metadata;
        RETURN inserted_rows;
    END;
    $$
    """


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS refresh_analytics_materializations(TEXT)")
    op.execute("DROP INDEX IF EXISTS ix_grants_recipient_source_id")
    op.execute("DROP INDEX IF EXISTS ix_grants_funding_source_id")
    op.execute(
        "DROP INDEX IF EXISTS ix_registry_current_normalized_name"
    )
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
