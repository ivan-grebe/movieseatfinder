import { expect, test } from "@playwright/test";

const emptySearch = {
  matches: [],
  page: 1,
  pageSize: 20,
  hasPreviousPage: false,
  hasNextPage: false,
  checkedShowtimes: 1,
  checkedSeatMaps: 1,
};

async function mockSearchDependencies(page, onSearch) {
  await page.route("**/api/theatres*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ place: "Testville", theatres: [] }),
  }));
  await page.route("**/api/movies*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ movies: [{ title: "Test Movie" }] }),
  }));
  await page.route("**/api/formats*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ formats: ["Standard"] }),
  }));
  await page.route("**/api/search*", onSearch);
}

test("mobile form fits a narrow phone without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/");

  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    dateWidths: [...document.querySelectorAll('input[type="date"]')].map(input => input.getBoundingClientRect().width),
    inputFontSize: getComputedStyle(document.querySelector("#zipInput")).fontSize,
  }));

  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.dateWidths.every(width => width >= 200)).toBe(true);
  expect(layout.inputFontSize).toBe("16px");
});

test("mobile search keeps content stable while loading and then renders its response", async ({ page }) => {
  let releaseSearch;
  let markSearchStarted;
  const searchStarted = new Promise(resolve => { markSearchStarted = resolve; });
  await mockSearchDependencies(page, async route => {
    markSearchStarted();
    await new Promise(resolve => { releaseSearch = resolve; });
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(emptySearch) });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await page.locator("#movieInput").fill("Test Movie");

  const searchButton = page.locator("#searchButton");
  const emptyState = page.locator(".empty-state");
  await expect(emptyState).toBeVisible();
  await searchButton.click();
  await searchStarted;

  await expect(searchButton).toBeDisabled();
  await expect(searchButton).toContainText("Searching real showtimes and seat maps");
  await expect(emptyState).toBeVisible();

  releaseSearch();
  await expect(page.locator("#summary")).toContainText("No matching showtimes");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("mobile validation keeps required-field feedback at the field", async ({ page }) => {
  await page.goto("/");
  await page.locator("#searchButton").click();

  const zip = page.locator("#zipInput");
  await expect(zip).toHaveJSProperty("validationMessage", "Enter a ZIP code or allow location access first.");
  await expect(page.locator("#summary")).toBeEmpty();
});
