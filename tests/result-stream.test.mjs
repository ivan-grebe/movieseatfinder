import assert from "node:assert/strict";
import { streamSearch } from "../src/scripts/result-stream.js";
import test from "node:test";

function abortableFetch(_url, { signal }) {
  return new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

function startSearch() {
  return streamSearch(new URLSearchParams(), "earliest");
}

test("cancelling before a response rejects existing and future waiters", async (t) => {
  t.mock.method(globalThis, "fetch", abortableFetch);
  const results = startSearch();
  const first = assert.rejects(
    results.until(() => false),
    { name: "AbortError" },
  );
  const second = assert.rejects(
    results.until(() => false),
    { name: "AbortError" },
  );

  results.abort();
  results.abort();
  assert.equal(results.state, "cancelled");
  await Promise.all([first, second]);
  await assert.rejects(
    results.until(() => true),
    { name: "AbortError" },
  );
});

test("cancelling while reading the body rejects a pending page wait", async (t) => {
  const started = Promise.withResolvers();

  t.mock.method(globalThis, "fetch", (_url, { signal }) => {
    const body = new ReadableStream({
      pull() {
        started.resolve();
      },
      start(controller) {
        signal.addEventListener("abort", () => controller.error(signal.reason), { once: true });
      },
    });

    return Promise.resolve(new Response(body));
  });
  const results = startSearch();
  const pending = assert.rejects(
    results.until((current) => current.matches.length > 20),
    {
      name: "AbortError",
    },
  );

  await started.promise;
  results.abort();
  await pending;
  assert.equal(results.state, "cancelled");
});

test("a late response cannot revive a cancelled search", async (t) => {
  const response = Promise.withResolvers();

  t.mock.method(globalThis, "fetch", () => response.promise);
  const results = startSearch();

  results.abort();
  response.resolve(new Response(`${JSON.stringify({ complete: true, type: "done" })}\n`));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(results.state, "cancelled");
  await assert.rejects(
    results.until(() => false),
    { name: "AbortError" },
  );
});

for (const complete of [true, false]) {
  test(`aborting preserves a ${complete ? "complete" : "capped"} search`, async (t) => {
    t.mock.method(globalThis, "fetch", () =>
      Promise.resolve(new Response(`${JSON.stringify({ complete, type: "done" })}\n`)),
    );
    const results = startSearch();

    await results.until(() => false);
    results.abort();
    assert.equal(results.state, complete ? "complete" : "capped");
    assert.equal(await results.until(() => false), results);
  });
}

test("aborting a failed search preserves the original error", async (t) => {
  const error = new TypeError("Network failure");

  t.mock.method(globalThis, "fetch", () => Promise.reject(error));
  const results = startSearch();

  await assert.rejects(
    results.until(() => false),
    error,
  );
  results.abort();
  assert.equal(results.state, "failed");
  await assert.rejects(
    results.until(() => false),
    error,
  );
});
