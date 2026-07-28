import assert from "node:assert/strict";
import test from "node:test";

import { validateOptionalNumericRange } from "../src/lib/numericRange.js";

test("optional numeric ranges accept empty and inclusive boundaries", () => {
  assert.deepEqual(validateOptionalNumericRange("", "", "Income"), {
    minimum: null,
    maximum: null,
    error: null,
  });
  assert.deepEqual(validateOptionalNumericRange("1000", "1000", "Income"), {
    minimum: 1000,
    maximum: 1000,
    error: null,
  });
});

test("optional numeric ranges reject a minimum above the maximum", () => {
  assert.deepEqual(validateOptionalNumericRange("2000", "1000", "Income"), {
    minimum: 2000,
    maximum: 1000,
    error: "Income minimum cannot exceed its maximum.",
  });
});

test("optional numeric ranges reject invalid or negative values", () => {
  assert.equal(
    validateOptionalNumericRange("not-a-number", "1000", "Expenditure").error,
    "Expenditure limits must be valid non-negative numbers.",
  );
  assert.equal(
    validateOptionalNumericRange("0", "-1", "Expenditure").error,
    "Expenditure limits must be valid non-negative numbers.",
  );
});
