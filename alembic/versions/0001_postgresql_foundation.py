"""Create the versioned PostgreSQL serving and operational schema.

Revision ID: 0001_postgresql_foundation
Revises: None
Create Date: 2026-07-29
"""

from __future__ import annotations

from typing import Optional, Sequence, Union

from alembic import op


revision: str = "0001_postgresql_foundation"
down_revision: Optional[str] = None
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def _execute_all(statements: Sequence[str]) -> None:
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    _execute_all(
        [
            "CREATE EXTENSION IF NOT EXISTS pg_trgm",
            """
            CREATE TABLE dataset_versions (
                dataset_version TEXT PRIMARY KEY,
                revision BIGINT GENERATED ALWAYS AS IDENTITY,
                status TEXT NOT NULL DEFAULT 'created',
                is_active BOOLEAN NOT NULL DEFAULT FALSE,
                source_checksum CHAR(64),
                source_schema_version TEXT,
                code_revision CHAR(40),
                previous_dataset_version TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMPTZ,
                activated_at TIMESTAMPTZ,
                rejected_at TIMESTAMPTZ,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT uq_dataset_versions_revision UNIQUE (revision),
                CONSTRAINT fk_dataset_versions_previous
                    FOREIGN KEY (previous_dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_dataset_versions_status CHECK (
                    status IN ('created', 'loading', 'validating', 'rejected',
                               'approved', 'active', 'rolled_back', 'failed')
                ),
                CONSTRAINT ck_dataset_versions_activation CHECK (
                    (is_active AND status = 'active' AND activated_at IS NOT NULL)
                    OR (NOT is_active AND status <> 'active')
                ),
                CONSTRAINT ck_dataset_versions_checksum CHECK (
                    source_checksum IS NULL OR source_checksum ~ '^[a-f0-9]{64}$'
                ),
                CONSTRAINT ck_dataset_versions_code_revision CHECK (
                    code_revision IS NULL OR code_revision ~ '^[a-f0-9]{40}$'
                )
            )
            """,
            """
            CREATE UNIQUE INDEX uq_dataset_versions_single_active
            ON dataset_versions ((is_active)) WHERE is_active
            """,
            """
            CREATE TABLE migration_runs (
                migration_run_id UUID PRIMARY KEY,
                target_dataset_version TEXT NOT NULL,
                source_database_checksum CHAR(64) NOT NULL,
                source_schema_version TEXT NOT NULL,
                source_fact_version TEXT NOT NULL,
                target_schema_version TEXT NOT NULL,
                code_revision CHAR(40) NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMPTZ,
                source_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                target_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                reconciliation_results JSONB NOT NULL DEFAULT '{}'::jsonb,
                errors JSONB NOT NULL DEFAULT '[]'::jsonb,
                actor_id TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                CONSTRAINT uq_migration_runs_target UNIQUE (target_dataset_version),
                CONSTRAINT fk_migration_runs_dataset FOREIGN KEY (target_dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_migration_runs_checksum CHECK (
                    source_database_checksum ~ '^[a-f0-9]{64}$'
                ),
                CONSTRAINT ck_migration_runs_revision CHECK (
                    code_revision ~ '^[a-f0-9]{40}$'
                ),
                CONSTRAINT ck_migration_runs_status CHECK (
                    status IN ('created', 'loading', 'validating', 'rejected',
                               'approved', 'active', 'rolled_back', 'failed')
                ),
                CONSTRAINT ck_migration_runs_actor_type CHECK (
                    actor_type IN ('human', 'service', 'ci')
                ),
                CONSTRAINT ck_migration_runs_completion CHECK (
                    completed_at IS NULL OR completed_at >= started_at
                )
            )
            """,
            """
            CREATE TABLE charities (
                dataset_version TEXT NOT NULL,
                charity_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                type TEXT,
                website TEXT,
                email TEXT,
                address TEXT,
                city TEXT,
                state TEXT,
                country TEXT,
                latitude NUMERIC(9, 6),
                longitude NUMERIC(9, 6),
                annual_income NUMERIC(24, 4),
                annual_expenditure NUMERIC(24, 4),
                thematic_focus TEXT,
                geographic_focus TEXT,
                raw_source_data JSONB,
                programme_areas_source JSONB,
                programme_areas_inferred JSONB,
                programme_area_scores JSONB,
                programme_area_method TEXT,
                programme_area_evidence JSONB,
                programme_area_review_required BOOLEAN NOT NULL DEFAULT FALSE,
                geographic_focus_source JSONB,
                geographic_focus_inferred JSONB,
                headquarters_country TEXT,
                headquarters_region TEXT,
                geography_method TEXT,
                geography_confidence NUMERIC(6, 5),
                geography_evidence JSONB,
                geography_review_required BOOLEAN NOT NULL DEFAULT FALSE,
                enrichment_rule_version TEXT,
                enrichment_review_reasons JSONB,
                insufficient_source_text BOOLEAN NOT NULL DEFAULT FALSE,
                normalized_name TEXT,
                normalized_domain TEXT,
                organization_type TEXT,
                primary_source TEXT,
                source_names JSONB,
                source_record_id TEXT,
                source_url TEXT,
                source_records JSONB,
                ingestion_timestamp TIMESTAMPTZ,
                transaction_coverage TEXT,
                deduplication_status TEXT,
                deduplication_candidates JSONB,
                PRIMARY KEY (dataset_version, charity_id),
                CONSTRAINT uq_charities_source_identity
                    UNIQUE (dataset_version, primary_source, source_record_id),
                CONSTRAINT fk_charities_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_charities_name CHECK (btrim(name) <> ''),
                CONSTRAINT ck_charities_latitude CHECK (
                    latitude IS NULL OR latitude BETWEEN -90 AND 90
                ),
                CONSTRAINT ck_charities_longitude CHECK (
                    longitude IS NULL OR longitude BETWEEN -180 AND 180
                ),
                CONSTRAINT ck_charities_geography_confidence CHECK (
                    geography_confidence IS NULL
                    OR geography_confidence BETWEEN 0 AND 1
                ),
                CONSTRAINT ck_charities_programme_method CHECK (
                    programme_area_method IS NULL OR programme_area_method IN (
                        'deterministic_regex', 'source_normalization',
                        'source_normalization+deterministic_regex', 'unavailable'
                    )
                ),
                CONSTRAINT ck_charities_geography_method CHECK (
                    geography_method IS NULL OR geography_method IN (
                        'deterministic_regex', 'source_normalization',
                        'source_normalization+deterministic_regex', 'unavailable'
                    )
                )
            )
            """,
            """
            CREATE INDEX ix_charities_normalized_name
            ON charities (dataset_version, normalized_name, charity_id)
            """,
            """
            CREATE TABLE charity_programme_categories (
                dataset_version TEXT NOT NULL,
                charity_id BIGINT NOT NULL,
                programme_area TEXT NOT NULL,
                provenance TEXT NOT NULL,
                confidence NUMERIC(6, 5),
                PRIMARY KEY (dataset_version, charity_id, programme_area, provenance),
                CONSTRAINT fk_charity_programmes_charity
                    FOREIGN KEY (dataset_version, charity_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_charity_programmes_area CHECK (btrim(programme_area) <> ''),
                CONSTRAINT ck_charity_programmes_provenance CHECK (
                    provenance IN ('source', 'inferred', 'manual', 'unclassified')
                ),
                CONSTRAINT ck_charity_programmes_confidence CHECK (
                    confidence IS NULL OR confidence BETWEEN 0 AND 1
                )
            )
            """,
            """
            CREATE TABLE charity_geographic_areas (
                dataset_version TEXT NOT NULL,
                charity_id BIGINT NOT NULL,
                country_code CHAR(2) NOT NULL,
                administrative_region TEXT NOT NULL DEFAULT '',
                provenance TEXT NOT NULL,
                confidence NUMERIC(6, 5),
                PRIMARY KEY (
                    dataset_version, charity_id, country_code,
                    administrative_region, provenance
                ),
                CONSTRAINT fk_charity_geographies_charity
                    FOREIGN KEY (dataset_version, charity_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_charity_geographies_country CHECK (
                    country_code ~ '^[A-Z]{2}$'
                ),
                CONSTRAINT ck_charity_geographies_provenance CHECK (
                    provenance IN ('source', 'inferred', 'manual', 'unclassified')
                ),
                CONSTRAINT ck_charity_geographies_confidence CHECK (
                    confidence IS NULL OR confidence BETWEEN 0 AND 1
                )
            )
            """,
            """
            CREATE TABLE charity_registry_organizations (
                dataset_version TEXT NOT NULL,
                registry_id TEXT NOT NULL,
                charity_number TEXT NOT NULL,
                linked_charity_number TEXT,
                registered_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                registration_status TEXT,
                registration_date DATE,
                removal_date DATE,
                income NUMERIC(24, 4),
                expenditure NUMERIC(24, 4),
                financial_period_end_date DATE,
                address_line_one TEXT,
                address_line_two TEXT,
                address_line_three TEXT,
                address_line_four TEXT,
                address_line_five TEXT,
                postcode TEXT,
                city TEXT,
                administrative_region TEXT,
                country_code CHAR(2),
                registered_latitude NUMERIC(9, 6),
                registered_longitude NUMERIC(9, 6),
                activity_text TEXT,
                source_name TEXT NOT NULL,
                source_record_updated_at TIMESTAMPTZ,
                imported_at TIMESTAMPTZ NOT NULL,
                is_current_source_record BOOLEAN NOT NULL DEFAULT TRUE,
                search_vector TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector(
                        'simple'::regconfig,
                        coalesce(registered_name, '') || ' ' ||
                        coalesce(normalized_name, '') || ' ' ||
                        coalesce(charity_number, '') || ' ' ||
                        coalesce(postcode, '') || ' ' ||
                        coalesce(activity_text, '')
                    )
                ) STORED,
                PRIMARY KEY (dataset_version, registry_id),
                CONSTRAINT fk_registry_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_registry_id CHECK (btrim(registry_id) <> ''),
                CONSTRAINT ck_registry_charity_number CHECK (btrim(charity_number) <> ''),
                CONSTRAINT ck_registry_registered_name CHECK (btrim(registered_name) <> ''),
                CONSTRAINT ck_registry_normalized_name CHECK (btrim(normalized_name) <> ''),
                CONSTRAINT ck_registry_country CHECK (
                    country_code IS NULL OR country_code ~ '^[A-Z]{2}$'
                ),
                CONSTRAINT ck_registry_latitude CHECK (
                    registered_latitude IS NULL OR registered_latitude BETWEEN -90 AND 90
                ),
                CONSTRAINT ck_registry_longitude CHECK (
                    registered_longitude IS NULL OR registered_longitude BETWEEN -180 AND 180
                ),
                CONSTRAINT ck_registry_dates CHECK (
                    removal_date IS NULL OR registration_date IS NULL
                    OR removal_date >= registration_date
                )
            )
            """,
            """
            CREATE INDEX ix_registry_charity_number
            ON charity_registry_organizations
                (dataset_version, charity_number, registry_id)
            """,
            """
            CREATE INDEX ix_registry_linked_charity_number
            ON charity_registry_organizations
                (dataset_version, linked_charity_number, registry_id)
            """,
            """
            CREATE INDEX ix_registry_status_income
            ON charity_registry_organizations
                (dataset_version, registration_status, income DESC, registry_id)
            """,
            """
            CREATE INDEX ix_registry_status_expenditure
            ON charity_registry_organizations
                (dataset_version, registration_status, expenditure DESC, registry_id)
            """,
            """
            CREATE INDEX ix_registry_country_region
            ON charity_registry_organizations
                (dataset_version, country_code, administrative_region, registry_id)
            """,
            """
            CREATE INDEX ix_registry_search_vector
            ON charity_registry_organizations USING GIN (search_vector)
            """,
            """
            CREATE INDEX ix_registry_registered_name_trgm
            ON charity_registry_organizations USING GIN (registered_name gin_trgm_ops)
            """,
            """
            CREATE INDEX ix_registry_normalized_name_trgm
            ON charity_registry_organizations USING GIN (normalized_name gin_trgm_ops)
            """,
            """
            CREATE TABLE grants (
                dataset_version TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                funding_charity_id BIGINT,
                funding_name TEXT,
                funding_org_source_id TEXT,
                recipient_name TEXT NOT NULL,
                recipient_charity_id BIGINT,
                recipient_org_source_id TEXT,
                amount NUMERIC(24, 4),
                amount_eur NUMERIC(24, 4),
                currency CHAR(3),
                description TEXT,
                award_date DATE,
                recipient_latitude NUMERIC(9, 6),
                recipient_longitude NUMERIC(9, 6),
                recipient_region TEXT,
                beneficiary_geography TEXT,
                project_geography TEXT,
                programme_area_source TEXT,
                tags JSONB,
                geographic_focus TEXT,
                source TEXT,
                source_record_id TEXT,
                source_url TEXT,
                ingestion_timestamp TIMESTAMPTZ,
                raw_grant_data JSONB,
                programme_area_inferred TEXT,
                programme_area_scores JSONB,
                programme_area_method TEXT,
                programme_area_evidence JSONB,
                programme_area_review_required BOOLEAN NOT NULL DEFAULT FALSE,
                beneficiary_geography_normalized TEXT,
                geographic_focus_inferred TEXT,
                geography_method TEXT,
                geography_confidence NUMERIC(6, 5),
                geography_evidence JSONB,
                geography_review_required BOOLEAN NOT NULL DEFAULT FALSE,
                enrichment_rule_version TEXT,
                enrichment_review_reasons JSONB,
                insufficient_source_text BOOLEAN NOT NULL DEFAULT FALSE,
                exchange_rate NUMERIC(24, 12),
                exchange_rate_date DATE,
                exchange_rate_source TEXT,
                conversion_status TEXT,
                PRIMARY KEY (dataset_version, grant_id),
                CONSTRAINT uq_grants_source_identity
                    UNIQUE (dataset_version, source, source_record_id),
                CONSTRAINT fk_grants_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT fk_grants_funder FOREIGN KEY (dataset_version, funding_charity_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT fk_grants_recipient FOREIGN KEY (dataset_version, recipient_charity_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_grants_id CHECK (btrim(grant_id) <> ''),
                CONSTRAINT ck_grants_recipient CHECK (btrim(recipient_name) <> ''),
                CONSTRAINT ck_grants_currency CHECK (
                    currency IS NULL OR currency ~ '^[A-Z]{3}$'
                ),
                CONSTRAINT ck_grants_latitude CHECK (
                    recipient_latitude IS NULL OR recipient_latitude BETWEEN -90 AND 90
                ),
                CONSTRAINT ck_grants_longitude CHECK (
                    recipient_longitude IS NULL OR recipient_longitude BETWEEN -180 AND 180
                ),
                CONSTRAINT ck_grants_geography_confidence CHECK (
                    geography_confidence IS NULL OR geography_confidence BETWEEN 0 AND 1
                ),
                CONSTRAINT ck_grants_exchange_rate CHECK (
                    exchange_rate IS NULL OR exchange_rate > 0
                ),
                CONSTRAINT ck_grants_programme_method CHECK (
                    programme_area_method IS NULL OR programme_area_method IN (
                        'deterministic_regex', 'source_normalization',
                        'source_normalization+deterministic_regex', 'unavailable'
                    )
                ),
                CONSTRAINT ck_grants_geography_method CHECK (
                    geography_method IS NULL OR geography_method IN (
                        'deterministic_regex', 'source_normalization',
                        'source_normalization+deterministic_regex', 'unavailable'
                    )
                ),
                CONSTRAINT ck_grants_conversion_status CHECK (
                    conversion_status IS NULL OR conversion_status IN (
                        'native_eur', 'ecb_monthly_average',
                        'unavailable_missing_rate', 'not_applicable', 'invalid_currency'
                    )
                )
            )
            """,
            """
            CREATE INDEX ix_grants_funder
            ON grants (dataset_version, funding_charity_id, grant_id)
            """,
            """
            CREATE INDEX ix_grants_recipient
            ON grants (dataset_version, recipient_charity_id, grant_id)
            """,
            """
            CREATE INDEX ix_grants_source_date
            ON grants (dataset_version, source, award_date, grant_id)
            """,
            """
            CREATE INDEX ix_grants_currency_date
            ON grants (dataset_version, currency, award_date, grant_id)
            """,
            """
            CREATE INDEX ix_grants_conversion_status
            ON grants (dataset_version, conversion_status, grant_id)
            """,
            """
            CREATE TABLE grant_beneficiary_countries (
                dataset_version TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                country_code CHAR(2) NOT NULL,
                country_name TEXT NOT NULL,
                PRIMARY KEY (dataset_version, grant_id, country_code),
                CONSTRAINT fk_grant_countries_grant
                    FOREIGN KEY (dataset_version, grant_id)
                    REFERENCES grants(dataset_version, grant_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_grant_countries_code CHECK (
                    country_code ~ '^[A-Z]{2}$'
                ),
                CONSTRAINT ck_grant_countries_name CHECK (btrim(country_name) <> '')
            )
            """,
            """
            CREATE INDEX ix_grant_countries_lookup
            ON grant_beneficiary_countries
                (dataset_version, country_code, grant_id)
            """,
            """
            CREATE TABLE grant_beneficiary_terms (
                dataset_version TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                term TEXT NOT NULL,
                PRIMARY KEY (dataset_version, grant_id, term),
                CONSTRAINT fk_grant_terms_grant
                    FOREIGN KEY (dataset_version, grant_id)
                    REFERENCES grants(dataset_version, grant_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_grant_terms_term CHECK (btrim(term) <> '')
            )
            """,
            """
            CREATE INDEX ix_grant_terms_lookup
            ON grant_beneficiary_terms (dataset_version, term, grant_id)
            """,
            """
            CREATE TABLE grant_programme_categories (
                dataset_version TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                programme_area TEXT NOT NULL,
                PRIMARY KEY (dataset_version, grant_id, programme_area),
                CONSTRAINT fk_grant_programmes_grant
                    FOREIGN KEY (dataset_version, grant_id)
                    REFERENCES grants(dataset_version, grant_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_grant_programmes_area CHECK (btrim(programme_area) <> '')
            )
            """,
            """
            CREATE INDEX ix_grant_programmes_lookup
            ON grant_programme_categories
                (dataset_version, programme_area, grant_id)
            """,
            """
            CREATE TABLE grant_overview_facts (
                dataset_version TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                source_namespace TEXT NOT NULL,
                award_date DATE,
                award_date_status TEXT NOT NULL,
                currency CHAR(3),
                original_amount_minor BIGINT,
                original_amount_status TEXT NOT NULL,
                eur_amount_minor BIGINT,
                eur_amount_status TEXT NOT NULL,
                conversion_status TEXT,
                funding_name TEXT NOT NULL,
                funding_name_normalized TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                recipient_name_normalized TEXT NOT NULL,
                country_count INTEGER NOT NULL,
                programme_category_count INTEGER NOT NULL,
                programme_provenance TEXT NOT NULL,
                invalid_source_label BOOLEAN NOT NULL DEFAULT FALSE,
                low_confidence_inference BOOLEAN NOT NULL DEFAULT FALSE,
                origin_country_code CHAR(2),
                origin_country_name TEXT,
                origin_source TEXT,
                data_revision TEXT NOT NULL,
                PRIMARY KEY (dataset_version, grant_id),
                CONSTRAINT fk_overview_facts_grant
                    FOREIGN KEY (dataset_version, grant_id)
                    REFERENCES grants(dataset_version, grant_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_overview_source CHECK (btrim(source_namespace) <> ''),
                CONSTRAINT ck_overview_currency CHECK (
                    currency IS NULL OR currency ~ '^[A-Z]{3}$'
                ),
                CONSTRAINT ck_overview_country CHECK (
                    origin_country_code IS NULL OR origin_country_code ~ '^[A-Z]{2}$'
                ),
                CONSTRAINT ck_overview_counts CHECK (
                    country_count >= 0 AND programme_category_count >= 0
                ),
                CONSTRAINT ck_overview_award_status CHECK (
                    award_date_status IN ('valid', 'missing', 'invalid')
                ),
                CONSTRAINT ck_overview_original_status CHECK (
                    original_amount_status IN ('valid', 'missing', 'negative', 'zero', 'invalid')
                ),
                CONSTRAINT ck_overview_eur_status CHECK (
                    eur_amount_status IN ('valid', 'missing', 'negative', 'zero', 'invalid')
                ),
                CONSTRAINT ck_overview_provenance CHECK (
                    programme_provenance IN ('source', 'inferred', 'unclassified', 'manual')
                )
            )
            """,
            """
            CREATE INDEX ix_overview_funder
            ON grant_overview_facts
                (dataset_version, source_namespace, funding_name_normalized, grant_id)
            """,
            """
            CREATE INDEX ix_overview_recipient
            ON grant_overview_facts
                (dataset_version, source_namespace, recipient_name_normalized, grant_id)
            """,
            """
            CREATE INDEX ix_overview_source_date
            ON grant_overview_facts
                (dataset_version, source_namespace, award_date, grant_id)
            """,
            """
            CREATE TABLE grant_source_funder_facts (
                dataset_version TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                country_code CHAR(2) NOT NULL,
                country_name TEXT NOT NULL,
                source_namespace TEXT NOT NULL,
                source_funder_key TEXT NOT NULL,
                identity_method TEXT NOT NULL,
                source_organization_id TEXT,
                normalized_name_fallback TEXT,
                display_name TEXT NOT NULL,
                recipient_key TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                award_date DATE,
                currency CHAR(3),
                original_amount_minor BIGINT,
                original_amount_status TEXT NOT NULL,
                eur_amount_minor BIGINT,
                eur_amount_status TEXT NOT NULL,
                conversion_status TEXT,
                country_count INTEGER NOT NULL,
                linked_profile_id BIGINT,
                publisher_source_url TEXT,
                source_record_id TEXT,
                data_revision TEXT NOT NULL,
                PRIMARY KEY (dataset_version, grant_id, country_code),
                CONSTRAINT fk_funder_facts_grant
                    FOREIGN KEY (dataset_version, grant_id)
                    REFERENCES grants(dataset_version, grant_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT fk_funder_facts_profile
                    FOREIGN KEY (dataset_version, linked_profile_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_funder_facts_country CHECK (
                    country_code ~ '^[A-Z]{2}$'
                ),
                CONSTRAINT ck_funder_facts_currency CHECK (
                    currency IS NULL OR currency ~ '^[A-Z]{3}$'
                ),
                CONSTRAINT ck_funder_facts_identity CHECK (
                    identity_method IN ('source_id', 'normalized_name', 'manual', 'unknown')
                ),
                CONSTRAINT ck_funder_facts_count CHECK (country_count >= 1),
                CONSTRAINT ck_funder_facts_original_status CHECK (
                    original_amount_status IN ('valid', 'missing', 'negative', 'zero', 'invalid')
                ),
                CONSTRAINT ck_funder_facts_eur_status CHECK (
                    eur_amount_status IN ('valid', 'missing', 'negative', 'zero', 'invalid')
                )
            )
            """,
            """
            CREATE INDEX ix_funder_facts_country_key
            ON grant_source_funder_facts
                (dataset_version, country_code, source_funder_key, grant_id)
            """,
            """
            CREATE INDEX ix_funder_facts_profile
            ON grant_source_funder_facts
                (dataset_version, linked_profile_id, source_funder_key, grant_id)
            """,
            """
            CREATE INDEX ix_funder_facts_source_name
            ON grant_source_funder_facts
                (dataset_version, source_namespace, display_name, grant_id)
            """,
            """
            CREATE TABLE organization_registry_links (
                dataset_version TEXT NOT NULL,
                registry_id TEXT NOT NULL,
                enriched_organization_id BIGINT NOT NULL,
                match_status TEXT NOT NULL,
                match_method TEXT NOT NULL,
                match_confidence NUMERIC(6, 5),
                match_reason TEXT,
                reviewed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (dataset_version, registry_id, enriched_organization_id),
                CONSTRAINT fk_registry_links_registry
                    FOREIGN KEY (dataset_version, registry_id)
                    REFERENCES charity_registry_organizations(dataset_version, registry_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT fk_registry_links_charity
                    FOREIGN KEY (dataset_version, enriched_organization_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_registry_links_status CHECK (
                    match_status IN ('candidate', 'accepted', 'rejected', 'needs_review')
                ),
                CONSTRAINT ck_registry_links_method CHECK (
                    match_method IN (
                        'exact_identifier', 'normalized_name', 'manual', 'unmatched'
                    )
                ),
                CONSTRAINT ck_registry_links_confidence CHECK (
                    match_confidence IS NULL OR match_confidence BETWEEN 0 AND 1
                ),
                CONSTRAINT ck_registry_links_dates CHECK (updated_at >= created_at)
            )
            """,
            """
            CREATE INDEX ix_registry_links_profile
            ON organization_registry_links
                (dataset_version, enriched_organization_id, match_status, registry_id)
            """,
            """
            CREATE INDEX ix_registry_links_status
            ON organization_registry_links
                (dataset_version, registry_id, match_status, enriched_organization_id)
            """,
            """
            CREATE TABLE source_funder_link_overrides (
                source_namespace TEXT NOT NULL,
                source_organization_id TEXT NOT NULL,
                link_mode TEXT NOT NULL,
                target_profile_id BIGINT,
                target_dataset_version TEXT,
                reason TEXT,
                updated_by TEXT,
                updated_at TIMESTAMPTZ NOT NULL,
                revision BIGINT NOT NULL DEFAULT 0,
                PRIMARY KEY (source_namespace, source_organization_id),
                CONSTRAINT fk_funder_overrides_target
                    FOREIGN KEY (target_dataset_version, target_profile_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_funder_overrides_source CHECK (
                    btrim(source_namespace) <> '' AND btrim(source_organization_id) <> ''
                ),
                CONSTRAINT ck_funder_overrides_mode CHECK (
                    link_mode IN ('observed_only', 'link_profile', 'unlink', 'blocked')
                ),
                CONSTRAINT ck_funder_overrides_target CHECK (
                    (link_mode = 'link_profile' AND target_profile_id IS NOT NULL
                     AND target_dataset_version IS NOT NULL)
                    OR (link_mode <> 'link_profile' AND target_profile_id IS NULL
                        AND target_dataset_version IS NULL)
                ),
                CONSTRAINT ck_funder_overrides_revision CHECK (revision >= 0)
            )
            """,
            """
            CREATE FUNCTION enforce_override_revision() RETURNS TRIGGER
            LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'UPDATE' AND NEW.revision <> OLD.revision + 1 THEN
                    RAISE EXCEPTION 'override revision must increase by exactly one';
                END IF;
                RETURN NEW;
            END;
            $$
            """,
            """
            CREATE TRIGGER trg_source_funder_override_revision
            BEFORE UPDATE ON source_funder_link_overrides
            FOR EACH ROW EXECUTE FUNCTION enforce_override_revision()
            """,
            """
            CREATE TABLE source_funder_profile_cache (
                dataset_version TEXT NOT NULL,
                source_funder_key TEXT NOT NULL,
                profile_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                payload JSONB,
                error TEXT,
                updated_at TIMESTAMPTZ NOT NULL,
                job_token UUID,
                link_revision BIGINT NOT NULL DEFAULT 0,
                PRIMARY KEY (dataset_version, source_funder_key),
                CONSTRAINT fk_funder_cache_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT fk_funder_cache_profile
                    FOREIGN KEY (dataset_version, profile_id)
                    REFERENCES charities(dataset_version, charity_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_funder_cache_key CHECK (btrim(source_funder_key) <> ''),
                CONSTRAINT ck_funder_cache_status CHECK (
                    status IN ('pending', 'ready', 'failed', 'stale')
                ),
                CONSTRAINT ck_funder_cache_payload CHECK (
                    (status = 'ready' AND payload IS NOT NULL AND error IS NULL)
                    OR status <> 'ready'
                ),
                CONSTRAINT ck_funder_cache_revision CHECK (link_revision >= 0)
            )
            """,
            """
            CREATE INDEX ix_funder_cache_profile
            ON source_funder_profile_cache
                (dataset_version, profile_id, status, source_funder_key)
            """,
            """
            CREATE TABLE exchange_rates (
                currency CHAR(3) NOT NULL,
                rate_date DATE NOT NULL,
                eur_reference_rate NUMERIC(24, 12) NOT NULL,
                source_series TEXT NOT NULL,
                source_url TEXT NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (currency, rate_date),
                CONSTRAINT ck_exchange_rates_currency CHECK (
                    currency ~ '^[A-Z]{3}$' AND currency <> 'EUR'
                ),
                CONSTRAINT ck_exchange_rates_value CHECK (eur_reference_rate > 0),
                CONSTRAINT ck_exchange_rates_source CHECK (
                    btrim(source_series) <> '' AND btrim(source_url) <> ''
                )
            )
            """,
            """
            CREATE TABLE job_runs (
                job_run_id UUID PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                dataset_version TEXT,
                idempotency_key TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                attempt INTEGER NOT NULL DEFAULT 1,
                max_attempts INTEGER NOT NULL DEFAULT 1,
                input JSONB NOT NULL DEFAULT '{}'::jsonb,
                result JSONB,
                error_class TEXT,
                error_message TEXT,
                CONSTRAINT uq_job_runs_idempotency UNIQUE (job_type, idempotency_key),
                CONSTRAINT fk_job_runs_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE SET NULL,
                CONSTRAINT ck_job_runs_type CHECK (btrim(job_type) <> ''),
                CONSTRAINT ck_job_runs_status CHECK (
                    status IN ('created', 'queued', 'running', 'succeeded', 'failed',
                               'cancelled', 'timed_out', 'dead_lettered')
                ),
                CONSTRAINT ck_job_runs_attempts CHECK (
                    attempt >= 1 AND max_attempts >= 1 AND attempt <= max_attempts
                ),
                CONSTRAINT ck_job_runs_timestamps CHECK (
                    (started_at IS NULL OR started_at >= requested_at)
                    AND (completed_at IS NULL OR started_at IS NOT NULL)
                    AND (completed_at IS NULL OR completed_at >= started_at)
                )
            )
            """,
            """
            CREATE INDEX ix_job_runs_status_requested
            ON job_runs (status, requested_at DESC, job_run_id)
            """,
            """
            CREATE TABLE job_events (
                job_event_id UUID PRIMARY KEY,
                job_run_id UUID NOT NULL,
                sequence BIGINT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actor_id TEXT,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT uq_job_events_sequence UNIQUE (job_run_id, sequence),
                CONSTRAINT fk_job_events_run FOREIGN KEY (job_run_id)
                    REFERENCES job_runs(job_run_id)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_job_events_sequence CHECK (sequence >= 1),
                CONSTRAINT ck_job_events_type CHECK (btrim(event_type) <> '')
            )
            """,
            """
            CREATE INDEX ix_job_events_occurred
            ON job_events (job_run_id, occurred_at, sequence)
            """,
            """
            CREATE TABLE audit_events (
                audit_event_id UUID PRIMARY KEY,
                request_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_role TEXT,
                action TEXT NOT NULL,
                target TEXT,
                reason TEXT,
                outcome TEXT NOT NULL,
                http_status INTEGER,
                dataset_version TEXT,
                error_class TEXT,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT fk_audit_events_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE SET NULL,
                CONSTRAINT ck_audit_events_request CHECK (btrim(request_id) <> ''),
                CONSTRAINT ck_audit_events_actor CHECK (btrim(actor_id) <> ''),
                CONSTRAINT ck_audit_events_action CHECK (btrim(action) <> ''),
                CONSTRAINT ck_audit_events_role CHECK (
                    actor_role IS NULL OR actor_role IN (
                        'anonymous', 'viewer', 'analyst', 'operator', 'administrator',
                        'service'
                    )
                ),
                CONSTRAINT ck_audit_events_outcome CHECK (
                    outcome IN ('allowed', 'denied', 'succeeded', 'failed')
                ),
                CONSTRAINT ck_audit_events_http_status CHECK (
                    http_status IS NULL OR http_status BETWEEN 100 AND 599
                )
            )
            """,
            """
            CREATE INDEX ix_audit_events_actor_time
            ON audit_events (actor_id, occurred_at DESC, audit_event_id)
            """,
            """
            CREATE INDEX ix_audit_events_action_time
            ON audit_events (action, occurred_at DESC, audit_event_id)
            """,
            """
            CREATE FUNCTION forbid_audit_event_mutation() RETURNS TRIGGER
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'audit_events is append-only';
            END;
            $$
            """,
            """
            CREATE TRIGGER trg_audit_events_no_update
            BEFORE UPDATE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION forbid_audit_event_mutation()
            """,
            """
            CREATE TRIGGER trg_audit_events_no_delete
            BEFORE DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION forbid_audit_event_mutation()
            """,
            """
            CREATE TABLE source_ingestion_runs (
                source_ingestion_run_id UUID PRIMARY KEY,
                source_namespace TEXT NOT NULL,
                dataset_version TEXT NOT NULL,
                job_run_id UUID,
                status TEXT NOT NULL DEFAULT 'created',
                source_version TEXT,
                source_uri TEXT,
                object_checksum CHAR(64),
                record_count BIGINT,
                started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMPTZ,
                metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT uq_ingestion_source_version
                    UNIQUE (source_namespace, source_version, dataset_version),
                CONSTRAINT fk_ingestion_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT fk_ingestion_job FOREIGN KEY (job_run_id)
                    REFERENCES job_runs(job_run_id)
                    ON UPDATE CASCADE ON DELETE SET NULL,
                CONSTRAINT ck_ingestion_source CHECK (btrim(source_namespace) <> ''),
                CONSTRAINT ck_ingestion_status CHECK (
                    status IN ('created', 'fetching', 'validating', 'loaded',
                               'rejected', 'failed')
                ),
                CONSTRAINT ck_ingestion_checksum CHECK (
                    object_checksum IS NULL OR object_checksum ~ '^[a-f0-9]{64}$'
                ),
                CONSTRAINT ck_ingestion_count CHECK (
                    record_count IS NULL OR record_count >= 0
                ),
                CONSTRAINT ck_ingestion_completion CHECK (
                    completed_at IS NULL OR completed_at >= started_at
                )
            )
            """,
            """
            CREATE TABLE data_quality_issues (
                data_quality_issue_id UUID PRIMARY KEY,
                dataset_version TEXT NOT NULL,
                migration_run_id UUID,
                source_table TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                field_name TEXT,
                original_value JSONB,
                reason TEXT NOT NULL,
                rule TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMPTZ,
                resolution TEXT,
                CONSTRAINT fk_quality_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT fk_quality_migration FOREIGN KEY (migration_run_id)
                    REFERENCES migration_runs(migration_run_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_quality_source CHECK (
                    btrim(source_table) <> '' AND btrim(source_record_id) <> ''
                ),
                CONSTRAINT ck_quality_reason CHECK (
                    btrim(reason) <> '' AND btrim(rule) <> ''
                ),
                CONSTRAINT ck_quality_status CHECK (
                    status IN ('open', 'quarantined', 'accepted', 'corrected', 'dismissed')
                ),
                CONSTRAINT ck_quality_resolution CHECK (
                    (status IN ('open', 'quarantined') AND resolved_at IS NULL)
                    OR (status IN ('accepted', 'corrected', 'dismissed')
                        AND resolved_at IS NOT NULL)
                )
            )
            """,
            """
            CREATE INDEX ix_quality_dataset_status
            ON data_quality_issues
                (dataset_version, status, source_table, source_record_id)
            """,
            """
            CREATE TABLE materialization_versions (
                materialization_version_id UUID PRIMARY KEY,
                dataset_version TEXT NOT NULL,
                materialization_name TEXT NOT NULL,
                revision BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                is_active BOOLEAN NOT NULL DEFAULT FALSE,
                row_count BIGINT,
                checksum CHAR(64),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                activated_at TIMESTAMPTZ,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT uq_materialization_revision
                    UNIQUE (dataset_version, materialization_name, revision),
                CONSTRAINT fk_materialization_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT ck_materialization_name CHECK (
                    btrim(materialization_name) <> ''
                ),
                CONSTRAINT ck_materialization_revision CHECK (revision >= 1),
                CONSTRAINT ck_materialization_status CHECK (
                    status IN ('created', 'building', 'validated', 'active',
                               'superseded', 'failed')
                ),
                CONSTRAINT ck_materialization_activation CHECK (
                    (is_active AND status = 'active' AND activated_at IS NOT NULL)
                    OR (NOT is_active AND status <> 'active')
                ),
                CONSTRAINT ck_materialization_count CHECK (
                    row_count IS NULL OR row_count >= 0
                ),
                CONSTRAINT ck_materialization_checksum CHECK (
                    checksum IS NULL OR checksum ~ '^[a-f0-9]{64}$'
                )
            )
            """,
            """
            CREATE UNIQUE INDEX uq_materialization_single_active
            ON materialization_versions (dataset_version, materialization_name)
            WHERE is_active
            """,
            """
            CREATE TABLE retention_actions (
                retention_action_id UUID PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reported',
                dry_run BOOLEAN NOT NULL DEFAULT TRUE,
                legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
                reason TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                approved_by TEXT,
                approved_at TIMESTAMPTZ,
                executed_at TIMESTAMPTZ,
                manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT ck_retention_target CHECK (
                    btrim(target_type) <> '' AND btrim(target_id) <> ''
                ),
                CONSTRAINT ck_retention_status CHECK (
                    status IN ('reported', 'held', 'approved', 'executed',
                               'cancelled', 'failed')
                ),
                CONSTRAINT ck_retention_approval CHECK (
                    dry_run OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)
                ),
                CONSTRAINT ck_retention_hold CHECK (
                    NOT legal_hold OR status IN ('reported', 'held', 'cancelled')
                ),
                CONSTRAINT ck_retention_execution CHECK (
                    executed_at IS NULL OR (status = 'executed' AND NOT dry_run)
                )
            )
            """,
            """
            CREATE TABLE export_jobs (
                export_job_id UUID PRIMARY KEY,
                job_run_id UUID,
                dataset_version TEXT NOT NULL,
                export_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                requested_by TEXT NOT NULL,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMPTZ,
                object_key TEXT,
                checksum CHAR(64),
                row_count BIGINT,
                expires_at TIMESTAMPTZ,
                CONSTRAINT fk_export_job_run FOREIGN KEY (job_run_id)
                    REFERENCES job_runs(job_run_id)
                    ON UPDATE CASCADE ON DELETE SET NULL,
                CONSTRAINT fk_export_dataset FOREIGN KEY (dataset_version)
                    REFERENCES dataset_versions(dataset_version)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT ck_export_type CHECK (btrim(export_type) <> ''),
                CONSTRAINT ck_export_status CHECK (
                    status IN ('created', 'queued', 'running', 'succeeded',
                               'failed', 'expired')
                ),
                CONSTRAINT ck_export_checksum CHECK (
                    checksum IS NULL OR checksum ~ '^[a-f0-9]{64}$'
                ),
                CONSTRAINT ck_export_count CHECK (row_count IS NULL OR row_count >= 0),
                CONSTRAINT ck_export_completion CHECK (
                    completed_at IS NULL OR completed_at >= requested_at
                )
            )
            """,
            """
            CREATE INDEX ix_export_jobs_status_requested
            ON export_jobs (status, requested_at DESC, export_job_id)
            """,
            """
            CREATE TABLE idempotency_records (
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash CHAR(64) NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                response_status INTEGER,
                response_body JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (actor_id, action, idempotency_key),
                CONSTRAINT ck_idempotency_actor CHECK (btrim(actor_id) <> ''),
                CONSTRAINT ck_idempotency_action CHECK (btrim(action) <> ''),
                CONSTRAINT ck_idempotency_key CHECK (btrim(idempotency_key) <> ''),
                CONSTRAINT ck_idempotency_hash CHECK (
                    request_hash ~ '^[a-f0-9]{64}$'
                ),
                CONSTRAINT ck_idempotency_status CHECK (
                    status IN ('reserved', 'completed', 'failed')
                ),
                CONSTRAINT ck_idempotency_response CHECK (
                    response_status IS NULL OR response_status BETWEEN 100 AND 599
                ),
                CONSTRAINT ck_idempotency_expiry CHECK (expires_at > created_at),
                CONSTRAINT ck_idempotency_completion CHECK (
                    (status = 'reserved' AND completed_at IS NULL)
                    OR (status <> 'reserved' AND completed_at IS NOT NULL)
                )
            )
            """,
            """
            CREATE INDEX ix_idempotency_expiry
            ON idempotency_records (expires_at, status)
            """,
        ]
    )


def downgrade() -> None:
    _execute_all(
        [
            "DROP TABLE IF EXISTS idempotency_records",
            "DROP TABLE IF EXISTS export_jobs",
            "DROP TABLE IF EXISTS retention_actions",
            "DROP TABLE IF EXISTS materialization_versions",
            "DROP TABLE IF EXISTS data_quality_issues",
            "DROP TABLE IF EXISTS source_ingestion_runs",
            "DROP TRIGGER IF EXISTS trg_audit_events_no_delete ON audit_events",
            "DROP TRIGGER IF EXISTS trg_audit_events_no_update ON audit_events",
            "DROP TABLE IF EXISTS audit_events",
            "DROP FUNCTION IF EXISTS forbid_audit_event_mutation()",
            "DROP TABLE IF EXISTS job_events",
            "DROP TABLE IF EXISTS job_runs",
            "DROP TABLE IF EXISTS exchange_rates",
            "DROP TABLE IF EXISTS source_funder_profile_cache",
            "DROP TRIGGER IF EXISTS trg_source_funder_override_revision ON source_funder_link_overrides",
            "DROP TABLE IF EXISTS source_funder_link_overrides",
            "DROP FUNCTION IF EXISTS enforce_override_revision()",
            "DROP TABLE IF EXISTS organization_registry_links",
            "DROP TABLE IF EXISTS grant_source_funder_facts",
            "DROP TABLE IF EXISTS grant_overview_facts",
            "DROP TABLE IF EXISTS grant_programme_categories",
            "DROP TABLE IF EXISTS grant_beneficiary_terms",
            "DROP TABLE IF EXISTS grant_beneficiary_countries",
            "DROP TABLE IF EXISTS grants",
            "DROP TABLE IF EXISTS charity_registry_organizations",
            "DROP TABLE IF EXISTS charity_geographic_areas",
            "DROP TABLE IF EXISTS charity_programme_categories",
            "DROP TABLE IF EXISTS charities",
            "DROP TABLE IF EXISTS migration_runs",
            "DROP TABLE IF EXISTS dataset_versions",
        ]
    )
