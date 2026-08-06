"""Traceable deterministic programme-area and geography enrichment.

The configuration in this module is the single active taxonomy/rule source.
Rules are deliberately deterministic and must not be described as AI. Raw values
are returned separately from normalized and inferred values.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence


RULE_VERSION = "deterministic-enrichment-v2"
DEFAULT_REVIEW_THRESHOLD = 0.55


class RuleConfigurationError(ValueError):
    """Raised when an enrichment rule cannot be compiled or validated."""


@dataclass(frozen=True)
class RegexRule:
    """One declared enrichment rule with its pattern, category and confidence."""
    rule_id: str
    target_field: str
    target_category: str
    pattern: str
    weight: float = 0.80
    positive_context: tuple[str, ...] = ()
    negative_context: tuple[str, ...] = (
        r"\bnot\b", r"\bno\b", r"\bwithout\b", r"\bexclude[sd]?\b",
        r"\bdoes\s+not\b", r"\bdo\s+not\b",
    )
    context_window: int = 48
    enabled: bool = True
    case_sensitive: bool = False
    rule_version: str = RULE_VERSION
    use_boundaries: bool = True
    ambiguous: bool = False
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledRule:
    """A compiled enrichment rule ready for matching."""
    config: RegexRule
    pattern: re.Pattern[str]
    positive_context: tuple[re.Pattern[str], ...]
    negative_context: tuple[re.Pattern[str], ...]


PROGRAMME_TAXONOMY = (
    "Arts & Culture",
    "Citizenship, Social Justice & Public Affairs",
    "Civil society, Voluntarism & Non-Profit Sector",
    "Diversity & Inclusion",
    "Education",
    "Environment/Climate",
    "Food, Agriculture & Nutrition",
    "Health",
    "Human/Civil Rights",
    "Humanitarian & Disaster Relief",
    "Peace & Conflict Resolution",
    "Sciences & Research",
    "Socio-economic Development, Poverty",
    "Youth/Children Development",
    "tech-enablement",
)


PROGRAMME_SOURCE_ALIASES = {
    "arts and culture": "Arts & Culture",
    "arts & culture": "Arts & Culture",
    "arts/culture/heritage/science": ("Arts & Culture", "Sciences & Research"),
    "citizenship, social justice & public affairs": "Citizenship, Social Justice & Public Affairs",
    "policy development": "Citizenship, Social Justice & Public Affairs",
    "civil society, voluntarism & non-profit sector": "Civil society, Voluntarism & Non-Profit Sector",
    "other charities or voluntary bodies": "Civil society, Voluntarism & Non-Profit Sector",
    "general charitable purposes": "Civil society, Voluntarism & Non-Profit Sector",
    "diversity & inclusion": "Diversity & Inclusion",
    "education": "Education",
    "education/training": "Education",
    "environment/climate": "Environment/Climate",
    "environment/conservation/heritage": "Environment/Climate",
    "animals": "Environment/Climate",
    "animal-related": "Environment/Climate",
    "nature": "Environment/Climate",
    "water": "Environment/Climate",
    "food, agriculture & nutrition": "Food, Agriculture & Nutrition",
    "health": "Health",
    "the advancement of health or saving of lives": "Health",
    "amateur sport": "Health",
    "recreation, sport & well-being": "Health",
    "human/civil rights": "Human/Civil Rights",
    "human rights/religious or racial harmony/equality or diversity": (
        "Human/Civil Rights", "Diversity & Inclusion"
    ),
    "humanitarian & disaster relief": "Humanitarian & Disaster Relief",
    "overseas aid/famine relief": "Humanitarian & Disaster Relief",
    "disaster relief": "Humanitarian & Disaster Relief",
    "peace & conflict resolution": "Peace & Conflict Resolution",
    "sciences & research": "Sciences & Research",
    "sponsors or undertakes research": "Sciences & Research",
    "socio-economic development": "Socio-economic Development, Poverty",
    "socio-economic development, poverty": "Socio-economic Development, Poverty",
    "the prevention or relief of poverty": "Socio-economic Development, Poverty",
    "accommodation/housing": "Socio-economic Development, Poverty",
    "economic/community development/employment": "Socio-economic Development, Poverty",
    "employment/workforce": "Socio-economic Development, Poverty",
    "social/human services": "Socio-economic Development, Poverty",
    "children/young people": "Youth/Children Development",
    "youth/children development": "Youth/Children Development",
    "technology": "tech-enablement",
    "digital transformation": "tech-enablement",
    "digital infrastructure": "tech-enablement",
    "digital public infrastructure": "tech-enablement",
    "digital skills": "tech-enablement",
    "digital literacy": "tech-enablement",
    "open data": "tech-enablement",
    "civic tech": "tech-enablement",
    "civic technology": "tech-enablement",
    "govtech": "tech-enablement",
    "edtech": "tech-enablement",
    "education technology": "tech-enablement",
    "learning technology": "tech-enablement",
    "stem": "tech-enablement",
    "engineering": "tech-enablement",
    "ict": "tech-enablement",
    "computer science": "tech-enablement",
    "cybersecurity": "tech-enablement",
    "cyber security": "tech-enablement",
    "software": "tech-enablement",
    "artificial intelligence": "tech-enablement",
    "technology transfer": "tech-enablement",
    "scientific research and technology transfer": "tech-enablement",
    "tech-enablement": "tech-enablement",
}


def _programme_rule(rule_id: str, category: str, pattern: str, weight: float = 0.8, **kwargs):
    """Declare one programme-area classification rule."""
    return RegexRule(rule_id, "programme_area", category, pattern, weight=weight, **kwargs)


PROGRAMME_RULES = (
    _programme_rule("programme.education", "Education", r"education(?:al)?|learn(?:ing|ers?)?|schools?|scholarships?|students?|teachers?|\bSTEM\b|training", 0.85),
    _programme_rule("programme.health", "Health", r"health(?:care)?|medical|diseases?|illness(?:es)?|sanitation|well[- ]?being|sports?|recreation", 0.85),
    _programme_rule("programme.environment", "Environment/Climate", r"climate|emissions?|carbon|energy transition|biodiversity|nature conservation|wildlife|water (?:security|supply)|agroecology|pollution", 0.85),
    _programme_rule("programme.humanitarian", "Humanitarian & Disaster Relief", r"humanitarian|disaster relief|emergency response|refugees?|asylum seekers?|forced migration|foreign aid", 0.85),
    _programme_rule("programme.poverty", "Socio-economic Development, Poverty", r"poverty|low[- ]income|homeless(?:ness)?|economic development|community development|employment|workforce|social services?|disadvantaged", 0.85),
    _programme_rule("programme.rights", "Human/Civil Rights", r"human rights?|civil rights?|women['’]?s rights?|gender justice|press freedom|\bLGBTI\+?\b|feminist", 0.85),
    _programme_rule("programme.children", "Youth/Children Development", r"children|childhood|child|youths?|young people|infants?|neonatal", 0.85),
    _programme_rule("programme.gender_inclusion", "Diversity & Inclusion", r"diversity|inclusion|inclusive|gender equality|accessibility|minority groups?|\bLGBTQ\w*|neurodivergent|racial harmony", 0.80),
    _programme_rule("programme.food", "Food, Agriculture & Nutrition", r"food security|food banks?|agriculture|nutrition|farming|diets?", 0.85),
    _programme_rule("programme.arts", "Arts & Culture", r"arts?|culture|cultural|museums?|exhibitions?|music|artists?|heritage|theatres?", 0.80),
    _programme_rule("programme.research", "Sciences & Research", r"research|scientific|sciences?|\bPhD\b|academia|universit(?:y|ies)", 0.80),
    _programme_rule("programme.civic", "Citizenship, Social Justice & Public Affairs", r"democracy|civil society|civic|citizenship|public affairs|advocacy|journalism|social cohesion|public policy", 0.80),
    _programme_rule("programme.nonprofit", "Civil society, Voluntarism & Non-Profit Sector", r"philanthropy|philanthropic|fundraising|donors?|grant[- ]making|non[- ]profits?|voluntarism", 0.75),
    _programme_rule("programme.peace", "Peace & Conflict Resolution", r"peacebuilding|peace work|conflict resolution|conflict sensitivity", 0.85),
    # Tech Enablement requires a concrete technology capability,
    # infrastructure, application or transfer activity. Generic "innovation"
    # is intentionally absent: alone it is too broad to be a technology signal.
    _programme_rule("programme.technology_core", "tech-enablement", r"technology|technological|digital(?:isation|ization| transformation| inclusion)?|software|data science|artificial intelligence|machine learning|\bAI\b|\bIT\b(?=\s+(?:systems?|services?|infrastructure|support))", 0.80),
    _programme_rule("programme.technology_infrastructure", "tech-enablement", r"digital (?:public )?infrastructure|digital (?:skills?|literacy|capabilit(?:y|ies)|access|connectivity)|broadband|internet access|data (?:infrastructure|governance|platforms?)|open data", 0.80),
    _programme_rule("programme.technology_public_interest", "tech-enablement", r"civic tech(?:nology)?|govtech|public[- ]interest technology|assistive technology", 0.80),
    _programme_rule("programme.technology_learning", "tech-enablement", r"edtech|education(?:al)? technology|learning technology|computer science|coding", 0.80),
    _programme_rule("programme.technology_security_transfer", "tech-enablement", r"cyber ?security|cyber safety|robotics|(?:technology|tech) transfer|technical research(?:\s+(?:and|&))?\s+(?:technology|tech) transfer", 0.80),
    _programme_rule("programme.technology_stem_engineering", "tech-enablement", r"\bSTEM\b|engineering", 0.80),
    _programme_rule("programme.technology_computing", "tech-enablement", r"\bICT\b|computing|computer(?: science|s?| programming| games? development| equipment| literacy)|digital fabrication|makerspaces?|maker spaces?|fab labs?", 0.80),
    _programme_rule("programme.weak_social", "Socio-economic Development, Poverty", r"social change|social innovation", 0.45),
)


GEOGRAPHY_TAXONOMY = {
    "Worldwide": {"code": "GLOBAL", "macro_region": "Worldwide", "scope": "global"},
    "DACH region": {"code": "DACH", "macro_region": "Europe (DACH)", "scope": "regional"},
    "United Kingdom": {"code": "GB", "macro_region": "Europe (Western / General)", "scope": "country"},
    "England": {"code": "GB-ENG", "macro_region": "Europe (Western / General)", "scope": "constituent_country"},
    "Scotland": {"code": "GB-SCT", "macro_region": "Europe (Western / General)", "scope": "constituent_country"},
    "Wales": {"code": "GB-WLS", "macro_region": "Europe (Western / General)", "scope": "constituent_country"},
    "Northern Ireland": {"code": "GB-NIR", "macro_region": "Europe (Western / General)", "scope": "constituent_country"},
    "Germany": {"code": "DE", "macro_region": "Europe (DACH)", "scope": "country"},
    "Austria": {"code": "AT", "macro_region": "Europe (DACH)", "scope": "country"},
    "Switzerland": {"code": "CH", "macro_region": "Europe (DACH)", "scope": "country"},
    "Denmark": {"code": "DK", "macro_region": "Europe (Nordic Region)", "scope": "country"},
    "Norway": {"code": "NO", "macro_region": "Europe (Nordic Region)", "scope": "country"},
    "Sweden": {"code": "SE", "macro_region": "Europe (Nordic Region)", "scope": "country"},
    "Finland": {"code": "FI", "macro_region": "Europe (Nordic Region)", "scope": "country"},
    "Ukraine": {"code": "UA", "macro_region": "Europe (Central & Eastern / Balkans)", "scope": "country"},
    "France": {"code": "FR", "macro_region": "Europe (Western / General)", "scope": "country"},
    "Netherlands": {"code": "NL", "macro_region": "Europe (Western / General)", "scope": "country"},
    "Belgium": {"code": "BE", "macro_region": "Europe (Western / General)", "scope": "country"},
    "Ireland": {"code": "IE", "macro_region": "Europe (Western / General)", "scope": "country"},
    "United States": {"code": "US", "macro_region": "North America", "scope": "country"},
    "Canada": {"code": "CA", "macro_region": "North America", "scope": "country"},
    "Ghana": {"code": "GH", "macro_region": "Africa / Sub-Saharan Africa", "scope": "country"},
    "Kenya": {"code": "KE", "macro_region": "Africa / Sub-Saharan Africa", "scope": "country"},
    "Tanzania": {"code": "TZ", "macro_region": "Africa / Sub-Saharan Africa", "scope": "country"},
    "Uganda": {"code": "UG", "macro_region": "Africa / Sub-Saharan Africa", "scope": "country"},
    "South Africa": {"code": "ZA", "macro_region": "Africa / Sub-Saharan Africa", "scope": "country"},
    "India": {"code": "IN", "macro_region": "Asia & Pacific", "scope": "country"},
    "Bangladesh": {"code": "BD", "macro_region": "Asia & Pacific", "scope": "country"},
    "Nepal": {"code": "NP", "macro_region": "Asia & Pacific", "scope": "country"},
    "Jordan": {"code": "JO", "macro_region": "Middle East & North Africa (MENA)", "scope": "country"},
    "Georgia": {"code": "GE", "macro_region": "Europe (Central & Eastern / Balkans)", "scope": "country"},
}


COUNTRY_CODE_TO_NAME = {
    details["code"]: name
    for name, details in GEOGRAPHY_TAXONOMY.items()
    if details["scope"] in {"country", "constituent_country"}
}
COUNTRY_CODE_TO_NAME.update({"GB": "United Kingdom", "UK": "United Kingdom"})


def _geo_rule(rule_id: str, target: str, pattern: str, weight: float = 0.9, **kwargs):
    """Declare one geography classification rule."""
    return RegexRule(rule_id, "geographic_focus", target, pattern, weight=weight, **kwargs)


GEOGRAPHY_RULES = (
    _geo_rule("geo.global", "Worldwide", r"worldwide|global(?:ly)?|international(?:ly)?|\bworld\b", 0.95),
    _geo_rule("geo.dach", "DACH region", r"\bDACH\b|german[- ]speaking (?:countries|region)", 0.90),
    _geo_rule("geo.uk", "United Kingdom", r"United Kingdom|Great Britain|\bU\.?K\.?\b|UK[- ]wide|nationwide|London|English", 0.95),
    _geo_rule("geo.uk_counties", "United Kingdom", r"Cumbria|Lancashire|Oxfordshire|Cambridgeshire|Cornwall|Somerset|Wales", 0.85),
    _geo_rule("geo.england", "England", r"\bEngland\b", 0.95),
    _geo_rule("geo.scotland", "Scotland", r"\bScotland\b", 0.95),
    _geo_rule("geo.wales", "Wales", r"\bWales\b", 0.95),
    _geo_rule("geo.northern_ireland", "Northern Ireland", r"Northern Ireland", 0.95),
    _geo_rule("geo.germany", "Germany", r"Germany|German|Deutschland", 0.95),
    _geo_rule("geo.austria", "Austria", r"Austria|Austrian|Österreich", 0.95),
    _geo_rule("geo.switzerland", "Switzerland", r"Switzerland|Swiss|Schweiz|Suisse", 0.95),
    _geo_rule("geo.denmark", "Denmark", r"Denmark|Danish", 0.95),
    _geo_rule("geo.norway", "Norway", r"Norway|Norwegian", 0.95),
    _geo_rule("geo.sweden", "Sweden", r"Sweden|Swedish", 0.95),
    _geo_rule("geo.finland", "Finland", r"Finland|Finnish", 0.95),
    _geo_rule("geo.ukraine", "Ukraine", r"Ukraine|Ukrainian", 0.95),
    _geo_rule("geo.france", "France", r"France|French", 0.95),
    _geo_rule("geo.netherlands", "Netherlands", r"Netherlands|Dutch", 0.95),
    _geo_rule("geo.belgium", "Belgium", r"Belgium|Belgian", 0.95),
    _geo_rule("geo.ireland", "Ireland", r"Ireland|Irish", 0.95),
    _geo_rule("geo.us", "United States", r"United States|U\.?S\.?A\.?|American", 0.95),
    _geo_rule("geo.canada", "Canada", r"Canada|Canadian", 0.95),
    _geo_rule("geo.ghana", "Ghana", r"Ghana|Ghanaian", 0.95),
    _geo_rule("geo.kenya", "Kenya", r"Kenya|Kenyan", 0.95),
    _geo_rule("geo.tanzania", "Tanzania", r"Tanzania|Tanzanian", 0.95),
    _geo_rule("geo.uganda", "Uganda", r"Uganda|Ugandan", 0.95),
    _geo_rule("geo.south_africa", "South Africa", r"South Africa|South African", 0.95),
    _geo_rule("geo.india", "India", r"India|Indian", 0.95),
    _geo_rule("geo.bangladesh", "Bangladesh", r"Bangladesh|Bangladeshi", 0.95),
    _geo_rule("geo.nepal", "Nepal", r"Nepal|Nepalese|Nepali", 0.95),
    _geo_rule("geo.jordan_ambiguous", "Jordan", r"Jordan", 0.60, ambiguous=True),
    _geo_rule("geo.georgia_ambiguous", "Georgia", r"Georgia", 0.60, ambiguous=True),
)


def compile_rules(rules: Sequence[RegexRule]) -> tuple[CompiledRule, ...]:
    """Validate and compile every enabled rule, failing with its stable rule ID."""
    compiled = []
    seen = set()
    for rule in rules:
        if not rule.enabled:
            continue
        if not rule.rule_id or rule.rule_id in seen:
            raise RuleConfigurationError(f"Duplicate or empty rule_id: {rule.rule_id!r}")
        if not 0 <= rule.weight <= 1:
            raise RuleConfigurationError(f"Rule {rule.rule_id} has weight outside 0..1")
        if rule.context_window < 0:
            raise RuleConfigurationError(f"Rule {rule.rule_id} has a negative context window")
        seen.add(rule.rule_id)
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        source_pattern = rule.pattern
        if rule.use_boundaries:
            source_pattern = rf"(?<![A-Za-z0-9])(?:{source_pattern})(?![A-Za-z0-9])"
        try:
            compiled.append(CompiledRule(
                rule,
                re.compile(source_pattern, flags),
                tuple(re.compile(value, flags) for value in rule.positive_context),
                tuple(re.compile(value, flags) for value in rule.negative_context),
            ))
        except re.error as exc:
            raise RuleConfigurationError(f"Invalid regex in rule {rule.rule_id}: {exc}") from exc
    return tuple(compiled)


COMPILED_PROGRAMME_RULES = compile_rules(PROGRAMME_RULES)
COMPILED_GEOGRAPHY_RULES = compile_rules(GEOGRAPHY_RULES)


def _excerpt(text: str, start: int, end: int, radius: int = 60) -> str:
    """Return the matched excerpt retained as classification evidence."""
    return re.sub(r"\s+", " ", text[max(0, start - radius):min(len(text), end + radius)]).strip()


def apply_rules(
    fields: Mapping[str, Any],
    rules: Sequence[CompiledRule],
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    """Apply deterministic rules and return classifications, evidence and review state."""
    evidence: list[dict[str, Any]] = []
    accepted: list[tuple[CompiledRule, str, int, int]] = []
    negative_matches = 0
    for field_name, raw_value in fields.items():
        if raw_value is None:
            continue
        values = raw_value if isinstance(raw_value, (list, tuple, set)) else [raw_value]
        for raw_text in values:
            if isinstance(raw_text, (dict, list)):
                raw_text = json.dumps(raw_text, ensure_ascii=False)
            text = str(raw_text).strip()
            if not text:
                continue
            for compiled in rules:
                rule = compiled.config
                for match in compiled.pattern.finditer(text):
                    window_start = max(0, match.start() - rule.context_window)
                    window_end = min(len(text), match.end() + rule.context_window)
                    context = text[window_start:window_end]
                    has_positive_context = not compiled.positive_context or any(
                        pattern.search(context) for pattern in compiled.positive_context
                    )
                    negative = any(pattern.search(context) for pattern in compiled.negative_context)
                    accepted_match = has_positive_context and not negative
                    item = {
                        "rule_id": rule.rule_id,
                        "rule_version": rule.rule_version,
                        "matched_keyword": match.group(0),
                        "matched_text": match.group(0),
                        "source_field": field_name,
                        "target_category": rule.target_category,
                        "rule_weight": rule.weight,
                        "context_excerpt": _excerpt(text, match.start(), match.end()),
                        "negative_context": negative,
                        "accepted": accepted_match,
                        "ambiguous": rule.ambiguous,
                    }
                    evidence.append(item)
                    if accepted_match:
                        accepted.append((compiled, field_name, match.start(), match.end()))
                    elif negative:
                        negative_matches += 1

    scores: dict[str, float] = {}
    for compiled, _, _, _ in accepted:
        category = compiled.config.target_category
        scores[category] = round(min(1.0, scores.get(category, 0.0) + compiled.config.weight), 3)

    categories = sorted(scores)
    overlaps = 0
    for index, (_, field_a, start_a, end_a) in enumerate(accepted):
        for _, field_b, start_b, end_b in accepted[index + 1:]:
            if field_a == field_b and start_a < end_b and start_b < end_a:
                overlaps += 1

    conflict_pairs = set()
    for compiled, _, _, _ in accepted:
        category = compiled.config.target_category
        for other in compiled.config.conflicts_with:
            if other in scores:
                conflict_pairs.add(tuple(sorted((category, other))))

    ambiguous = any(compiled.config.ambiguous for compiled, _, _, _ in accepted)
    weak_only = bool(accepted) and max(item.config.weight for item, _, _, _ in accepted) < review_threshold
    confidence = round(sum(scores.values()) / len(scores), 3) if scores else 0.0
    review_reasons = []
    if weak_only:
        review_reasons.append("weak_rules_only")
    if conflict_pairs:
        review_reasons.append("conflicting_categories")
    if negative_matches:
        review_reasons.append("negative_context_detected")
    if ambiguous:
        review_reasons.append("ambiguous_geography")
    if categories and confidence < review_threshold:
        review_reasons.append("below_confidence_threshold")
    return {
        "categories": categories,
        "scores": scores,
        "method": "deterministic_regex" if evidence else "unavailable",
        "confidence": confidence,
        "evidence": evidence,
        "review_required": bool(review_reasons),
        "review_reasons": review_reasons,
        "overlapping_match_count": overlaps,
        "conflicting_categories": [list(pair) for pair in sorted(conflict_pairs)],
        "insufficient_source_text": not any(str(value or "").strip() for value in fields.values()),
        "rule_version": RULE_VERSION,
    }


def _as_values(value: Any) -> list[str]:
    """Coerce a source field into a list of candidate values."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if parsed != value:
                return _as_values(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return [value]
    if isinstance(value, Mapping):
        for key in ("classification_desc", "name", "title", "value", "country"):
            if value.get(key):
                return [str(value[key])]
        return []
    if isinstance(value, Iterable):
        result = []
        for item in value:
            result.extend(_as_values(item))
        return result
    return [str(value)]


def normalize_programme_sources(values: Iterable[Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Normalize source-declared programme areas onto the taxonomy."""
    categories = set()
    evidence = []
    for raw in _as_values(list(values)):
        normalized_raw = re.sub(r"\s+", " ", raw).strip()
        for alias, target in PROGRAMME_SOURCE_ALIASES.items():
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)
            match = pattern.search(normalized_raw)
            if not match:
                continue
            targets = target if isinstance(target, tuple) else (target,)
            for category in targets:
                categories.add(category)
                evidence.append({
                    "rule_id": f"source.programme.{re.sub(r'[^a-z0-9]+', '_', alias).strip('_')}",
                    "rule_version": RULE_VERSION,
                    "matched_keyword": match.group(0),
                    "matched_text": match.group(0),
                    "source_field": "source_classification",
                    "target_category": category,
                    "rule_weight": 1.0,
                    "context_excerpt": _excerpt(normalized_raw, match.start(), match.end()),
                    "negative_context": False,
                    "accepted": True,
                    "ambiguous": False,
                })
    return sorted(categories), evidence


def classify_programme_fields(fields: Mapping[str, Any], source_values: Iterable[Any] = ()) -> dict[str, Any]:
    """Infer programme areas deterministically, emitting evidence and confidence."""
    source_categories, source_evidence = normalize_programme_sources(source_values)
    inferred = apply_rules(fields, COMPILED_PROGRAMME_RULES)
    # A source-provided category remains a source category even if the same words
    # also trigger a regex. Do not duplicate it as an inferred classification.
    inferred["categories"] = [
        category for category in inferred["categories"] if category not in source_categories
    ]
    inferred["scores"] = {
        category: score for category, score in inferred["scores"].items()
        if category not in source_categories
    }
    inferred["source_categories"] = source_categories
    inferred["source_evidence"] = source_evidence
    inferred["method"] = "+".join(filter(None, [
        "source_normalization" if source_categories else "",
        "deterministic_regex" if inferred["evidence"] else "",
    ])) or "unavailable"
    return inferred


def normalize_country_value(value: Any) -> dict[str, Any] | None:
    """Normalize a country label, flagging ambiguous names for review."""
    text = str(value or "").strip()
    if not text:
        return None
    upper = text.upper().replace(".", "")
    if upper in COUNTRY_CODE_TO_NAME:
        name = COUNTRY_CODE_TO_NAME[upper]
        return {"name": name, **GEOGRAPHY_TAXONOMY[name]}
    aliases = {
        "uk": "United Kingdom", "u.k": "United Kingdom", "great britain": "United Kingdom",
        "britain": "United Kingdom", "deutschland": "Germany", "österreich": "Austria",
        "schweiz": "Switzerland", "suisse": "Switzerland", "international": "Worldwide",
        "global": "Worldwide", "worldwide": "Worldwide", "uk wide": "United Kingdom",
        "uk-wide": "United Kingdom", "nationwide": "United Kingdom",
    }
    name = aliases.get(text.casefold())
    if name:
        return {"name": name, **GEOGRAPHY_TAXONOMY[name]}
    for canonical, details in GEOGRAPHY_TAXONOMY.items():
        if text.casefold() == canonical.casefold():
            return {"name": canonical, **details}
    return None


def normalize_geography_sources(values: Iterable[Any], source_field: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize source-declared operating and funding geographies."""
    normalized = {}
    evidence = []
    for raw_item in values:
        code = raw_item.get("countryCode") if isinstance(raw_item, Mapping) else None
        raw_values = _as_values(raw_item)
        candidate = normalize_country_value(code) if code else None
        if not candidate:
            for raw in raw_values:
                candidate = normalize_country_value(raw)
                if candidate:
                    break
        if not candidate:
            continue
        normalized[(candidate["code"], candidate["name"])] = candidate
        evidence.append({
            "rule_id": "source.geography.normalization",
            "rule_version": RULE_VERSION,
            "matched_keyword": str(code or (raw_values[0] if raw_values else "")),
            "matched_text": str(raw_item),
            "source_field": source_field,
            "target_category": candidate["name"],
            "rule_weight": 1.0,
            "context_excerpt": str(raw_item)[:160],
            "negative_context": False,
            "accepted": True,
            "ambiguous": False,
        })
    return sorted(normalized.values(), key=lambda item: (item["name"], item["code"])), evidence


def classify_geography_fields(fields: Mapping[str, Any], source_values: Iterable[Any] = ()) -> dict[str, Any]:
    """Infer geographic focus deterministically, emitting evidence and confidence."""
    source_values = list(source_values)
    normalized_source, source_evidence = normalize_geography_sources(source_values, "source_geography")
    inferred = apply_rules(fields, COMPILED_GEOGRAPHY_RULES)
    inferred["source_normalized"] = normalized_source
    inferred["source_evidence"] = source_evidence
    inferred["method"] = "+".join(filter(None, [
        "source_normalization" if normalized_source else "",
        "deterministic_regex" if inferred["evidence"] else "",
    ])) or "unavailable"
    return inferred


def _source_classifications(record: Mapping[str, Any]) -> list[Any]:
    """Collect the normalized source-declared classifications."""
    details = record.get("all_details") if isinstance(record.get("all_details"), Mapping) else {}
    result = []
    result.extend(_as_values(record.get("who_what_how")))
    result.extend(_as_values(details.get("who_what_where")))
    philea = record.get("philea_info") if isinstance(record.get("philea_info"), Mapping) else {}
    result.extend(_as_values(philea.get("Programme Areas")))
    return result


def _source_geography(record: Mapping[str, Any]) -> list[Any]:
    """Collect the normalized source-declared geographies."""
    details = record.get("all_details") if isinstance(record.get("all_details"), Mapping) else {}
    result = []
    for field in ("CharityAoOCountryContinent", "CharityAoORegion", "CharityAoOLocalAuthority"):
        value = details.get(field)
        if isinstance(value, list):
            result.extend(value)
    philea = record.get("philea_info") if isinstance(record.get("philea_info"), Mapping) else {}
    result.extend(_as_values(philea.get("Geographic Focus")))
    result.extend(_as_values(philea.get("areaOfOperation")))
    return result


def enrich_organization(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return additive organization enrichment without mutating raw source data."""
    raw_record = record.get("raw_cc_data")
    source_record = raw_record if isinstance(raw_record, Mapping) and raw_record else record
    details = source_record.get("all_details") if isinstance(source_record.get("all_details"), Mapping) else {}
    philea = source_record.get("philea_info") if isinstance(source_record.get("philea_info"), Mapping) else {}
    programme_sources = _source_classifications(source_record)
    programme_fields = {
        "description": source_record.get("description"),
        "activities": details.get("activities") or details.get("charitable_objects"),
        "about": philea.get("About"),
        "mission": philea.get("Mission"),
        "programme_area_text": philea.get("Programme Areas"),
    }
    programme = classify_programme_fields(programme_fields, programme_sources)

    geography_sources = _source_geography(source_record)
    geography_fields = {
        "stated_geographic_focus": philea.get("Geographic Focus"),
        "area_of_operation": philea.get("areaOfOperation"),
        "activities": details.get("activities") or details.get("charitable_objects"),
        "description": source_record.get("description"),
        "mission": philea.get("Mission"),
    }
    geography = classify_geography_fields(geography_fields, geography_sources)

    headquarters_raw = record.get("country") or details.get("address_country")
    headquarters = normalize_country_value(headquarters_raw)
    region_raw = record.get("state") or details.get("address_line_three")
    headquarters_region = str(region_raw).strip() if region_raw else None
    return {
        "programme_areas_source": programme["source_categories"],
        "programme_areas_inferred": programme["categories"],
        "programme_area_scores": programme["scores"],
        "programme_area_method": programme["method"],
        "programme_area_evidence": programme["source_evidence"] + programme["evidence"],
        "programme_area_review_required": programme["review_required"],
        "geographic_focus_source": geography_sources,
        "geographic_focus_inferred": geography["categories"],
        "headquarters_country": headquarters["name"] if headquarters else None,
        "headquarters_region": headquarters_region,
        "geography_method": geography["method"],
        "geography_confidence": geography["confidence"],
        "geography_evidence": geography["source_evidence"] + geography["evidence"],
        "geography_review_required": geography["review_required"],
        "enrichment_rule_version": RULE_VERSION,
        "enrichment_review_reasons": sorted(set(
            programme["review_reasons"] + geography["review_reasons"]
        )),
        "insufficient_source_text": (
            programme["insufficient_source_text"] and geography["insufficient_source_text"]
        ),
    }


def enrich_grant(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return additive grant enrichment with beneficiary geography kept distinct."""
    programme_source = _as_values(record.get("programme_area_source"))
    programme = classify_programme_fields({"description": record.get("description")}, programme_source)
    beneficiary_raw = record.get("beneficiary_geography")
    try:
        beneficiary_values = json.loads(beneficiary_raw) if isinstance(beneficiary_raw, str) else beneficiary_raw
    except json.JSONDecodeError:
        beneficiary_values = []
    beneficiary_values = beneficiary_values if isinstance(beneficiary_values, list) else []
    beneficiary_normalized, beneficiary_evidence = normalize_geography_sources(
        beneficiary_values, "beneficiary_geography"
    )
    focus = classify_geography_fields({"description": record.get("description")})
    return {
        "programme_area_inferred": programme["categories"],
        "programme_area_scores": programme["scores"],
        "programme_area_method": programme["method"],
        "programme_area_evidence": programme["source_evidence"] + programme["evidence"],
        "programme_area_review_required": programme["review_required"],
        "beneficiary_geography_normalized": beneficiary_normalized,
        "geographic_focus_inferred": focus["categories"],
        "geography_method": (
            "source_normalization" if beneficiary_normalized else focus["method"]
        ),
        "geography_confidence": 1.0 if beneficiary_normalized else focus["confidence"],
        "geography_evidence": beneficiary_evidence + focus["evidence"],
        "geography_review_required": focus["review_required"],
        "enrichment_rule_version": RULE_VERSION,
        "enrichment_review_reasons": sorted(set(
            programme["review_reasons"] + focus["review_reasons"]
        )),
        "insufficient_source_text": (
            programme["insufficient_source_text"] and focus["insufficient_source_text"]
            and not beneficiary_values
        ),
    }


def build_enrichment_report(
    organizations: Sequence[Mapping[str, Any]], grants: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Return coverage statistics. Coverage is not a measurement of accuracy."""
    records = [("organization", item) for item in organizations] + [("grant", item) for item in grants]
    total = len(records)
    by_source: dict[str, int] = {}
    by_method: dict[str, int] = {}
    metrics = {
        "total_records_processed": total,
        "records_with_source_programme_areas": 0,
        "records_with_inferred_programme_areas": 0,
        "records_without_programme_area": 0,
        "records_with_source_geographic_focus": 0,
        "records_with_inferred_geographic_focus": 0,
        "records_without_geographic_focus": 0,
        "records_requiring_review": 0,
        "records_with_conflicting_classifications": 0,
        "records_with_insufficient_source_text": 0,
    }
    for record_type, item in records:
        source_programmes = _as_values(item.get("programme_areas_source") or item.get("programme_area_source"))
        inferred_programmes = _as_values(item.get("programme_areas_inferred") or item.get("programme_area_inferred"))
        source_geo = _as_values(item.get("geographic_focus_source") or item.get("beneficiary_geography"))
        inferred_geo = _as_values(item.get("geographic_focus_inferred"))
        metrics["records_with_source_programme_areas"] += bool(source_programmes)
        metrics["records_with_inferred_programme_areas"] += bool(inferred_programmes)
        metrics["records_without_programme_area"] += not (source_programmes or inferred_programmes)
        metrics["records_with_source_geographic_focus"] += bool(source_geo)
        metrics["records_with_inferred_geographic_focus"] += bool(inferred_geo)
        metrics["records_without_geographic_focus"] += not (source_geo or inferred_geo)
        review = bool(item.get("programme_area_review_required") or item.get("geography_review_required"))
        metrics["records_requiring_review"] += review
        reasons = _as_values(item.get("enrichment_review_reasons"))
        metrics["records_with_conflicting_classifications"] += "conflicting_categories" in reasons
        metrics["records_with_insufficient_source_text"] += bool(item.get("insufficient_source_text"))
        source = str(item.get("source") or record_type)
        by_source[source] = by_source.get(source, 0) + 1
        for method_field in ("programme_area_method", "geography_method"):
            method = str(item.get(method_field) or "unavailable")
            by_method[method] = by_method.get(method, 0) + 1
    return {
        **metrics,
        "classifications_grouped_by_source": dict(sorted(by_source.items())),
        "classifications_grouped_by_method": dict(sorted(by_method.items())),
        "coverage_is_accuracy": False,
        "predictive_accuracy_measured": False,
        "rule_version": RULE_VERSION,
    }


def rule_configuration() -> dict[str, Any]:
    """Expose serializable rule configuration for documentation/debugging."""
    return {
        "rule_version": RULE_VERSION,
        "programme_taxonomy": list(PROGRAMME_TAXONOMY),
        "geography_taxonomy": GEOGRAPHY_TAXONOMY,
        "programme_rules": [asdict(item) for item in PROGRAMME_RULES],
        "geography_rules": [asdict(item) for item in GEOGRAPHY_RULES],
    }
