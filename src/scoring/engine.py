"""Configurable, deterministic and explainable target-profile relevance score."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_COMPONENTS = {
    "thematic_fit",
    "geographic_fit",
    "funding_capacity_fit",
    "historical_grant_size_fit",
    "organization_type_fit",
}


class ScoreConfigurationError(ValueError):
    """Raised when score configuration is invalid or misleading."""


@dataclass(frozen=True)
class ScoreConfiguration:
    score_version: str
    configuration_status: str
    score_target: str
    missing_data_behavior: str
    review_confidence_threshold: float
    weights: dict[str, float]
    example_target_profile: dict[str, Any]
    assumptions: tuple[str, ...]


def validate_score_configuration(raw: Mapping[str, Any]) -> ScoreConfiguration:
    version = str(raw.get("score_version") or "").strip()
    if not version:
        raise ScoreConfigurationError("score_version is required")
    status = str(raw.get("configuration_status") or "").strip()
    if status not in {"experimental", "approved"}:
        raise ScoreConfigurationError("configuration_status must be experimental or approved")
    target = str(raw.get("score_target") or "").strip()
    if not target:
        raise ScoreConfigurationError("score_target is required")
    missing_behavior = str(raw.get("missing_data_behavior") or "")
    if missing_behavior != "zero_for_missing_components":
        raise ScoreConfigurationError(
            "Only explicit zero_for_missing_components missing-data behavior is supported"
        )
    weights_raw = raw.get("weights")
    if not isinstance(weights_raw, Mapping) or not weights_raw:
        raise ScoreConfigurationError("weights must be a non-empty object")
    unknown = set(weights_raw) - SUPPORTED_COMPONENTS
    if unknown:
        raise ScoreConfigurationError(f"Unsupported score components: {', '.join(sorted(unknown))}")
    missing_components = SUPPORTED_COMPONENTS - set(weights_raw)
    if missing_components:
        raise ScoreConfigurationError(
            f"Missing score component weights: {', '.join(sorted(missing_components))}"
        )
    weights = {}
    for key, value in weights_raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ScoreConfigurationError(f"Weight {key} must be a finite number")
        if value < 0 or value > 1:
            raise ScoreConfigurationError(f"Weight {key} must be between 0 and 1")
        weights[key] = float(value)
    if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
        raise ScoreConfigurationError("Score weights must sum to 1.0")
    threshold = raw.get("review_confidence_threshold", 0.6)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
        raise ScoreConfigurationError("review_confidence_threshold must be between 0 and 1")
    profile = raw.get("example_target_profile")
    if not isinstance(profile, Mapping):
        raise ScoreConfigurationError("example_target_profile must be an object")
    assumptions = raw.get("assumptions") or []
    if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
        raise ScoreConfigurationError("assumptions must be a list of strings")
    return ScoreConfiguration(
        score_version=version,
        configuration_status=status,
        score_target=target,
        missing_data_behavior=missing_behavior,
        review_confidence_threshold=float(threshold),
        weights=weights,
        example_target_profile=dict(profile),
        assumptions=tuple(assumptions),
    )


def default_score_config_path() -> str:
    project_root = Path(__file__).resolve().parents[2]
    return os.environ.get(
        "SCORE_CONFIG_PATH", str(project_root / "config" / "scoring.example.json")
    )


def load_score_configuration(path: str | None = None) -> ScoreConfiguration:
    config_path = path or default_score_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as source:
            raw = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScoreConfigurationError(f"Cannot load score configuration {config_path}: {exc}") from exc
    return validate_score_configuration(raw)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _normalized_set(values: Any) -> set[str]:
    return {str(value).strip().casefold() for value in _list(values) if str(value).strip()}


def _normalized_geography_set(values: Any) -> set[str]:
    """Normalize scalar or source-object geography values for score comparison."""
    normalized = set()
    for value in _list(values):
        if isinstance(value, Mapping):
            candidates = [
                value.get("name"), value.get("country"), value.get("region"),
                value.get("macro_region"), value.get("value"),
            ]
        else:
            candidates = [value]
        for candidate in candidates:
            text = str(candidate or "").strip().casefold()
            if text:
                normalized.add(text)
    if normalized.intersection({"england", "scotland", "wales", "northern ireland"}):
        normalized.add("united kingdom")
    return normalized


def _component(score, weight, confidence, evidence, missing_reason=None):
    available = score is not None
    return {
        "score": round(float(score), 2) if available else None,
        "weight": weight,
        "weighted_score": round(float(score) * weight, 2) if available else None,
        "confidence": round(float(confidence), 3) if available else 0.0,
        "available": available,
        "evidence": evidence,
        "missing_reason": missing_reason,
    }


def score_relevance(
    organization: Mapping[str, Any],
    target_profile: Mapping[str, Any],
    grant_statistics: Mapping[str, Any] | None = None,
    configuration: ScoreConfiguration | None = None,
) -> dict[str, Any]:
    """Score organization relevance; never infer or predict donation probability."""
    config = configuration or load_score_configuration()
    grants = grant_statistics or {}
    components = {}
    missing_inputs = []

    target_programmes = _normalized_set(target_profile.get("programme_areas"))
    organization_programme_source = _normalized_set(organization.get("programme_areas_source"))
    organization_programme_inferred = _normalized_set(organization.get("programme_areas_inferred"))
    # A donor's observed grant history is direct evidence of the themes it
    # actually funded.  It supplements (but never overwrites) the profile's
    # own classification, which can be sparse for a registry-only profile.
    organization_programme_observed = _normalized_set(
        organization.get("observed_grant_programme_areas")
    )
    organization_programmes = (
        organization_programme_source
        | organization_programme_inferred
        | organization_programme_observed
    )
    if target_programmes and organization_programmes:
        matches = target_programmes & organization_programmes
        score = 100 * len(matches) / len(target_programmes)
        confidence = (
            0.95 if matches & organization_programme_source
            else 0.85 if matches & organization_programme_observed
            else 0.70
        )
        components["thematic_fit"] = _component(score, config.weights["thematic_fit"], confidence, [{
            "target_values": sorted(target_programmes),
            "organization_values": sorted(organization_programmes),
            "observed_grant_values": sorted(organization_programme_observed),
            "matched_values": sorted(matches),
            "method": "exact_normalized_category_overlap_including_observed_grant_history",
        }])
    else:
        reason = "target programme areas missing" if not target_programmes else "organization programme areas missing"
        components["thematic_fit"] = _component(None, config.weights["thematic_fit"], 0, [], reason)
        missing_inputs.append(reason)

    target_geographies = _normalized_geography_set(target_profile.get("geographies"))
    organization_geo_source = _normalized_geography_set(
        organization.get("geographic_focus_source")
    )
    organization_geo_inferred = _normalized_geography_set(
        organization.get("geographic_focus_inferred")
    )
    # These are recipient/beneficiary geographies observed in transactions,
    # not a claim about the funder's headquarters or legal operating address.
    organization_geo_observed = _normalized_geography_set(
        organization.get("observed_beneficiary_geographies")
    )
    organization_geographies = (
        organization_geo_source
        | organization_geo_inferred
        | organization_geo_observed
    )
    if target_geographies and organization_geographies:
        matches = target_geographies & organization_geographies
        score = 100 * len(matches) / len(target_geographies)
        confidence = (
            0.95 if matches & organization_geo_source
            else 0.85 if matches & organization_geo_observed
            else 0.70
        )
        components["geographic_fit"] = _component(score, config.weights["geographic_fit"], confidence, [{
            "target_values": sorted(target_geographies),
            "organization_values": sorted(organization_geographies),
            "observed_beneficiary_values": sorted(organization_geo_observed),
            "matched_values": sorted(matches),
            "method": "normalized_geography_overlap_including_observed_beneficiary_history",
            "headquarters_excluded": True,
        }])
    else:
        reason = "target geographies missing" if not target_geographies else "organization geographic focus missing"
        components["geographic_fit"] = _component(None, config.weights["geographic_fit"], 0, [], reason)
        missing_inputs.append(reason)

    minimum_expenditure = target_profile.get("minimum_annual_expenditure")
    expenditure = organization.get("latest_expenditure", organization.get("annual_expenditure"))
    if isinstance(minimum_expenditure, (int, float)) and minimum_expenditure > 0 and isinstance(expenditure, (int, float)):
        score = min(100.0, max(0.0, float(expenditure) / float(minimum_expenditure) * 100))
        components["funding_capacity_fit"] = _component(score, config.weights["funding_capacity_fit"], 0.90, [{
            "annual_expenditure": expenditure,
            "target_minimum": minimum_expenditure,
            "method": "capped_ratio",
        }])
    else:
        reason = "target minimum expenditure missing" if not minimum_expenditure else "annual expenditure missing"
        components["funding_capacity_fit"] = _component(None, config.weights["funding_capacity_fit"], 0, [], reason)
        missing_inputs.append(reason)

    target_average = target_profile.get("target_average_grant_amount")
    requested_currency = str(target_profile.get("currency") or "").upper()
    average_grant = grants.get("average_amount")
    grant_currency = str(grants.get("currency") or "").upper()
    if (
        isinstance(target_average, (int, float)) and target_average > 0
        and isinstance(average_grant, (int, float))
        and requested_currency and requested_currency == grant_currency
    ):
        score = min(100.0, max(0.0, float(average_grant) / float(target_average) * 100))
        components["historical_grant_size_fit"] = _component(
            score, config.weights["historical_grant_size_fit"], 0.90, [{
                "observed_average_grant": average_grant,
                "target_average_grant": target_average,
                "currency": requested_currency,
                "grant_count": grants.get("grant_count", 0),
                "method": "currency_specific_capped_ratio",
            }]
        )
    else:
        if not target_average or not requested_currency:
            reason = "target average grant amount or currency missing"
        elif average_grant is None:
            reason = "historical grant amounts missing"
        else:
            reason = "grant currency does not match requested currency"
        components["historical_grant_size_fit"] = _component(
            None, config.weights["historical_grant_size_fit"], 0, [], reason
        )
        missing_inputs.append(reason)

    target_types = _normalized_set(target_profile.get("organization_types"))
    organization_type = str(organization.get("organization_type") or "").strip().casefold()
    if target_types and organization_type:
        matched = organization_type in target_types
        components["organization_type_fit"] = _component(
            100.0 if matched else 0.0,
            config.weights["organization_type_fit"],
            0.95,
            [{
                "target_values": sorted(target_types),
                "organization_value": organization_type,
                "matched": matched,
                "method": "exact_normalized_type_match",
            }],
        )
    else:
        reason = "target organization types missing" if not target_types else "organization type missing"
        components["organization_type_fit"] = _component(
            None, config.weights["organization_type_fit"], 0, [], reason
        )
        missing_inputs.append(reason)

    available = [item for item in components.values() if item["available"] and item["weight"] > 0]
    available_weight = sum(item["weight"] for item in available)
    data_completeness = round(available_weight, 3)
    if available_weight:
        # Do not renormalize partial evidence. A 100% match on only one
        # criterion is a partial fit, not a 100-point profile. Each missing
        # criterion therefore contributes zero to the weighted total.
        relevance = round(
            sum(item["score"] * item["weight"] for item in available),
            2,
        )
        confidence = round(
            sum(item["confidence"] * item["weight"] for item in available),
            3,
        )
    else:
        relevance = None
        confidence = 0.0

    return {
        "score": relevance,
        "score_target": config.score_target,
        "score_version": config.score_version,
        "configuration_status": config.configuration_status,
        "confidence": confidence,
        "data_completeness": data_completeness,
        "components": components,
        "missing_inputs": sorted(set(missing_inputs)),
        "review_required": confidence < config.review_confidence_threshold,
        "assumptions": list(config.assumptions),
        "missing_data_behavior": config.missing_data_behavior,
        "not_a_prediction": True,
    }
