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
  await expect(page.locator("#excludeAccessibleInput")).toBeChecked();

  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    dateWidths: [...document.querySelectorAll('input[type="date"]')].map(input => input.getBoundingClientRect().width),
    inputFontSize: getComputedStyle(document.querySelector("#zipInput")).fontSize,
    githubShadow: getComputedStyle(document.querySelector(".github-link")).boxShadow,
  }));

  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.dateWidths.every(width => width >= 200)).toBe(true);
  expect(layout.inputFontSize).toBe("16px");
  expect(layout.githubShadow).toBe("none");
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
  expect(searchUrl).toContain("excludeAccessible=1");
});

test("result sorting defaults to earliest and reruns the search when changed", async ({ page }) => {
  const searchSorts = [];
  let releaseLatestSearch;
  let markLatestSearchStarted;
  const latestSearchStarted = new Promise(resolve => { markLatestSearchStarted = resolve; });
  const latestSearchGate = new Promise(resolve => { releaseLatestSearch = resolve; });
  const matchingSearch = {
    ...emptySearch,
    matches: [{
      theatre: { name: "Test Cinema", address: "1 Main St", distanceMiles: 1, source: "Fandango" },
      movieTitle: "Test Movie",
      date: "2026-08-01",
      time: "19:00",
      displayTime: "7:00 PM",
      format: "Standard",
      amenities: "Reserved seating",
      seatMap: {
        availableSeatCount: 1,
        totalSeatCount: 1,
        layout: {
          width: 30,
          height: 30,
          seats: [{ id: "A1", status: "A", type: "standard", x: 10, y: 10, width: 10, height: 10, matched: true }],
        },
      },
    }],
  };
  await mockSearchDependencies(page, async route => {
    const sort = new URL(route.request().url()).searchParams.get("sort");
    searchSorts.push(sort);
    if (sort === "latest") {
      markLatestSearchStarted();
      await latestSearchGate;
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(matchingSearch) });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await page.locator("#movieInput").fill("Test Movie");
  await page.locator("#searchButton").click();

  const sortInput = page.locator("#sortInput");
  await expect(sortInput).toBeVisible();
  await expect(sortInput).toHaveValue("earliest");
  await page.locator("#resultsToolbar").scrollIntoViewIfNeeded();
  const beforeReorder = await page.evaluate(() => ({
    resultsMarkup: document.querySelector("#results").innerHTML,
    resultsHeight: document.querySelector("#results").getBoundingClientRect().height,
    scrollY: window.scrollY,
  }));

  await sortInput.selectOption("latest");
  await latestSearchStarted;
  await expect(page.locator("#sortStatus")).toHaveText("Reordering…");
  await expect(sortInput).toBeDisabled();
  await expect(page.locator("#searchButton")).toBeEnabled();
  await expect(page.locator("#searchButton")).toHaveText("Find matching seats");
  const duringReorder = await page.evaluate(() => ({
    resultsMarkup: document.querySelector("#results").innerHTML,
    minHeight: Number.parseFloat(document.querySelector("#results").style.minHeight),
    scrollY: window.scrollY,
  }));
  expect(duringReorder.resultsMarkup).toBe(beforeReorder.resultsMarkup);
  expect(duringReorder.minHeight).toBeGreaterThanOrEqual(beforeReorder.resultsHeight);
  expect(duringReorder.scrollY).toBe(beforeReorder.scrollY);

  releaseLatestSearch();
  await expect.poll(() => searchSorts.at(-1)).toBe("latest");
  await expect(sortInput).toBeEnabled();
  await expect(page.locator("#sortStatus")).toBeEmpty();
  await page.waitForTimeout(50);
  const afterReorder = await page.evaluate(() => ({
    scrollY: window.scrollY,
    animationName: getComputedStyle(document.querySelector(".result")).animationName,
  }));
  expect(afterReorder.scrollY).toBe(beforeReorder.scrollY);
  expect(afterReorder.animationName).toBe("none");

  await sortInput.selectOption("nearest");
  await expect.poll(() => searchSorts.at(-1)).toBe("nearest");
  expect(searchSorts[0]).toBe("earliest");

  const dimensions = await sortInput.evaluate(select => ({
    height: select.getBoundingClientRect().height,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  expect(dimensions.height).toBeGreaterThanOrEqual(44);
  expect(dimensions.overflow).toBeLessThanOrEqual(0);
});

test("showtimes from one theatre share a single theatre card", async ({ page }) => {
  const theatre = { name: "Test Cinema", address: "1 Main St", distanceMiles: 1, source: "Fandango" };
  const makeMatch = (time, displayTime, amenity) => ({
    theatre,
    movieTitle: "Test Movie",
    date: "2026-08-01",
    time,
    displayTime,
    format: "Standard",
    amenities: `Reserved seating, ${amenity}`,
    seatMap: {
      availableSeatCount: 1,
      totalSeatCount: 1,
      layout: {
        width: 30,
        height: 30,
        seats: [{ id: time, status: "A", type: "standard", x: 10, y: 10, width: 10, height: 10, matched: true }],
      },
    },
  });
  await mockSearchDependencies(page, route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      ...emptySearch,
      matches: [makeMatch("19:00", "7:00 PM", "Closed caption"), makeMatch("20:00", "8:00 PM", "Open caption")],
    }),
  }));

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await page.locator("#movieInput").fill("Test Movie");
  await page.locator("#searchButton").click();

  await expect(page.locator(".theatre-result")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Test Cinema", exact: true })).toHaveCount(1);
  await expect(page.locator(".result-showtime")).toHaveCount(2);
  await expect(page.getByText("2 matching showtimes", { exact: true })).toBeVisible();
  await expect(page.locator(".theatre-disclosure")).not.toHaveAttribute("open", "");
  await expect(page.locator(".result-showtime").first()).toBeHidden();
  await expect(page.locator(".result-amenities").first()).toBeHidden();
  const theatreSummary = page.locator(".theatre-summary");
  expect(await theatreSummary.evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await theatreSummary.click();
  await expect(page.locator(".theatre-disclosure")).toHaveAttribute("open", "");
  await expect(page.locator(".result-showtime")).toHaveCount(2);
  await expect(page.locator(".result-amenities").first()).toContainText("Closed caption");
  await expect(page.locator(".result-amenities").first()).toBeVisible();
  await expect(page.getByText("Showtime details", { exact: true })).toHaveCount(0);
});

test("pagination counts theatre cards and keeps a theatre on one page", async ({ page }) => {
  const makeMatch = (name, address, time) => ({
    theatre: { name, address, distanceMiles: 1, source: "Fandango" },
    movieTitle: "Test Movie",
    date: "2026-08-01",
    time,
    displayTime: time,
    format: "Standard",
    amenities: "Reserved seating",
    seatMap: {
      availableSeatCount: 1,
      totalSeatCount: 1,
      layout: {
        width: 30,
        height: 30,
        seats: [{ id: `${name}-${time}`, status: "A", type: "standard", x: 10, y: 10, width: 10, height: 10, matched: true }],
      },
    },
  });
  await mockSearchDependencies(page, route => {
    const requestedPage = Number(new URL(route.request().url()).searchParams.get("page"));
    const response = requestedPage === 2
      ? {
          ...emptySearch,
          page: 2,
          pageSize: 2,
          hasPreviousPage: true,
          matches: [makeMatch("Cinema C", "3 Main St", "12:00")],
        }
      : {
          ...emptySearch,
          pageSize: 2,
          hasNextPage: true,
          matches: [
            makeMatch("Cinema A", "1 Main St", "10:00"),
            makeMatch("Cinema A", "1 Main St", "13:00"),
            makeMatch("Cinema B", "2 Main St", "11:00"),
          ],
        };
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(response) });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await page.locator("#movieInput").fill("Test Movie");
  await page.locator("#searchButton").click();

  await expect(page.locator(".theatre-result")).toHaveCount(2);
  await expect(page.locator("#summary")).toContainText("Showing 2 theatres with 3 matching showtimes");
  await page.getByRole("button", { name: "Next page of theatres" }).click();

  await expect(page.locator(".theatre-result")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Cinema C", exact: true })).toBeVisible();
  await expect(page.locator("#summary")).toContainText("Showing 1 theatre with 1 matching showtime");
  await expect(page.getByRole("button", { name: "Previous page of theatres" })).toBeEnabled();
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
    accessibleSeatsExcluded: true,
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
            { id: "A3", status: "U", type: "wheelchair", x: 50, y: 10, width: 10, height: 10, matched: false },
            { id: "A4", status: "A", type: "companion", x: 70, y: 10, width: 10, height: 10, matched: false },
            { id: "A5", status: "U", type: "companion", x: 90, y: 10, width: 10, height: 10, matched: false },
          ],
        },
      },
    }],
  };
  await mockSearchDependencies(page, route => {
    const accessibleSeatsExcluded = new URL(route.request().url()).searchParams.get("excludeAccessible") === "1";
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...matchingSearch, accessibleSeatsExcluded }),
    });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await page.locator("#movieInput").fill("Test Movie");
  await page.locator("#searchButton").click();
  await page.locator(".theatre-summary").click();

  const matchedSeat = page.locator(".real-seat.matched");
  await expect(matchedSeat).toHaveCount(1);
  await expect(matchedSeat).toHaveCSS("background-color", "rgb(201, 58, 58)");
  const matchedGlow = await matchedSeat.evaluate(seat => {
    const style = getComputedStyle(seat, "::before");
    return { backgroundColor: style.backgroundColor, filter: style.filter };
  });
  expect(matchedGlow.backgroundColor).toBe("rgba(201, 58, 58, 0.62)");
  expect(matchedGlow.filter).toBe("blur(2px)");
  const wheelchairSeat = page.locator('.real-seat[title="A2 - available - wheelchair - excluded by filter"]');
  await expect(wheelchairSeat).toHaveClass(/accessible/);
  await expect(wheelchairSeat).toHaveCSS("background-color", "rgb(199, 206, 216)");
  expect(await wheelchairSeat.evaluate(seat => getComputedStyle(seat, "::before").content)).toBe("none");
  const unavailableWheelchairSeat = page.locator('.real-seat[title="A3 - unavailable - wheelchair"]');
  await expect(unavailableWheelchairSeat).toHaveCSS("background-color", "rgb(199, 206, 216)");
  const companionSeat = page.locator('.real-seat[title="A4 - available - companion - excluded by filter"]');
  await expect(companionSeat).toHaveClass(/accessible/);
  await expect(companionSeat).toHaveCSS("background-color", "rgb(199, 206, 216)");
  const unavailableCompanionSeat = page.locator('.real-seat[title="A5 - unavailable - companion"]');
  await expect(unavailableCompanionSeat).toHaveCSS("background-color", "rgb(199, 206, 216)");
  await expect(page.getByText("Unavailable or excluded", { exact: true })).toBeVisible();
  await expect(page.getByText("Accessible seating", { exact: true })).toHaveCount(0);

  await page.getByText("Exclude accessible, companion, & wheelchair seats from matches", { exact: true }).click();
  await expect(page.locator("#excludeAccessibleInput")).not.toBeChecked();
  await page.locator("#searchButton").click();
  await page.locator(".theatre-summary").click();

  const includedWheelchairSeat = page.locator('.real-seat[title="A2 - available - wheelchair"]');
  await expect(includedWheelchairSeat).toHaveCSS("background-color", "rgb(37, 99, 199)");
  const wheelchairGlow = await includedWheelchairSeat.evaluate(seat => {
    const style = getComputedStyle(seat, "::before");
    return { backgroundColor: style.backgroundColor, filter: style.filter };
  });
  expect(wheelchairGlow.backgroundColor).toBe("rgba(37, 99, 199, 0.62)");
  expect(wheelchairGlow.filter).toBe("blur(2px)");
  const includedCompanionSeat = page.locator('.real-seat[title="A4 - available - companion"]');
  await expect(includedCompanionSeat).toHaveCSS("background-color", "rgb(37, 99, 199)");
  const companionGlow = await includedCompanionSeat.evaluate(seat => {
    const style = getComputedStyle(seat, "::before");
    return { backgroundColor: style.backgroundColor, filter: style.filter };
  });
  expect(companionGlow.backgroundColor).toBe("rgba(37, 99, 199, 0.62)");
  expect(companionGlow.filter).toBe("blur(2px)");
  await expect(page.getByText("Accessible seating", { exact: true })).toBeVisible();
  await expect(page.getByText("Unavailable", { exact: true })).toBeVisible();
});

test("accessible matches retain both accessible and matching states", async ({ page }) => {
  const accessibleMatchSearch = {
    ...emptySearch,
    accessibleSeatsExcluded: false,
    matches: [{
      theatre: { name: "Test Cinema", address: "1 Main St", distanceMiles: 1, source: "Fandango" },
      movieTitle: "Test Movie",
      date: "2026-07-22",
      displayTime: "7:00 PM",
      format: "Standard",
      amenities: "Reserved seating",
      seatMap: {
        availableSeatCount: 1,
        totalSeatCount: 1,
        layout: {
          width: 50,
          height: 30,
          seats: [
            { id: "WC1", status: "A", type: "wheelchair", x: 10, y: 10, width: 10, height: 10, matched: true },
          ],
        },
      },
    }],
  };
  await mockSearchDependencies(page, route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(accessibleMatchSearch),
  }));

  await page.goto("/?excludeAccessible=0");
  await page.locator("#zipInput").fill("10001");
  await page.locator("#movieInput").fill("Test Movie");
  await page.locator("#searchButton").click();
  await page.locator(".theatre-summary").click();

  const accessibleMatch = page.locator('.real-seat[title="WC1 - available - wheelchair"]');
  const combinedBackground = await accessibleMatch.evaluate(seat => getComputedStyle(seat).backgroundImage);
  expect(combinedBackground).toContain("rgb(201, 58, 58)");
  expect(combinedBackground).toContain("rgb(37, 99, 199)");
  expect(combinedBackground).toContain("rgb(143, 31, 38)");
  expect(combinedBackground).toContain("rgb(23, 74, 151)");
  await expect(accessibleMatch).toHaveCSS("border-color", "rgba(0, 0, 0, 0)");
  await expect(accessibleMatch).toHaveCSS("box-shadow", "none");
  const combinedGlow = await accessibleMatch.evaluate(seat => {
    const style = getComputedStyle(seat, "::before");
    return { backgroundImage: style.backgroundImage, filter: style.filter };
  });
  expect(combinedGlow.backgroundImage).toContain("rgba(201, 58, 58, 0.62)");
  expect(combinedGlow.backgroundImage).toContain("rgba(37, 99, 199, 0.62)");
  expect(combinedGlow.filter).toBe("blur(2px)");
  await expect(page.getByText("Accessible match", { exact: true })).toHaveCount(0);
});
