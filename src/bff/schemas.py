from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

# Authentication Schemas
class UserLogin(BaseModel):
    username: str = Field(..., examples=["admin"])
    password: str = Field(..., examples=["password"])

class Token(BaseModel):
    access_token: str
    token_type: str

# Charity Nested Schemas
class CharityFinancialHistoryItem(BaseModel):
    ar_cycle_reference: Optional[str] = None
    financial_period_end_date: Optional[str] = None
    income: Optional[float] = None
    expenditure: Optional[float] = None
    consolidated_account: Optional[bool] = None
    charity_only_account: Optional[bool] = None
    income_from_govt_contracts: Optional[float] = None
    income_from_govt_grants: Optional[float] = None
    inc_donations_and_legacies: Optional[float] = None
    inc_other_trading_activities: Optional[float] = None
    inc_charitable_activities: Optional[float] = None
    inc_endowments: Optional[float] = None
    inc_legacies: Optional[float] = None
    inc_investment: Optional[float] = None
    inc_other: Optional[float] = None
    inc_total: Optional[float] = None
    exp_charitable_activities: Optional[float] = None
    exp_raising_funds: Optional[float] = None
    exp_governance: Optional[float] = None
    exp_grants_institution: Optional[float] = None
    exp_investment_management: Optional[float] = None
    exp_other: Optional[float] = None
    exp_total: Optional[float] = None

class CharityAssetsLiabilitiesItem(BaseModel):
    organisation_number: Optional[int] = None
    fin_period_end_date: Optional[str] = None
    assets_own_use: Optional[float] = None
    assets_long_term_investment: Optional[float] = None
    defined_net_assets_pension: Optional[float] = None
    assets_other_assets: Optional[float] = None
    assets_total_liabilities: Optional[float] = None

class CharityAllDetails(BaseModel):
    organisation_number: int
    reg_charity_number: int
    group_subsid_suffix: int
    charity_name: str
    charity_type: Optional[str] = None
    insolvent: Optional[bool] = False
    in_administration: Optional[bool] = False
    prev_excepted_ind: Optional[bool] = False
    cif_cdf_ind: Optional[str] = None
    cio_dissolution_ind: Optional[bool] = False
    interim_manager_ind: Optional[bool] = False
    date_of_interim_manager_appt: Optional[str] = None
    reg_status: str
    date_of_registration: Optional[str] = None
    date_of_removal: Optional[str] = None
    latest_acc_fin_year_start_date: Optional[str] = None
    latest_acc_fin_year_end_date: Optional[str] = None
    latest_income: Optional[float] = None
    latest_expenditure: Optional[float] = None
    address_line_one: Optional[str] = None
    address_line_two: Optional[str] = None
    address_line_three: Optional[str] = None
    address_line_four: Optional[str] = None
    address_line_five: Optional[str] = None
    address_post_code: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    web: Optional[str] = None
    charity_co_reg_number: Optional[str] = None
    reporting_status: Optional[str] = None
    removal_reason: Optional[str] = None
    cio_ind: Optional[bool] = False
    last_modified_time: Optional[str] = None
    trustee_names: Optional[List[Any]] = []
    who_what_where: Optional[List[Any]] = []
    CharityAoOCountryContinent: Optional[List[Any]] = []
    CharityAoOLocalAuthority: Optional[List[Any]] = []
    CharityAoORegion: Optional[List[Any]] = []
    other_names: Optional[List[Any]] = []
    constituency_name: Optional[List[Any]] = []

# Main API Response Schemas
class CharityBase(BaseModel):
    registered_charity_number: int
    suffix: int
    link: Optional[str] = None
    charity_name: str
    reg_status: str
    reporting_status: Optional[str] = None
    removal_reason: Optional[str] = None
    latest_income: Optional[float] = None
    latest_expenditure: Optional[float] = None
    programme_areas_source: List[str] = []
    programme_areas_inferred: List[str] = []
    geographic_focus_source: List[Any] = []
    geographic_focus_inferred: List[str] = []
    headquarters_country: Optional[str] = None
    headquarters_region: Optional[str] = None
    programme_area_review_required: bool = False
    geography_review_required: bool = False
    enrichment_rule_version: Optional[str] = None
    organization_type: str = "unknown"
    primary_source: Optional[str] = None
    source_names: List[str] = []
    source_record_id: Optional[str] = None
    source_url: Optional[str] = None
    transaction_coverage: str = "unknown"
    relevance_score: Optional[float] = None
    score_confidence: Optional[float] = None
    score_completeness: Optional[float] = None
    score_target: Optional[str] = None
    score_version: Optional[str] = None
    score_configuration_status: Optional[str] = None

class CharityDetail(BaseModel):
    registered_charity_number: int
    suffix: int
    link: Optional[str] = None
    all_details: CharityAllDetails
    assets_liabilities: List[CharityAssetsLiabilitiesItem] = []
    primary_grants: Optional[Any] = None
    who_what_how: Optional[List[Any]] = []
    financial_history: List[CharityFinancialHistoryItem] = []
    programme_areas_source: List[str] = []
    programme_areas_inferred: List[str] = []
    programme_area_scores: Dict[str, float] = {}
    programme_area_method: Optional[str] = None
    programme_area_evidence: List[Dict[str, Any]] = []
    programme_area_review_required: bool = False
    geographic_focus_source: List[Any] = []
    geographic_focus_inferred: List[str] = []
    headquarters_country: Optional[str] = None
    headquarters_region: Optional[str] = None
    geography_method: Optional[str] = None
    geography_confidence: Optional[float] = None
    geography_evidence: List[Dict[str, Any]] = []
    geography_review_required: bool = False
    enrichment_rule_version: Optional[str] = None
    organization_type: str = "unknown"
    primary_source: Optional[str] = None
    source_names: List[str] = []
    source_record_id: Optional[str] = None
    source_url: Optional[str] = None
    source_records: List[Dict[str, Any]] = []
    ingestion_timestamp: Optional[str] = None
    transaction_coverage: str = "unknown"
    deduplication_status: Optional[str] = None
    deduplication_candidates: List[Dict[str, Any]] = []

class CharityStats(BaseModel):
    total_charities: int
    active_charities: int
    removed_charities: int
    average_income: float
    average_expenditure: float
    total_grants: Optional[int] = None
    data_mode: str = "unknown"
    source: List[str] = []
    source_counts: Dict[str, int] = {}
    organization_type_counts: Dict[str, int] = {}


class RegistryOrganizationSummary(BaseModel):
    """A lightweight official register result, not an assertion of funding activity."""
    registry_id: str
    charity_number: str
    registered_name: str
    registration_status: Optional[str] = None
    income: Optional[float] = None
    expenditure: Optional[float] = None
    city: Optional[str] = None
    administrative_region: Optional[str] = None
    country_code: Optional[str] = None
    source_record_updated_at: Optional[str] = None
    has_enriched_profile: bool = False
    has_grant_data: bool = False
    has_philea_data: bool = False


class RegistryDirectoryPage(BaseModel):
    results: List[RegistryOrganizationSummary] = []
    next_cursor: Optional[str] = None
    has_more: bool = False
    applied_filters: Dict[str, Any] = {}
    page_size: int
    registry_count: Optional[int] = None
    search_strategy: str = "indexed_prefix"


class RegistryEnrichedLink(BaseModel):
    enriched_organization_id: int
    organization_name: str
    match_status: str
    match_method: str
    match_confidence: Optional[float] = None
    match_reason: Optional[str] = None
    has_grant_data: bool = False
    has_philea_data: bool = False


class RegistryOrganizationDetail(BaseModel):
    registry_id: str
    charity_number: str
    linked_charity_number: Optional[str] = None
    registered_name: str
    registration_status: Optional[str] = None
    registration_date: Optional[str] = None
    removal_date: Optional[str] = None
    income: Optional[float] = None
    expenditure: Optional[float] = None
    financial_period_end_date: Optional[str] = None
    address_lines: List[str] = []
    postcode: Optional[str] = None
    city: Optional[str] = None
    administrative_region: Optional[str] = None
    country_code: Optional[str] = None
    activity_text: Optional[str] = None
    source_name: str
    source_record_updated_at: Optional[str] = None
    imported_at: str
    is_current_source_record: bool = True
    observed_grant_data_message: str
    enriched_profile: Optional[RegistryEnrichedLink] = None

class GrantMapItem(BaseModel):
    region_or_country_code: Optional[str] = None
    region_or_country_name: str
    grant_count: int
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    distinct_funders: int = 0
    distinct_recipients: int = 0
    top_programme_areas: List[Dict[str, Any]] = []
    top_funders: List[Dict[str, Any]] = []
    top_recipients: List[Dict[str, Any]] = []
    original_geographies: List[str] = []
    funding_grant_count: int = 0
    excluded_multi_country_grant_count: int = 0
    excluded_invalid_amount_grant_count: int = 0

class GrantMapConnection(BaseModel):
    origin_country_code: str
    origin_country_name: str
    destination_country_code: str
    destination_country_name: str
    grant_count: int
    top_funders: List[Dict[str, Any]] = []
    origin_sources: List[str] = []

class DataMetadata(BaseModel):
    data_mode: str
    source: List[str] = []
    generated_at: Optional[str] = None
    record_count: int = 0
    derivation: Optional[str] = None
    coverage: Optional[float] = None
    limitations: List[str] = []

class GrantMapResponse(BaseModel):
    status: str
    geographic_dimension: str
    items: List[GrantMapItem] = []
    known_geography_count: int = 0
    unknown_geography_count: int = 0
    coverage_percentage: float = 0.0
    currencies: List[str] = []
    selected_currency: Optional[str] = None
    funding_status: str = "unavailable"
    funding_mode_available: bool = False
    grant_country_association_count: int = 0
    multi_country_grant_count: int = 0
    funding_excluded_multi_country_count: int = 0
    funding_excluded_multi_country_amount: float = 0.0
    funding_excluded_currency_count: int = 0
    funding_excluded_invalid_amount_count: int = 0
    connections: List[GrantMapConnection] = []
    connection_grant_count: int = 0
    connection_excluded_no_headquarters_count: int = 0
    connection_same_country_count: int = 0
    minimum_coverage_threshold: float = 0.30
    metadata: DataMetadata

class GrantDetail(BaseModel):
    grant_id: str
    funding_charity_id: Optional[int] = None
    funding_name: Optional[str] = None
    funding_org_source_id: Optional[str] = None
    recipient_name: str
    recipient_charity_id: Optional[int] = None
    recipient_org_source_id: Optional[str] = None
    amount: Optional[float] = None
    amount_eur: Optional[float] = None
    exchange_rate: Optional[float] = None
    exchange_rate_date: Optional[str] = None
    exchange_rate_source: Optional[str] = None
    conversion_status: Optional[str] = None
    currency: str
    description: str
    date: str
    recipient_region: Optional[str] = None
    beneficiary_geography: List[Any] = []
    tags: List[str] = []
    source: Optional[str] = None
    source_record_id: Optional[str] = None
    source_url: Optional[str] = None
    programme_area_source: List[str] = []
    programme_area_inferred: List[str] = []
    programme_area_scores: Dict[str, float] = {}
    programme_area_method: Optional[str] = None
    programme_area_evidence: List[Dict[str, Any]] = []
    programme_area_review_required: bool = False
    beneficiary_geography_normalized: List[Dict[str, Any]] = []
    geographic_focus_inferred: List[str] = []
    geography_method: Optional[str] = None
    geography_confidence: Optional[float] = None
    geography_evidence: List[Dict[str, Any]] = []
    geography_review_required: bool = False
    enrichment_rule_version: Optional[str] = None

class GrantListResponse(BaseModel):
    status: str
    organization_id: int
    role: str
    transaction_coverage: str
    grant_count: int
    currencies: List[str] = []
    grants: List[GrantDetail] = []
    metadata: DataMetadata

class GrantRankingItem(BaseModel):
    organization_id: Optional[int] = None
    organization_name: str
    total_amount: float
    currency: str
    grant_count: int

class GrantNetworkSummary(BaseModel):
    status: str
    total_grant_count: int
    currencies: List[str] = []
    largest_donors: List[GrantRankingItem] = []
    largest_recipients: List[GrantRankingItem] = []
    metadata: DataMetadata


class SourceFunderProfileLink(BaseModel):
    charity_id: int
    name: Optional[str] = None


class SourceEvidenceLink(BaseModel):
    kind: str
    label: str
    role: Optional[str] = None
    organization_name: Optional[str] = None
    link_type: str = "website"
    url: str
    origin: str


class SourceFunderActivity(BaseModel):
    grant_count: int = 0
    distinct_recipient_count: int = 0
    first_award_date: Optional[str] = None
    latest_award_date: Optional[str] = None


class SourceFunderObservedFunding(BaseModel):
    amount: Optional[float] = None
    currency: Optional[str] = None
    included_grant_count: int = 0
    excluded_multi_country_grant_count: int = 0
    excluded_multi_country_amount: float = 0.0
    excluded_conversion_grant_count: int = 0
    excluded_missing_amount_grant_count: int = 0
    excluded_invalid_amount_grant_count: int = 0
    excluded_negative_amount_grant_count: int = 0
    fallback_original_amount: Optional[float] = None
    fallback_original_currency: Optional[str] = None
    fallback_original_grant_count: int = 0


class SourceFunderItem(BaseModel):
    rank: Optional[int] = None
    kind: str = "source_funder"
    identity: Dict[str, Any] = {}
    source_funder_key: str
    display_name: str
    identity_method: str
    source_ids: List[str] = []
    sources: List[str] = []
    source_only: bool = True
    linked_directory_profile: Optional[SourceFunderProfileLink] = None
    profile_link: Dict[str, Any] = {"status": "none"}
    evidence_sources: List[str] = []
    activity: SourceFunderActivity
    observed_activity: Dict[str, Any] = {}
    observed_funding: SourceFunderObservedFunding
    amount_policy: Dict[str, Any] = {}
    leading_programme_areas: List[Dict[str, Any]] = []
    representative_source_url: Optional[str] = None


class SourceFunderPagination(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class SourceFunderListResponse(BaseModel):
    status: str
    country: Dict[str, str]
    summary: Dict[str, Any] = {}
    items: List[SourceFunderItem] = []
    pagination: SourceFunderPagination
    available_date_range: Dict[str, Optional[str]] = {}
    available_currencies: List[str] = []
    available_sort_modes: List[str] = []
    applied_filters: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}


class SourceFunderGrantSample(BaseModel):
    grant_id: str
    recipient_name: str
    award_date: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    original_amount: Optional[float] = None
    original_currency: Optional[str] = None
    source_url: Optional[str] = None
    description: Optional[str] = None
    evidence_links: List[SourceEvidenceLink] = []


class SourceFunderRelationshipNode(BaseModel):
    id: str
    label: str
    role: str


class SourceFunderRelationshipLink(BaseModel):
    source: str
    target: str
    value: float
    currency: Optional[str] = None
    grant_count: int


class SourceFunderRelationshipFlow(BaseModel):
    """A source-identity donor-to-recipient flow, never a synthetic profile."""

    status: str
    nodes: List[SourceFunderRelationshipNode] = []
    links: List[SourceFunderRelationshipLink] = []
    metadata: Dict[str, Any] = {}


class SourceFunderDetailResponse(BaseModel):
    status: str
    country: Dict[str, str]
    funder: SourceFunderItem
    top_recipients: List[Dict[str, Any]] = []
    relationships: SourceFunderRelationshipFlow
    grant_sample: List[SourceFunderGrantSample] = []
    source_evidence: List[SourceEvidenceLink] = []
    relationship_summary: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}


class GrantAggregationExclusions(BaseModel):
    missing_date: int = 0
    invalid_date: int = 0
    missing_amount: int = 0
    invalid_amount: int = 0
    negative_amount: int = 0
    unsupported_currency: int = 0
    currency_filtered: int = 0
    unsupported_source: int = 0
    outside_period: int = 0


class GrantAggregationScope(BaseModel):
    coverage_note: str
    market_scope: str = "available cached 360Giving records"


class GrantAmountPolicy(BaseModel):
    monetary_precision: str = "minor_units_2_decimal_places"
    rounding: str = "ROUND_HALF_UP"
    zero_amounts: str = "included_when_source_value_is_numeric_zero"
    negative_amounts: str = "excluded_and_reported"
    upper_bound: str = "no_unapproved_implausibility_threshold_applied"
    maximum_observed_amount: Optional[float] = None


class GrantTrendPeriod(BaseModel):
    from_month: str = Field(alias="from")
    to: str
    months: int
    anchor: str

    model_config = {"populate_by_name": True}


class GrantTrendItem(BaseModel):
    month: str
    grant_count: Optional[int] = None
    source_record_count: int = 0
    total_amount: Optional[float] = None
    coverage_status: str
    mapped_grant_count: int = 0
    unmapped_grant_count: int = 0


class GrantTrendsResponse(BaseModel):
    status: str
    currency: Optional[str] = None
    available_currencies: List[str] = []
    date_basis: str = "award_date"
    granularity: str = "monthly"
    period: Optional[GrantTrendPeriod] = None
    items: List[GrantTrendItem] = []
    excluded: GrantAggregationExclusions
    zero_amount_count: int = 0
    latest_award_date: Optional[str] = None
    last_refreshed_at: Optional[str] = None
    source: List[str] = []
    data_mode: str
    amount_policy: GrantAmountPolicy
    scope: GrantAggregationScope


class ProgrammeAllocationItem(BaseModel):
    programme_area: str
    distinct_grant_count: int
    weighted_grant_count: float
    allocated_amount: float
    source_classified_grant_count: int = 0
    inferred_classified_grant_count: int = 0
    unclassified_grant_count: int = 0


class ProgrammeClassificationCoverage(BaseModel):
    qualifying_grant_count: int
    classified_grant_count: int
    unclassified_grant_count: int
    classified_percentage: float
    source_classified_grant_count: int
    inferred_classified_grant_count: int
    source_percentage: float
    inferred_percentage: float
    multiple_programme_area_grant_count: int
    invalid_source_label_count: int
    low_confidence_inference_count: int


class GrantThemesResponse(BaseModel):
    status: str
    currency: Optional[str] = None
    available_currencies: List[str] = []
    allocation_method: str = "equal_split_across_available_categories"
    classification_precedence: List[str] = []
    inference_confidence_threshold: float
    items: List[ProgrammeAllocationItem] = []
    classification_coverage: ProgrammeClassificationCoverage
    qualifying_amount: float = 0.0
    allocated_amount: float = 0.0
    excluded: GrantAggregationExclusions
    zero_amount_count: int = 0
    last_refreshed_at: Optional[str] = None
    source: List[str] = []
    data_mode: str
    amount_policy: GrantAmountPolicy
    scope: GrantAggregationScope


class ScoreTargetProfile(BaseModel):
    programme_areas: List[str] = []
    geographies: List[str] = []
    minimum_annual_expenditure: Optional[float] = None
    target_average_grant_amount: Optional[float] = None
    currency: Optional[str] = None
    organization_types: List[str] = []


class ScoreRequest(BaseModel):
    target_profile: Optional[ScoreTargetProfile] = None


class ScoreComponent(BaseModel):
    score: Optional[float] = None
    weight: float
    weighted_score: Optional[float] = None
    confidence: float
    available: bool
    evidence: List[Dict[str, Any]] = []
    missing_reason: Optional[str] = None


class ScoreResponse(BaseModel):
    score: Optional[float] = None
    score_target: str
    score_version: str
    configuration_status: str
    confidence: float
    data_completeness: float
    components: Dict[str, ScoreComponent]
    missing_inputs: List[str] = []
    review_required: bool
    assumptions: List[str] = []
    missing_data_behavior: str
    not_a_prediction: bool = True

class PipelineStatus(BaseModel):
    status: str = Field(..., description="idle, running, success, failed")
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_run_source: Optional[str] = None
    error: Optional[str] = None

class PipelineTrigger(BaseModel):
    source: str = Field(..., description="quick_consolidate, refresh_charities, refresh_grants, full_run")
    limit: Optional[int] = None
    fresh: Optional[bool] = False
    search_term: Optional[str] = None
    reg_numbers: Optional[List[int]] = None
    skip_contact_crawler: Optional[bool] = False


class SourceFunderEnrichmentRequest(BaseModel):
    """A deliberately bounded request to enrich observed funders.

    This is separate from the broader administrative pipeline trigger: the
    donor-directory UI can only submit confirmed Charity Commission numbers
    and may never start an unbounded scrape.
    """

    reg_numbers: List[int] = Field(..., min_length=1, max_length=5)
    skip_contact_crawler: bool = False


class SankeyNode(BaseModel):
    id: str
    label: str
    role: Optional[str] = None

class SankeyLink(BaseModel):
    source: str
    target: str
    value: float
    currency: str
    grant_count: int

class SankeyMetadata(BaseModel):
    source: List[str] = []
    generated_at: str
    grant_count: int
    included_grant_count: int
    excluded_grant_count: int
    excluded_reasons: Dict[str, int] = {}
    included_value: float
    currencies: List[str] = []
    selected_currency: Optional[str] = None
    conversion_method: str = "none"
    filters_applied: Dict[str, Any] = {}
    truncation_applied: bool = False

class SankeyData(BaseModel):
    status: str
    nodes: List[SankeyNode]
    links: List[SankeyLink]
    metadata: SankeyMetadata

# Foundation News Schemas
class NewsSource(BaseModel):
    title: str
    link: str
    source: str
    published: str
    note: str = ""  # e.g. "page content too short or blocked, falling back to RSS title only"

class NewsSummary(BaseModel):
    foundation: str
    summary: str
    sources: List[NewsSource] = []
    searched_weeks: int = 4
    generated_at: str
