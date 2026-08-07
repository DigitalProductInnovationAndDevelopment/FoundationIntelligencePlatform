import { expect, test } from "@playwright/test";

test("anonymous demo exposes the reviewed read-only UI routes", async ({ context, page }) => {
  const overviewResponsePromise = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === "/api/charities/grants/overview"
      && response.request().method() === "GET";
  });

  await page.goto("/");
  const overviewResponse = await overviewResponsePromise;
  expect(overviewResponse.status()).toBe(200);
  expect(overviewResponse.headers()["set-cookie"]).toBeUndefined();
  await expect(page.locator(".world-map-card")).toBeVisible();

  const adminResponse = await page.request.get("/api/admin/pipeline/status");
  expect(adminResponse.status()).toBe(401);
  const directoryResponse = await page.request.get("/api/charities");
  expect(directoryResponse.status()).toBe(200);
  expect(directoryResponse.headers()["set-cookie"]).toBeUndefined();
  const mutationResponse = await page.request.post("/api/admin/pipeline/trigger", {
    data: { source: "quick_consolidate" },
    headers: { "Idempotency-Key": "public-demo-browser-test" },
  });
  expect(mutationResponse.status()).toBe(401);

  expect(await context.cookies()).toEqual([]);
});
