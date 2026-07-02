from __future__ import annotations

import math

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard_utils import (
    DEFAULT_DATA_PATH,
    availability_summary,
    build_country_allocation_table,
    build_country_summary,
    build_non_country_summary,
    build_office_to_geography_links,
    build_tag_allocation_table,
    build_term_count,
    country_to_centroid,
    dominant_currency,
    filter_dataframe,
    format_money,
    infer_origin_country_for_selected_org,
    is_informative_value,
    list_to_display,
    load_consolidated_data,
    make_ranking_dataframe,
    make_table_dataframe,
    normalize_records,
)


st.set_page_config(
    page_title="Foundation Intelligence Platform",
    layout="wide",
)


MAP_DISPLAY_MODES = [
    "Funder count",
    "Estimated annual giving, continuous scale",
    "Estimated annual giving, log scale",
    "Estimated annual giving, bucket scale",
]

ARROW_MODES = [
    "Off",
    "Selected organisation only",
    "Aggregated top links",
]


@st.cache_data(show_spinner=False)
def load_data(data_mtime_ns: int) -> pd.DataFrame:
    records = load_consolidated_data(DEFAULT_DATA_PATH)
    return normalize_records(records)


def render_intro() -> None:
    st.title("Foundation Intelligence Platform — Funder Discovery")
    st.caption(f"Source of truth: `{DEFAULT_DATA_PATH}`")
    st.info(
        "This dashboard uses the already consolidated dataset and does not run scrapers. "
        "Geography refers to funding or operating geography where available, not registered office address. "
        "Monetary views are estimated from annual_giving values. Grant range reflects typical individual grant size, "
        "not total annual giving. Optional arrows are visual office-to-stated-geography links only and do not "
        "represent real grant flows."
    )


def render_methodology_notes() -> None:
    with st.expander("Methodology notes", expanded=False):
        st.markdown(
            """
- Funding geography is based on `geographic_focus` / `geo_locations` where available, not registered office address.
- Monetary visualizations are estimated from `annual_giving` values.
- Where a funder targets multiple countries or topics, amounts are fractionally allocated to avoid double-counting.
- Grant range reflects typical individual grant size, not total yearly giving.
- Non-country and regional geographies such as Global, Europe, Wales, or Scotland are shown separately unless country-level boundaries are available locally.
- Optional arrows are illustrative office-to-stated-geography links only. They do not represent transaction-level grant flows or actual money transfers.
            """.strip()
        )


def render_sidebar(df: pd.DataFrame) -> dict[str, object]:
    st.sidebar.header("Filters")

    source_options = sorted(df["source"].dropna().unique().tolist()) if not df.empty else []
    selected_sources = st.sidebar.multiselect("Source", source_options)

    geo_options = sorted({term for values in df.get("geo_terms", []) for term in values})
    selected_geo = st.sidebar.multiselect("Country / region", geo_options)

    tag_options = sorted({tag for values in df.get("thematic_tags", []) for tag in values})
    selected_tags = st.sidebar.multiselect("Thematic tags", tag_options)

    annual_filter = st.sidebar.radio("annual_giving available", ["All", "Yes", "No"], horizontal=True)
    grant_filter = st.sidebar.radio("grant_range available", ["All", "Yes", "No"], horizontal=True)
    link_filter = st.sidebar.radio("Website/application link available", ["All", "Yes", "No"], horizontal=True)

    exclude_uk = st.sidebar.checkbox("Exclude United Kingdom funding geography")
    exclude_non_country = st.sidebar.checkbox("Only country-level funding geographies")

    min_annual_giving = None
    numeric_annual = pd.to_numeric(df.get("annual_giving_mid", pd.Series(dtype=float)), errors="coerce").dropna()
    if len(numeric_annual) >= 25:
        max_value = float(numeric_annual.quantile(0.99))
        if max_value > 0:
            min_annual_giving = st.sidebar.slider(
                "Minimum annual_giving midpoint",
                min_value=0.0,
                max_value=max_value,
                value=0.0,
                step=max(max_value / 100, 1_000.0),
                format="%.0f",
            )

    search = st.sidebar.text_input("Search funder name")

    st.sidebar.header("Map")
    show_bubbles = st.sidebar.checkbox("Show country bubbles", value=True)
    bubble_metric = st.sidebar.radio(
        "Bubble size",
        ["Estimated allocated annual giving", "Number of matching funders"],
        horizontal=False,
    )
    exclude_uk_visuals = st.sidebar.checkbox("Exclude UK from lower country charts", value=True)

    return {
        "sources": selected_sources,
        "geo_terms": selected_geo,
        "tags": selected_tags,
        "annual_filter": annual_filter,
        "grant_filter": grant_filter,
        "link_filter": link_filter,
        "search": search,
        "exclude_uk": exclude_uk,
        "exclude_non_country": exclude_non_country,
        "min_annual_giving": min_annual_giving,
        "show_bubbles": show_bubbles,
        "bubble_metric": bubble_metric,
        "exclude_uk_visuals": exclude_uk_visuals,
    }


def render_kpis(df: pd.DataFrame) -> None:
    currency = dominant_currency(df)
    annual_values = pd.to_numeric(df.get("annual_giving_mid", pd.Series(dtype=float)), errors="coerce").dropna()
    annual_total = annual_values.sum() if not annual_values.empty else None
    annual_median = annual_values.median() if not annual_values.empty else None

    top = st.columns(4)
    bottom = st.columns(4)
    with top[0]:
        st.metric("Total funders", len(df))
    with top[1]:
        st.metric("With geography", int(df["has_geography"].sum()) if not df.empty else 0)
    with top[2]:
        st.metric("With tags", int(df["has_tags"].sum()) if not df.empty else 0)
    with top[3]:
        st.metric("Website/app link", int(df["has_link"].sum()) if not df.empty else 0)
    with bottom[0]:
        st.metric("Annual giving", int(df["annual_giving_available"].sum()) if not df.empty else 0)
    with bottom[1]:
        st.metric("Grant range", int(df["grant_range_available"].sum()) if not df.empty else 0)
    with bottom[2]:
        st.metric("Estimated total annual giving", format_money(annual_total, currency) if annual_total else "n/a")
    with bottom[3]:
        st.metric("Median annual giving", format_money(annual_median, currency) if annual_median else "n/a")


def render_map_controls(df: pd.DataFrame) -> dict[str, object]:
    st.subheader("Funding Geography Map")
    left, right = st.columns([1, 1])
    with left:
        display_mode = st.selectbox(
            "Map display",
            MAP_DISPLAY_MODES,
            index=MAP_DISPLAY_MODES.index("Estimated annual giving, continuous scale"),
        )
    with right:
        arrow_mode = st.selectbox("Arrows", ARROW_MODES, index=0)

    st.caption(
        "Map colors are display transformations based on the current filtered data. The underlying estimated "
        "annual_giving values are not modified. Monetary values are estimates from funder-level annual_giving "
        "fields, not transaction-level grant flows."
    )

    selected_record_id = None
    aggregate_top_n = 20
    aggregate_weight = "Number of funders"
    if arrow_mode != "Off":
        st.info(
            "Arrows are illustrative office-to-stated-geography links only. They do not represent actual money "
            "transfers or transaction-level grant flows."
        )

    if arrow_mode == "Selected organisation only":
        eligible = df[df["has_country_geography"]].sort_values(["name", "source"]).copy()
        options = [""] + eligible["record_id"].tolist()
        labels = {
            "": "Select organisation",
            **{
                row["record_id"]: f"{row['name']} ({row['source']})"
                for _, row in eligible.iterrows()
            },
        }
        selected = st.selectbox(
            "Organisation",
            options,
            format_func=lambda option: labels.get(option, str(option)),
        )
        selected_record_id = int(selected) if selected != "" else None
    elif arrow_mode == "Aggregated top links":
        col_a, col_b = st.columns([1, 1])
        with col_a:
            aggregate_weight = st.radio(
                "Aggregated link weight",
                ["Number of funders", "Estimated annual giving potential"],
                horizontal=True,
            )
        with col_b:
            aggregate_top_n = st.slider("Maximum aggregated links", min_value=5, max_value=50, value=20, step=5)

    return {
        "display_mode": display_mode,
        "arrow_mode": arrow_mode,
        "selected_record_id": selected_record_id,
        "aggregate_top_n": aggregate_top_n,
        "aggregate_weight": aggregate_weight,
    }


def _scaled_marker_sizes(
    values: pd.Series,
    scale_values: pd.Series | None = None,
    min_size: float = 8.0,
    max_size: float = 44.0,
) -> list[float]:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0)
    if numeric.empty:
        return []
    scale_numeric = pd.to_numeric(scale_values if scale_values is not None else values, errors="coerce").dropna()
    scale_numeric = scale_numeric[scale_numeric > 0]
    if scale_numeric.empty:
        return [18.0 for _ in numeric]
    max_value = float(scale_numeric.max())
    min_value = float(scale_numeric.min())
    if max_value <= 0 or max_value == min_value:
        return [18.0 if value <= max_value else max_size for value in numeric]
    sizes = []
    for value in numeric:
        bounded = min(max(float(value), min_value), max_value)
        sizes.append(min_size + ((bounded - min_value) / (max_value - min_value)) ** 0.5 * (max_size - min_size))
    return sizes


def _selected_country_from_plotly_event(event: object, iso_to_country: dict[str, str]) -> str | None:
    try:
        selection = event.selection if hasattr(event, "selection") else event.get("selection", {})
        points = selection.get("points", []) if isinstance(selection, dict) else getattr(selection, "points", [])
    except Exception:
        return None
    if not points:
        return None
    point = points[0]
    customdata = point.get("customdata") if isinstance(point, dict) else None
    if isinstance(customdata, (list, tuple)) and customdata:
        return str(customdata[0])
    if isinstance(customdata, str) and customdata:
        return customdata
    location = point.get("location") if isinstance(point, dict) else None
    if location:
        return iso_to_country.get(str(location))
    return None


def _format_map_number(value: object, monetary: bool, currency: str | None) -> str:
    if pd.isna(value):
        return "n/a"
    if monetary:
        return format_money(value, currency)
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def _compact_number_label(value: float, monetary: bool = False) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    amount = float(value)
    abs_amount = abs(amount)
    if abs_amount >= 1_000_000_000_000:
        label = f"{amount / 1_000_000_000_000:g}T"
    elif abs_amount >= 1_000_000_000:
        label = f"{amount / 1_000_000_000:g}B"
    elif abs_amount >= 1_000_000:
        label = f"{amount / 1_000_000:g}M"
    elif abs_amount >= 1_000:
        label = f"{amount / 1_000:g}K"
    else:
        label = f"{amount:g}"
    return label if monetary else label


def _nice_log_ticks(values: pd.Series) -> tuple[list[float], list[str]]:
    positive = pd.to_numeric(values, errors="coerce").dropna()
    positive = positive[positive > 0]
    if positive.empty:
        return [], []

    low_power = math.floor(math.log10(float(positive.min())))
    high_power = math.ceil(math.log10(float(positive.max())))
    tick_amounts = [10**power for power in range(low_power, high_power + 1)]
    tick_amounts = [amount for amount in tick_amounts if positive.min() <= amount <= positive.max()]
    if not tick_amounts:
        tick_amounts = [float(positive.min()), float(positive.max())]
    return [math.log10(amount + 1) for amount in tick_amounts], [_compact_number_label(amount, monetary=True) for amount in tick_amounts]


def _money_bucket(value: object) -> tuple[int, str]:
    if pd.isna(value):
        return 0, "No data"
    amount = float(value)
    if amount < 1_000_000:
        return 1, "<1M"
    if amount < 10_000_000:
        return 2, "1M-10M"
    if amount < 100_000_000:
        return 3, "10M-100M"
    if amount < 1_000_000_000:
        return 4, "100M-1B"
    if amount < 10_000_000_000:
        return 5, "1B-10B"
    if amount < 1_000_000_000_000:
        return 6, "10B+"
    return 7, "Extreme value"


def _discrete_colorscale(colors: list[str]) -> list[list[object]]:
    if len(colors) == 1:
        return [[0, colors[0]], [1, colors[0]]]
    scale = []
    denominator = len(colors)
    for index, color in enumerate(colors):
        scale.append([index / denominator, color])
        scale.append([(index + 1) / denominator, color])
    return scale


def _prepare_map_color_values(plot_df: pd.DataFrame, display_mode: str) -> dict[str, object]:
    if display_mode == "Funder count":
        values = pd.to_numeric(plot_df["funder_count"], errors="coerce")
        min_value = float(values.min()) if values.notna().any() else 0.0
        max_value = float(values.max()) if values.notna().any() else 1.0
        if min_value == max_value:
            max_value = min_value + 1
        return {
            "metric_column": "funder_count",
            "metric_label": "Funder count",
            "monetary": False,
            "z": values,
            "colorscale": [[0.0, "#d9d9d9"], [1.0, "#006400"]],
            "colorbar": {"title": "Funder count", "tickformat": ",.0f"},
            "zmin": min_value,
            "zmax": max_value,
            "display_label": display_mode,
        }

    values = pd.to_numeric(plot_df["estimated_annual_giving"], errors="coerce")
    positive = values.dropna()
    if display_mode == "Estimated annual giving, log scale":
        color_values = values.apply(lambda value: math.log10(float(value) + 1) if pd.notna(value) and value >= 0 else None)
        tick_values, tick_text = _nice_log_ticks(positive[positive > 0])
        return {
            "metric_column": "estimated_annual_giving",
            "metric_label": "Estimated annual giving",
            "monetary": True,
            "z": color_values,
            "colorscale": [[0.0, "#d9d9d9"], [1.0, "#006400"]],
            "colorbar": {
                "title": "Estimated annual giving<br>log scale",
                "tickvals": tick_values,
                "ticktext": tick_text,
            },
            "zmin": float(color_values.dropna().min()) if color_values.notna().any() else 0.0,
            "zmax": float(color_values.dropna().max()) if color_values.notna().any() else 1.0,
            "display_label": display_mode,
        }

    if display_mode == "Estimated annual giving, bucket scale":
        buckets = values.apply(lambda value: _money_bucket(value)[0])
        return {
            "metric_column": "estimated_annual_giving",
            "metric_label": "Estimated annual giving",
            "monetary": True,
            "z": buckets,
            "colorscale": _discrete_colorscale(
                ["#f2f2f2", "#d9d9d9", "#b7d7b7", "#8fc28f", "#5ea65e", "#2f8732", "#006400", "#3b1f00"]
            ),
            "colorbar": {
                "title": "Estimated annual giving<br>bucket scale",
                "tickvals": list(range(8)),
                "ticktext": ["No data", "<1M", "1M-10M", "10M-100M", "100M-1B", "1B-10B", "10B+", "Extreme"],
            },
            "zmin": 0,
            "zmax": 7,
            "display_label": display_mode,
        }

    min_value = float(positive.min()) if not positive.empty else 0.0
    max_value = float(positive.max()) if not positive.empty else 1.0
    if min_value == max_value:
        max_value = min_value + 1
    return {
        "metric_column": "estimated_annual_giving",
        "metric_label": "Estimated annual giving",
        "monetary": True,
        "z": values,
        "colorscale": [[0.0, "#d9d9d9"], [1.0, "#006400"]],
        "colorbar": {"title": "Estimated annual giving"},
        "zmin": min_value,
        "zmax": max_value,
        "display_label": display_mode,
    }


def _add_illustrative_links(
    fig: go.Figure,
    df: pd.DataFrame,
    currency: str | None,
    metric: str,
    top_n: int,
) -> int:
    links = build_office_to_geography_links(df)
    if links.empty:
        return 0

    metric_column = "estimated_annual_giving" if metric == "Estimated annual giving potential" else "funder_count"
    links = links.copy()
    links["_sort_metric"] = pd.to_numeric(links[metric_column], errors="coerce").fillna(0)
    links = links.sort_values(["_sort_metric", "funder_count"], ascending=False).head(top_n)
    if links.empty:
        return 0

    max_weight = float(links["_sort_metric"].max()) if links["_sort_metric"].max() else 1.0
    added = 0
    for _, row in links.iterrows():
        weight = float(row["_sort_metric"]) if pd.notna(row["_sort_metric"]) else 0.0
        width = 0.8 + (weight / max_weight) * 3.2 if max_weight else 1.0
        amount = (
            format_money(row["estimated_annual_giving"], currency)
            if pd.notna(row.get("estimated_annual_giving"))
            else "n/a"
        )
        hover_text = (
            f"{row['origin_country']} -> {row['destination_country']}<br>"
            f"Funders: {int(row['funder_count'])}<br>"
            f"Estimated annual giving potential: {amount}<br>"
            f"Origin confidence: {row.get('origin_confidence', 'n/a')}<br>"
            f"Origin reason: {row.get('origin_reason', 'n/a')}<br>"
            f"Sample funders: {row['sample_funders']}<br>"
            "Illustrative office-to-stated-geography link, not a real funding flow."
        )
        fig.add_trace(
            go.Scattergeo(
                lon=[row["origin_longitude"], row["destination_longitude"]],
                lat=[row["origin_latitude"], row["destination_latitude"]],
                mode="lines",
                line={"width": width, "color": "rgba(220, 95, 25, 0.38)"},
                hoverinfo="text",
                text=[hover_text, hover_text],
                showlegend=False,
                name="Illustrative office-to-stated-geography link",
            )
        )
        added += 1
    return added


def _organisation_country_links(df: pd.DataFrame, record_id: int | None) -> tuple[pd.DataFrame, dict[str, object]]:
    columns = [
        "origin_country",
        "origin_iso3",
        "origin_latitude",
        "origin_longitude",
        "origin_confidence",
        "origin_reason",
        "destination_country",
        "destination_latitude",
        "destination_longitude",
        "allocated_annual_giving",
    ]
    empty_details = {
        "organisation_name": "",
        "origin_country": None,
        "origin_confidence": "missing",
        "origin_reason": "no selected organisation",
        "country_destination_count": 0,
        "drawn_destination_count": 0,
        "domestic_destinations": [],
        "skipped_destinations": [],
        "non_country_destinations": [],
        "message": "",
    }
    if record_id is None or df.empty:
        return pd.DataFrame(columns=columns), empty_details

    selected = df[df["record_id"] == record_id]
    if selected.empty:
        details = {**empty_details, "message": "Selected organisation is not available in the current filters."}
        return pd.DataFrame(columns=columns), details
    row = selected.iloc[0]
    organisation_name = str(row.get("name") or "")
    origin = infer_origin_country_for_selected_org(row)
    origin_iso3 = str(origin.get("origin_country_iso3") or "")
    origin_country = str(origin.get("origin_country_name") or "")
    origin_confidence = str(origin.get("origin_confidence") or "missing")
    origin_reason = str(origin.get("origin_reason") or "")
    details = {
        **empty_details,
        "organisation_name": organisation_name,
        "origin_country": origin_country or None,
        "origin_confidence": origin_confidence,
        "origin_reason": origin_reason,
        "non_country_destinations": list(row.get("regions", []) or []) + list(row.get("non_country_geographies", []) or []),
    }
    origin_centroid = country_to_centroid(origin_iso3)
    if not origin_iso3 or not origin_country or not origin_centroid:
        details["message"] = "No illustrative arrows can be shown for this organisation because no reliable origin country could be determined."
        return pd.DataFrame(columns=columns), details

    country_names = row.get("countries", []) or []
    country_codes = row.get("country_codes", []) or []
    count = min(len(country_names), len(country_codes))
    details["country_destination_count"] = count
    if count == 0:
        details["message"] = "No illustrative arrows can be shown because no country-level destinations are available."
        return pd.DataFrame(columns=columns), details

    annual_mid = row.get("annual_giving_mid")
    allocated = annual_mid / count if pd.notna(annual_mid) and count else None
    rows = []
    domestic = []
    skipped = []
    for destination_country, destination_iso3 in zip(country_names, country_codes):
        destination_centroid = country_to_centroid(destination_iso3)
        if not destination_centroid:
            skipped.append(destination_country)
            continue
        if destination_iso3 == origin_iso3:
            domestic.append(destination_country)
            continue
        rows.append(
            {
                "origin_country": origin_country,
                "origin_iso3": origin_iso3,
                "origin_latitude": origin_centroid[0],
                "origin_longitude": origin_centroid[1],
                "origin_confidence": origin_confidence,
                "origin_reason": origin_reason,
                "destination_country": destination_country,
                "destination_latitude": destination_centroid[0],
                "destination_longitude": destination_centroid[1],
                "allocated_annual_giving": allocated,
            }
        )

    details["drawn_destination_count"] = len(rows)
    details["domestic_destinations"] = domestic
    details["skipped_destinations"] = skipped
    if not rows:
        details["message"] = "No illustrative arrows can be shown because no reliable origin and country-level destinations are available."
    return pd.DataFrame(rows, columns=columns), details


def _add_organisation_links(
    fig: go.Figure,
    links: pd.DataFrame,
    organisation_name: str,
    currency: str | None,
) -> None:
    if links.empty:
        return

    for _, row in links.iterrows():
        amount = (
            format_money(row["allocated_annual_giving"], currency)
            if pd.notna(row.get("allocated_annual_giving"))
            else "n/a"
        )
        hover_text = (
            f"{organisation_name}<br>"
            f"{row['origin_country']} -> {row['destination_country']}<br>"
            f"Display allocation: {amount}<br>"
            f"Origin confidence: {row.get('origin_confidence', 'n/a')}<br>"
            f"Origin reason: {row.get('origin_reason', 'n/a')}<br>"
            "Illustrative office-to-stated-geography link, not a real funding flow."
        )
        fig.add_trace(
            go.Scattergeo(
                lon=[row["origin_longitude"], row["destination_longitude"]],
                lat=[row["origin_latitude"], row["destination_latitude"]],
                mode="lines",
                line={"width": 4, "color": "rgba(34, 139, 34, 0.78)"},
                hoverinfo="text",
                text=[hover_text, hover_text],
                showlegend=False,
                name="Selected organisation stated geography link",
            )
        )
    endpoint_rows = []
    first = links.iloc[0]
    endpoint_rows.append(
        {
            "country": first["origin_country"],
            "latitude": first["origin_latitude"],
            "longitude": first["origin_longitude"],
            "role": "Registered office country",
        }
    )
    endpoint_rows.extend(
        {
            "country": row["destination_country"],
            "latitude": row["destination_latitude"],
            "longitude": row["destination_longitude"],
            "role": "Stated funding geography country",
        }
        for _, row in links.iterrows()
    )
    endpoints = pd.DataFrame(endpoint_rows).drop_duplicates()
    fig.add_trace(
        go.Scattergeo(
            lon=endpoints["longitude"],
            lat=endpoints["latitude"],
            mode="markers",
            customdata=endpoints[["country", "role"]].values,
            marker={
                "size": [15 if role == "Registered office country" else 11 for role in endpoints["role"]],
                "color": [
                    "rgba(20, 20, 20, 0.95)"
                    if role == "Registered office country"
                    else "rgba(34, 139, 34, 0.9)"
                    for role in endpoints["role"]
                ],
                "line": {"color": "white", "width": 1},
            },
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<extra></extra>",
            showlegend=False,
            name="Selected organisation link endpoints",
        )
    )


def render_map(
    df: pd.DataFrame,
    display_mode: str,
    show_bubbles: bool,
    bubble_metric: str,
    arrow_mode: str,
    selected_organisation_id: int | None,
    aggregate_weight: str,
    aggregate_top_n: int,
) -> str | None:
    country_summary = build_country_summary(df)

    if country_summary.empty:
        st.info("No country-level funding geography is available for the current filter selection.")
        return None

    plot_df = country_summary.copy()
    color_config = _prepare_map_color_values(plot_df, display_mode)
    metric_column = str(color_config["metric_column"])
    metric_label = str(color_config["metric_label"])
    monetary = bool(color_config["monetary"])
    plot_df["metric_value"] = pd.to_numeric(plot_df[metric_column], errors="coerce")

    if plot_df["metric_value"].dropna().empty and display_mode != "Estimated annual giving, bucket scale":
        st.info(f"No values are available for map display: {display_mode}.")
        return None

    currency = dominant_currency(df)
    plot_df["estimated_annual_giving_display"] = plot_df["estimated_annual_giving"].apply(
        lambda value: format_money(value, currency) if pd.notna(value) else "n/a"
    )
    plot_df["median_annual_giving_display"] = plot_df["median_annual_giving"].apply(
        lambda value: format_money(value, currency) if pd.notna(value) else "n/a"
    )
    plot_df["metric_display"] = plot_df["metric_value"].apply(
        lambda value: format_money(value, currency) if monetary and pd.notna(value) else f"{value:,.0f}"
    )
    plot_df["display_mode"] = display_mode

    fig = go.Figure()
    fig.add_trace(
        go.Choropleth(
            locations=plot_df["iso3"],
            z=color_config["z"],
            zmin=color_config["zmin"],
            zmax=color_config["zmax"],
            text=plot_df["country"],
            customdata=plot_df[
                [
                    "country",
                    "metric_display",
                    "funder_count",
                    "estimated_annual_giving_display",
                    "median_annual_giving_display",
                    "display_mode",
                    "top_tags",
                    "sample_funders",
                ]
            ].values,
            colorscale=color_config["colorscale"],
            marker_line_color="white",
            marker_line_width=0.4,
            colorbar=color_config["colorbar"],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                f"{metric_label}: %{{customdata[1]}}<br>"
                "Matching funders: %{customdata[2]}<br>"
                "Estimated total annual giving: %{customdata[3]}<br>"
                "Median annual giving: %{customdata[4]}<br>"
                "Display mode: %{customdata[5]}<br>"
                "Top tags: %{customdata[6]}<br>"
                "Sample funders: %{customdata[7]}<extra></extra>"
            ),
            name="Country-level funding geography",
        )
    )

    bubble_df = plot_df.dropna(subset=["latitude", "longitude"]).copy()
    if show_bubbles and not bubble_df.empty:
        if bubble_metric == "Estimated allocated annual giving" and bubble_df["estimated_annual_giving"].notna().any():
            bubble_values = bubble_df["estimated_annual_giving"]
            bubble_label = "Estimated annual giving potential"
        else:
            bubble_values = bubble_df["funder_count"]
            bubble_label = "Matching funders"
        bubble_df["bubble_display"] = bubble_values.apply(
            lambda value: _format_map_number(value, bubble_label.startswith("Estimated"), currency)
        )
        scale_values = bubble_values[bubble_df["iso3"] != "GBR"]
        if scale_values.dropna().empty:
            scale_values = bubble_values
        bubble_colors = [
            "rgba(214, 39, 40, 0.68)" if iso3 == "GBR" else "rgba(18, 101, 176, 0.42)"
            for iso3 in bubble_df["iso3"]
        ]
        fig.add_trace(
            go.Scattergeo(
                lon=bubble_df["longitude"],
                lat=bubble_df["latitude"],
                mode="markers",
                customdata=bubble_df[
                    [
                        "country",
                        "funder_count",
                        "bubble_display",
                        "estimated_annual_giving_display",
                        "median_annual_giving_display",
                        "top_tags",
                        "sample_funders",
                    ]
                ].values,
                marker={
                    "size": _scaled_marker_sizes(bubble_values, scale_values=scale_values),
                    "color": bubble_colors,
                    "line": {"color": "rgba(8, 45, 85, 0.8)", "width": 0.8},
                },
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Matching funders: %{customdata[1]}<br>"
                    f"{bubble_label}: %{{customdata[2]}}<br>"
                    "Estimated total annual giving: %{customdata[3]}<br>"
                    "Median annual giving: %{customdata[4]}<br>"
                    "Top tags: %{customdata[5]}<br>"
                    "Sample funders: %{customdata[6]}<extra></extra>"
                ),
                name="Country bubbles",
            )
        )

    if arrow_mode == "Aggregated top links":
        added_links = _add_illustrative_links(fig, df, currency, aggregate_weight, aggregate_top_n)
        if added_links:
            st.caption(
                f"Showing top {added_links} aggregated illustrative office-to-stated-geography links. "
                "These are not real funding flows."
            )
        else:
            st.info("No illustrative arrows can be shown because no reliable origin and country-level destinations are available.")
    elif arrow_mode == "Selected organisation only":
        if selected_organisation_id is None:
            st.info("Select an organisation to draw illustrative country-level arrows.")
        else:
            organisation_name = ""
            details: dict[str, object] = {}
            links = pd.DataFrame()
            selected_rows = df[df["record_id"] == selected_organisation_id]
            if not selected_rows.empty:
                organisation_name = str(selected_rows.iloc[0]["name"])
                links, details = _organisation_country_links(df, selected_organisation_id)
            if organisation_name:
                st.markdown(f"**Selected organisation:** {organisation_name}")
            if details:
                cols = st.columns(4)
                with cols[0]:
                    st.metric("Origin country", details.get("origin_country") or "Missing")
                with cols[1]:
                    st.metric("Origin confidence", str(details.get("origin_confidence") or "missing"))
                with cols[2]:
                    st.metric("Country destinations", int(details.get("country_destination_count") or 0))
                with cols[3]:
                    st.metric("Drawn arrows", int(details.get("drawn_destination_count") or 0))
                st.caption(f"Origin reason: {details.get('origin_reason') or 'n/a'}")
                if details.get("origin_confidence") not in {"direct", "missing"}:
                    st.info("Origin inferred from name/domain/source metadata. Illustrative only.")
                non_country = list(details.get("non_country_destinations") or [])
                domestic = list(details.get("domestic_destinations") or [])
                skipped = list(details.get("skipped_destinations") or [])
                if non_country:
                    st.caption("Skipped non-country/regional destinations: " + ", ".join(non_country[:10]))
                if domestic:
                    st.caption("Domestic/same-country focus not drawn as arrows: " + ", ".join(domestic[:10]))
                if skipped:
                    st.caption("Skipped countries without local centroid support: " + ", ".join(skipped[:10]))
                if details.get("message"):
                    st.warning(str(details["message"]))
            if not links.empty:
                _add_organisation_links(fig, links, organisation_name, currency)
                st.success(
                    "Selected-organisation arrows use concrete country-level funding geographies only. "
                    "They are illustrative office-to-stated-geography links, not real funding flows."
                )

    fig.update_layout(
        height=520,
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        geo={
            "projection_type": "natural earth",
            "showframe": False,
            "showcoastlines": True,
            "coastlinecolor": "rgba(90, 90, 90, 0.35)",
            "landcolor": "rgb(242, 244, 247)",
        },
        legend={"orientation": "h", "y": 0, "x": 0},
    )
    event = st.plotly_chart(
        fig,
        use_container_width=True,
        key="funding_geography_map",
        on_select="rerun",
        selection_mode="points",
    )
    selected_country = _selected_country_from_plotly_event(
        event,
        dict(zip(plot_df["iso3"], plot_df["country"])),
    )
    if selected_country:
        st.caption(f"Selected from map: {selected_country}")
    elif show_bubbles:
        st.caption("Tip: select a bubble to use it in the country explorer, or use the dropdown below the map.")
    return selected_country


def render_non_country_geographies(df: pd.DataFrame) -> None:
    st.subheader("Non-country and regional geographies")
    summary = build_non_country_summary(df).head(30)
    if summary.empty:
        st.caption("No non-country or regional geographies for the current filters.")
        return
    st.dataframe(summary, use_container_width=True, hide_index=True, height=260)


def filter_by_geography_label(df: pd.DataFrame, selected_label: str | None) -> pd.DataFrame:
    if df.empty or not selected_label:
        return df
    return df[df["geo_terms"].apply(lambda values: selected_label in set(values or []))]


def _truncate_text(value: object, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _drilldown_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    table = df.copy()
    table["_sort_annual"] = pd.to_numeric(table["annual_giving_mid"], errors="coerce").fillna(-1)
    table = table.sort_values("_sort_annual", ascending=False)
    return pd.DataFrame(
        {
            "funder name": table["name"],
            "source": table["source"],
            "annual_giving": table["annual_giving"],
            "annual_giving_mid": table["annual_giving_mid"],
            "grant_range": table["grant_range"],
            "grant_range_mid": table["grant_range_mid"],
            "tags": table["thematic_tags"].apply(lambda values: ", ".join(values)),
            "website/application link": table["best_link"],
            "email": table["email"],
            "description/about": table["description"].apply(_truncate_text),
            "data quality": table["data_quality"],
        }
    )


def render_country_explorer_selector(df: pd.DataFrame, clicked_country: str | None = None) -> str | None:
    st.subheader("Country Explorer")
    country_summary = build_country_summary(df)
    options = ["All countries"] + country_summary.sort_values("funder_count", ascending=False)["country"].tolist()
    if clicked_country and clicked_country in options:
        st.session_state["country_explorer_selection"] = clicked_country
    if st.session_state.get("country_explorer_selection", "All countries") not in options:
        st.session_state["country_explorer_selection"] = "All countries"

    selected = st.selectbox(
        "Country Explorer",
        options,
        key="country_explorer_selection",
    )
    return None if selected == "All countries" else selected


def render_country_explorer(df: pd.DataFrame, selected_label: str | None) -> pd.DataFrame:
    if not selected_label:
        st.markdown("### Country Explorer: All countries")
        currency = dominant_currency(df)
        allocation = build_country_allocation_table(df)
        amount_values = pd.to_numeric(allocation.get("allocated_annual_giving", pd.Series(dtype=float)), errors="coerce").dropna()
        annual_values = pd.to_numeric(df.get("annual_giving_mid", pd.Series(dtype=float)), errors="coerce").dropna()
        metrics = st.columns(4)
        with metrics[0]:
            st.metric("Funders", int(df["record_id"].nunique()) if not df.empty else 0)
        with metrics[1]:
            st.metric("Country geographies", int(allocation["country_iso3"].nunique()) if not allocation.empty else 0)
        with metrics[2]:
            st.metric("Estimated annual giving", format_money(float(amount_values.sum()), currency) if not amount_values.empty else "n/a")
        with metrics[3]:
            st.metric("Median annual giving", format_money(float(annual_values.median()), currency) if not annual_values.empty else "n/a")

        left, right = st.columns(2)
        with left:
            st.markdown("**Top countries**")
            country_summary = build_country_summary(df).head(10)
            if country_summary.empty:
                st.caption("No country-level funding geography is available.")
            else:
                st.dataframe(
                    country_summary[["country", "funder_count", "estimated_annual_giving", "top_tags"]],
                    use_container_width=True,
                    hide_index=True,
                    height=280,
                )
        with right:
            st.markdown("**Top themes and source split**")
            tags = build_term_count(df, "thematic_tags").head(10)
            if tags.empty:
                st.caption("No thematic tags available.")
            else:
                fig = px.bar(
                    tags.sort_values("count"),
                    x="count",
                    y="term",
                    orientation="h",
                    labels={"term": "Theme", "count": "Funders"},
                )
                fig.update_layout(height=240, margin={"l": 0, "r": 0, "t": 10, "b": 0})
                st.plotly_chart(fig, use_container_width=True)
            source_split = df["source"].value_counts().rename_axis("source").reset_index(name="funders")
            st.dataframe(source_split, use_container_width=True, hide_index=True, height=120)

        st.markdown("**Top funders by estimated annual giving**")
        ranked = df[df["annual_giving_mid"].notna()].sort_values("annual_giving_mid", ascending=False)
        if ranked.empty:
            st.caption("No informative annual_giving values are available.")
        else:
            st.dataframe(
                pd.DataFrame(
                    {
                        "funder": ranked["name"].head(10),
                        "source": ranked["source"].head(10),
                        "annual_giving": ranked["annual_giving"].head(10),
                        "annual_giving_mid": ranked["annual_giving_mid"].head(10),
                        "countries / regions": ranked["geo_terms"].head(10).apply(lambda values: ", ".join(values)),
                    }
                ),
                use_container_width=True,
                hide_index=True,
                height=280,
            )
        return df

    selected_df = filter_by_geography_label(df, selected_label)
    if selected_df.empty:
        st.info(f"No funders match selected geography: {selected_label}")
        return selected_df

    country_allocation = build_country_allocation_table(df)
    selected_country_allocation = country_allocation[country_allocation["country_name"] == selected_label]
    is_country_level = not selected_country_allocation.empty
    currency = dominant_currency(selected_df)

    if is_country_level:
        amount_values = pd.to_numeric(
            selected_country_allocation["allocated_annual_giving"], errors="coerce"
        ).dropna()
        estimated_amount = float(amount_values.sum()) if not amount_values.empty else None
        median_values = pd.to_numeric(selected_country_allocation["annual_giving_mid"], errors="coerce").dropna()
        median_amount = float(median_values.median()) if not median_values.empty else None
        geography_note = "Country-level stated funding geography."
        amount_label = "Estimated annual giving potential"
    else:
        amount_values = pd.to_numeric(selected_df["annual_giving_mid"], errors="coerce").dropna()
        estimated_amount = float(amount_values.sum()) if not amount_values.empty else None
        median_amount = float(amount_values.median()) if not amount_values.empty else None
        geography_note = "Regional or non-country geography label; not mapped to a country boundary."
        amount_label = "Estimated annual giving among selected funders"

    st.markdown(f"### Country Explorer: {selected_label}")
    st.caption(f"{geography_note} This uses funding / operating geography, not registered office address.")

    metrics = st.columns(4)
    with metrics[0]:
        st.metric("Funders", int(selected_df["record_id"].nunique()))
    with metrics[1]:
        st.metric(amount_label, format_money(estimated_amount, currency) if estimated_amount else "n/a")
    with metrics[2]:
        st.metric("Median annual giving", format_money(median_amount, currency) if median_amount else "n/a")
    with metrics[3]:
        st.metric("With grant range", int(selected_df["grant_range_available"].sum()))

    left, right = st.columns(2)
    with left:
        st.markdown("**Top funders by estimated annual giving**")
        ranked = selected_df[selected_df["annual_giving_mid"].notna()].sort_values(
            "annual_giving_mid", ascending=False
        )
        if ranked.empty:
            st.caption("No informative annual_giving values are available for this selection.")
        else:
            st.dataframe(
                pd.DataFrame(
                    {
                        "funder": ranked["name"].head(10),
                        "source": ranked["source"].head(10),
                        "annual_giving": ranked["annual_giving"].head(10),
                        "annual_giving_mid": ranked["annual_giving_mid"].head(10),
                        "website/application link": ranked["best_link"].head(10),
                    }
                ),
                use_container_width=True,
                hide_index=True,
                height=280,
            )
    with right:
        st.markdown("**Top themes and source split**")
        tags = build_term_count(selected_df, "thematic_tags").head(10)
        if tags.empty:
            st.caption("No thematic tags available for this selection.")
        else:
            fig = px.bar(
                tags.sort_values("count"),
                x="count",
                y="term",
                orientation="h",
                labels={"term": "Theme", "count": "Funders"},
            )
            fig.update_layout(height=240, margin={"l": 0, "r": 0, "t": 10, "b": 0})
            st.plotly_chart(fig, use_container_width=True)
        source_split = selected_df["source"].value_counts().rename_axis("source").reset_index(name="funders")
        st.dataframe(source_split, use_container_width=True, hide_index=True, height=120)

    grant_values = selected_df[["grant_range_mid", "source"]].dropna()
    grant_values = grant_values[grant_values["grant_range_mid"] > 0]
    if not grant_values.empty:
        fig = px.box(
            grant_values,
            x="source",
            y="grant_range_mid",
            points="outliers",
            labels={"source": "Source", "grant_range_mid": "Typical grant size midpoint"},
        )
        fig.update_yaxes(type="log")
        fig.update_layout(height=300, margin={"l": 0, "r": 0, "t": 10, "b": 0})
        st.plotly_chart(fig, use_container_width=True)

    link_table = selected_df[selected_df["has_link"]][["name", "source", "best_link"]].head(20)
    if not link_table.empty:
        st.markdown("**Website / application links**")
        st.dataframe(link_table, use_container_width=True, hide_index=True, height=220)

    st.markdown("**Country drill-down table**")
    st.dataframe(_drilldown_table(selected_df), use_container_width=True, hide_index=True, height=360)
    return selected_df


def render_country_potential(df: pd.DataFrame, exclude_uk_by_default: bool = True) -> None:
    st.subheader("Estimated Funding Potential by Country")
    st.caption("Amounts are fractionally allocated across countries when a funder targets multiple countries.")

    allocation = build_country_allocation_table(df)
    if allocation.empty or allocation["allocated_annual_giving"].dropna().empty:
        st.info("No informative annual_giving values are available for the current filter selection.")
        return

    col_a, col_b = st.columns([1, 1])
    with col_a:
        include_uk = st.checkbox("Include UK in country potential chart", value=not exclude_uk_by_default)
    with col_b:
        mode = st.radio("Country metric", ["Absolute total", "Median per funder"], horizontal=True)

    if not include_uk:
        allocation = allocation[allocation["country_iso3"] != "GBR"]
    if allocation.empty:
        st.info("No countries remain after excluding the UK.")
        return

    if mode == "Median per funder":
        chart_df = (
            allocation.dropna(subset=["annual_giving_mid"])
            .groupby("country_name", as_index=False)
            .agg(value=("annual_giving_mid", "median"), funders=("record_id", "nunique"))
        )
        x_label = "Median annual giving per funder"
    else:
        chart_df = (
            allocation.dropna(subset=["allocated_annual_giving"])
            .groupby("country_name", as_index=False)
            .agg(value=("allocated_annual_giving", "sum"), funders=("record_id", "nunique"))
        )
        x_label = "Estimated allocated annual giving"

    if chart_df.empty:
        st.info("No informative annual_giving values are available for the current filter selection.")
        return

    chart_df = chart_df.sort_values("value", ascending=False).head(15).sort_values("value")
    fig = px.bar(
        chart_df,
        x="value",
        y="country_name",
        orientation="h",
        hover_data={"funders": True, "value": ":,.0f"},
        labels={"country_name": "Country", "value": x_label, "funders": "Funders"},
    )
    fig.update_layout(height=430, margin={"l": 0, "r": 0, "t": 10, "b": 0})
    st.plotly_chart(fig, use_container_width=True)


def render_topic_potential(df: pd.DataFrame) -> None:
    st.subheader("Estimated Funding Potential by Topic")
    tag_allocation = build_tag_allocation_table(df)
    tag_allocation = tag_allocation.dropna(subset=["allocated_annual_giving"])
    if tag_allocation.empty:
        st.info("No informative annual_giving values are available for the current filter selection.")
        return

    chart_df = (
        tag_allocation.groupby("tag", as_index=False)
        .agg(value=("allocated_annual_giving", "sum"), funders=("record_id", "nunique"))
        .sort_values("value", ascending=False)
        .head(15)
        .sort_values("value")
    )
    fig = px.bar(
        chart_df,
        x="value",
        y="tag",
        orientation="h",
        hover_data={"funders": True, "value": ":,.0f"},
        labels={"tag": "Topic", "value": "Estimated allocated annual giving", "funders": "Funders"},
    )
    fig.update_layout(height=430, margin={"l": 0, "r": 0, "t": 10, "b": 0})
    st.plotly_chart(fig, use_container_width=True)


def render_country_topic_matrix(df: pd.DataFrame, exclude_uk: bool = True) -> None:
    st.subheader("Country x Topic Funding Matrix")
    if exclude_uk:
        st.caption("United Kingdom is excluded from this matrix by default to make other stated operating countries visible.")
    allocation = build_country_allocation_table(df).dropna(subset=["allocated_annual_giving"])
    if exclude_uk and not allocation.empty:
        allocation = allocation[allocation["country_iso3"] != "GBR"]
    if allocation.empty:
        st.info("No informative annual_giving values are available for the current filter selection.")
        return

    rows = []
    for _, row in allocation.iterrows():
        tags = row.get("tags") or []
        if not tags:
            continue
        per_tag_amount = row["allocated_annual_giving"] / len(tags)
        for tag in tags:
            rows.append({"country": row["country_name"], "tag": tag, "value": per_tag_amount})
    if not rows:
        st.info("No country-topic funding matrix can be built for the current filter selection.")
        return

    matrix_source = pd.DataFrame(rows)
    top_countries = matrix_source.groupby("country")["value"].sum().nlargest(10).index
    top_tags = matrix_source.groupby("tag")["value"].sum().nlargest(10).index
    matrix_source = matrix_source[
        matrix_source["country"].isin(top_countries) & matrix_source["tag"].isin(top_tags)
    ]
    if matrix_source.empty:
        st.info("The country-topic matrix is too sparse for the current filter selection.")
        return

    matrix = matrix_source.pivot_table(index="country", columns="tag", values="value", aggfunc="sum", fill_value=0)
    matrix = matrix.loc[top_countries.intersection(matrix.index), top_tags.intersection(matrix.columns)]
    if matrix.empty or matrix.to_numpy().sum() <= 0:
        st.info("The country-topic matrix is too sparse for the current filter selection.")
        return

    fig = px.imshow(
        matrix,
        aspect="auto",
        color_continuous_scale="Blues",
        labels={"x": "Topic", "y": "Country", "color": "Estimated annual giving"},
    )
    fig.update_layout(height=520, margin={"l": 0, "r": 0, "t": 10, "b": 0})
    st.plotly_chart(fig, use_container_width=True)


def render_grant_size_distribution(df: pd.DataFrame) -> None:
    st.subheader("Typical Grant Size Distribution")
    st.caption("Typical individual grant size, based on available grant_range data.")
    values = df[["grant_range_mid", "source"]].dropna()
    values = values[values["grant_range_mid"] > 0]
    if values.empty:
        st.info("No informative grant_range values are available for the current filter selection.")
        return

    fig = px.histogram(
        values,
        x="grant_range_mid",
        color="source",
        nbins=40,
        labels={"grant_range_mid": "Typical grant size midpoint", "source": "Source"},
    )
    fig.update_xaxes(type="log")
    fig.update_layout(height=420, margin={"l": 0, "r": 0, "t": 10, "b": 0})
    st.plotly_chart(fig, use_container_width=True)


def render_supporting_charts(df: pd.DataFrame) -> None:
    left, right = st.columns(2)
    with left:
        st.subheader("Top Thematic Tags")
        tag_counts = build_term_count(df, "thematic_tags").head(12)
        if tag_counts.empty:
            st.info("No thematic tags available for the current filters.")
        else:
            fig = px.bar(
                tag_counts.sort_values("count"),
                x="count",
                y="term",
                orientation="h",
                labels={"term": "Tag", "count": "Funders"},
            )
            fig.update_layout(height=420, margin={"l": 0, "r": 0, "t": 10, "b": 0})
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.subheader("Data Availability")
        availability = availability_summary(df)
        if availability.empty:
            st.info("No availability data for the current filters.")
        else:
            fig = px.bar(
                availability,
                x="field",
                y="count",
                color="status",
                barmode="group",
                labels={"field": "Field", "count": "Funders", "status": "Status"},
            )
            fig.update_layout(height=420, margin={"l": 0, "r": 0, "t": 10, "b": 0})
            st.plotly_chart(fig, use_container_width=True)


def render_ranking_table(df: pd.DataFrame) -> None:
    st.subheader("Ranked Funders by Estimated Annual Giving")
    st.caption("Parsed range values use an estimated midpoint where the source provides a range.")
    ranking = make_ranking_dataframe(df).head(100)
    if ranking.empty:
        st.info("No informative annual_giving values are available for the current filter selection.")
        return
    st.dataframe(ranking, use_container_width=True, hide_index=True, height=380)


def render_table(df: pd.DataFrame) -> None:
    st.subheader("Searchable Funder Table")
    table = make_table_dataframe(df)
    if table.empty:
        st.info("No funders match the current filters.")
        return
    st.dataframe(table, use_container_width=True, hide_index=True, height=420)


def render_detail_panel(df: pd.DataFrame, selected_record_id: int | None = None) -> None:
    st.subheader("Funder Detail")
    if df.empty:
        st.info("Select different filters to inspect a funder.")
        return

    options = df.index.tolist()
    default_index = 0
    if selected_record_id is not None:
        matches = [position for position, index in enumerate(options) if df.loc[index, "record_id"] == selected_record_id]
        if matches:
            default_index = matches[0]
    selected_index = st.selectbox(
        "Select funder",
        options,
        index=default_index,
        format_func=lambda idx: f"{df.loc[idx, 'name']} ({df.loc[idx, 'source']})",
    )
    row = df.loc[selected_index]

    st.markdown(f"### {row['name']}")
    st.caption(f"Source: {row['source']} | Data quality: {row['data_quality']}")

    description = row.get("description", "")
    if is_informative_value(description):
        st.write(description)
    else:
        st.info("No description/about text is available in the consolidated dataset for this funder.")

    detail_cols = st.columns(2)
    with detail_cols[0]:
        st.markdown("**Funding geography**")
        st.write(list_to_display(row.get("geo_terms", [])) or "Missing")
        st.markdown("**Thematic tags**")
        st.write(list_to_display(row.get("thematic_tags", [])) or "Missing")
        st.markdown("**Website / application**")
        if is_informative_value(row.get("best_link")):
            st.link_button("Open link", row["best_link"])
            st.caption(row["best_link"])
        else:
            st.write("Missing")
        st.markdown("**Email**")
        st.write(row["email"] if is_informative_value(row.get("email")) else "Missing")

    with detail_cols[1]:
        st.markdown("**Funding fields**")
        st.write(f"Annual giving: {row['annual_giving'] or 'Missing'}")
        st.write(f"Annual giving midpoint: {format_money(row['annual_giving_mid'], row['annual_giving_currency'])}")
        st.write(f"Grant range: {row['grant_range'] or 'Missing'}")
        st.write(f"Grant range midpoint: {format_money(row['grant_range_mid'], row['grant_range_currency'])}")
        st.write(f"Average grant: {row['average_grant'] or 'Missing'}")
        if row["success_rate_informative"]:
            st.write(f"Success rate: {row['success_rate']}")
        st.write(f"Decision time: {row['decision_time'] or 'Missing'}")
        st.write(f"Funding model: {row['funding_model'] or 'Missing'}")

    if is_informative_value(row.get("office_address")):
        st.caption(f"Registered office / mailing address: {row['office_address']}")

    notes = row.get("missing_notes", [])
    if notes:
        st.warning("Missing important fields: " + ", ".join(notes))


def main() -> None:
    render_intro()
    df = load_data(DEFAULT_DATA_PATH.stat().st_mtime_ns)

    filters = render_sidebar(df)
    data_filters = {
        key: filters[key]
        for key in [
            "sources",
            "geo_terms",
            "tags",
            "annual_filter",
            "grant_filter",
            "link_filter",
            "search",
            "exclude_uk",
            "exclude_non_country",
            "min_annual_giving",
        ]
    }
    filtered_df = filter_dataframe(df, **data_filters)

    render_kpis(filtered_df)
    render_methodology_notes()

    if filtered_df.empty:
        st.info("No funders match the current filters.")
        return

    map_controls = render_map_controls(filtered_df)
    clicked_country = render_map(
        filtered_df,
        display_mode=str(map_controls["display_mode"]),
        show_bubbles=bool(filters["show_bubbles"]),
        bubble_metric=str(filters["bubble_metric"]),
        arrow_mode=str(map_controls["arrow_mode"]),
        selected_organisation_id=map_controls["selected_record_id"],
        aggregate_weight=str(map_controls["aggregate_weight"]),
        aggregate_top_n=int(map_controls["aggregate_top_n"]),
    )
    active_geography = render_country_explorer_selector(filtered_df, clicked_country)
    selected_df = render_country_explorer(filtered_df, active_geography)
    render_non_country_geographies(filtered_df)

    left, right = st.columns(2)
    with left:
        render_country_potential(filtered_df, exclude_uk_by_default=bool(filters["exclude_uk_visuals"]))
    with right:
        render_topic_potential(filtered_df)

    render_country_topic_matrix(filtered_df, exclude_uk=bool(filters["exclude_uk_visuals"]))
    table_df = selected_df if active_geography else filtered_df
    render_grant_size_distribution(table_df)
    render_supporting_charts(filtered_df)
    render_ranking_table(table_df)
    render_table(table_df)
    selected_record_id = map_controls["selected_record_id"] if map_controls["arrow_mode"] == "Selected organisation only" else None
    render_detail_panel(table_df, selected_record_id=selected_record_id)


if __name__ == "__main__":
    main()
