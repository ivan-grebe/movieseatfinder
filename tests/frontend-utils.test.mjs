import assert from "node:assert/strict";
import test from "node:test";

import { pickAmbientTarget } from "../src/frontend/scripts/ambient-motion.js";
import { addDays, getJson, todayString } from "../src/frontend/scripts/utils.js";
import { logTicketClick } from "../src/frontend/scripts/tracking.js";

test("ambient targets stay bounded and a useful distance from the current glow position", () => {
  const bounds = { x: [-20, 10], y: [0, 18], minDistance: 12 };
  const first = pickAmbientTarget({ x: 0, y: 0 }, bounds, () => 0);
  const second = pickAmbientTarget(first, bounds, () => 1);

  for (const target of [first, second]) {
    assert.ok(target.x >= bounds.x[0] && target.x <= bounds.x[1]);
    assert.ok(target.y >= bounds.y[0] && target.y <= bounds.y[1]);
  }
  assert.ok(Math.hypot(first.x, first.y) >= bounds.minDistance);
  assert.ok(Math.hypot(second.x - first.x, second.y - first.y) >= bounds.minDistance);
});

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  };
}

async function withFetch(fakeFetch, run) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fakeFetch;
  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test("getJson returns a successful JSON response", async () => {
  await withFetch(async () => response(200, '{"movies":[]}'), async () => {
    assert.deepEqual(await getJson("/api/movies"), { movies: [] });
  });
});

test("getJson surfaces API error messages", async () => {
  await withFetch(async () => response(400, '{"error":"Enter a movie title."}'), async () => {
    await assert.rejects(getJson("/api/search"), /Enter a movie title/);
  });
});

test("getJson preserves API error routing metadata", async () => {
  await withFetch(
    async () => response(400, '{"error":"ZIP not found.","code":"location"}'),
    async () => {
      await assert.rejects(
        getJson("/api/theatres"),
        error => error.message === "ZIP not found."
          && error.code === "location"
          && error.status === 400,
      );
    },
  );
});

test("getJson never exposes a JSON parser error for an HTML server failure", async () => {
  await withFetch(async () => response(500, "Internal Server Error"), async () => {
    await assert.rejects(
      getJson("/api/search"),
      /search service is temporarily unavailable/,
    );
  });
});

test("date helpers preserve calendar dates in the browser's timezone", () => {
  const originalTimezone = process.env.TZ;
  try {
    process.env.TZ = "America/New_York";
    assert.equal(todayString(new Date("2026-07-23T00:26:31Z")), "2026-07-22");

    process.env.TZ = "Pacific/Kiritimati";
    assert.equal(addDays("2026-07-22", 7), "2026-07-29");
  } finally {
    if (originalTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = originalTimezone;
  }
});

test("ticket click tracking uses a non-blocking beacon", () => {
  const originalNavigator = globalThis.navigator;
  const calls = [];
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: (url) => calls.push(url) > 0 },
  });
  try {
    logTicketClick();
    assert.deepEqual(calls, ["/api/events/ticket-click"]);
  } finally {
    if (originalNavigator === undefined) delete globalThis.navigator;
    else Object.defineProperty(globalThis, "navigator", { configurable: true, value: originalNavigator });
  }
});

test("ticket click tracking falls back when a beacon cannot be queued", async () => {
  const originalNavigator = globalThis.navigator;
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: () => false },
  });
  try {
    await withFetch(async (url, options) => {
      assert.equal(url, "/api/events/ticket-click");
      assert.deepEqual(options, { method: "POST", keepalive: true });
      return response(204, "");
    }, async () => logTicketClick());
  } finally {
    if (originalNavigator === undefined) delete globalThis.navigator;
    else Object.defineProperty(globalThis, "navigator", { configurable: true, value: originalNavigator });
  }
});
