import assert from "node:assert/strict";
import test from "node:test";

import { addDays, getJson, todayString } from "../frontend/utils.js";

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
