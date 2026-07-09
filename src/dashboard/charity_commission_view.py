from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard.charity_commission import (
    DEFAULT_CHARITY_COMMISSION_PATH,
    DEFAULT_CHARITY_COMMISSION_DATABASE,
    build_charity_commission_index,
    charity_commission_bulk_geographies,
    charity_commission_bulk_summary,
    charity_commission_database_available,
    filter_charity_commission_records,
    find_charity_commission_bulk_match,
    find_charity_commission_match,
    get_charity_commission_bulk_record,
    load_charity_commission_cache,
    normalize_charity_commission_records,
    query_charity_commission_bulk,
    summarize_charity_commission_records,
)
from src.dashboard.dashboard_utils import format_money


BOOLEAN_FILTER_OPTIONS = ["All", "Yes", "No"]


@st.cache_data(show_spinner=False)
def _load_register_view_data(
    data_mtime_ns: int,
    database_mtime_ns: int,
) -> dict[str, Any]:
    if charity_commission_database_available(DEFAULT_CHARITY_COMMISSION_DATABASE):
        try:
            return {
                "state": "bulk",
                "message": "",
                "normalized_records": [],
                "summary": charity_commission_bulk_summary(DEFAULT_CHARITY_COMMISSION_DATABASE),
                "geography_options": charity_commission_bulk_geographies(
                    DEFAULT_CHARITY_COMMISSION_DATABASE
                ),
            }
        except (OSError, sqlite3.DatabaseError) as exc:
            bulk_warning = f"Full-register index is unavailable; using the local sample cache instead: {exc}"
    else:
        bulk_warning = ""
    result = load_charity_commission_cache(DEFAULT_CHARITY_COMMISSION_PATH)
    normalized = []
    if result["state"] == "ok":
        normalized = normalize_charity_commission_records(
            result["records"],
            source_file=DEFAULT_CHARITY_COMMISSION_PATH.name,
        )
    return {**result, "normalized_records": normalized, "bulk_warning": bulk_warning}


def _register_cache_version() -> int:
    try:
        return DEFAULT_CHARITY_COMMISSION_PATH.stat().st_mtime_ns
    except OSError:
        return -1


def _register_database_version() -> int:
    try:
        return DEFAULT_CHARITY_COMMISSION_DATABASE.stat().st_mtime_ns
    except OSError:
        return -1


def get_register_view_data() -> dict[str, Any]:
    return _load_register_view_data(
        _register_cache_version(),
        _register_database_version(),
    )


def _display(value: Any, missing: str = "Not available") -> str:
    if value is None or value == "" or value == [] or value == {}:
        return missing
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple, set)):
        text = ", ".join(str(item) for item in value if str(item).strip())
        return text or missing
    return str(value)


def _money(value: Any) -> str:
    return format_money(value, "GBP")


def _status_panel(full_extract: bool = False) -> None:
    st.subheader("Integration status")
    top = st.columns(4)
    top[0].metric("Source", "Charity Commission Register")
    top[1].metric("Access type", "Official daily extract" if full_extract else "Official API")
    top[2].metric("Import status", "Complete" if full_extract else "Implemented")
    top[3].metric("Live API calls", "Disabled")
    bottom = st.columns(3)
    bottom[0].metric(
        "UI mode",
        "Full daily extract" if full_extract else "Local sample/cache display",
    )
    bottom[1].metric("Intended integration", "Charity-number enrichment")
    bottom[2].metric("Production status", "Not in consolidation")
    st.caption(
        "Production consolidation is intentionally unchanged. This view reads only a local cache artifact and "
        "does not require or access an API key."
    )
    st.info("Production status: Not yet merged into the consolidation pipeline.")


def _summary_panel(
    records: list[dict[str, Any]] | None = None,
    summary: dict[str, int] | None = None,
    *,
    full_extract: bool = False,
) -> None:
    summary = summary or summarize_charity_commission_records(records or [])
    st.subheader("Official full-register summary" if full_extract else "Local sample summary")
    metrics = [
        ("Records loaded", summary["total"]),
        ("Active", summary["active"]),
        ("Removed", summary["removed"]),
        ("With website", summary["with_website"]),
        ("With email", summary["with_email"]),
        ("With phone", summary["with_phone"]),
        ("With address", summary["with_address"]),
        ("With latest income", summary["with_latest_income"]),
        ("With latest expenditure", summary["with_latest_expenditure"]),
        ("With financial history", summary["with_financial_history"]),
        ("With assets/liabilities", summary["with_assets_liabilities"]),
        ("With grant-maker flag", summary["with_grant_maker_flag"]),
        ("Primary grant makers", summary["primary_grant_makers"]),
        ("With areas of operation", summary["with_geography"]),
        ("With classifications", summary["with_classifications"]),
    ]
    for start in range(0, len(metrics), 5):
        row = st.columns(5)
        for column, (label, value) in zip(row, metrics[start : start + 5]):
            column.metric(label, value)

    incomplete = not full_extract and summary["total"] and (
        summary["removed"] >= summary["total"] / 2
        or summary["with_latest_income"] + summary["with_geography"] + summary["with_classifications"]
        < summary["total"]
    )
    if incomplete:
        st.warning(
            "The local sample demonstrates scraper output structure but should not be treated as production "
            "funder data."
        )
    if full_extract:
        st.info(
            "The full register includes all registered and removed charities. Register inclusion does not by "
            "itself qualify an organization as a funder; use the status and grant-maker indicators as separate "
            "signals."
        )


def _filter_controls(geography_options: list[str]) -> dict[str, str]:
    st.subheader("Explore register records")
    first = st.columns([1, 1, 1, 2])
    status = first[0].selectbox(
        "Status",
        ["All", "Active", "Removed", "Unknown"],
        key="cc_status_filter",
    )
    has_website = first[1].selectbox(
        "Has website", BOOLEAN_FILTER_OPTIONS, key="cc_website_filter"
    )
    has_email = first[2].selectbox("Has email", BOOLEAN_FILTER_OPTIONS, key="cc_email_filter")
    geography = first[3].selectbox(
        "Country / region / area of operation",
        ["All", *geography_options],
        key="cc_geography_filter",
    )

    second = st.columns([1, 1, 1, 1, 2])
    has_latest_income = second[0].selectbox(
        "Has latest income", BOOLEAN_FILTER_OPTIONS, key="cc_income_filter"
    )
    has_financial_history = second[1].selectbox(
        "Has financial history", BOOLEAN_FILTER_OPTIONS, key="cc_history_filter"
    )
    has_grant_maker_flag = second[2].selectbox(
        "Grant-maker flag reported", BOOLEAN_FILTER_OPTIONS, key="cc_grant_filter"
    )
    grant_maker_value = second[3].selectbox(
        "Primary grant maker", BOOLEAN_FILTER_OPTIONS, key="cc_grant_value_filter"
    )
    search = second[4].text_input(
        "Search name, identifier, geography or classification",
        key="cc_search_filter",
    )
    return {
        "status": status,
        "has_website": has_website,
        "has_email": has_email,
        "has_latest_income": has_latest_income,
        "has_financial_history": has_financial_history,
        "has_grant_maker_flag": has_grant_maker_flag,
        "grant_maker_value": grant_maker_value,
        "geography": geography,
        "search": search,
    }


def _render_filters(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    geography_options = sorted(
        {
            geography
            for record in records
            for geography in record.get("areas_of_operation", [])
            if geography
        }
    )
    filters = _filter_controls(geography_options)
    return filter_charity_commission_records(records, **filters)


def _records_table(
    records: list[dict[str, Any]],
    total_count: int | None = None,
) -> None:
    if not records:
        st.info("No local register records match the selected filters.")
        return
    table = pd.DataFrame(
        [
            {
                "Charity name": record["charity_name"],
                "Charity number": record["registered_charity_number"],
                "Organisation number": record["organisation_number"],
                "Registration status": record["registration_status"],
                "Registration date": record["registration_date"],
                "Removal date": record["removal_date"],
                "Charity type": record["charity_type"],
                "Website": record["website"],
                "Email": record["email"],
                "Phone": record["phone"],
                "Address": record["address"],
                "Countries": _display(record["countries"], ""),
                "Regions": _display(record["regions"], ""),
                "Areas of operation": _display(record["areas_of_operation"], ""),
                "Who the charity helps": _display(record["who_classifications"], ""),
                "What the charity does": _display(record["what_classifications"], ""),
                "How the charity operates": _display(record["how_classifications"], ""),
                "Latest income": _money(record["latest_income"]),
                "Latest expenditure": _money(record["latest_expenditure"]),
                "Assets": _money(record["assets"]),
                "Liabilities": _money(record["liabilities"]),
                "Financial history": "Available" if record["has_financial_history"] else "Not available",
                "Primary purpose grant making": _display(record["primary_purpose_grant_making"]),
                "Source": record["source_label"],
                "Source file": record["source_file"],
            }
            for record in records
        ]
    )
    if total_count is not None and total_count > len(table):
        st.caption(
            f"Showing the first {len(table):,} of {total_count:,} matching register records. "
            "Refine the filters or search to narrow the result."
        )
    else:
        st.caption(f"Showing {len(table):,} register record(s).")
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        height=430,
        column_config={
            "Website": st.column_config.LinkColumn("Website", display_text="Open website"),
            "Latest income": st.column_config.TextColumn("Latest income"),
            "Latest expenditure": st.column_config.TextColumn("Latest expenditure"),
        },
    )


def _history_dataframe(history: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(history, columns=["financial_year", "income", "expenditure", "assets", "liabilities"])


def _render_history(history: list[dict[str, Any]], key_prefix: str) -> None:
    if not history:
        st.info("No financial history is available in the local cache for this record.")
        return
    history_df = _history_dataframe(history)
    display_df = history_df.rename(
        columns={
            "financial_year": "Financial year",
            "income": "Income",
            "expenditure": "Expenditure",
            "assets": "Assets",
            "liabilities": "Liabilities",
        }
    )
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column, format="£%.0f")
            for column in ("Income", "Expenditure", "Assets", "Liabilities")
        },
    )
    plot_source = history_df[["financial_year", "income", "expenditure"]].copy()
    plot_source = plot_source.dropna(subset=["income", "expenditure"], how="all")
    if len(plot_source) >= 2:
        long = plot_source.melt(
            id_vars="financial_year",
            value_vars=["income", "expenditure"],
            var_name="Metric",
            value_name="Amount",
        ).dropna(subset=["Amount"])
        chart = px.line(
            long,
            x="financial_year",
            y="Amount",
            color="Metric",
            markers=True,
            labels={"financial_year": "Financial year", "Amount": "Amount (GBP)"},
        )
        chart.update_layout(height=340, margin={"l": 0, "r": 0, "t": 20, "b": 0})
        st.plotly_chart(chart, use_container_width=True, key=f"{key_prefix}_history_chart")


def _record_detail(
    records: list[dict[str, Any]],
    *,
    full_extract: bool = False,
) -> None:
    st.subheader("Register record detail")
    if not records:
        st.info("No record is available for detailed inspection.")
        return
    options = list(range(len(records)))
    selected = st.selectbox(
        "Select local register record",
        options,
        format_func=lambda index: (
            f"{records[index]['charity_name']} "
            f"({records[index]['registered_charity_number'] or 'no charity number'})"
        ),
        key="cc_detail_record",
    )
    record = records[selected]
    if full_extract:
        detailed = get_charity_commission_bulk_record(
            record.get("organisation_number"),
            DEFAULT_CHARITY_COMMISSION_DATABASE,
        )
        if detailed is not None:
            record = detailed

    with st.container(border=True):
        st.markdown(f"### {record['charity_name']}")
        st.caption(
            f"{record['source_label']} · local cache · {record['registration_status']}"
        )

        st.markdown("#### Identity")
        identity = st.columns(3)
        identity[0].metric("Charity number", _display(record["registered_charity_number"]))
        identity[1].metric("Organisation number", _display(record["organisation_number"]))
        identity[2].metric("Registration status", record["registration_status"])
        st.write(
            {
                "Charity type": _display(record["charity_type"]),
                "Registration date": _display(record["registration_date"]),
                "Removal date": _display(record["removal_date"]),
                "Removal reason": _display(record["removal_reason"]),
            }
        )

        left, right = st.columns(2)
        with left:
            st.markdown("#### Contact and profile")
            if record["website"]:
                st.link_button("Open official website", record["website"])
            if record["register_link"]:
                st.link_button("Open register entry", record["register_link"])
            st.write(f"**Email:** {_display(record['email'])}")
            st.write(f"**Phone:** {_display(record['phone'])}")
            st.write(f"**Address:** {_display(record['address'])}")
        with right:
            st.markdown("#### Geography")
            st.write(f"**Countries:** {_display(record['countries'])}")
            st.write(f"**Regions:** {_display(record['regions'])}")
            st.write(f"**Areas of operation:** {_display(record['areas_of_operation'])}")
            st.caption("Area of operation is a geographic profile, not confirmed funding geography.")

        st.markdown("#### Classifications")
        classification_columns = st.columns(3)
        classification_columns[0].write(
            "**Who the charity helps**\n\n" + _display(record["who_classifications"])
        )
        classification_columns[1].write(
            "**What the charity does**\n\n" + _display(record["what_classifications"])
        )
        classification_columns[2].write(
            "**How the charity operates**\n\n" + _display(record["how_classifications"])
        )

        st.markdown("#### Financial profile")
        financial = st.columns(4)
        financial[0].metric("Latest income", _money(record["latest_income"]))
        financial[1].metric("Latest expenditure", _money(record["latest_expenditure"]))
        financial[2].metric("Assets", _money(record["assets"]))
        financial[3].metric("Liabilities", _money(record["liabilities"]))
        st.caption(
            "Latest income and expenditure are official financial-profile fields. They are kept separate from "
            "annual giving and grant-payout estimates."
        )

        st.markdown("#### Financial history")
        _render_history(record["financial_history"], f"cc_{selected}")

        st.markdown("#### Grant making")
        st.metric(
            "Primary purpose grant making",
            _display(record["primary_purpose_grant_making"]),
        )
        st.warning(
            "Grant-maker indicators require validation before being used as final funder qualification logic."
        )

        supplemental_fields = any(
            record.get(field)
            for field in (
                "charity_activities",
                "other_names",
                "other_regulators",
                "policies",
                "published_reports",
                "governing_documents",
                "event_history",
            )
        )
        if supplemental_fields:
            with st.expander("Additional official register data", expanded=False):
                if record.get("charity_activities"):
                    st.markdown("**Activities**")
                    st.write(record["charity_activities"])
                operational = st.columns(4)
                operational[0].metric("Employees", _display(record.get("employee_count")))
                operational[1].metric("Volunteers", _display(record.get("volunteer_count")))
                operational[2].metric("Gift Aid", _display(record.get("gift_aid")))
                operational[3].metric("Owns/uses land", _display(record.get("has_land")))
                if record.get("grant_expenditure") is not None:
                    st.metric(
                        "Institution grant expenditure",
                        _money(record["grant_expenditure"]),
                    )
                    st.caption(
                        "This official Annual Return field is shown separately and is not included in the "
                        "existing funding-potential calculations."
                    )
                if record.get("other_names"):
                    st.markdown("**Other registered names**")
                    st.dataframe(
                        pd.DataFrame(record["other_names"]),
                        use_container_width=True,
                        hide_index=True,
                    )
                if record.get("other_regulators"):
                    st.markdown("**Other regulators**")
                    st.dataframe(
                        pd.DataFrame(record["other_regulators"]),
                        use_container_width=True,
                        hide_index=True,
                    )
                if record.get("policies"):
                    st.markdown("**Reported policies**")
                    st.write(", ".join(record["policies"]))
                if record.get("governing_documents"):
                    st.markdown("**Governing documents and charitable objects**")
                    for document in record["governing_documents"]:
                        st.write(document)
                if record.get("published_reports"):
                    st.markdown("**Published regulatory reports**")
                    st.dataframe(
                        pd.DataFrame(record["published_reports"]),
                        use_container_width=True,
                        hide_index=True,
                    )
                if record.get("event_history"):
                    st.markdown("**Registration event history**")
                    st.dataframe(
                        pd.DataFrame(record["event_history"]),
                        use_container_width=True,
                        hide_index=True,
                    )

        with st.expander("Raw cached record (debug)", expanded=False):
            st.json(record["raw_record"])


def _mapping_explanation() -> None:
    st.subheader("How this enriches existing organizations")
    st.graphviz_chart(
        """
        digraph enrichment {
          rankdir=LR;
          node [shape=box, style="rounded,filled", fillcolor="#F3F6FA", color="#667085"];
          existing [label="Existing UI\norganization"];
          identifier [label="Charity / organisation\nnumber"];
          api [label="Official API /\ndaily extract"];
          cache [label="Local cache"];
          adapter [label="Canonical adapter"];
          detail [label="Enriched\ndetail view"];
          existing -> identifier -> api -> cache -> adapter -> detail;
        }
        """,
        use_container_width=True,
    )
    mapping = pd.DataFrame(
        [
            ["charity_number", "registered_charity_number", "Matching key", "Safe identifier"],
            ["organisation_number", "organisation_number", "Matching key", "Safe identifier"],
            ["charity_name", "official_registered_name", "Identity validation", "May differ from display name"],
            ["reg_status", "registration_status", "Active/removed filter", "Remove inactive records from production import"],
            ["website / email / address / phone", "contact fields", "Detail view", "May be missing"],
            ["area_of_operation", "area_of_operation", "Geographic profile", "Not automatically funding geography"],
            ["latest_income", "latest_income", "Financial profile", "Not annual giving"],
            ["latest_expenditure", "latest_expenditure", "Financial profile", "Not grant payout"],
            ["financial_history", "financial_history", "Historical profile", "Separate from funding estimates"],
            ["primary_purpose_grant_making", "grant_maker_flag", "Potential grant-maker filter", "Validate before production use"],
            ["trustees", "Excluded", "Not displayed", "Avoid unnecessary personal-data exposure"],
        ],
        columns=["API field", "Canonical field", "UI usage", "Caveat"],
    )
    st.dataframe(mapping, use_container_width=True, hide_index=True)


def _readiness_panel() -> None:
    st.subheader("Recommended next integration steps")
    with st.container(border=True):
        st.markdown(
            """
1. Enrich existing organizations with known charity numbers first.
2. Cache API responses locally.
3. Build a canonical adapter for nested Charity Commission fields.
4. Match primarily by registered charity number or organisation number.
5. Filter removed charities before production display.
6. Surface the financial profile and grant-maker flag in organization detail pages.
7. Only later consider discovering new grant makers from the official API.
            """.strip()
        )


def render_charity_commission_page() -> None:
    st.title("Official Register Data")
    st.caption(
        "Presentation of locally cached Charity Commission API enrichment data. "
        "No live request is made from this dashboard."
    )
    st.caption(
        "Source: [Charity Commission daily public register extract]"
        "(https://register-of-charities.charitycommission.gov.uk/en/register/full-register-download) · "
        "Open Government Licence v3.0"
    )
    result = get_register_view_data()
    if result.get("bulk_warning"):
        st.warning(result["bulk_warning"])
    full_extract = result["state"] == "bulk"
    _status_panel(full_extract=full_extract)
    if result["state"] == "missing":
        st.info(result["message"])
        _mapping_explanation()
        _readiness_panel()
        return
    if result["state"] not in {"ok", "bulk"}:
        st.warning(result["message"])
        _mapping_explanation()
        _readiness_panel()
        return

    records = result["normalized_records"]
    if full_extract:
        _summary_panel(summary=result["summary"], full_extract=True)
        filters = _filter_controls(result["geography_options"])
        filtered, total_count = query_charity_commission_bulk(
            **filters,
            limit=500,
            path=DEFAULT_CHARITY_COMMISSION_DATABASE,
        )
        _records_table(filtered, total_count=total_count)
        _record_detail(filtered, full_extract=True)
    else:
        _summary_panel(records)
        filtered = _render_filters(records)
        _records_table(filtered)
        _record_detail(filtered)
    _mapping_explanation()
    _readiness_panel()


def render_official_register_preview(organization: dict[str, Any]) -> None:
    result = get_register_view_data()
    if result["state"] == "bulk":
        match = find_charity_commission_bulk_match(
            organization,
            DEFAULT_CHARITY_COMMISSION_DATABASE,
        )
    elif result["state"] == "ok":
        records = result["normalized_records"]
        match = find_charity_commission_match(
            organization,
            build_charity_commission_index(records),
        )
    else:
        return
    if match is None:
        st.caption("No official register enrichment is available in the local cache for this organization.")
        return

    st.divider()
    st.subheader("Official Register Enrichment")
    st.caption("Matched locally by charity number or organisation number; no live API request was made.")
    identity = st.columns(3)
    identity[0].metric("Registration status", match["registration_status"])
    identity[1].metric("Latest income", _money(match["latest_income"]))
    identity[2].metric("Latest expenditure", _money(match["latest_expenditure"]))
    st.write(f"**Official registered name:** {match['charity_name']}")
    st.write(f"**Website:** {_display(match['website'])}")
    st.write(f"**Email:** {_display(match['email'])}")
    st.write(
        "**Primary purpose grant making:** "
        + _display(match["primary_purpose_grant_making"])
    )
    _render_history(match["financial_history"], "existing_org_register")
    st.caption(
        "Official income and expenditure are shown as a separate financial profile and are not used in "
        "funding-potential calculations."
    )
