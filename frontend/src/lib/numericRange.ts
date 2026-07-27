export type OptionalNumericRangeValidation = {
  minimum: number | null;
  maximum: number | null;
  error: string | null;
};

function parseOptionalNonNegativeNumber(value: string): number | null | "invalid" {
  const normalized = value.trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : "invalid";
}

export function validateOptionalNumericRange(
  minimumInput: string,
  maximumInput: string,
  label: string,
): OptionalNumericRangeValidation {
  const minimum = parseOptionalNonNegativeNumber(minimumInput);
  const maximum = parseOptionalNonNegativeNumber(maximumInput);

  if (minimum === "invalid" || maximum === "invalid") {
    return {
      minimum: minimum === "invalid" ? null : minimum,
      maximum: maximum === "invalid" ? null : maximum,
      error: `${label} limits must be valid non-negative numbers.`,
    };
  }
  if (minimum !== null && maximum !== null && minimum > maximum) {
    return {
      minimum,
      maximum,
      error: `${label} minimum cannot exceed its maximum.`,
    };
  }
  return { minimum, maximum, error: null };
}
