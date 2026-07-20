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

class CharityDetail(BaseModel):
    registered_charity_number: int
    suffix: int
    link: Optional[str] = None
    all_details: CharityAllDetails
    assets_liabilities: List[CharityAssetsLiabilitiesItem] = []
    primary_grants: Optional[List[Any]] = None
    who_what_how: Optional[List[Any]] = []
    financial_history: List[CharityFinancialHistoryItem] = []

class CharityStats(BaseModel):
    total_charities: int
    active_charities: int
    removed_charities: int
    average_income: float
    average_expenditure: float

class GrantMapItem(BaseModel):
    region: str
    total_amount_eur: float
    grants_count: int

class GrantDetail(BaseModel):
    grant_id: str
    funding_charity_id: Optional[int] = None
    recipient_name: str
    recipient_charity_id: Optional[int] = None
    amount_eur: float
    currency: str
    description: str
    date: str
    recipient_region: str
    tags: List[str] = []

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

class SankeyNode(BaseModel):
    id: str
    label: str

class SankeyLink(BaseModel):
    source: str
    target: str
    value: float

class SankeyData(BaseModel):
    nodes: List[SankeyNode]
    links: List[SankeyLink]

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
