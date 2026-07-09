from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import pycountry


SRC_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = SRC_DIR / "data" / "preprocessed" / "consolidated_members_preprocessed.json"

MISSING_STRINGS = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "no information",
    "not available",
    "not disclosed",
    "not specified",
    "not published",
    "not applicable",
    "not publicly available",
    "not publicly disclosed",
    "not publicly specified",
    "data not available",
    "data not publicly available",
    "information not available",
    "information not publicly available",
}
MISSING_PREFIXES = tuple(
    sorted((value for value in MISSING_STRINGS if value and value != "-"), key=len, reverse=True)
)

NON_COUNTRY_KEYWORDS = {
    "africa",
    "arab world",
    "asia",
    "balkans",
    "caribbean",
    "cee",
    "central & eastern europe",
    "central and eastern europe",
    "dach",
    "developing countries",
    "developing world",
    "europe",
    "european union",
    "global",
    "global south",
    "international",
    "latin america",
    "majority world",
    "mena",
    "middle east",
    "nordic region",
    "north america",
    "pacific",
    "south america",
    "sub-saharan africa",
    "world",
    "worldwide",
    "england",
    "northern ireland",
    "scotland",
    "wales",
}

COUNTRY_ALIASES = {
    "bolivia": "BOL",
    "cabo verde": "CPV",
    "cape verde": "CPV",
    "congo": "COG",
    "cote d'ivoire": "CIV",
    "czech republic": "CZE",
    "democratic republic of congo": "COD",
    "democratic republic of the congo": "COD",
    "drc": "COD",
    "east timor": "TLS",
    "eswatini": "SWZ",
    "great britain": "GBR",
    "guinea bissau": "GNB",
    "guinea-bissau": "GNB",
    "iran": "IRN",
    "ivory coast": "CIV",
    "laos": "LAO",
    "moldova": "MDA",
    "north korea": "PRK",
    "palestine": "PSE",
    "republic of ireland": "IRL",
    "russia": "RUS",
    "sao tome and principe": "STP",
    "south korea": "KOR",
    "syria": "SYR",
    "tanzania": "TZA",
    "turkey": "TUR",
    "turkiye": "TUR",
    "uk": "GBR",
    "united kingdom": "GBR",
    "united states": "USA",
    "usa": "USA",
    "venezuela": "VEN",
    "vietnam": "VNM",
}

COUNTRY_DISPLAY_NAMES = {
    "GBR": "United Kingdom",
    "USA": "United States",
    "TZA": "Tanzania",
    "BOL": "Bolivia",
    "COD": "Democratic Republic of the Congo",
    "COG": "Congo",
    "CIV": "Cote d'Ivoire",
    "IRN": "Iran",
    "KOR": "South Korea",
    "LAO": "Laos",
    "MDA": "Moldova",
    "PSE": "Palestine",
    "RUS": "Russia",
    "SYR": "Syria",
    "TUR": "Turkey",
    "VNM": "Vietnam",
}

OFFICE_COUNTRY_ALIASES = {
    "england": "GBR",
    "northern ireland": "GBR",
    "scotland": "GBR",
    "wales": "GBR",
}

REGIONAL_GEOGRAPHY_TERMS = {
    "england",
    "northern ireland",
    "scotland",
    "wales",
}

COUNTRY_DOMAIN_SUFFIXES = {
    ".org.uk": "GBR",
    ".co.uk": "GBR",
    ".ac.uk": "GBR",
    ".gov.uk": "GBR",
    ".uk": "GBR",
    ".ie": "IRL",
    ".de": "DEU",
    ".at": "AUT",
    ".ch": "CHE",
    ".fr": "FRA",
    ".nl": "NLD",
    ".se": "SWE",
    ".no": "NOR",
    ".dk": "DNK",
    ".fi": "FIN",
    ".es": "ESP",
    ".it": "ITA",
    ".be": "BEL",
    ".us": "USA",
    ".ca": "CAN",
    ".au": "AUS",
}

GENERIC_DOMAIN_SUFFIXES = {
    "com",
    "org",
    "net",
    "foundation",
    "ngo",
}

COUNTRY_CENTROIDS = {
    "AFG": (33.9391, 67.7100),
    "ALB": (41.1533, 20.1683),
    "ARE": (23.4241, 53.8478),
    "ARG": (-38.4161, -63.6167),
    "ARM": (40.0691, 45.0382),
    "AUS": (-25.2744, 133.7751),
    "AUT": (47.5162, 14.5501),
    "BEL": (50.5039, 4.4699),
    "BGD": (23.6850, 90.3563),
    "BGR": (42.7339, 25.4858),
    "BOL": (-16.2902, -63.5887),
    "BRA": (-14.2350, -51.9253),
    "CAN": (56.1304, -106.3468),
    "CHE": (46.8182, 8.2275),
    "CHL": (-35.6751, -71.5430),
    "CHN": (35.8617, 104.1954),
    "CIV": (7.5400, -5.5471),
    "CMR": (7.3697, 12.3547),
    "COD": (-4.0383, 21.7587),
    "COG": (-0.2280, 15.8277),
    "COL": (4.5709, -74.2973),
    "CPV": (16.5388, -23.0418),
    "CZE": (49.8175, 15.4730),
    "DEU": (51.1657, 10.4515),
    "DNK": (56.2639, 9.5018),
    "EGY": (26.8206, 30.8025),
    "ESP": (40.4637, -3.7492),
    "EST": (58.5953, 25.0136),
    "ETH": (9.1450, 40.4897),
    "FIN": (61.9241, 25.7482),
    "FRA": (46.2276, 2.2137),
    "GBR": (55.3781, -3.4360),
    "GHA": (7.9465, -1.0232),
    "GRC": (39.0742, 21.8243),
    "HRV": (45.1000, 15.2000),
    "HUN": (47.1625, 19.5033),
    "IDN": (-0.7893, 113.9213),
    "IND": (20.5937, 78.9629),
    "IRL": (53.1424, -7.6921),
    "IRN": (32.4279, 53.6880),
    "IRQ": (33.2232, 43.6793),
    "ISR": (31.0461, 34.8516),
    "ITA": (41.8719, 12.5674),
    "JOR": (30.5852, 36.2384),
    "JPN": (36.2048, 138.2529),
    "KEN": (-0.0236, 37.9062),
    "KOR": (35.9078, 127.7669),
    "LAO": (19.8563, 102.4955),
    "LBN": (33.8547, 35.8623),
    "LIE": (47.1660, 9.5554),
    "LKA": (7.8731, 80.7718),
    "LTU": (55.1694, 23.8813),
    "LVA": (56.8796, 24.6032),
    "AGO": (-11.2027, 17.8739),
    "AND": (42.5063, 1.5218),
    "ATG": (17.0608, -61.7964),
    "AZE": (40.1431, 47.5769),
    "BDI": (-3.3731, 29.9189),
    "BEN": (9.3077, 2.3158),
    "BFA": (12.2383, -1.5616),
    "BHR": (25.9304, 50.6378),
    "BHS": (25.0343, -77.3963),
    "BIH": (43.9159, 17.6791),
    "BLR": (53.7098, 27.9534),
    "BLZ": (17.1899, -88.4976),
    "BRB": (13.1939, -59.5432),
    "BTN": (27.5142, 90.4336),
    "BWA": (-22.3285, 24.6849),
    "COM": (-11.8750, 43.8722),
    "CRI": (9.7489, -83.7534),
    "CUB": (21.5218, -77.7812),
    "CYP": (35.1264, 33.4299),
    "DJI": (11.8251, 42.5903),
    "DMA": (15.4150, -61.3710),
    "DOM": (18.7357, -70.1627),
    "DZA": (28.0339, 1.6596),
    "ECU": (-1.8312, -78.1834),
    "ERI": (15.1794, 39.7823),
    "FJI": (-17.7134, 178.0650),
    "FRO": (61.8926, -6.9118),
    "GAB": (-0.8037, 11.6094),
    "GEO": (42.3154, 43.3569),
    "GIN": (9.9456, -9.6966),
    "GMB": (13.4432, -15.3101),
    "GNB": (11.8037, -15.1804),
    "GNQ": (1.6508, 10.2679),
    "GRD": (12.1165, -61.6790),
    "GRL": (71.7069, -42.6043),
    "GTM": (15.7835, -90.2308),
    "GUY": (4.8604, -58.9302),
    "HND": (15.2000, -86.2419),
    "HTI": (18.9712, -72.2852),
    "ISL": (64.9631, -19.0208),
    "JAM": (18.1096, -77.2975),
    "KAZ": (48.0196, 66.9237),
    "KGZ": (41.2044, 74.7661),
    "KHM": (12.5657, 104.9910),
    "KIR": (-3.3704, -168.7340),
    "KNA": (17.3578, -62.7830),
    "KWT": (29.3117, 47.4818),
    "LBR": (6.4281, -9.4295),
    "LBY": (26.3351, 17.2283),
    "LCA": (13.9094, -60.9789),
    "LSO": (-29.6100, 28.2336),
    "LUX": (49.8153, 6.1296),
    "MDG": (-18.7669, 46.8691),
    "MDV": (3.2028, 73.2207),
    "MHL": (7.1315, 171.1845),
    "MLI": (17.5707, -3.9962),
    "MLT": (35.9375, 14.3754),
    "MCO": (43.7384, 7.4246),
    "MMR": (21.9162, 95.9560),
    "MNE": (42.7087, 19.3744),
    "MNG": (46.8625, 103.8467),
    "MRT": (21.0079, -10.9408),
    "MUS": (-20.3484, 57.5522),
    "NAM": (-22.9576, 18.4904),
    "NER": (17.6078, 8.0817),
    "NIC": (12.8654, -85.2072),
    "NRU": (-0.5228, 166.9315),
    "OMN": (21.4735, 55.9754),
    "PAN": (8.5380, -80.7821),
    "PHL": (12.8797, 121.7740),
    "PLW": (7.5150, 134.5825),
    "PNG": (-6.3150, 143.9555),
    "PRK": (40.3399, 127.5101),
    "PRY": (-23.4425, -58.4438),
    "QAT": (25.3548, 51.1839),
    "SAU": (23.8859, 45.0792),
    "SGP": (1.3521, 103.8198),
    "SLB": (-9.6457, 160.1562),
    "SLE": (8.4606, -11.7799),
    "SLV": (13.7942, -88.8965),
    "SMR": (43.9424, 12.4578),
    "SOM": (5.1521, 46.1996),
    "SSD": (6.8770, 31.3070),
    "STP": (0.1864, 6.6131),
    "SUR": (3.9193, -56.0278),
    "SYC": (-4.6796, 55.4920),
    "TCD": (15.4542, 18.7322),
    "THA": (15.8700, 100.9925),
    "TJK": (38.8610, 71.2761),
    "TKM": (38.9697, 59.5563),
    "TGO": (8.6195, 0.8248),
    "TON": (-21.1790, -175.1982),
    "TTO": (10.6918, -61.2225),
    "TUV": (-7.1095, 177.6493),
    "TWN": (23.6978, 120.9605),
    "URY": (-32.5228, -55.7658),
    "UZB": (41.3775, 64.5853),
    "VCT": (12.9843, -61.2872),
    "VUT": (-15.3767, 166.9592),
    "WSM": (-13.7590, -172.1046),
    "YEM": (15.5527, 48.5164),
    "MAR": (31.7917, -7.0926),
    "MDA": (47.4116, 28.3699),
    "MEX": (23.6345, -102.5528),
    "MKD": (41.6086, 21.7453),
    "MOZ": (-18.6657, 35.5296),
    "MWI": (-13.2543, 34.3015),
    "NGA": (9.0820, 8.6753),
    "NLD": (52.1326, 5.2913),
    "NOR": (60.4720, 8.4689),
    "NPL": (28.3949, 84.1240),
    "NZL": (-40.9006, 174.8860),
    "PAK": (30.3753, 69.3451),
    "PER": (-9.1900, -75.0152),
    "POL": (51.9194, 19.1451),
    "PRT": (39.3999, -8.2245),
    "PSE": (31.9522, 35.2332),
    "ROU": (45.9432, 24.9668),
    "RUS": (61.5240, 105.3188),
    "RWA": (-1.9403, 29.8739),
    "SDN": (12.8628, 30.2176),
    "SEN": (14.4974, -14.4524),
    "SRB": (44.0165, 21.0059),
    "SVK": (48.6690, 19.6990),
    "SVN": (46.1512, 14.9955),
    "SWE": (60.1282, 18.6435),
    "SWZ": (-26.5225, 31.4659),
    "SYR": (34.8021, 38.9968),
    "TLS": (-8.8742, 125.7275),
    "TZA": (-6.3690, 34.8888),
    "TUN": (33.8869, 9.5375),
    "TUR": (38.9637, 35.2433),
    "UGA": (1.3733, 32.2903),
    "UKR": (48.3794, 31.1656),
    "USA": (37.0902, -95.7129),
    "VEN": (6.4238, -66.5897),
    "VNM": (14.0583, 108.2772),
    "ZAF": (-30.5595, 22.9375),
    "ZMB": (-13.1339, 27.8493),
    "ZWE": (-19.0154, 29.1549),
}

MONEY_MULTIPLIERS = {
    "k": 1_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "million": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
}

_MONEY_NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?|\d+(?:,\d+)?"
_MONEY_UNIT = r"thousand|million|billion|mn|bn|[kmb](?![a-z])"
MONEY_PATTERN = re.compile(
    rf"(?P<currency>[\u00a3\u20ac$])?\s*"
    rf"(?P<number>{_MONEY_NUMBER})"
    rf"(?P<plus>\+)?"
    rf"(?:\s*(?P<unit>{_MONEY_UNIT}))?",
    re.IGNORECASE,
)
ANNUAL_CONTEXT_PATTERN = re.compile(
    r"\b(annual(?:ly)?|yearly|per year|each year|p\.?a\.?|charitable expenditure|grant expenditure|grants? awarded|"
    r"grants? distributed|disbursed|new research grants)\b",
    re.IGNORECASE,
)
STRONG_ANNUAL_CONTEXT_PATTERN = re.compile(
    r"\b(annual(?:ly)?|yearly|per year|each year|p\.?a\.?|charitable expenditure|grant expenditure|"
    r"new research grants)\b",
    re.IGNORECASE,
)
CUMULATIVE_CONTEXT_PATTERN = re.compile(
    r"\b(since|to date|cumulative|lifetime|inception|endowment|pledged|committed|invested|deployed|"
    r"total(?:ly)?|over\s+\d+\s+years?|triennium|programme?\s+budget|program(?:me)?\s+budget)\b",
    re.IGNORECASE,
)
MULTI_YEAR_RANGE_PATTERN = re.compile(r"\b(19|20)(\d{2})\s*[-/]\s*(?:(19|20)?(\d{2}))\b")

_COUNTRY_LABEL_ISO3_CACHE: list[tuple[str, str]] | None = None


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return ""
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(filter(None, (stringify(item) for item in value)))
    if isinstance(value, dict):
        return ", ".join(filter(None, (stringify(item) for item in value.values())))
    return str(value).strip()


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        text = value.strip()
        normalized = re.sub(r"\s+", " ", text.casefold())
        if not text or normalized in MISSING_STRINGS:
            return True
        return any(
            re.match(rf"^{re.escape(placeholder)}(?:\s|[(:;,\-])", normalized)
            for placeholder in MISSING_PREFIXES
        )
    if isinstance(value, (list, tuple, set)):
        return not value or all(is_missing_value(item) for item in value)
    if isinstance(value, dict):
        return not value or all(is_missing_value(item) for item in value.values())
    return False


def is_informative_value(value: Any) -> bool:
    return not is_missing_value(value)


def safe_get(record: dict[str, Any], candidate_fields: list[Any] | tuple[Any, ...], default: Any = None) -> Any:
    for field in candidate_fields:
        if isinstance(field, (list, tuple)):
            current: Any = record
            for part in field:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(part)
        elif isinstance(field, str) and "." in field:
            current = record
            for part in field.split("."):
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(part)
        else:
            current = record.get(field) if isinstance(record, dict) else None
        if is_informative_value(current):
            return current
    return default


def normalize_to_list(value: Any, split_commas: bool = False) -> list[str]:
    if is_missing_value(value):
        return []
    if isinstance(value, dict):
        items: list[Any] = []
        for nested in value.values():
            items.extend(normalize_to_list(nested, split_commas=split_commas))
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, str):
        separators = r"\s*[;\n|]\s*"
        if split_commas:
            separators = r"\s*[;,\n|]\s*"
        items = re.split(separators, value.strip())
    else:
        items = [value]

    normalized = []
    seen = set()
    for item in items:
        text = stringify(item)
        if is_missing_value(text):
            continue
        if text not in seen:
            normalized.append(text)
            seen.add(text)
    return normalized


def load_consolidated_data(path: Path = DEFAULT_DATA_PATH) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return [record for record in payload["data"] if isinstance(record, dict)]
        return [record for record in payload.values() if isinstance(record, dict)]
    return []


def load_json_records(path: Path = DEFAULT_DATA_PATH) -> list[dict[str, Any]]:
    return load_consolidated_data(path)


def detect_currency(value: Any) -> str | None:
    text = stringify(value)
    lowered = text.casefold()
    if "\u20ac" in text or " eur" in f" {lowered}" or "euro" in lowered:
        return "EUR"
    if "\u00a3" in text or "gbp" in lowered or "pound" in lowered:
        return "GBP"
    if "$" in text or "usd" in lowered or "dollar" in lowered:
        return "USD"
    return None


def _parse_money_number(number_text: str, unit: str | None = None) -> float | None:
    has_unit = bool(unit)
    token = number_text.strip()

    if "," in token and "." in token:
        token = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        if len(parts) == 2 and len(parts[-1]) in {1, 2}:
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "." in token:
        parts = token.split(".")
        if len(parts) > 2:
            token = token.replace(".", "")
        elif not has_unit and len(parts[-1]) == 3:
            token = token.replace(".", "")

    try:
        amount = float(token)
    except ValueError:
        return None
    return amount * MONEY_MULTIPLIERS.get(str(unit or "").casefold(), 1)


def _is_probable_year(number_text: str, amount: float) -> bool:
    compact = number_text.replace(",", "").replace(".", "")
    return compact.isdigit() and len(compact) == 4 and 1900 <= amount <= 2100


def _money_matches(value: Any) -> list[dict[str, Any]]:
    text = stringify(value)
    if not text:
        return []
    text = (
        text.replace("\u00c2\u00a3", "\u00a3")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )
    matches = []
    for match in MONEY_PATTERN.finditer(text):
        unit = match.group("unit")
        amount = _parse_money_number(match.group("number"), unit)
        if amount is None:
            continue
        has_money_signal = bool(match.group("currency") or unit)
        matches.append(
            {
                "amount": amount,
                "number_text": match.group("number"),
                "unit": unit,
                "has_money_signal": has_money_signal,
                "start": match.start(),
                "end": match.end(),
            }
        )

    if not matches:
        return []

    has_any_money_signal = any(match["has_money_signal"] for match in matches)
    filtered = []
    for match in matches:
        if has_any_money_signal and not match["has_money_signal"]:
            continue
        if not match["has_money_signal"] and _is_probable_year(match["number_text"], match["amount"]):
            continue
        filtered.append(match)
    return filtered


def _money_amounts(value: Any) -> list[float]:
    return [match["amount"] for match in _money_matches(value)]


def _has_range_separator(text: str, first: dict[str, Any], second: dict[str, Any]) -> bool:
    between = text[first["end"] : second["start"]]
    normalized = re.sub(r"\s+", " ", between.casefold()).strip()
    if re.fullmatch(r"[-]+|to", normalized):
        return True
    return normalized == "and" and bool(re.search(r"\bbetween\b", text[: first["start"]], re.IGNORECASE))


def _range_amounts(first: dict[str, Any], second: dict[str, Any]) -> tuple[float, float]:
    first_amount = float(first["amount"])
    second_amount = float(second["amount"])
    first_unit = first.get("unit")
    second_unit = second.get("unit")

    if not first_unit and second_unit and first_amount < 1_000 <= second_amount:
        first_amount *= MONEY_MULTIPLIERS.get(str(second_unit).casefold(), 1)
    elif first_unit and not second_unit and second_amount < 1_000 <= first_amount:
        second_amount *= MONEY_MULTIPLIERS.get(str(first_unit).casefold(), 1)

    return first_amount, second_amount


def _bound_applies_to_first_amount(text: str, first: dict[str, Any], pattern: str) -> bool:
    prefix = text[max(0, first["start"] - 40) : first["start"]].casefold()
    return bool(re.search(pattern, prefix))


def _has_multi_year_range(text: str) -> bool:
    for match in MULTI_YEAR_RANGE_PATTERN.finditer(text):
        start_year = int(match.group(1) + match.group(2))
        end_prefix = match.group(3) or match.group(1)
        end_year = int(end_prefix + match.group(4))
        if end_year < start_year:
            end_year += 100
        if end_year - start_year > 1:
            return True
    return False


def _looks_like_non_annual_aggregate(text: str) -> bool:
    normalized = text.casefold()
    if _has_multi_year_range(normalized):
        return True
    if CUMULATIVE_CONTEXT_PATTERN.search(normalized):
        return True
    return False


def _annual_context_candidates(text: str, match: dict[str, Any]) -> list[tuple[int, bool]]:
    candidates = []
    for annual_match in ANNUAL_CONTEXT_PATTERN.finditer(text):
        keyword = annual_match.group(0)
        strong = bool(STRONG_ANNUAL_CONTEXT_PATTERN.search(keyword))
        if match["end"] <= annual_match.start():
            distance = annual_match.start() - match["end"]
            if distance <= 80:
                candidates.append((distance, strong))
        elif annual_match.end() <= match["start"]:
            distance = match["start"] - annual_match.end()
            if distance <= 50:
                candidates.append((distance + 100, strong))
    return candidates


def _parse_explicit_annual_amount(
    text: str,
    matches: list[dict[str, Any]],
    currency: str | None,
    require_strong_context: bool = False,
) -> dict[str, Any] | None:
    candidates = []
    for index, match in enumerate(matches):
        for score, strong in _annual_context_candidates(text, match):
            if require_strong_context and not strong:
                continue
            candidates.append((score, index, match))
    if not candidates:
        return None

    _, index, match = min(candidates, key=lambda item: (item[0], item[1]))
    amount = float(match["amount"])
    confidence = "explicit_annual_value"

    if index + 1 < len(matches) and _has_range_separator(text, match, matches[index + 1]):
        first_amount, second_amount = _range_amounts(match, matches[index + 1])
        min_amount = min(first_amount, second_amount)
        max_amount = max(first_amount, second_amount)
        midpoint = (min_amount + max_amount) / 2
        confidence = "explicit_annual_range"
    elif index > 0 and _has_range_separator(text, matches[index - 1], match):
        first_amount, second_amount = _range_amounts(matches[index - 1], match)
        min_amount = min(first_amount, second_amount)
        max_amount = max(first_amount, second_amount)
        midpoint = (min_amount + max_amount) / 2
        confidence = "explicit_annual_range"
    else:
        min_amount = amount
        max_amount = amount
        midpoint = amount

    return {
        "min_amount": min_amount,
        "max_amount": max_amount,
        "midpoint_amount": midpoint,
        "currency": currency,
        "confidence": confidence,
        "parsed": True,
    }


def parse_money_range(value: Any) -> dict[str, Any]:
    currency = detect_currency(value)
    if isinstance(value, (int, float)) and not is_missing_value(value):
        amount = float(value)
        return {
            "min_amount": amount,
            "max_amount": amount,
            "midpoint_amount": amount,
            "currency": currency,
            "confidence": "numeric",
            "parsed": True,
        }
    if is_missing_value(value):
        return {
            "min_amount": None,
            "max_amount": None,
            "midpoint_amount": None,
            "currency": currency,
            "confidence": "missing",
            "parsed": False,
        }

    text = stringify(value)
    normalized = text.casefold().replace("\u00c2\u00a3", "\u00a3")
    matches = _money_matches(text)
    amounts = [match["amount"] for match in matches]
    if not amounts:
        return {
            "min_amount": None,
            "max_amount": None,
            "midpoint_amount": None,
            "currency": currency,
            "confidence": "unparsed",
            "parsed": False,
        }

    is_upper_bound = _bound_applies_to_first_amount(
        normalized,
        matches[0],
        r"\b(up to|up\s*-|under|below|max(?:imum)?|less than)\b",
    )
    is_lower_bound = _bound_applies_to_first_amount(
        normalized,
        matches[0],
        r"\b(over|above|at least|min(?:imum)?|more than)\b",
    )

    if len(matches) >= 2 and _has_range_separator(text, matches[0], matches[1]):
        first_amount, second_amount = _range_amounts(matches[0], matches[1])
        min_amount = min(first_amount, second_amount)
        max_amount = max(first_amount, second_amount)
        midpoint = (min_amount + max_amount) / 2
        confidence = "range"
    elif is_upper_bound:
        min_amount = 0.0
        max_amount = amounts[0]
        midpoint = max_amount / 2
        confidence = "upper_bound"
    elif is_lower_bound:
        min_amount = amounts[0]
        max_amount = None
        midpoint = amounts[0]
        confidence = "lower_bound"
    else:
        min_amount = amounts[0]
        max_amount = amounts[0]
        midpoint = amounts[0]
        confidence = "single_value" if len(amounts) == 1 else "first_value_from_multi_value_text"

    return {
        "min_amount": min_amount,
        "max_amount": max_amount,
        "midpoint_amount": midpoint,
        "currency": currency,
        "confidence": confidence,
        "parsed": True,
    }


def parse_annual_giving_value(value: Any) -> dict[str, Any]:
    currency = detect_currency(value)
    if isinstance(value, (int, float)) and not is_missing_value(value):
        return parse_money_range(value)
    if is_missing_value(value):
        parsed = parse_money_range(value)
        parsed["confidence"] = "missing"
        return parsed

    text = stringify(value)
    matches = _money_matches(text)
    if not matches:
        return {
            "min_amount": None,
            "max_amount": None,
            "midpoint_amount": None,
            "currency": currency,
            "confidence": "unparsed",
            "parsed": False,
        }

    is_non_annual_aggregate = _looks_like_non_annual_aggregate(text)
    explicit_annual = _parse_explicit_annual_amount(
        text,
        matches,
        currency,
        require_strong_context=is_non_annual_aggregate,
    )
    if explicit_annual is not None:
        return explicit_annual

    if is_non_annual_aggregate:
        return {
            "min_amount": None,
            "max_amount": None,
            "midpoint_amount": None,
            "currency": currency,
            "confidence": "non_annual_aggregate",
            "parsed": False,
        }

    return parse_money_range(value)


def extract_money_midpoint(value: Any) -> float | None:
    parsed = parse_money_range(value)
    return parsed["midpoint_amount"] if parsed["parsed"] else None


def is_informative_money_value(value: Any) -> bool:
    return parse_money_range(value)["parsed"]


def is_informative_success_rate(value: Any) -> bool:
    if is_missing_value(value):
        return False
    text = stringify(value).casefold()
    if any(term in text for term in ["not applicable", "invitation", "trustee discretion", "no public"]):
        return False
    return bool(re.search(r"\d+(?:\.\d+)?\s*%|\b\d+\s*(?:in|/)\s*\d+\b", text))


def is_non_country_geography(value: Any) -> bool:
    text = stringify(value)
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    if normalized in NON_COUNTRY_KEYWORDS:
        return True
    return any(keyword in normalized for keyword in NON_COUNTRY_KEYWORDS if len(keyword) > 4)


def country_to_iso3(value: Any) -> tuple[str | None, str | None]:
    text = stringify(value)
    if not text:
        return None, None
    if is_non_country_geography(text):
        return None, text

    key = text.casefold().strip()
    if key in COUNTRY_ALIASES:
        iso3 = COUNTRY_ALIASES[key]
        return iso3, COUNTRY_DISPLAY_NAMES.get(iso3, text)

    try:
        country = pycountry.countries.lookup(text)
    except LookupError:
        return None, text

    return country.alpha_3, COUNTRY_DISPLAY_NAMES.get(country.alpha_3, country.name)


def _display_country_name(iso3: str, fallback: str = "") -> str:
    if iso3 in COUNTRY_DISPLAY_NAMES:
        return COUNTRY_DISPLAY_NAMES[iso3]
    country = pycountry.countries.get(alpha_3=iso3)
    if country:
        return country.name
    return fallback or iso3


def country_to_centroid(value: Any) -> tuple[float, float] | None:
    text = stringify(value)
    if not text:
        return None
    iso3 = text.upper() if len(text) == 3 and text.upper() in COUNTRY_CENTROIDS else None
    if not iso3:
        iso3, _ = country_to_iso3(text)
    if not iso3:
        return None
    return COUNTRY_CENTROIDS.get(iso3)


def _office_country_lookup(value: Any) -> tuple[str | None, str | None]:
    text = stringify(value)
    if not text:
        return None, None

    key = text.casefold().strip()
    iso3 = OFFICE_COUNTRY_ALIASES.get(key) or COUNTRY_ALIASES.get(key)
    if iso3:
        return iso3, _display_country_name(iso3, text)

    try:
        country = pycountry.countries.lookup(text)
    except LookupError:
        return None, None
    return country.alpha_3, _display_country_name(country.alpha_3, country.name)


def _country_from_explicit_text(value: Any) -> tuple[str | None, str | None]:
    text = stringify(value)
    if not text:
        return None, None
    normalized = re.sub(r"\s+", " ", text.casefold())
    for label, iso3 in _country_label_iso3_pairs():
        if label not in normalized:
            continue
        if re.search(rf"(?<![a-z]){re.escape(label)}(?![a-z])", normalized):
            return iso3, _display_country_name(iso3, label)
    return None, None


def _country_label_iso3_pairs() -> list[tuple[str, str]]:
    global _COUNTRY_LABEL_ISO3_CACHE
    if _COUNTRY_LABEL_ISO3_CACHE is not None:
        return _COUNTRY_LABEL_ISO3_CACHE

    pairs: dict[str, str] = {}
    for label, iso3 in {**COUNTRY_ALIASES, **OFFICE_COUNTRY_ALIASES}.items():
        if len(label) >= 3:
            pairs[label.casefold()] = iso3
    for country in pycountry.countries:
        candidate_names = [country.name]
        if hasattr(country, "official_name"):
            candidate_names.append(country.official_name)
        if hasattr(country, "common_name"):
            candidate_names.append(country.common_name)
        for candidate in candidate_names:
            label = candidate.casefold()
            if len(label) >= 3:
                pairs[label] = country.alpha_3

    _COUNTRY_LABEL_ISO3_CACHE = sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True)
    return _COUNTRY_LABEL_ISO3_CACHE


def normalize_office_country(record: dict[str, Any]) -> dict[str, Any]:
    """Parse registered-office country without treating it as funding geography."""
    direct_value = safe_get(
        record,
        [
            "country",
            "office_country",
            "registered_country",
            ("registered_office", "country"),
            ("office", "country"),
        ],
        default="",
    )
    iso3, name = _office_country_lookup(direct_value)
    if iso3 and name:
        return {
            "office_country_name": name,
            "office_country_iso3": iso3,
            "office_country_confidence": "explicit_country_field",
        }

    text_value = safe_get(
        record,
        [
            "address",
            "office_address",
            "registered_address",
            ("registered_office", "address"),
            ("office", "address"),
        ],
        default="",
    )
    iso3, name = _country_from_explicit_text(text_value)
    if iso3 and name:
        return {
            "office_country_name": name,
            "office_country_iso3": iso3,
            "office_country_confidence": "explicit_address_text",
        }

    return {
        "office_country_name": "",
        "office_country_iso3": "",
        "office_country_confidence": "",
    }


def _record_as_dict(record: Any) -> dict[str, Any]:
    if hasattr(record, "to_dict"):
        converted = record.to_dict()
        return converted if isinstance(converted, dict) else {}
    return record if isinstance(record, dict) else {}


def _raw_record(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("raw_record")
    return raw if isinstance(raw, dict) else record


def _candidate_values(record: dict[str, Any], raw_record: dict[str, Any], fields: list[Any]) -> list[Any]:
    values = []
    for field in fields:
        for source in (record, raw_record):
            value = safe_get(source, [field], default=None)
            if is_informative_value(value):
                values.append(value)
    return values


def _origin_result(
    country_name: str | None,
    iso3: str | None,
    confidence: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "origin_country_name": country_name,
        "origin_country_iso3": iso3,
        "origin_confidence": confidence,
        "origin_reason": reason,
    }


def _country_from_name(value: Any) -> tuple[str | None, str | None, str | None]:
    text = stringify(value)
    if not text:
        return None, None, None

    parenthetical_values = re.findall(r"\(([^()]+)\)", text)
    for item in parenthetical_values:
        iso3, country_name = _office_country_lookup(item)
        if iso3 and country_name:
            return iso3, country_name, "country inferred from organisation name parenthetical"

    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    for label, iso3 in _country_label_iso3_pairs():
        if len(label) < 4:
            continue
        if not normalized.endswith(label):
            continue
        if re.search(rf"(?:,|\s|-){re.escape(label)}$", normalized):
            return iso3, _display_country_name(iso3, label), "country inferred from organisation name suffix"
    return None, None, None


def _host_from_url(value: Any) -> str:
    text = stringify(value).casefold()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.split("/")[0].split("?")[0].split("#")[0].strip()


def _country_from_domain(value: Any) -> tuple[str | None, str | None, str | None]:
    host = _host_from_url(value)
    if not host:
        return None, None, None

    for suffix, iso3 in sorted(COUNTRY_DOMAIN_SUFFIXES.items(), key=lambda item: len(item[0]), reverse=True):
        if host.endswith(suffix.lstrip(".")) or host.endswith(suffix):
            return iso3, _display_country_name(iso3), "country inferred from website/domain suffix"

    labels = host.split(".")
    if len(labels) >= 2 and labels[-1] in GENERIC_DOMAIN_SUFFIXES:
        second_level = labels[-2]
        if second_level.endswith("uk") and len(second_level) >= 4:
            return "GBR", _display_country_name("GBR"), "country inferred from strong UK domain label"

    return None, None, None


def _country_from_source_metadata(record: dict[str, Any], raw_record: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    charity_fields = [
        "charity_number",
        "registered_charity_number",
        "uk_charity_number",
        ("funding_info", "charity_number"),
        ("quick_stats", "Charity Number"),
        ("funding_info", "quick_stats", "Charity Number"),
    ]
    values = _candidate_values(record, raw_record, charity_fields)
    source = stringify(safe_get(record, ["source"], default=safe_get(raw_record, ["source"], default=""))).casefold()
    for value in values:
        text = stringify(value)
        if not text:
            continue
        if re.search(r"\bSC\d{5,7}\b", text, flags=re.IGNORECASE):
            return "GBR", _display_country_name("GBR"), "country inferred from Scottish charity metadata"
        if "hinchilla" in source or re.search(r"\b(uk|united kingdom|england|wales)\b", text, flags=re.IGNORECASE):
            return "GBR", _display_country_name("GBR"), "country inferred from UK charity/source metadata"
    return None, None, None


def infer_origin_country_for_selected_org(record: Any) -> dict[str, Any]:
    normalized = _record_as_dict(record)
    raw = _raw_record(normalized)

    direct_iso = stringify(normalized.get("office_country_iso3"))
    direct_name = stringify(normalized.get("office_country_name")) or _display_country_name(direct_iso)
    if direct_iso and direct_name:
        return _origin_result(
            direct_name,
            direct_iso,
            "direct",
            "explicit office/registered country field",
        )

    direct_fields = [
        "office_country",
        "registered_country",
        "country",
        "address_country",
        "headquarters_country",
        ("registered_office", "country"),
        ("office", "country"),
        ("headquarters", "country"),
    ]
    for value in _candidate_values(normalized, raw, direct_fields):
        iso3, country_name = _office_country_lookup(value)
        if iso3 and country_name:
            return _origin_result(country_name, iso3, "direct", "explicit office/registered country field")

    address_fields = [
        "address",
        "office_address",
        "registered_address",
        "contact_address",
        "headquarters",
        "location",
        ("registered_office", "address"),
        ("office", "address"),
    ]
    for value in _candidate_values(normalized, raw, address_fields):
        iso3, country_name = _country_from_explicit_text(value)
        if iso3 and country_name:
            return _origin_result(country_name, iso3, "inferred_from_address", "country parsed from address")

    name_iso, name_country, name_reason = _country_from_name(
        safe_get(normalized, ["name"], default=safe_get(raw, ["name"], default=""))
    )
    domain_iso = domain_country = domain_reason = None
    for value in _candidate_values(normalized, raw, ["website", "best_link", "url", "link"]):
        domain_iso, domain_country, domain_reason = _country_from_domain(value)
        if domain_iso:
            break

    if name_iso and domain_iso and name_iso == domain_iso:
        return _origin_result(
            name_country,
            name_iso,
            "inferred_from_name_and_domain",
            "country inferred from matching name and domain signals",
        )
    if name_iso and name_country:
        return _origin_result(name_country, name_iso, "inferred_from_name", name_reason or "country inferred from organisation name")
    if domain_iso and domain_country:
        return _origin_result(domain_country, domain_iso, "inferred_from_domain", domain_reason or "country inferred from website/domain")

    metadata_iso, metadata_country, metadata_reason = _country_from_source_metadata(normalized, raw)
    if metadata_iso and metadata_country:
        return _origin_result(
            metadata_country,
            metadata_iso,
            "inferred_from_source_metadata",
            metadata_reason or "country inferred from charity/source metadata",
        )

    return _origin_result(None, None, "missing", "no reliable origin field or inference signal")


def normalize_tags(record: dict[str, Any]) -> list[str]:
    return normalize_to_list(
        safe_get(
            record,
            [
                "thematic_focus",
                "tags_focus",
                ("philea_info", "thematic_focus"),
                ("philea_info", "programme_areas"),
                ("philea_info", "Programme Areas"),
            ],
            default=[],
        )
    )


def normalize_geographies(record: dict[str, Any]) -> dict[str, list[str]]:
    raw_geo = safe_get(record, ["geographic_focus", "geo_locations"], default={})
    countries: list[str] = []
    country_codes: list[str] = []
    regions: list[str] = []
    non_country_geographies: list[str] = []

    def add_country_or_non_country(item: Any) -> None:
        for value in normalize_to_list(item, split_commas=True):
            iso3, country_name = country_to_iso3(value)
            if iso3 and country_name:
                if iso3 not in country_codes:
                    country_codes.append(iso3)
                    countries.append(country_name)
            elif country_name and country_name not in non_country_geographies:
                non_country_geographies.append(country_name)

    if isinstance(raw_geo, dict):
        for region, values in raw_geo.items():
            region_text = stringify(region)
            if region_text and region_text not in regions:
                regions.append(region_text)
            add_country_or_non_country(values)
    else:
        add_country_or_non_country(raw_geo)

    return {
        "countries": countries,
        "country_codes": country_codes,
        "regions": regions,
        "non_country_geographies": non_country_geographies,
        "geo_terms": sorted(set(countries + regions + non_country_geographies)),
    }


def _funding_info(record: dict[str, Any]) -> dict[str, Any]:
    funding_info = record.get("funding_info")
    return funding_info if isinstance(funding_info, dict) else {}


def _raw_money(record: dict[str, Any], field: str) -> Any:
    funding_info = _funding_info(record)
    return safe_get(record, [("funding_info", field), field], default=funding_info.get(field, ""))


def _description(record: dict[str, Any]) -> str:
    value = safe_get(
        record,
        [
            "about",
            "description",
            "mission",
            "programme_areas",
            "program_areas",
            ("philea_info", "About"),
            ("philea_info", "Description"),
            ("philea_info", "Mission"),
            ("philea_info", "Programme Areas"),
            ("funding_info", "application_details"),
        ],
        default="",
    )
    return stringify(value)


def _quality_label(score: float) -> str:
    if score >= 0.75:
        return "High"
    if score >= 0.45:
        return "Medium"
    return "Low"


def _missing_notes(row: dict[str, Any]) -> list[str]:
    notes = []
    if not row["has_geography"]:
        notes.append("missing funding geography")
    if not row["has_tags"]:
        notes.append("missing tags")
    if not row["annual_giving_available"]:
        notes.append("missing annual giving")
    if not row["grant_range_available"]:
        notes.append("missing grant range")
    if not row["has_link"]:
        notes.append("missing website/application link")
    return notes


def normalize_record(record: dict[str, Any], record_id: int = 0) -> dict[str, Any]:
    geography = normalize_geographies(record)
    tags = normalize_tags(record)
    office_country = normalize_office_country(record)

    website = stringify(safe_get(record, ["website", "link"], default=""))
    application_link = stringify(
        safe_get(
            record,
            [
                ("funding_info", "application_portal"),
                ("funding_info", "applicationPortal"),
                "applicationPortal",
                "application_link",
            ],
            default="",
        )
    )
    best_link = application_link if is_informative_value(application_link) else website

    annual_giving_source_text = stringify(_raw_money(record, "annual_giving"))
    annual_parsed = parse_annual_giving_value(annual_giving_source_text)
    annual_giving = annual_giving_source_text if annual_parsed["parsed"] else ""
    grant_range = stringify(_raw_money(record, "grant_range"))
    grant_parsed = parse_money_range(grant_range)
    average_grant = stringify(_raw_money(record, "average_grant"))
    average_parsed = parse_money_range(average_grant)
    success_rate = stringify(_raw_money(record, "success_rate"))

    row = {
        "record_id": record_id,
        "name": stringify(safe_get(record, ["name"], default="Unnamed funder")) or "Unnamed funder",
        "source": stringify(safe_get(record, ["source"], default="Unknown")) or "Unknown",
        "website": website,
        "application_link": application_link,
        "best_link": best_link,
        "email": stringify(safe_get(record, ["email"], default="")),
        "office_address": stringify(safe_get(record, ["address"], default="")),
        "office_city": stringify(safe_get(record, ["city"], default="")),
        "office_country": stringify(safe_get(record, ["country"], default="")),
        "office_country_name": office_country["office_country_name"],
        "office_country_iso3": office_country["office_country_iso3"],
        "office_country_confidence": office_country["office_country_confidence"],
        "countries": geography["countries"],
        "country_codes": geography["country_codes"],
        "regions": geography["regions"],
        "non_country_geographies": geography["non_country_geographies"],
        "geo_terms": geography["geo_terms"],
        "thematic_tags": tags,
        "annual_giving": annual_giving,
        "annual_giving_source_text": annual_giving_source_text,
        "annual_giving_mid": annual_parsed["midpoint_amount"],
        "annual_giving_currency": annual_parsed["currency"],
        "annual_giving_parse_confidence": annual_parsed["confidence"],
        "grant_range": grant_range,
        "grant_range_mid": grant_parsed["midpoint_amount"],
        "grant_range_currency": grant_parsed["currency"],
        "grant_range_parse_confidence": grant_parsed["confidence"],
        "average_grant": average_grant,
        "average_grant_mid": average_parsed["midpoint_amount"],
        "success_rate": success_rate,
        "decision_time": stringify(_raw_money(record, "decision_time")),
        "funding_model": stringify(_raw_money(record, "funding_model")),
        "description": _description(record),
        "raw_record": record,
        "has_geography": bool(geography["geo_terms"]),
        "has_country_geography": bool(geography["country_codes"]),
        "has_non_country_geography": bool(geography["non_country_geographies"] or geography["regions"]),
        "has_tags": bool(tags),
        "annual_giving_available": annual_parsed["parsed"],
        "grant_range_available": grant_parsed["parsed"],
        "success_rate_informative": is_informative_success_rate(success_rate),
        "has_link": is_informative_value(best_link),
    }
    flags = [
        row["has_geography"],
        row["has_tags"],
        row["annual_giving_available"],
        row["grant_range_available"],
        row["success_rate_informative"],
        row["has_link"],
        is_informative_value(row["email"]),
    ]
    row["data_quality_score"] = sum(bool(flag) for flag in flags) / len(flags)
    row["data_quality"] = _quality_label(row["data_quality_score"])
    row["missing_notes"] = _missing_notes(row)
    return row


def normalize_records(raw_data: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [normalize_record(record, index) for index, record in enumerate(raw_data)]
    columns = [
        "record_id",
        "name",
        "source",
        "website",
        "application_link",
        "best_link",
        "email",
        "office_address",
        "office_city",
        "office_country",
        "office_country_name",
        "office_country_iso3",
        "office_country_confidence",
        "countries",
        "country_codes",
        "regions",
        "non_country_geographies",
        "geo_terms",
        "thematic_tags",
        "annual_giving",
        "annual_giving_source_text",
        "annual_giving_mid",
        "annual_giving_currency",
        "annual_giving_parse_confidence",
        "grant_range",
        "grant_range_mid",
        "grant_range_currency",
        "grant_range_parse_confidence",
        "average_grant",
        "average_grant_mid",
        "success_rate",
        "decision_time",
        "funding_model",
        "description",
        "raw_record",
        "has_geography",
        "has_country_geography",
        "has_non_country_geography",
        "has_tags",
        "annual_giving_available",
        "grant_range_available",
        "success_rate_informative",
        "has_link",
        "data_quality_score",
        "data_quality",
        "missing_notes",
    ]
    return pd.DataFrame(rows, columns=columns)


def records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    return normalize_records(records)


def list_to_display(values: Any) -> str:
    return ", ".join(normalize_to_list(values))


def format_money(value: Any, currency: str | None = None) -> str:
    if value is None:
        return "Not available"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "Not available"
    if math.isnan(amount):
        return "Not available"

    symbol = {"EUR": "\u20ac", "GBP": "\u00a3", "USD": "$"}.get(currency or "", "")
    suffix = " mixed" if currency == "mixed" else ""
    abs_amount = abs(amount)
    if abs_amount >= 1_000_000_000:
        formatted = f"{symbol}{amount / 1_000_000_000:,.1f}B"
    elif abs_amount >= 1_000_000:
        formatted = f"{symbol}{amount / 1_000_000:,.1f}M"
    elif abs_amount >= 1_000:
        formatted = f"{symbol}{amount / 1_000:,.1f}K"
    else:
        formatted = f"{symbol}{amount:,.0f}"
    return f"{formatted}{suffix}"


def dominant_currency(df: pd.DataFrame, column: str = "annual_giving_currency") -> str | None:
    if df.empty or column not in df:
        return None
    currencies = [value for value in df[column].dropna().tolist() if value]
    if not currencies:
        return None
    counts = Counter(currencies)
    if len(counts) == 1:
        return currencies[0]
    return "mixed"


def filter_dataframe(
    df: pd.DataFrame,
    sources: list[str],
    geo_terms: list[str],
    tags: list[str],
    annual_filter: str,
    grant_filter: str,
    link_filter: str,
    search: str,
    exclude_uk: bool = False,
    exclude_non_country: bool = False,
    min_annual_giving: float | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df

    filtered = df.copy()
    if sources:
        filtered = filtered[filtered["source"].isin(sources)]
    if geo_terms:
        selected = set(geo_terms)
        filtered = filtered[filtered["geo_terms"].apply(lambda values: bool(selected & set(values)))]
    if tags:
        selected = set(tags)
        filtered = filtered[filtered["thematic_tags"].apply(lambda values: bool(selected & set(values)))]
    if annual_filter == "Yes":
        filtered = filtered[filtered["annual_giving_available"]]
    elif annual_filter == "No":
        filtered = filtered[~filtered["annual_giving_available"]]
    if grant_filter == "Yes":
        filtered = filtered[filtered["grant_range_available"]]
    elif grant_filter == "No":
        filtered = filtered[~filtered["grant_range_available"]]
    if link_filter == "Yes":
        filtered = filtered[filtered["has_link"]]
    elif link_filter == "No":
        filtered = filtered[~filtered["has_link"]]
    if exclude_uk:
        filtered = filtered[~filtered["country_codes"].apply(lambda codes: "GBR" in set(codes))]
    if exclude_non_country:
        filtered = filtered[filtered["has_country_geography"]]
    if min_annual_giving is not None and min_annual_giving > 0:
        filtered = filtered[filtered["annual_giving_mid"].fillna(-1) >= min_annual_giving]
    if search.strip():
        query = search.strip().lower()
        filtered = filtered[filtered["name"].str.lower().str.contains(re.escape(query), na=False)]
    return filtered


def build_country_allocation_table(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "record_id",
        "funder_name",
        "source",
        "country_name",
        "country_iso3",
        "annual_giving_mid",
        "country_count_for_funder",
        "country_weight",
        "allocated_annual_giving",
        "tags",
        "grant_range_mid",
        "data_quality_score",
        "grant_range_available",
        "annual_giving_currency",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for _, row in df.iterrows():
        countries = row.get("countries", []) or []
        codes = row.get("country_codes", []) or []
        count = min(len(countries), len(codes))
        if count == 0:
            continue
        annual_mid = row.get("annual_giving_mid")
        country_weight = 1 / count
        allocated = annual_mid * country_weight if pd.notna(annual_mid) else None
        for country_name, country_iso3 in zip(countries, codes):
            rows.append(
                {
                    "record_id": row["record_id"],
                    "funder_name": row["name"],
                    "source": row["source"],
                    "country_name": country_name,
                    "country_iso3": country_iso3,
                    "annual_giving_mid": annual_mid,
                    "country_count_for_funder": count,
                    "country_weight": country_weight,
                    "allocated_annual_giving": allocated,
                    "tags": row.get("thematic_tags", []),
                    "grant_range_mid": row.get("grant_range_mid"),
                    "data_quality_score": row.get("data_quality_score", 0),
                    "grant_range_available": row.get("grant_range_available", False),
                    "annual_giving_currency": row.get("annual_giving_currency"),
                }
            )
    return pd.DataFrame(rows, columns=columns)


def build_tag_allocation_table(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "record_id",
        "funder_name",
        "source",
        "tag",
        "annual_giving_mid",
        "tag_count_for_funder",
        "tag_weight",
        "allocated_annual_giving",
        "annual_giving_currency",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for _, row in df.iterrows():
        annual_mid = row.get("annual_giving_mid")
        tags = row.get("thematic_tags", []) or []
        if not tags:
            continue
        tag_weight = 1 / len(tags)
        allocated = annual_mid * tag_weight if pd.notna(annual_mid) else None
        for tag in tags:
            rows.append(
                {
                    "record_id": row["record_id"],
                    "funder_name": row["name"],
                    "source": row["source"],
                    "tag": tag,
                    "annual_giving_mid": annual_mid,
                    "tag_count_for_funder": len(tags),
                    "tag_weight": tag_weight,
                    "allocated_annual_giving": allocated,
                    "annual_giving_currency": row.get("annual_giving_currency"),
                }
            )
    return pd.DataFrame(rows, columns=columns)


def build_office_to_geography_links(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "origin_country",
        "origin_iso3",
        "origin_latitude",
        "origin_longitude",
        "origin_confidence",
        "origin_reason",
        "destination_country",
        "destination_iso3",
        "destination_latitude",
        "destination_longitude",
        "funder_count",
        "estimated_annual_giving",
        "sample_funders",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for _, row in df.iterrows():
        origin = infer_origin_country_for_selected_org(row)
        origin_iso3 = stringify(origin.get("origin_country_iso3"))
        origin_country = stringify(origin.get("origin_country_name"))
        if not origin_iso3 or not origin_country:
            continue
        origin_centroid = country_to_centroid(origin_iso3)
        if not origin_centroid:
            continue

        country_names = row.get("countries", []) or []
        country_codes = row.get("country_codes", []) or []
        count = min(len(country_names), len(country_codes))
        if count == 0:
            continue

        annual_mid = row.get("annual_giving_mid")
        allocated = annual_mid / count if pd.notna(annual_mid) and count else None
        for destination_country, destination_iso3 in zip(country_names, country_codes):
            if origin_iso3 == destination_iso3:
                continue
            destination_centroid = country_to_centroid(destination_iso3)
            if not destination_centroid:
                continue
            rows.append(
                {
                    "record_id": row["record_id"],
                    "funder_name": row["name"],
                    "origin_country": origin_country,
                    "origin_iso3": origin_iso3,
                    "origin_latitude": origin_centroid[0],
                    "origin_longitude": origin_centroid[1],
                    "origin_confidence": origin.get("origin_confidence", ""),
                    "origin_reason": origin.get("origin_reason", ""),
                    "destination_country": destination_country,
                    "destination_iso3": destination_iso3,
                    "destination_latitude": destination_centroid[0],
                    "destination_longitude": destination_centroid[1],
                    "allocated_annual_giving": allocated,
                }
            )
    if not rows:
        return pd.DataFrame(columns=columns)

    links = pd.DataFrame(rows)
    summary_rows = []
    group_columns = [
        "origin_country",
        "origin_iso3",
        "origin_latitude",
        "origin_longitude",
        "origin_confidence",
        "origin_reason",
        "destination_country",
        "destination_iso3",
        "destination_latitude",
        "destination_longitude",
    ]
    for group_key, group in links.groupby(group_columns, dropna=False):
        values = group["allocated_annual_giving"].dropna()
        summary_rows.append(
            {
                **dict(zip(group_columns, group_key)),
                "funder_count": int(group["record_id"].nunique()),
                "estimated_annual_giving": float(values.sum()) if not values.empty else None,
                "sample_funders": ", ".join(group["funder_name"].drop_duplicates().head(3)),
            }
        )
    return pd.DataFrame(summary_rows, columns=columns).sort_values(
        ["funder_count", "estimated_annual_giving"], ascending=False, na_position="last"
    )


def _sum_or_none(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.sum())


def _median_or_none(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.median())


def build_country_summary(df: pd.DataFrame) -> pd.DataFrame:
    allocation = build_country_allocation_table(df)
    columns = [
        "country",
        "iso3",
        "latitude",
        "longitude",
        "funder_count",
        "estimated_annual_giving",
        "median_annual_giving",
        "funders_with_grant_range",
        "data_coverage_score",
        "sample_funders",
        "top_tags",
    ]
    if allocation.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for (country, iso3), group in allocation.groupby(["country_name", "country_iso3"], dropna=False):
        tags = Counter(tag for tag_list in group["tags"] for tag in normalize_to_list(tag_list))
        centroid = country_to_centroid(iso3)
        latitude, longitude = centroid if centroid else (None, None)
        rows.append(
            {
                "country": country,
                "iso3": iso3,
                "latitude": latitude,
                "longitude": longitude,
                "funder_count": int(group["record_id"].nunique()),
                "estimated_annual_giving": _sum_or_none(group["allocated_annual_giving"]),
                "median_annual_giving": _median_or_none(group["annual_giving_mid"]),
                "funders_with_grant_range": int(group[group["grant_range_available"]]["record_id"].nunique()),
                "data_coverage_score": float(group["data_quality_score"].mean() * 100),
                "sample_funders": ", ".join(group["funder_name"].drop_duplicates().head(3)),
                "top_tags": ", ".join(tag for tag, _ in tags.most_common(3)),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("funder_count", ascending=False)


def build_term_count(df: pd.DataFrame, column: str) -> pd.DataFrame:
    counter = Counter()
    if df.empty or column not in df:
        return pd.DataFrame(columns=["term", "count"])
    for values in df[column]:
        counter.update(normalize_to_list(values))
    return pd.DataFrame(counter.most_common(), columns=["term", "count"])


def build_non_country_summary(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["geography", "type", "funder_count", "sample_funders", "top_tags"]
    if df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for _, row in df.iterrows():
        labels = [(label, "region") for label in row.get("regions", [])]
        labels.extend((label, "non-country geography") for label in row.get("non_country_geographies", []))
        for label, label_type in labels:
            rows.append(
                {
                    "geography": label,
                    "type": label_type,
                    "record_id": row["record_id"],
                    "funder_name": row["name"],
                    "tags": row.get("thematic_tags", []),
                }
            )
    if not rows:
        return pd.DataFrame(columns=columns)

    exploded = pd.DataFrame(rows)
    summary_rows = []
    for (label, label_type), group in exploded.groupby(["geography", "type"]):
        tag_counter = Counter(tag for tags in group["tags"] for tag in tags)
        summary_rows.append(
            {
                "geography": label,
                "type": label_type,
                "funder_count": int(group["record_id"].nunique()),
                "sample_funders": ", ".join(group["funder_name"].drop_duplicates().head(3)),
                "top_tags": ", ".join(tag for tag, _ in tag_counter.most_common(3)),
            }
        )
    return pd.DataFrame(summary_rows, columns=columns).sort_values("funder_count", ascending=False)


def availability_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["field", "status", "count"])
    rows = []
    checks = [
        ("annual_giving", "annual_giving_available"),
        ("grant_range", "grant_range_available"),
        ("success_rate", "success_rate_informative"),
        ("website/application link", "has_link"),
    ]
    for label, column in checks:
        if column not in df:
            continue
        available = int(df[column].sum())
        missing = int(len(df) - available)
        rows.append({"field": label, "status": "available / informative", "count": available})
        rows.append({"field": label, "status": "missing / placeholder", "count": missing})
    return pd.DataFrame(rows)


def make_table_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "name": df["name"],
            "source": df["source"],
            "country / region": df["geo_terms"].apply(lambda values: ", ".join(values)),
            "thematic tags": df["thematic_tags"].apply(lambda values: ", ".join(values)),
            "annual_giving": df["annual_giving"],
            "annual_giving_mid": df["annual_giving_mid"],
            "grant_range": df["grant_range"],
            "grant_range_mid": df["grant_range_mid"],
            "average_grant": df["average_grant"],
            "success_rate": df.apply(
                lambda row: row["success_rate"] if row["success_rate_informative"] else "",
                axis=1,
            ),
            "website/application link": df["best_link"],
            "email": df["email"],
            "data quality": df["data_quality"],
        }
    )


def make_ranking_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    ranked = df[df["annual_giving_mid"].notna()].sort_values("annual_giving_mid", ascending=False)
    if ranked.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "funder name": ranked["name"],
            "source": ranked["source"],
            "annual_giving": ranked["annual_giving"],
            "annual_giving_mid": ranked["annual_giving_mid"],
            "grant_range": ranked["grant_range"],
            "grant_range_mid": ranked["grant_range_mid"],
            "countries / regions": ranked["geo_terms"].apply(lambda values: ", ".join(values)),
            "tags": ranked["thematic_tags"].apply(lambda values: ", ".join(values)),
            "website/application link": ranked["best_link"],
        }
    )
