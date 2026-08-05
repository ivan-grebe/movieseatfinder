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

test("initial render inlines CSS and loads one application bundle", async ({ page }) => {
  const applicationAssets = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/_vercel/")) return;
    if (["script", "stylesheet"].includes(request.resourceType())) {
      applicationAssets.push({ path: url.pathname, type: request.resourceType() });
    }
  });

  await page.goto("/");

  expect(applicationAssets.filter(asset => asset.type === "stylesheet")).toEqual([]);
  expect(applicationAssets.filter(asset => asset.type === "script")).toEqual([
    { path: "/app.bundle.js", type: "script" },
  ]);
  await expect(page.locator("head > style")).toHaveCount(1);
  await expect(page.locator('script[src^="app.bundle.js"]')).toHaveAttribute("async", "");
  const entranceAnimations = await page.locator("h1, .tagline, .card").evaluateAll(elements => elements.map(element => (
    element.getAnimations()[0]?.effect.getKeyframes().flatMap(frame => (
      Object.keys(frame).filter(property => !["offset", "easing", "composite", "computedOffset"].includes(property))
    )) || []
  )));
  expect(entranceAnimations[0]).toEqual([]);
  expect(new Set(entranceAnimations[1])).toEqual(new Set(["opacity", "transform"]));
  expect(new Set(entranceAnimations[2])).toEqual(new Set(["opacity", "transform"]));

  const protectedLayout = await page.evaluate(() => {
    const grid = document.querySelector("#seatPreferenceGrid");
    const style = getComputedStyle(grid);
    let trustedTypesEnforced = false;
    try {
      document.createElement("div").innerHTML = "<span></span>";
    } catch (error) {
      trustedTypesEnforced = error.name === "TypeError";
    }
    return {
      aspectRatio: style.aspectRatio,
      gridHeight: grid.getBoundingClientRect().height,
      gridWidth: grid.getBoundingClientRect().width,
      rowCount: style.gridTemplateRows.split(" ").length,
      trustedTypesEnforced,
    };
  });
  expect(protectedLayout.aspectRatio).toBe("1 / 1");
  expect(Math.abs(protectedLayout.gridHeight - protectedLayout.gridWidth)).toBeLessThan(1);
  expect(protectedLayout.rowCount).toBe(15);
  expect(protectedLayout.trustedTypesEnforced).toBe(true);
});

test("mobile form fits a narrow phone without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/");
  await expect(page.locator("#excludeAccessibleInput")).toBeChecked();
  await expect(page.locator("#startTimeInput")).toHaveValue("00:00");
  await expect(page.locator("#endTimeInput")).toHaveValue("23:59");

  const layout = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    dateWidths: [...document.querySelectorAll('input[type="date"]')].map(input => input.getBoundingClientRect().width),
    preferenceWidths: [...document.querySelectorAll(".preferences-group .grid.thirds > label")]
      .map(label => label.getBoundingClientRect().width),
    balancedTextWraps: [
      ".tagline",
      ".accessible-check > span:last-child",
      ".seat-help",
      ".empty-state p",
    ].map(selector => getComputedStyle(document.querySelector(selector)).textWrap),
    inputFontSize: getComputedStyle(document.querySelector("#zipInput")).fontSize,
    githubShadow: getComputedStyle(document.querySelector(".github-link")).boxShadow,
  }));

  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.dateWidths.every(width => width >= 200)).toBe(true);
  expect(layout.preferenceWidths.slice(0, 2).every(width => width >= 100)).toBe(true);
  expect(layout.preferenceWidths[2]).toBeGreaterThan(layout.preferenceWidths[0] * 1.8);
  expect(layout.balancedTextWraps).toEqual(["balance", "balance", "balance", "balance"]);
  expect(layout.inputFontSize).toBe("16px");
  expect(layout.githubShadow).toBe("none");
});

test("ambient background glows wander randomly, scroll with the page, and respect reduced motion", async ({ page }) => {
  await page.goto("/");

  const movingStyles = await page.evaluate(() => {
    const layer = document.querySelector(".ambient-glows");
    const glows = [
      document.querySelector(".ambient-glow-warm"),
      document.querySelector(".ambient-glow-cool"),
    ];
    const layerTop = layer.getBoundingClientRect().top;
    const styles = glows.map(element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const animation = element.getAnimations()[0];
      animation.pause();
      const duration = animation.effect.getTiming().duration;
      const keyframes = animation.effect.getKeyframes();
      animation.currentTime = 0;
      const startRect = element.getBoundingClientRect();
      const startTransform = getComputedStyle(element).transform;
      animation.currentTime = duration * .99;
      const destinationRect = element.getBoundingClientRect();
      const destinationTransform = getComputedStyle(element).transform;
      return {
        animationName: style.animationName,
        animationDuration: duration,
        destination: keyframes.at(-1).transform,
        glowAlpha: Number.parseFloat(style.backgroundImage.match(/rgba\([^)]*,\s*([\d.]+)\)/)?.[1] ?? "0"),
        keyframeCount: keyframes.length,
        motionState: element.dataset.ambientMotion,
        travelDistance: Math.hypot(destinationRect.x - startRect.x, destinationRect.y - startRect.y),
        intersectsViewport: rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth,
        moves: startTransform !== destinationTransform,
        willChange: style.willChange,
      };
    });
    return {
      layerPosition: getComputedStyle(layer).position,
      layerTop,
      styles,
    };
  });

  expect(movingStyles.layerPosition).toBe("absolute");
  expect(movingStyles.layerTop).toBeCloseTo(0, 1);
  expect(movingStyles.styles.every(style => style.animationName === "none")).toBe(true);
  expect(movingStyles.styles.every(style => style.animationDuration >= 6_500 && style.animationDuration <= 10_500)).toBe(true);
  expect(movingStyles.styles.every(style => style.keyframeCount === 2)).toBe(true);
  expect(movingStyles.styles.every(style => style.motionState === "wandering")).toBe(true);
  expect(movingStyles.styles.every(style => style.glowAlpha >= .4)).toBe(true);
  expect(movingStyles.styles.every(style => style.travelDistance >= 45)).toBe(true);
  expect(movingStyles.styles.every(style => style.intersectsViewport)).toBe(true);
  expect(movingStyles.styles.every(style => style.moves)).toBe(true);
  expect(movingStyles.styles.every(style => style.willChange === "transform")).toBe(true);

  const regeneratedDestinations = await page.evaluate(async () => {
    const glows = [
      document.querySelector(".ambient-glow-warm"),
      document.querySelector(".ambient-glow-cool"),
    ];
    glows.forEach(element => element.getAnimations()[0].finish());
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return glows.map(element => ({
      animationCount: element.getAnimations().length,
      destination: element.getAnimations()[0].effect.getKeyframes().at(-1).transform,
    }));
  });
  expect(regeneratedDestinations.every((style, index) => style.animationCount === 1
    && style.destination !== movingStyles.styles[index].destination)).toBe(true);

  await page.evaluate(() => {
    document.documentElement.style.scrollBehavior = "auto";
    scrollTo(0, 600);
  });
  const scrolledLayer = await page.evaluate(() => ({
    scrollY,
    top: document.querySelector(".ambient-glows").getBoundingClientRect().top,
  }));
  expect(scrolledLayer.scrollY).toBeGreaterThan(0);
  expect(scrolledLayer.top).toBeCloseTo(-scrolledLayer.scrollY, 1);

  await page.emulateMedia({ colorScheme: "dark" });
  const mobileDarkGlowAlpha = await page.evaluate(() => [
    document.querySelector(".ambient-glow-warm"),
    document.querySelector(".ambient-glow-cool"),
  ].map(element => {
    const background = getComputedStyle(element).backgroundImage;
    return Number.parseFloat(background.match(/rgba\([^)]*,\s*([\d.]+)\)/)?.[1] ?? "0");
  }));
  expect(mobileDarkGlowAlpha.every(alpha => alpha >= .32)).toBe(true);

  await page.setViewportSize({ width: 1280, height: 900 });
  const desktopDarkGlowAlpha = await page.evaluate(() => [
    document.querySelector(".ambient-glow-warm"),
    document.querySelector(".ambient-glow-cool"),
  ].map(element => {
    const background = getComputedStyle(element).backgroundImage;
    return Number.parseFloat(background.match(/rgba\([^)]*,\s*([\d.]+)\)/)?.[1] ?? "0");
  }));
  expect(desktopDarkGlowAlpha.every(alpha => alpha >= .30)).toBe(true);

  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.waitForFunction(() => document.querySelector(".ambient-glow-warm").dataset.ambientMotion === "paused");
  const reducedStyles = await page.evaluate(() => [
    document.querySelector(".ambient-glow-warm"),
    document.querySelector(".ambient-glow-cool"),
  ].map(element => {
    const style = getComputedStyle(element);
    return {
      animationCount: element.getAnimations().length,
      motionState: element.dataset.ambientMotion,
      willChange: style.willChange,
    };
  }));
  expect(reducedStyles).toEqual([
    { animationCount: 0, motionState: "paused", willChange: "auto" },
    { animationCount: 0, motionState: "paused", willChange: "auto" },
  ]);
});

test("desktop location and movie cards share a row height", async ({ page }) => {
  for (const width of [1280, 1600]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");

    const cards = await page.evaluate(() => {
      const location = document.querySelector(".location-group").getBoundingClientRect();
      const movie = document.querySelector(".movie-group").getBoundingClientRect();
      return {
        locationTop: location.top,
        movieTop: movie.top,
        locationHeight: location.height,
        movieHeight: movie.height,
      };
    });

    expect(cards.movieTop).toBeCloseTo(cards.locationTop, 1);
    expect(cards.movieHeight).toBeCloseTo(cards.locationHeight, 1);
  }
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
  await expect(grid).toHaveCSS("touch-action", "pan-y");
  await expect(grid).toHaveCSS("opacity", "0.56");
  await expect(firstCell).toHaveCSS("pointer-events", "none");
  await expect(page.locator("#seatPreferenceHelp")).toHaveText(
    "Tap Edit seat area to choose where you'd like to sit.",
  );
  const seatSilhouette = await firstCell.evaluate(cell => ({
    backrest: getComputedStyle(cell, "::before").content,
    cushion: getComputedStyle(cell, "::after").content,
    cushionHeight: getComputedStyle(cell, "::after").height,
  }));
  expect(seatSilhouette).toEqual({ backrest: '\"\"', cushion: '\"\"', cushionHeight: "5px" });

  const overlayAppearance = await page.evaluate(() => {
    const grid = document.querySelector("#seatPreferenceGrid").getBoundingClientRect();
    const button = document.querySelector("#editSeatGridButton");
    const rect = button.getBoundingClientRect();
    const style = getComputedStyle(button);
    return {
      centerDeltaX: Math.abs((rect.left + rect.width / 2) - (grid.left + grid.width / 2)),
      centerDeltaY: Math.abs((rect.top + rect.height / 2) - (grid.top + grid.height / 2)),
      color: style.color,
      backgroundImage: style.backgroundImage,
      minHeight: rect.height,
      overlapsGrid: rect.left < grid.right && rect.right > grid.left && rect.top < grid.bottom && rect.bottom > grid.top,
    };
  });
  expect(overlayAppearance.centerDeltaX).toBeLessThan(1);
  expect(overlayAppearance.centerDeltaY).toBeLessThan(1);
  expect(overlayAppearance.color).toBe("rgb(255, 255, 255)");
  expect(overlayAppearance.backgroundImage).toContain("linear-gradient");
  expect(overlayAppearance.minHeight).toBeGreaterThanOrEqual(48);
  expect(overlayAppearance.overlapsGrid).toBe(true);

  await firstCell.dispatchEvent("click");
  await expect(firstCell).toHaveAttribute("aria-pressed", "false");

  await editButton.click();
  await expect(editButton).toBeHidden();
  await expect(grid).toHaveClass(/is-mobile-editing/);
  await expect(grid).toHaveCSS("opacity", "1");
  await expect(grid).toHaveCSS("touch-action", "none");
  await expect(centerButton).toBeVisible();
  await expect(clearButton).toBeVisible();
  await expect(cancelButton).toBeVisible();
  await expect(doneButton).toBeVisible();

  const centerAppearance = await centerButton.evaluate(button => ({
    color: getComputedStyle(button).color,
    background: getComputedStyle(button).backgroundColor,
    shadow: getComputedStyle(button).boxShadow,
  }));
  await centerButton.tap();
  await expect.poll(() => centerButton.evaluate(button => ({
    color: getComputedStyle(button).color,
    background: getComputedStyle(button).backgroundColor,
    shadow: getComputedStyle(button).boxShadow,
  }))).toEqual(centerAppearance);
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
  await expect(grid).toHaveCSS("touch-action", "none");
});

test("field loading messages do not reflow later controls", async ({ page }) => {
  let releaseTheatres;
  let markTheatresStarted;
  const theatresStarted = new Promise(resolve => { markTheatresStarted = resolve; });
  const theatreGate = new Promise(resolve => { releaseTheatres = resolve; });
  let releaseMovies;
  let markMoviesStarted;
  const moviesStarted = new Promise(resolve => { markMoviesStarted = resolve; });
  const movieGate = new Promise(resolve => { releaseMovies = resolve; });

  await page.route("**/api/theatres*", async route => {
    markTheatresStarted();
    await theatreGate;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ place: "Testville", theatres: [] }),
    });
  });
  await page.route("**/api/movies*", async route => {
    markMoviesStarted();
    await movieGate;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ movies: [{ title: "Test Movie" }] }),
    });
  });

  await page.goto("/");
  await page.locator("#zipInput").fill("10001");
  await theatresStarted;
  await expect(page.locator("#theatreMeta")).toContainText("Loading");
  await expect(page.locator("#theatreInput")).toBeDisabled();
  await expect(page.locator("#theatreInput")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#theatreInput")).toHaveCSS("background-color", "rgb(223, 227, 232)");
  await expect(page.locator("#theatreInput")).toHaveCSS("border-top-style", "dashed");
  await expect(page.locator("#movieInput")).toBeDisabled();
  await expect(page.locator("#searchButton")).toBeDisabled();
  const whileTheatresLoad = await page.evaluate(() => ({
    movieTop: document.querySelector(".movie-group").getBoundingClientRect().top,
  }));

  releaseTheatres();
  await moviesStarted;
  await expect(page.locator("#theatreInput")).toBeEnabled();
  await expect(page.locator("#theatreInput")).not.toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#movieMeta")).toContainText("Loading");
  await expect(page.locator("#movieInput")).toBeDisabled();
  await expect(page.locator("#movieInput")).toHaveAttribute("aria-busy", "true");
  const whileMoviesLoad = await page.evaluate(() => ({
    movieTop: document.querySelector(".movie-group").getBoundingClientRect().top,
    preferencesTop: document.querySelector(".preferences-group").getBoundingClientRect().top,
  }));
  expect(whileMoviesLoad.movieTop).toBeCloseTo(whileTheatresLoad.movieTop, 1);

  releaseMovies();
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");
  await expect(page.locator("#movieInput")).toBeEnabled();
  await expect(page.locator("#movieInput")).not.toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#movieStatus")).toBeEmpty();
  const settled = await page.evaluate(() => ({
    preferencesTop: document.querySelector(".preferences-group").getBoundingClientRect().top,
  }));
  expect(settled.preferencesTop).toBeCloseTo(whileMoviesLoad.preferencesTop, 1);
});

test("movie and seat controls stay locked until the location resolves without reflow", async ({ page }) => {
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
  const lockedLayout = await page.evaluate(() => ({
    movie: document.querySelector("#movieGroup").getBoundingClientRect().toJSON(),
    preferences: document.querySelector("#preferencesGroup").getBoundingClientRect().toJSON(),
  }));
  releaseTheatres();
  await expect(page.locator("#movieMeta")).toHaveText("1 showing");

  for (const group of [movieGroup, preferencesGroup]) {
    await expect(group).not.toHaveAttribute("inert", "");
    await expect(group).not.toHaveAttribute("aria-disabled", "true");
    await expect(group).not.toHaveClass(/is-location-locked/);
  }
  await expect(searchButton).toBeEnabled();
  const unlockedLayout = await page.evaluate(() => ({
    movie: document.querySelector("#movieGroup").getBoundingClientRect().toJSON(),
    preferences: document.querySelector("#preferencesGroup").getBoundingClientRect().toJSON(),
  }));
  expect(unlockedLayout.movie.top).toBeCloseTo(lockedLayout.movie.top, 1);
  expect(unlockedLayout.movie.height).toBeCloseTo(lockedLayout.movie.height, 1);
  expect(unlockedLayout.preferences.top).toBeCloseTo(lockedLayout.preferences.top, 1);
  expect(unlockedLayout.preferences.height).toBeCloseTo(lockedLayout.preferences.height, 1);
});

test("movie options can extend beyond the movie card without being clipped or covered", async ({ page }) => {
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

  // Hold the movie content at the same partial opacity used by the unlock
  // transition. Partial opacity creates a stacking context, and the menu must
  // still layer above the following preference card while that fade is active.
  const transitionOpacity = await page.locator("#movieGroup > .grid").evaluate(element => {
    const animation = element.animate(
      [{ opacity: .62 }, { opacity: 1 }],
      { duration: 160, fill: "both" },
    );
    animation.pause();
    animation.currentTime = 80;
    return Number.parseFloat(getComputedStyle(element).opacity);
  });
  expect(transitionOpacity).toBeGreaterThan(.62);
  expect(transitionOpacity).toBeLessThan(1);

  await page.locator("#movieInput").click();
  await expect(page.locator("#movieMenu")).toBeVisible();

  const menuLayout = await page.evaluate(() => {
    const group = document.querySelector("#movieGroup");
    const menu = document.querySelector("#movieMenu");
    const groupRect = group.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const sampleX = menuRect.left + (menuRect.width / 2);
    const sampleY = groupRect.bottom + 24;
    return {
      overflow: getComputedStyle(group).overflow,
      extendsBeyondCardBy: menuRect.bottom - groupRect.bottom,
      menuIsTopmostBelowCard: menu.contains(document.elementFromPoint(sampleX, sampleY)),
    };
  });

  expect(menuLayout.overflow).toBe("visible");
  expect(menuLayout.extendsBeyondCardBy).toBeGreaterThan(100);
  expect(menuLayout.menuIsTopmostBelowCard).toBe(true);

  const closingLayout = await page.evaluate(() => {
    const group = document.querySelector("#movieGroup");
    const menu = document.querySelector("#movieMenu");
    const groupRect = group.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    document.querySelector("#radiusInput").focus();
    const sampleX = menuRect.left + (menuRect.width / 2);
    const sampleY = groupRect.bottom + 24;
    return {
      menuStillOpenDuringClickDelay: !menu.hidden,
      menuRemainsTopmostDuringClickDelay: menu.contains(document.elementFromPoint(sampleX, sampleY)),
    };
  });

  expect(closingLayout.menuStillOpenDuringClickDelay).toBe(true);
  expect(closingLayout.menuRemainsTopmostDuringClickDelay).toBe(true);
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

test("format tier guide stays compact until the user opens it", async ({ page }) => {
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
  const animationHeights = await guide.evaluate(async element => {
    element.querySelector("button").click();
    const start = element.getBoundingClientRect().height;
    await new Promise(resolve => setTimeout(resolve, 80));
    const middle = element.getBoundingClientRect().height;
    await new Promise(resolve => setTimeout(resolve, 220));
    const end = element.getBoundingClientRect().height;
    return { start, middle, end };
  });
  expect(animationHeights.middle).toBeGreaterThan(animationHeights.start);
  expect(animationHeights.middle).toBeLessThan(animationHeights.end);
  await expect(guide).toHaveClass(/is-open/);
  await expect(guideButton).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#formatGuideContent")).toHaveAttribute("aria-hidden", "false");
  await expect(page.getByText("IMAX 70mm · Dolby Cinema · IMAX with Laser", { exact: true })).toBeVisible();
  await expect(page.getByText("IMAX · 70mm · AMC PRIME · Cinemark XD · Regal RPX · AMC XL", { exact: true })).toBeVisible();
  await expect(page.getByText("35mm · RealD 3D · 4DX · ScreenX · D-BOX", { exact: true })).toBeVisible();
  await expect(page.locator(".format-tier-badge")).toHaveText(["S", "A", "B", "?"]);
  const tierGeometry = await page.evaluate(() => {
    const panel = document.querySelector(".format-guide-body");
    const lastTier = document.querySelector(".format-tier:last-child");
    const badgeOffsets = [...document.querySelectorAll(".format-tier")].map(tier => {
      const badge = tier.querySelector(".format-tier-badge").getBoundingClientRect();
      const row = tier.getBoundingClientRect();
      return Math.abs((badge.top + badge.bottom) / 2 - (row.top + row.bottom) / 2);
    });
    return {
      bottomSpace: panel.getBoundingClientRect().bottom - lastTier.getBoundingClientRect().bottom,
      panelPadding: Number.parseFloat(getComputedStyle(panel).paddingBottom),
      badgeOffsets,
    };
  });
  expect(tierGeometry.bottomSpace).toBeCloseTo(tierGeometry.panelPadding, 1);
  expect(tierGeometry.badgeOffsets.every(offset => offset <= 1)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

  const restingColor = await guideButton.evaluate(element => getComputedStyle(element).color);
  await guideButton.tap();
  await expect(guide).not.toHaveClass(/is-open/);
  await expect(guideButton).toHaveAttribute("aria-expanded", "false");
  await expect(guideButton).toHaveCSS("color", restingColor);
});

test("result sorting defaults to earliest and reruns the search when changed", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
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
  const beforeReorder = await page.evaluate(() => ({
    resultsMarkup: document.querySelector("#results").innerHTML,
    resultsHeight: document.querySelector("#results").getBoundingClientRect().height,
    scrollY: window.scrollY,
  }));

  await sortInput.selectOption("latest");
  await latestSearchStarted;
  const reorderScrollY = await page.evaluate(() => window.scrollY);
  const sortStatus = page.locator("#sortStatus");
  await expect(sortStatus).toContainText("Reordering");
  await expect(sortStatus.locator(".loading-dot")).toHaveCount(3);
  // Under reduced motion the dots stop cycling and stay visible instead.
  await expect(sortStatus.locator(".loading-dot-1")).toHaveCSS("opacity", "1");
  await expect(sortStatus.locator(".loading-dot-2")).toHaveCSS("opacity", "1");
  await expect(sortStatus.locator(".loading-dot-3")).toHaveCSS("opacity", "1");
  expect(await sortStatus.locator(".loading-dot-2").evaluate(dot => getComputedStyle(dot).animationName)).toBe("none");
  expect(await sortStatus.locator(".loading-dot-3").evaluate(dot => getComputedStyle(dot).animationName)).toBe("none");
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
  expect(duringReorder.scrollY).toBe(reorderScrollY);

  releaseLatestSearch();
  await expect.poll(() => searchSorts.at(-1)).toBe("latest");
  await expect(sortInput).toBeEnabled();
  await expect(page.locator("#sortStatus")).toBeEmpty();
  await page.waitForTimeout(50);
  const afterReorder = await page.evaluate(() => ({
    scrollY: window.scrollY,
    animationName: getComputedStyle(document.querySelector(".result")).animationName,
  }));
  expect(afterReorder.scrollY).toBe(reorderScrollY);
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
  const controlsBeforeLoading = await page.evaluate(() => {
    const controls = [...document.querySelectorAll("#pagination button")];
    return controls.map(control => {
      const rect = control.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    });
  });
  await nextPage.click();
  await secondPageStarted;

  await expect(pagination).toHaveAttribute("aria-busy", "true");
  await expect(pagination.locator("button:disabled")).toHaveCount(2);
  await expect(previousPage).toBeDisabled();
  await expect(nextPage).toBeDisabled();
  const controlsDuringLoading = await page.evaluate(() => {
    const controls = [...document.querySelectorAll("#pagination button")];
    return controls.map(control => {
      const rect = control.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    });
  });
  expect(controlsDuringLoading).toEqual(controlsBeforeLoading);
  await expect(page.locator("#sortInput")).toBeDisabled();
  await expect(pagination.locator(".pagination-label")).toHaveText("Loading...");
  await expect(page.getByRole("heading", { name: "Page One Cinema", exact: true })).toBeVisible();
  await expect(page.locator("#searchButton")).toContainText("Checking showtimes");
  await expect(pagination.locator(".pagination-label")).toHaveText("Loading...");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

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
  await selectMovie(page);
  await page.locator("#searchButton").click();

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
  await expect(page.getByText("Unavailable / excluded", { exact: true })).toBeVisible();
  await expect(page.getByText("Accessible", { exact: true })).toHaveCount(0);

  await page.getByText("Exclude accessible, companion, & wheelchair seats from matches", { exact: true }).click();
  await expect(page.locator("#excludeAccessibleInput")).not.toBeChecked();
  await page.locator("#searchButton").click();

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
  await expect(page.getByText("Accessible", { exact: true })).toBeVisible();
  await expect(page.getByText("Unavailable", { exact: true })).toBeVisible();

  const mobileLegendRows = await page.locator(".seat-map-legend").evaluate(legend => (
    [...legend.children].map(item => Math.round(item.getBoundingClientRect().top))
  ));
  expect(new Set(mobileLegendRows).size).toBe(1);

  await page.setViewportSize({ width: 320, height: 700 });
  const narrowLegendRows = await page.locator(".seat-map-legend").evaluate(legend => {
    const rowCounts = new Map();
    for (const item of legend.children) {
      const top = Math.round(item.getBoundingClientRect().top);
      rowCounts.set(top, (rowCounts.get(top) || 0) + 1);
    }
    return [...rowCounts.values()].sort();
  });
  expect(narrowLegendRows).toEqual([2, 2]);
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
