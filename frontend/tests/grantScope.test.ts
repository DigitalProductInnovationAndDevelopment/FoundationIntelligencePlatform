import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_DONOR_DIRECTORY_STATE,
  applyDonorDirectoryStateToParams,
  applyGrantScopeToParams,
  donorDirectoryStateFromUrl,
  grantScopeFromUrl,
  grantScopeToApiParams,
  normalizeGrantScope,
} from "../src/lib/grantScope.js";

test("grant scope survives a complete URL round trip", () => {
  const source = normalizeGrantScope({
    beneficiaryCountry: "ng",
    beneficiaryGeographies: ["West Africa", "West Africa"],
    programmeAreas: ["tech-enablement"],
    dateFrom: "2024-01-01",
    dateTo: "2026-06-30",
    currency: "auto",
    donor: " Indigo ",
    recipient: "Digital school",
    sources: ["360Giving", "Philea"],
  });
  const params = applyGrantScopeToParams(new URLSearchParams("unrelated=kept"), source, {
    persistEmptySources: true,
  });

  assert.deepEqual(grantScopeFromUrl(params), source);
  assert.equal(params.get("unrelated"), "kept");
  assert.equal(params.get("funder_country"), "NG");
});

test("an explicit empty source scope differs from a missing source parameter", () => {
  const defaults = ["360Giving", "Philea"];

  assert.deepEqual(grantScopeFromUrl(new URLSearchParams(), defaults).sources, defaults);
  assert.deepEqual(grantScopeFromUrl(new URLSearchParams("grant_sources="), defaults).sources, []);
});

test("directory search, status, sort, page and selected donor survive copied URLs", () => {
  const state = {
    search: "indigo",
    status: "observed_only" as const,
    sort: "most_recently_active" as const,
    page: 4,
    donorKey: "360giving:funder:source_id:abc",
  };
  const params = applyDonorDirectoryStateToParams(new URLSearchParams(), state);

  assert.deepEqual(donorDirectoryStateFromUrl(params), state);
});

test("invalid route values normalize to safe defaults", () => {
  const scope = grantScopeFromUrl(new URLSearchParams(
    "funder_country=NGA&grant_from=yesterday&grant_currency=gbp&grant_sources=360Giving",
  ));
  const directory = donorDirectoryStateFromUrl(new URLSearchParams(
    "donor_status=unknown&funder_sort=random&funder_page=-2",
  ));

  assert.equal(scope.beneficiaryCountry, undefined);
  assert.equal(scope.dateFrom, undefined);
  assert.equal(scope.currency, "GBP");
  assert.deepEqual(directory, DEFAULT_DONOR_DIRECTORY_STATE);
});

test("API parameters retain beneficiary semantics and explicit source scope", () => {
  const params = grantScopeToApiParams({
    beneficiaryCountry: "CH",
    beneficiaryGeographies: [],
    programmeAreas: ["tech-enablement"],
    currency: "EUR",
    sources: [],
  }, { requireCountry: true });

  assert.equal(params.get("beneficiary_country"), "CH");
  assert.equal(params.get("programme_areas"), "tech-enablement");
  assert.equal(params.get("currency"), "EUR");
  assert.equal(params.has("sources"), true);
  assert.equal(params.get("sources"), "");
});
