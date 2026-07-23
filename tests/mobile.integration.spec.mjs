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

async function mockSearchDependencies(page, onSearch, formats = ["Standard"]) {
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
    body: JSON.stringify({ formats }),
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

test("mobile format chips send every selected format to the search", async ({ page }) => {
  let searchUrl = "";
  await mockSearchDependencies(page, route => {
    searchUrl = route.request().url();
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(emptySearch) });
  }, ["IMAX", "Dolby Cinema", "Standard"]);

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  // Wait for the ZIP-triggered movie refresh to finish before choosing a
  // movie and its formats, matching the order a user sees in the UI.
  await expect(page.locator("#movieStatus")).toContainText("1 movie");
  await page.locator("#movieInput").fill("Test Movie");
  await page.locator("#movieInput").press("Tab");

  const imax = page.getByRole("button", { name: "IMAX", exact: true });
  const dolby = page.getByRole("button", { name: "Dolby Cinema", exact: true });
  await expect(page.locator("#formatStatus")).toContainText("3 formats for this movie.");
  await expect(imax).toBeVisible();
  await imax.click();
  await dolby.click();
  await expect(imax).toHaveAttribute("aria-pressed", "true");
  await expect(dolby).toHaveAttribute("aria-pressed", "true");

  await page.locator("#searchButton").click();
  await expect.poll(() => searchUrl).toContain("format=IMAX%2CDolby+Cinema");
});

test("stale movie responses do not replace options for newer criteria", async ({ page }) => {
  let movieRequestCount = 0;
  let releaseFirstMovieRequest;
  let markFirstMovieRequestStarted;
  let firstMovieRequestFulfilled = false;
  const firstMovieRequestStarted = new Promise(resolve => { markFirstMovieRequestStarted = resolve; });
  const firstMovieRequestGate = new Promise(resolve => { releaseFirstMovieRequest = resolve; });

  await page.route("**/api/theatres*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ place: "Testville", theatres: [] }),
  }));
  await page.route("**/api/movies*", async route => {
    movieRequestCount += 1;
    if (movieRequestCount === 1) {
      markFirstMovieRequestStarted();
      await firstMovieRequestGate;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ movies: [{ title: "Stale Movie" }] }),
      });
      firstMovieRequestFulfilled = true;
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ movies: [{ title: "Current Movie" }] }),
    });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await firstMovieRequestStarted;
  await page.locator("#radiusInput").fill("10");
  await expect(page.locator("#movieStatus")).toContainText("1 movie");

  releaseFirstMovieRequest();
  await expect.poll(() => firstMovieRequestFulfilled).toBe(true);
  await page.locator("#movieInput").focus();
  await expect(page.getByRole("option", { name: "Current Movie", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "Stale Movie", exact: true })).toHaveCount(0);
});

test("mobile results visibly highlight seats that match the filter", async ({ page }) => {
  const matchingSearch = {
    ...emptySearch,
    matches: [{
      theatre: { name: "Test Cinema", address: "1 Main St", distanceMiles: 1, source: "Fandango" },
      movieTitle: "Test Movie",
      date: "2026-07-22",
      time: "19:00",
      displayTime: "7:00 PM",
      format: "IMAX",
      amenities: "Reserved seating",
      seatMap: {
        availableSeatCount: 2,
        totalSeatCount: 2,
        layout: {
          width: 100,
          height: 50,
          seats: [
            { id: "A1", status: "A", type: "standard", x: 10, y: 10, width: 10, height: 10, matched: true },
            { id: "A2", status: "A", type: "wheelchair", x: 30, y: 10, width: 10, height: 10, matched: false },
          ],
        },
      },
    }],
  };
  await mockSearchDependencies(page, route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(matchingSearch),
  }));

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await page.locator("#movieInput").fill("Test Movie");
  await page.locator("#searchButton").click();

  const matchedSeat = page.locator(".real-seat.matched");
  await expect(matchedSeat).toHaveCount(1);
  await expect(matchedSeat).toHaveCSS("background-color", "rgb(201, 58, 58)");
  const accessibleSeat = page.locator('.real-seat[title="A2 - available"]');
  await expect(accessibleSeat).toHaveClass(/available/);
});
