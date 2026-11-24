import assert from "node:assert/strict";
import { createRunGuard } from "../src/scripts/utils.js";
import test from "node:test";

test("starting a run aborts the previous run's signal", () => {
  const guard = createRunGuard();
  const first = guard.start();
  const second = guard.start();

  assert.equal(first.aborted, true);
  assert.equal(second.aborted, false);
});

test("cancelling aborts the current run", () => {
  const guard = createRunGuard();
  const run = guard.start();

  guard.cancel();
  assert.equal(run.aborted, true);
});
