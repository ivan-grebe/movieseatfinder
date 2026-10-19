import { expect, test } from "@playwright/test";

const match = {
  amenities: "Reserved seating",
  date: "2026-08-01",
  displayTime: "7 PM",
  format: "IMAX",
  genres: [],
  movieTitle: "Test Movie",
  seatMap: {
    availableSeatCount: 2,
    bestGroup: ["A1", "A2"],
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
        {
          height: 10,
          id: "A2",
          matched: true,
          status: "A",
          type: "standard",
          width: 10,
          x: 20,
          y: 10,
        },
      ],
      width: 40,
    },
    matchingGroups: [["A1", "A2"]],
    totalSeatCount: 2,
  },
  showtimeHashCode: "showtime-1",
  theatre: { address: "Main St", distanceMiles: 1, name: "Cinema" },
  ticketUrl: "https://tickets.fandango.com/order",
};
const searchResult = {
  checkedSeatMaps: 1,
  checkedShowtimes: 1,
  failedSeatMaps: 0,
  hasNextPage: false,
  hasPreviousPage: false,
  matches: [match],
  page: 1,
  pageSize: 20,
  zipCode: "10001",
};

async function mockApi(page, onSearch = (route) => route.fulfill({ json: searchResult })) {
  await page.route("**/api/theatres*", (route) =>
    route.fulfill({ json: { place: "Testville", theatres: [] } }),
  );
  await page.route("**/api/movies*", (route) =>
    route.fulfill({ json: { movies: [{ title: "Test Movie" }] } }),
  );
  await page.route("**/api/formats*", (route) =>
    route.fulfill({ json: { formats: ["IMAX", "Dolby Cinema"] } }),
  );
  await page.route("**/api/search*", onSearch);
}

async function selectMovie(page) {
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");
  await page.locator("#movieInput").fill("Test");
  await page.getByRole("option", { exact: true, name: "Test Movie" }).click();
}

test("search submits selected filters and displays matching seats", async ({ page }) => {
  let submitted = null;
  await mockApi(page, (route) => {
    submitted = Object.fromEntries(new URL(route.request().url()).searchParams);
    return route.fulfill({ json: searchResult });
  });
  await page.goto("/");
  await selectMovie(page);
  await page.getByRole("button", { exact: true, name: "IMAX" }).click();
  await page.getByRole("button", { exact: true, name: "Dolby Cinema" }).click();
  await page.locator("#searchButton").click();
  await expect(page.getByRole("heading", { exact: true, name: "Cinema" })).toBeVisible();
  await expect(page.locator(".real-seat.matched")).toHaveCount(2);
  expect(submitted).toMatchObject({
    adjacentSeats: "2",
    excludeAccessible: "1",
    format: "IMAX,Dolby Cinema",
    movie: "Test Movie",
    zip: "10001",
  });
});

test("shared links reopen the displayed search without precise coordinates", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: (text) => {
          globalThis.copiedLink = text;
          return Promise.resolve();
        },
      },
    });
  });
  await mockApi(page);
  await page.goto("/?lat=40.75&lon=-73.99&radius=5&movie=Test+Movie&adjacentSeats=2");
  await expect(page.locator(".result")).toHaveCount(1);
  expect(new URL(page.url()).searchParams.has("lat")).toBe(false);
  expect(new URL(page.url()).searchParams.has("lon")).toBe(false);
  await page.locator("#adjacentSeatsInput").fill("4");
  await page.getByRole("button", { name: "Copy showtime link" }).click();
  await expect(page.getByRole("button", { name: "Copied!" })).toBeVisible();
  const link = await page.evaluate(() => globalThis.copiedLink);
  expect(new URL(link).pathname).toMatch(/^\/s\//u);
  await page.goto(link);
  await expect(page.locator(".shared-showtime")).toHaveAttribute("data-showtime", "showtime-1");
  await expect(page.locator("#search")).toBeHidden();
  await page.getByRole("button", { exact: true, name: "Back" }).click();
  await expect(page.locator("#adjacentSeatsInput")).toHaveValue("2");
  await expect(page.locator("#zipInput")).toHaveValue("10001");
});

test("seat preferences require a selected movie", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  const seats = page.locator("#adjacentSeatsInput");
  await expect(seats).toBeDisabled();
  await page.locator("#zipInput").fill("10001");
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");
  await expect(seats).toBeDisabled();
  await page.locator("#movieInput").fill("Test");
  await page.getByRole("option", { exact: true, name: "Test Movie" }).click();
  await expect(seats).toBeEnabled();
  await page.locator("#movieInput").fill("");
  await expect(seats).toBeDisabled();
});

test("seat-area edits can be cancelled or saved", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await selectMovie(page);
  const seat = page.locator("#seatPreferenceGrid .seat-cell").first();
  await page.locator("#editSeatGridButton").click();
  await seat.click();
  await expect(seat).toHaveAttribute("aria-pressed", "true");
  await page.locator("#cancelSeatGridButton").click();
  await expect(seat).toHaveAttribute("aria-pressed", "false");
  await page.locator("#editSeatGridButton").click();
  await seat.click();
  await page.locator("#doneSeatGridButton").click();
  await expect(seat).toHaveAttribute("aria-pressed", "true");
});

test("partial availability failures warn without hiding confirmed matches", async ({ page }) => {
  await mockApi(page, (route) =>
    route.fulfill({
      json: { ...searchResult, checkedSeatMaps: 2, failedSeatMaps: 1 },
    }),
  );
  await page.goto("/");
  await selectMovie(page);
  await page.locator("#searchButton").click();
  await expect(page.locator("#summary")).toContainText("Results are incomplete");
  await expect(page.getByRole("heading", { exact: true, name: "Cinema" })).toBeVisible();
});

test("a delayed response cannot replace newer movie options", async ({ page }) => {
  await mockApi(page);
  const releaseOldRequest = Promise.withResolvers();
  let requests = 0;
  await page.route("**/api/movies*", async (route) => {
    requests += 1;
    const isOldRequest = requests === 1;
    let title = "Current Movie";
    if (isOldRequest) {
      await releaseOldRequest.promise;
      title = "Old Movie";
    }
    await route.fulfill({ json: { movies: [{ title }] } });
  });
  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await expect.poll(() => requests).toBe(1);
  await page.locator("#radiusInput").fill("10");
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");
  const oldResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/movies?") &&
      new URL(response.url()).searchParams.get("radius") === "25",
  );
  releaseOldRequest.resolve();
  await oldResponse;
  await page.locator("#movieInput").fill("Movie");
  await expect(page.getByRole("option", { exact: true, name: "Current Movie" })).toBeVisible();
  await expect(page.getByRole("option", { exact: true, name: "Old Movie" })).toHaveCount(0);
});
