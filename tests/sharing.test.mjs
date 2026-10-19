import assert from "node:assert/strict";
import { compactShareUrl } from "../src/frontend/scripts/sharing.js";
import { inflateSync } from "node:zlib";
import test from "node:test";

const match = {
  date: "2026-10-25",
  displayTime: "7 PM",
  format: "IMAX",
  genres: [],
  movieTitle: "Spirited Away — 千と千尋",
  showtimeHashCode: "showtime-123",
  theatre: { address: "Main St", distanceMiles: 2.5, name: "Cinema" },
};

test("showtime links preserve public metadata and seat preferences without search location", () => {
  const url = new URL("https://movieseatfinder.com/");
  url.search = new URLSearchParams({
    adjacentSeats: "3",
    endDate: "2026-10-25",
    excludeAccessible: "1",
    format: "IMAX",
    lat: "40.75012345",
    lon: "-73.99098765",
    movie: "Spirited Away — 千と千尋",
    privateNote: "must never be shared",
    radius: "25",
    seatGrid: Array.from({ length: 225 }, (_, i) => `${Math.floor(i / 15)}:${i % 15}`).join(","),
    startDate: "2026-10-18",
    zip: "10001",
  }).toString();
  const compact = new URL(compactShareUrl(url.href, match));
  const decoded = inflateSync(Buffer.from(compact.pathname.slice(3), "base64url")).toString("utf8");
  const params = new URLSearchParams(decoded);
  assert.equal(params.has("lat"), false);
  assert.equal(params.has("lon"), false);
  assert.equal(params.has("privateNote"), false);
  assert.equal(params.has("zip"), false);
  assert.equal(params.has("radius"), false);
  assert.equal(params.has("distanceMiles"), false);
  assert.equal(params.get("theatre"), "Cinema");
  assert.equal(params.get("movie"), "Spirited Away — 千と千尋");
  const bitmap = Buffer.from(params.get("seatGrid"), "base64url");
  assert.equal(bitmap.length, 29);
  assert.deepEqual([...bitmap], [...new Uint8Array(28).fill(255), 1]);
  assert.equal(params.get("showtime"), "showtime-123");
});

test("a sender ZIP is not required to share a showtime", () => {
  assert.match(
    new URL(compactShareUrl("https://movieseatfinder.com/?adjacentSeats=2", match)).pathname,
    /^\/s\//u,
  );
});
