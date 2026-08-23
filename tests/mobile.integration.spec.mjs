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

function seatMapSvg(label = "Matches") {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="1200"><text>${label}</text><rect id="matched-seat" fill="#c93a3a" width="20" height="20"/></svg>`;
}

function makeSimpleMatch(theatreName, time) {
  return {
    theatre: { name: theatreName, address: "1 Main St", distanceMiles: 1 },
    movieTitle: "Test Movie",
    date: "2026-08-01",
    displayTime: time,
    format: "Standard",
    amenities: "Reserved seating",
    genres: [],
    seatMap: {
      availableSeatCount: 1,
      totalSeatCount: 1,
      visualSvg: seatMapSvg(),
      layout: {
        width: 30,
        height: 30,
        seats: [{ id: `${theatreName}-${time}`, status: "A", type: "standard", x: 10, y: 10, width: 10, height: 10, matched: true }],
      },
    },
  };
}

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

async function selectMovie(page, title = "Test Movie") {
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");
  const input = page.locator("#movieInput");
  await input.fill(title);
  await page.getByRole("option", { name: title, exact: true }).click();
  await expect(input).toHaveValue(title);
}

test("mobile form fits a narrow phone without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/");
  await expect(page.locator("#excludeAccessibleInput")).toBeChecked();
  await expect(page.locator("#startTimeInput")).toHaveValue("00:00");
  await expect(page.locator("#endTimeInput")).toHaveValue("23:59");

  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    inputFontSize: getComputedStyle(document.querySelector("#zipInput")).fontSize,
  }));

  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.inputFontSize).toBe("16px");
});

test("mobile seat preferences require a deliberate, reversible edit mode", async ({ page }) => {
  await mockSearchDependencies(page, route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(emptySearch),
  }));

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

  const grid = page.locator("#seatPreferenceGrid");
  const firstCell = grid.locator(".seat-cell").first();
  const editButton = page.locator("#editSeatGridButton");
  const centerButton = page.locator("#selectCenterGridButton");
  const clearButton = page.locator("#clearGridButton");
  const cancelButton = page.locator("#cancelSeatGridButton");
  const doneButton = page.locator("#doneSeatGridButton");

  await expect(editButton).toBeVisible();
  await expect(centerButton).toBeHidden();
  await expect(clearButton).toBeHidden();
  await expect(grid).toHaveClass(/is-mobile-locked/);
  await expect(page.locator("#seatPreferenceHelp")).toHaveText(
    "Tap Edit seat area to choose where you'd like to sit.",
  );
  await firstCell.dispatchEvent("click");
  await expect(firstCell).toHaveAttribute("aria-pressed", "false");

  await editButton.click();
  await expect(editButton).toBeHidden();
  await expect(grid).toHaveClass(/is-mobile-editing/);
  await expect(centerButton).toBeVisible();
  await expect(clearButton).toBeVisible();
  await expect(cancelButton).toBeVisible();
  await expect(doneButton).toBeVisible();

  await centerButton.tap();
  await clearButton.tap();

  await firstCell.click();
  await expect(firstCell).toHaveAttribute("aria-pressed", "true");
  await cancelButton.click();
  await expect(firstCell).toHaveAttribute("aria-pressed", "false");
  await expect(grid).toHaveClass(/is-mobile-locked/);

  await editButton.click();
  await firstCell.click();
  await doneButton.click();
  await expect(firstCell).toHaveAttribute("aria-pressed", "true");
  await expect(grid).toHaveClass(/is-mobile-locked/);

  await page.setViewportSize({ width: 900, height: 700 });
  await expect(editButton).toBeHidden();
  await expect(centerButton).toBeVisible();
  await expect(clearButton).toBeVisible();
  await expect(grid).not.toHaveClass(/is-mobile-locked/);
});

test("movie and seat controls stay locked until the location resolves", async ({ page }) => {
  let releaseTheatres;
  let markTheatresStarted;
  const theatresStarted = new Promise(resolve => { markTheatresStarted = resolve; });
  const theatreGate = new Promise(resolve => { releaseTheatres = resolve; });
  await page.route("**/api/theatres*", async route => {
    markTheatresStarted();
    await theatreGate;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ place: "Testville", theatres: [] }),
    });
  });
  await page.route("**/api/movies*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ movies: [{ title: "Test Movie" }] }),
  }));
  await page.goto("/");

  const movieGroup = page.locator("#movieGroup");
  const preferencesGroup = page.locator("#preferencesGroup");
  const searchButton = page.locator("#searchButton");
  for (const group of [movieGroup, preferencesGroup]) {
    await expect(group).toHaveAttribute("inert", "");
    await expect(group).toHaveAttribute("aria-disabled", "true");
    await expect(group).toHaveClass(/is-location-locked/);
  }
  expect(await page.locator("#movieInput").evaluate(input => {
    input.focus();
    return document.activeElement === input;
  })).toBe(false);
  await expect(searchButton).toBeDisabled();
  await expect(searchButton).not.toHaveAttribute("aria-busy", "true");

  await page.locator("#zipInput").fill("10001");
  await theatresStarted;
  releaseTheatres();
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

  for (const group of [movieGroup, preferencesGroup]) {
    await expect(group).not.toHaveAttribute("inert", "");
    await expect(group).not.toHaveAttribute("aria-disabled", "true");
    await expect(group).not.toHaveClass(/is-location-locked/);
  }
  await expect(searchButton).toBeEnabled();
});

test("movie options remain selectable beyond the movie card", async ({ page }) => {
  const movies = Array.from({ length: 12 }, (_, index) => ({ title: `Test Movie ${index + 1}` }));
  await page.route("**/api/theatres*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ place: "Testville", theatres: [] }),
  }));
  await page.route("**/api/movies*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ movies }),
  }));

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("12 showing");

  await page.locator("#movieInput").click();
  await expect(page.locator("#movieMenu")).toBeVisible();
  await page.getByRole("option", { name: "Test Movie 12", exact: true }).click();
  await expect(page.locator("#movieInput")).toHaveValue("Test Movie 12");
  await expect(page.locator("#movieMenu")).toBeHidden();
});

test("an unknown ZIP shows one error at the ZIP field and stops dependent loads", async ({ page }) => {
  let movieRequestCount = 0;
  await page.route("**/api/theatres*", route => route.fulfill({
    status: 400,
    contentType: "application/json",
    body: JSON.stringify({
      error: "We couldn't find that ZIP code. Check it and try again.",
      code: "location",
    }),
  }));
  await page.route("**/api/movies*", route => {
    movieRequestCount += 1;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ movies: [] }) });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("00000");
  await expect(page.locator("#locationStatus")).toHaveText(
    "We couldn't find that ZIP code. Check it and try again.",
  );
  await expect(page.locator("#theatreStatus")).toBeEmpty();
  await expect(page.locator("#movieStatus")).toBeEmpty();
  await expect(page.locator("#theatreMeta")).toBeEmpty();
  await expect(page.locator("#movieMeta")).toBeEmpty();
  await expect(page.locator("#movieGroup")).toHaveAttribute("inert", "");
  await expect(page.locator("#preferencesGroup")).toHaveAttribute("inert", "");
  await expect(page.locator("#searchButton")).toBeDisabled();
  expect(movieRequestCount).toBe(0);
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
  await selectMovie(page);

  const searchButton = page.locator("#searchButton");
  const emptyState = page.locator(".empty-state");
  await expect(emptyState).toBeVisible();
  await searchButton.click();
  await searchStarted;

  await expect(searchButton).toBeDisabled();
  await expect(searchButton).toHaveAttribute("aria-busy", "true");
  await expect(searchButton).toContainText("Loading theatres");
  await expect(searchButton.locator(".loading-dot")).toHaveCount(3);
  await expect(emptyState).toBeVisible();
  await expect(searchButton).toContainText("Checking showtimes");
  await expect(searchButton).toContainText("Checking seat maps");

  releaseSearch();
  await expect(page.locator("#summary")).toContainText("No matching showtimes");
  await expect(searchButton).not.toHaveAttribute("aria-busy", "true");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("movie search accepts a selected suggestion or an exact loaded title", async ({ page }) => {
  let searchUrl = "";
  let searchCount = 0;
  await mockSearchDependencies(page, route => {
    searchCount += 1;
    searchUrl = route.request().url();
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(emptySearch) });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

  const movieInput = page.locator("#movieInput");
  const searchButton = page.locator("#searchButton");
  await movieInput.fill("Test");
  const suggestion = page.getByRole("option", { name: "Test Movie", exact: true });
  await expect(suggestion).toBeVisible();
  await searchButton.click();

  await expect(movieInput).toHaveJSProperty(
    "validationMessage",
    "Select an exact movie from the list before searching.",
  );
  expect(searchUrl).toBe("");

  await movieInput.fill("Test Movie");
  await expect(searchButton).toBeEnabled();
  await searchButton.click();
  await expect.poll(() => searchUrl).not.toBe("");
  await expect(searchButton).toBeEnabled();
  expect(new URL(searchUrl).searchParams.get("movie")).toBe("Test Movie");
  expect(searchCount).toBe(1);

  await page.locator("#radiusInput").evaluate(input => {
    input.value = "10";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    document.querySelector("#searchButton").click();
  });
  await expect(movieInput).toBeDisabled();
  expect(searchCount).toBe(1);
  await expect(movieInput).toBeEnabled();
  await expect(movieInput).toHaveValue("Test Movie");
});

test("homepage footer opens the dedicated FAQ page", async ({ page }) => {
  await page.goto("/");

  const faqLink = page.getByRole("link", { name: "FAQ", exact: true });
  await expect(faqLink).toBeVisible();
  expect(await faqLink.evaluate(link => (
    link.closest("footer").getBoundingClientRect().top >= document.querySelector("main").getBoundingClientRect().bottom - 1
  ))).toBe(true);
  await faqLink.click();

  await expect(page).toHaveURL(/\/faq$/);
  await expect(page.getByRole("heading", { name: "Movie Seat Finder FAQ" })).toBeVisible();
  await expect(page).toHaveTitle("FAQ | Movie Seat Finder");

  const firstQuestion = page.getByText("What does Movie Seat Finder do?", { exact: true });
  const firstAnswer = page.getByText(/searches nearby movie showtimes and live seat maps/i);
  await expect(firstAnswer).toBeHidden();
  expect(await firstQuestion.evaluate(node => node.closest("summary").getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await firstQuestion.click();
  await expect(firstAnswer).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to seat finder" })).toHaveAttribute("href", "/");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});

test("theatre filter accepts only an empty, selected, or exact loaded theatre", async ({ page }) => {
  let searchUrl = "";
  let movieUrls = [];
  await page.route("**/api/theatres*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      place: "Testville",
      theatres: [{ name: "Test Cinema" }, { name: "Test Cinema East" }],
    }),
  }));
  await page.route("**/api/movies*", route => {
    movieUrls.push(route.request().url());
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ movies: [{ title: "Test Movie" }] }),
    });
  });
  await page.route("**/api/formats*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ formats: ["Standard"] }),
  }));
  await page.route("**/api/search*", route => {
    searchUrl = route.request().url();
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(emptySearch) });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#theatreMeta")).toHaveText("2 nearby");

  const theatreInput = page.locator("#theatreInput");
  await theatreInput.fill("Test");
  await expect(page.getByRole("option", { name: "Test Cinema", exact: true })).toBeVisible();
  await page.locator("#searchButton").click();
  await expect(theatreInput).toHaveJSProperty(
    "validationMessage",
    "Select an exact theatre from the list before searching.",
  );
  expect(searchUrl).toBe("");

  await theatreInput.fill("Test Cinema");
  await theatreInput.press("Tab");
  await expect.poll(() => movieUrls.at(-1)).toContain("theatre=Test+Cinema");
  await selectMovie(page);
  await page.locator("#searchButton").click();
  await expect.poll(() => searchUrl).not.toBe("");
  expect(new URL(searchUrl).searchParams.get("theatre")).toBe("Test Cinema");
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
  await selectMovie(page);

  const imax = page.getByRole("button", { name: "IMAX", exact: true });
  const dolby = page.getByRole("button", { name: "Dolby Cinema", exact: true });
  await expect(page.locator("#formatStatus")).toBeEmpty();
  await expect(imax).toBeVisible();
  await imax.click();
  await dolby.click();
  await expect(imax).toHaveAttribute("aria-pressed", "true");
  await expect(dolby).toHaveAttribute("aria-pressed", "true");

  await page.locator("#searchButton").click();
  await expect.poll(() => searchUrl).toContain("format=IMAX%2CDolby+Cinema");
  expect(searchUrl).toContain("excludeAccessible=1");
});

test("format tier guide opens and closes accessibly", async ({ page }) => {
  await mockSearchDependencies(page, route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(emptySearch),
  }));

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

  const guide = page.locator(".format-guide");
  const guideButton = page.locator("#formatGuideButton");
  await expect(guide).not.toHaveClass(/is-open/);
  await expect(guideButton).toHaveAttribute("aria-expanded", "false");
  await guideButton.click();
  await expect(guide).toHaveClass(/is-open/);
  await expect(guideButton).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#formatGuideContent")).toHaveAttribute("aria-hidden", "false");
  await expect(page.getByText("IMAX 70mm · Dolby Cinema · IMAX with Laser", { exact: true })).toBeVisible();
  await expect(page.getByText("IMAX · 70mm · AMC PRIME · Cinemark XD · Regal RPX · AMC XL", { exact: true })).toBeVisible();
  await expect(page.getByText("35mm · RealD 3D · 4DX · ScreenX · D-BOX", { exact: true })).toBeVisible();
  await expect(page.locator(".format-tier-badge")).toHaveText(["S", "A", "B", "?"]);

  await guideButton.click();
  await expect(guide).not.toHaveClass(/is-open/);
  await expect(guideButton).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#formatGuideContent")).toHaveAttribute("aria-hidden", "true");
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
      theatre: { name: "Test Cinema", address: "1 Main St", distanceMiles: 1 },
      movieTitle: "Test Movie",
      date: "2026-08-01",
      displayTime: "7:00 PM",
      format: "Standard",
      amenities: "Reserved seating",
      genres: [],
      seatMap: {
        availableSeatCount: 1,
        totalSeatCount: 1,
        visualSvg: seatMapSvg(),
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
  await selectMovie(page);
  await page.locator("#searchButton").click();

  const sortInput = page.locator("#sortInput");
  await expect(sortInput).toBeVisible();
  await expect(sortInput).toHaveValue("earliest");
  await page.locator("#resultsToolbar").scrollIntoViewIfNeeded();
  await sortInput.selectOption("latest");
  await latestSearchStarted;
  const reorderScrollY = await page.evaluate(() => window.scrollY);
  const sortStatus = page.locator("#sortStatus");
  await expect(sortStatus).toContainText("Reordering");
  await expect(sortInput).toBeDisabled();
  await expect(page.locator("#searchButton")).toBeEnabled();

  releaseLatestSearch();
  await expect.poll(() => searchSorts.at(-1)).toBe("latest");
  await expect(sortInput).toBeEnabled();
  await expect(page.locator("#sortStatus")).toBeEmpty();
  expect(await page.evaluate(() => window.scrollY)).toBe(reorderScrollY);

  await sortInput.selectOption("nearest");
  await expect.poll(() => searchSorts.at(-1)).toBe("nearest");
  expect(searchSorts[0]).toBe("earliest");

});

test("pagination shows generic loading feedback beside the page controls", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  let markSecondPageStarted;
  let releaseSecondPage;
  const secondPageStarted = new Promise(resolve => { markSecondPageStarted = resolve; });
  const secondPageGate = new Promise(resolve => { releaseSecondPage = resolve; });
  await mockSearchDependencies(page, async route => {
    const requestedPage = Number(new URL(route.request().url()).searchParams.get("page"));
    if (requestedPage === 2) {
      markSecondPageStarted();
      await secondPageGate;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          ...emptySearch,
          page: 2,
          hasPreviousPage: true,
          matches: [makeSimpleMatch("Page Two Cinema", "20:00")],
        }),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...emptySearch,
        hasNextPage: true,
        matches: [makeSimpleMatch("Page One Cinema", "19:00")],
      }),
    });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await selectMovie(page);
  await page.locator("#searchButton").click();
  await expect(page.getByRole("heading", { name: "Page One Cinema", exact: true })).toBeVisible();

  const pagination = page.locator("#pagination");
  const previousPage = page.getByRole("button", { name: "Previous page of results" });
  const nextPage = page.getByRole("button", { name: "Next page of results" });
  await nextPage.scrollIntoViewIfNeeded();
  await nextPage.click();
  await secondPageStarted;

  await expect(pagination).toHaveAttribute("aria-busy", "true");
  await expect(pagination.locator("button:disabled")).toHaveCount(2);
  await expect(previousPage).toBeDisabled();
  await expect(nextPage).toBeDisabled();
  await expect(page.locator("#sortInput")).toBeDisabled();
  await expect(pagination.locator(".pagination-label")).toHaveText("Loading...");
  await expect(page.getByRole("heading", { name: "Page One Cinema", exact: true })).toBeVisible();

  releaseSecondPage();
  await expect(page.getByRole("heading", { name: "Page Two Cinema", exact: true })).toBeVisible();
  await expect(pagination.locator(".pagination-label")).toHaveText("Page 2");
  await expect(pagination).not.toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#sortInput")).toBeEnabled();
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
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

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
      theatre: { name: "Test Cinema", address: "1 Main St", distanceMiles: 1 },
      movieTitle: "Test Movie",
      date: "2026-07-22",
      displayTime: "7:00 PM",
      format: "IMAX",
      amenities: "Reserved seating",
      genres: [],
      seatMap: {
        availableSeatCount: 2,
        totalSeatCount: 2,
        visualSvg: seatMapSvg("Unavailable / excluded"),
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
    const visualSvg = seatMapSvg(accessibleSeatsExcluded ? "Unavailable / excluded" : "Accessible Unavailable");
    const matches = matchingSearch.matches.map(match => ({
      ...match,
      seatMap: { ...match.seatMap, visualSvg },
    }));
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...matchingSearch, accessibleSeatsExcluded, matches }),
    });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await selectMovie(page);
  await page.locator("#searchButton").click();

  const seatMapImage = page.locator(".real-seat-map-image");
  await expect(seatMapImage).toBeVisible();
  await expect(seatMapImage).toHaveAttribute("alt", "Live Fandango seat map: 2 available of 2 total seats");
  await expect.poll(() => seatMapImage.evaluate(node => decodeURIComponent(node.src.split(",")[1]))).toContain("Unavailable / excluded");
  await expect.poll(() => seatMapImage.evaluate(node => decodeURIComponent(node.src.split(",")[1]))).toContain('fill="#c93a3a"');

  await page.getByText("Exclude accessible, companion, & wheelchair seats from matches", { exact: true }).click();
  await expect(page.locator("#excludeAccessibleInput")).not.toBeChecked();
  await page.locator("#searchButton").click();

  await expect.poll(() => seatMapImage.evaluate(node => decodeURIComponent(node.src.split(",")[1]))).toContain("Accessible Unavailable");
});

test("accessible matches retain both accessible and matching states", async ({ page }) => {
  const accessibleMatchSearch = {
    ...emptySearch,
    accessibleSeatsExcluded: false,
    matches: [{
      theatre: { name: "Test Cinema", address: "1 Main St", distanceMiles: 1 },
      movieTitle: "Test Movie",
      date: "2026-07-22",
      displayTime: "7:00 PM",
      format: "Standard",
      amenities: "Reserved seating",
      genres: [],
      seatMap: {
        availableSeatCount: 1,
        totalSeatCount: 1,
        visualSvg: seatMapSvg("Accessible match"),
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
  await selectMovie(page);
  await page.locator("#searchButton").click();

  const visual = await page.locator(".real-seat-map-image").evaluate(node => decodeURIComponent(node.src.split(",")[1]));
  expect(visual).toContain("Accessible match");
  expect(visual).toContain('id="matched-seat"');
});
