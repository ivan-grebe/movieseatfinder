import { expect, test } from "@playwright/test";

const emptySearch = {
  checkedSeatMaps: 1,
  checkedShowtimes: 1,
  hasNextPage: false,
  hasPreviousPage: false,
  matches: [],
  page: 1,
  pageSize: 20,
};

const makeSimpleMatch = function makeSimpleMatch(theatreName, time) {
  return {
    amenities: "Reserved seating",
    date: "2026-08-01",
    displayTime: time,
    format: "Standard",
    genres: [],
    movieTitle: "Test Movie",
    seatMap: {
      availableSeatCount: 1,
      layout: {
        height: 30,
        seats: [
          {
            height: 10,
            id: `${theatreName}-${time}`,
            matched: true,
            status: "A",
            type: "standard",
            width: 10,
            x: 10,
            y: 10,
          },
        ],
        width: 30,
      },
      totalSeatCount: 1,
    },
    theatre: { address: "1 Main St", distanceMiles: 1, name: theatreName },
  };
};

const mockSearchDependencies = async function mockSearchDependencies(
  page,
  onSearch,
  formats = ["Standard"],
) {
  await page.route("**/api/theatres*", (route) =>
    route.fulfill({
      body: JSON.stringify({ place: "Testville", theatres: [] }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/movies*", (route) =>
    route.fulfill({
      body: JSON.stringify({ movies: [{ title: "Test Movie" }] }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/formats*", (route) =>
    route.fulfill({
      body: JSON.stringify({ formats }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/search*", onSearch);
};

const selectMovie = async function selectMovie(page, title = "Test Movie") {
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");
  const input = page.locator("#movieInput");
  await input.fill(title);
  await page.getByRole("option", { exact: true, name: title }).click();
  await expect(input).toHaveValue(title);
};

test("mobile form fits a narrow phone without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ height: 700, width: 320 });
  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);

  const layout = await page.evaluate(() => ({
    fontFamily: getComputedStyle(document.body).fontFamily,
    fontResourceLoaded: performance
      .getEntriesByType("resource")
      .some((entry) => new URL(entry.name).pathname === "/inter-variable.woff2"),
    inputFontSize: getComputedStyle(document.querySelector("#zipInput")).fontSize,
    scrollWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }));

  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.inputFontSize).toBe("16px");
  expect(layout.fontFamily).toContain("Inter");
  expect(layout.fontResourceLoaded).toBe(true);
});

test("mobile seat preferences require a deliberate, reversible edit mode", async ({ page }) => {
  await mockSearchDependencies(page, (route) =>
    route.fulfill({
      body: JSON.stringify(emptySearch),
      contentType: "application/json",
    }),
  );

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
  await firstCell.dispatchEvent("click");
  await expect(firstCell).toHaveAttribute("aria-pressed", "false");

  await editButton.click();
  await expect(editButton).toBeHidden();
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

  await editButton.click();
  await firstCell.click();
  await doneButton.click();
  await expect(firstCell).toHaveAttribute("aria-pressed", "true");

  await page.setViewportSize({ height: 700, width: 900 });
  await expect(editButton).toBeHidden();
  await expect(centerButton).toBeVisible();
  await expect(clearButton).toBeVisible();
});

test("movie and seat controls stay locked until the location resolves", async ({ page }) => {
  const { promise: theatresStarted, resolve: markTheatresStarted } = Promise.withResolvers();
  const { promise: theatreGate, resolve: releaseTheatres } = Promise.withResolvers();
  await page.route("**/api/theatres*", async (route) => {
    markTheatresStarted();
    await theatreGate;
    await route.fulfill({
      body: JSON.stringify({ place: "Testville", theatres: [] }),
      contentType: "application/json",
    });
  });
  await page.route("**/api/movies*", (route) =>
    route.fulfill({
      body: JSON.stringify({ movies: [{ title: "Test Movie" }] }),
      contentType: "application/json",
    }),
  );
  await page.goto("/");

  const movieGroup = page.locator("#movieGroup");
  const preferencesGroup = page.locator("#preferencesGroup");
  const searchButton = page.locator("#searchButton");
  await Promise.all(
    [movieGroup, preferencesGroup].flatMap((group) => [
      expect(group).toHaveAttribute("inert", ""),
      expect(group).toHaveAttribute("aria-disabled", "true"),
    ]),
  );
  expect(
    await page.locator("#movieInput").evaluate((input) => {
      input.focus();
      return document.activeElement === input;
    }),
  ).toBe(false);
  await expect(searchButton).toBeDisabled();
  await expect(searchButton).not.toHaveAttribute("aria-busy", "true");

  await page.locator("#zipInput").fill("10001");
  await theatresStarted;
  releaseTheatres();
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

  await Promise.all(
    [movieGroup, preferencesGroup].flatMap((group) => [
      expect(group).not.toHaveAttribute("inert", ""),
      expect(group).not.toHaveAttribute("aria-disabled", "true"),
    ]),
  );
  await expect(searchButton).toBeEnabled();
});

test("movie options remain selectable beyond the movie card", async ({ page }) => {
  const movies = Array.from({ length: 12 }, (_, index) => ({ title: `Test Movie ${index + 1}` }));
  await page.route("**/api/theatres*", (route) =>
    route.fulfill({
      body: JSON.stringify({ place: "Testville", theatres: [] }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/movies*", (route) =>
    route.fulfill({
      body: JSON.stringify({ movies }),
      contentType: "application/json",
    }),
  );

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("12 showing");

  await page.locator("#movieInput").click();
  await expect(page.locator("#movieMenu")).toBeVisible();
  await page.getByRole("option", { exact: true, name: "Test Movie 12" }).click();
  await expect(page.locator("#movieInput")).toHaveValue("Test Movie 12");
  await expect(page.locator("#movieMenu")).toBeHidden();
});

test("an unknown ZIP shows one error at the ZIP field and stops dependent loads", async ({
  page,
}) => {
  let movieRequestCount = 0;
  await page.route("**/api/theatres*", (route) =>
    route.fulfill({
      body: JSON.stringify({
        code: "location",
        error: "We couldn't find that ZIP code. Check it and try again.",
      }),
      contentType: "application/json",
      status: 400,
    }),
  );
  await page.route("**/api/movies*", (route) => {
    movieRequestCount += 1;
    return route.fulfill({ body: JSON.stringify({ movies: [] }), contentType: "application/json" });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("00000");
  await expect(page.locator("#locationStatus")).toHaveText(
    "We couldn't find that ZIP code. Check it and try again.",
  );
  await expect(page.locator("#movieGroup")).toHaveAttribute("inert", "");
  await expect(page.locator("#searchButton")).toBeDisabled();
  expect(movieRequestCount).toBe(0);
});

test("mobile search keeps content stable while loading and then renders its response", async ({
  page,
}) => {
  const { promise: searchStarted, resolve: markSearchStarted } = Promise.withResolvers();
  const { promise: searchGate, resolve: releaseSearch } = Promise.withResolvers();
  await mockSearchDependencies(page, async (route) => {
    markSearchStarted();
    await searchGate;
    await route.fulfill({ body: JSON.stringify(emptySearch), contentType: "application/json" });
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
  await expect(emptyState).toBeVisible();

  releaseSearch();
  await expect(page.locator("#summary")).toContainText("No matching showtimes");
  await expect(searchButton).not.toHaveAttribute("aria-busy", "true");
});

test("movie search accepts a selected suggestion or an exact loaded title", async ({ page }) => {
  let searchUrl = "";
  let searchCount = 0;
  await mockSearchDependencies(page, (route) => {
    searchCount += 1;
    searchUrl = route.request().url();
    return route.fulfill({ body: JSON.stringify(emptySearch), contentType: "application/json" });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

  const movieInput = page.locator("#movieInput");
  const searchButton = page.locator("#searchButton");
  await movieInput.fill("Test");
  const suggestion = page.getByRole("option", { exact: true, name: "Test Movie" });
  await expect(suggestion).toBeVisible();
  await searchButton.click();

  await expect(movieInput).not.toHaveJSProperty("validationMessage", "");
  expect(searchUrl).toBe("");

  await movieInput.fill("Test Movie");
  await expect(searchButton).toBeEnabled();
  await searchButton.click();
  await expect.poll(() => searchUrl).not.toBe("");
  await expect(searchButton).toBeEnabled();
  expect(new URL(searchUrl).searchParams.get("movie")).toBe("Test Movie");
  expect(searchCount).toBe(1);

  await page.locator("#radiusInput").evaluate((input) => {
    input.value = "10";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    document.querySelector("#searchButton").click();
  });
  await expect(movieInput).toBeDisabled();
  expect(searchCount).toBe(1);
  await expect(movieInput).toBeEnabled();
  await expect(movieInput).toHaveValue("Test Movie");
});

test("theatre filter accepts only an empty, selected, or exact loaded theatre", async ({
  page,
}) => {
  let searchUrl = "";
  const movieUrls = [];
  await page.route("**/api/theatres*", (route) =>
    route.fulfill({
      body: JSON.stringify({
        place: "Testville",
        theatres: [{ name: "Test Cinema" }, { name: "Test Cinema East" }],
      }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/movies*", (route) => {
    movieUrls.push(route.request().url());
    return route.fulfill({
      body: JSON.stringify({ movies: [{ title: "Test Movie" }] }),
      contentType: "application/json",
    });
  });
  await page.route("**/api/formats*", (route) =>
    route.fulfill({
      body: JSON.stringify({ formats: ["Standard"] }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/search*", (route) => {
    searchUrl = route.request().url();
    return route.fulfill({ body: JSON.stringify(emptySearch), contentType: "application/json" });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#theatreMeta")).toHaveText("2 nearby");

  const theatreInput = page.locator("#theatreInput");
  await theatreInput.fill("Test");
  await expect(page.getByRole("option", { exact: true, name: "Test Cinema" })).toBeVisible();
  await page.locator("#searchButton").click();
  await expect(theatreInput).not.toHaveJSProperty("validationMessage", "");
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
  await mockSearchDependencies(
    page,
    (route) => {
      searchUrl = route.request().url();
      return route.fulfill({ body: JSON.stringify(emptySearch), contentType: "application/json" });
    },
    ["IMAX", "Dolby Cinema", "Standard"],
  );

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  // Wait for the ZIP-triggered movie refresh to finish before choosing a
  // Movie and its formats, matching the order a user sees in the UI.
  await selectMovie(page);

  const imax = page.getByRole("button", { exact: true, name: "IMAX" });
  const dolby = page.getByRole("button", { exact: true, name: "Dolby Cinema" });
  await expect(page.locator("#formatStatus")).toBeEmpty();
  await expect(imax).toBeVisible();
  await imax.click();
  await dolby.click();
  await expect(imax).toHaveAttribute("aria-pressed", "true");
  await expect(dolby).toHaveAttribute("aria-pressed", "true");

  await page.locator("#searchButton").click();
  await expect.poll(() => searchUrl).toContain("format=IMAX%2CDolby+Cinema");
  expect(searchUrl).toContain("excludeAccessible=1");

  const guideButton = page.locator("#formatGuideButton");
  await guideButton.click();
  await expect(guideButton).toHaveAttribute("aria-expanded", "true");
  await guideButton.click();
  await expect(guideButton).toHaveAttribute("aria-expanded", "false");
});

test("result sorting defaults to earliest and reruns the search when changed", async ({ page }) => {
  const searchSorts = [];
  const { promise: latestSearchStarted, resolve: markLatestSearchStarted } =
    Promise.withResolvers();
  const { promise: latestSearchGate, resolve: releaseLatestSearch } = Promise.withResolvers();
  const matchingSearch = {
    ...emptySearch,
    matches: [
      {
        amenities: "Reserved seating",
        date: "2026-08-01",
        displayTime: "7:00 PM",
        format: "Standard",
        genres: [],
        movieTitle: "Test Movie",
        seatMap: {
          availableSeatCount: 1,
          layout: {
            height: 30,
            seats: [
              {
                height: 10,
                id: "A1",
                matched: true,
                status: "A",
                type: "standard",
                width: 10,
                x: 10,
                y: 10,
              },
            ],
            width: 30,
          },
          totalSeatCount: 1,
        },
        theatre: { address: "1 Main St", distanceMiles: 1, name: "Test Cinema" },
      },
    ],
  };
  await mockSearchDependencies(page, async (route) => {
    const sort = new URL(route.request().url()).searchParams.get("sort");
    searchSorts.push(sort);
    if (sort === "latest") {
      markLatestSearchStarted();
      await latestSearchGate;
    }
    return route.fulfill({ body: JSON.stringify(matchingSearch), contentType: "application/json" });
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
  await expect(sortInput).toBeDisabled();
  await expect(page.locator("#searchButton")).toBeEnabled();

  releaseLatestSearch();
  await expect.poll(() => searchSorts.at(-1)).toBe("latest");
  await expect(sortInput).toBeEnabled();
  await expect(page.locator("#sortStatus")).toBeEmpty();

  await sortInput.selectOption("nearest");
  await expect.poll(() => searchSorts.at(-1)).toBe("nearest");
  expect(searchSorts[0]).toBe("earliest");
});

test("pagination keeps current results until the next page loads", async ({ page }) => {
  await page.setViewportSize({ height: 700, width: 320 });
  const { promise: secondPageStarted, resolve: markSecondPageStarted } = Promise.withResolvers();
  const { promise: secondPageGate, resolve: releaseSecondPage } = Promise.withResolvers();
  await mockSearchDependencies(page, async (route) => {
    const requestedPage = Number(new URL(route.request().url()).searchParams.get("page"));
    if (requestedPage === 2) {
      markSecondPageStarted();
      await secondPageGate;
      await route.fulfill({
        body: JSON.stringify({
          ...emptySearch,
          hasPreviousPage: true,
          matches: [makeSimpleMatch("Page Two Cinema", "20:00")],
          page: 2,
        }),
        contentType: "application/json",
      });
      return;
    }
    await route.fulfill({
      body: JSON.stringify({
        ...emptySearch,
        hasNextPage: true,
        matches: [makeSimpleMatch("Page One Cinema", "19:00")],
      }),
      contentType: "application/json",
    });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await selectMovie(page);
  await page.locator("#searchButton").click();
  await expect(page.getByRole("heading", { exact: true, name: "Page One Cinema" })).toBeVisible();

  const pagination = page.locator("#pagination");
  const previousPage = page.getByRole("button", { name: "Previous page of results" });
  const nextPage = page.getByRole("button", { name: "Next page of results" });
  await nextPage.scrollIntoViewIfNeeded();
  await nextPage.click();
  await secondPageStarted;

  await expect(pagination).toHaveAttribute("aria-busy", "true");
  await expect(previousPage).toBeDisabled();
  await expect(nextPage).toBeDisabled();
  await expect(page.getByRole("heading", { exact: true, name: "Page One Cinema" })).toBeVisible();

  releaseSecondPage();
  await expect(page.getByRole("heading", { exact: true, name: "Page Two Cinema" })).toBeVisible();
  await expect(pagination).not.toHaveAttribute("aria-busy", "true");
});

test("stale movie responses do not replace options for newer criteria", async ({ page }) => {
  let movieRequestCount = 0;
  let firstMovieRequestFulfilled = false;
  const { promise: firstMovieRequestStarted, resolve: markFirstMovieRequestStarted } =
    Promise.withResolvers();
  const { promise: firstMovieRequestGate, resolve: releaseFirstMovieRequest } =
    Promise.withResolvers();

  await page.route("**/api/theatres*", (route) =>
    route.fulfill({
      body: JSON.stringify({ place: "Testville", theatres: [] }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/movies*", async (route) => {
    movieRequestCount += 1;
    if (movieRequestCount === 1) {
      markFirstMovieRequestStarted();
      await firstMovieRequestGate;
      await route.fulfill({
        body: JSON.stringify({ movies: [{ title: "Stale Movie" }] }),
        contentType: "application/json",
      });
      firstMovieRequestFulfilled = true;
      return;
    }
    await route.fulfill({
      body: JSON.stringify({ movies: [{ title: "Current Movie" }] }),
      contentType: "application/json",
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
  await expect(page.getByRole("option", { exact: true, name: "Current Movie" })).toBeVisible();
  await expect(page.getByRole("option", { exact: true, name: "Stale Movie" })).toHaveCount(0);
});

test("mobile results render the native seat map and update accessibility states", async ({
  page,
}) => {
  const matchingSearch = {
    ...emptySearch,
    accessibleSeatsExcluded: true,
    matches: [
      {
        amenities: "Reserved seating",
        date: "2026-07-22",
        displayTime: "7:00 PM",
        format: "IMAX",
        genres: [],
        movieTitle: "Test Movie",
        seatMap: {
          availableSeatCount: 2,
          layout: {
            height: 50,
            seats: [
              {
                height: 10,
                id: "A1",
                matched: true,
                status: "A",
                type: "standard",
                width: 10,
                x: 10,
                y: 10,
              },
              {
                height: 10,
                id: "A2",
                matched: false,
                status: "A",
                type: "wheelchair",
                width: 10,
                x: 30,
                y: 10,
              },
              {
                height: 10,
                id: "A3",
                matched: false,
                status: "U",
                type: "wheelchair",
                width: 10,
                x: 50,
                y: 10,
              },
              {
                height: 10,
                id: "A4",
                matched: false,
                status: "A",
                type: "companion",
                width: 10,
                x: 70,
                y: 10,
              },
              {
                height: 10,
                id: "A5",
                matched: false,
                status: "U",
                type: "companion",
                width: 10,
                x: 90,
                y: 10,
              },
            ],
            width: 100,
          },
          totalSeatCount: 2,
        },
        theatre: { address: "1 Main St", distanceMiles: 1, name: "Test Cinema" },
      },
    ],
  };
  await mockSearchDependencies(page, (route) => {
    const accessibleSeatsExcluded =
      new URL(route.request().url()).searchParams.get("excludeAccessible") === "1";
    return route.fulfill({
      body: JSON.stringify({ ...matchingSearch, accessibleSeatsExcluded }),
      contentType: "application/json",
    });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await selectMovie(page);
  await page.locator("#searchButton").click();

  const matchedSeat = page.locator(".real-seat.matched");
  await expect(matchedSeat).toHaveCount(1);
  const wheelchairSeat = page.locator(
    '.real-seat[title="A2 - available - wheelchair - excluded by filter"]',
  );
  await expect(wheelchairSeat).toHaveClass(/accessible/u);
  const companionSeat = page.locator(
    '.real-seat[title="A4 - available - companion - excluded by filter"]',
  );
  await expect(companionSeat).toHaveClass(/accessible/u);
  await expect(page.getByText("Unavailable / excluded", { exact: true })).toBeVisible();
  await expect(page.getByText("Accessible", { exact: true })).toHaveCount(0);

  await page
    .getByText("Exclude accessible, companion, & wheelchair seats from matches", { exact: true })
    .click();
  await expect(page.locator("#excludeAccessibleInput")).not.toBeChecked();
  await page.locator("#searchButton").click();

  const includedWheelchairSeat = page.locator('.real-seat[title="A2 - available - wheelchair"]');
  await expect(includedWheelchairSeat).toHaveClass(/accessible/u);
  const includedCompanionSeat = page.locator('.real-seat[title="A4 - available - companion"]');
  await expect(includedCompanionSeat).toHaveClass(/accessible/u);
  await expect(page.getByText("Accessible", { exact: true })).toBeVisible();
  await expect(page.getByText("Unavailable", { exact: true })).toBeVisible();
});
