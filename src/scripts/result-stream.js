/**
 * Streams a search's matches from /api/search/stream and answers later page, sort and
 * narrowing requests from what has loaded, when that is enough to be correct.
 */

/** Where a streamed search is in its life. */
export const StreamState = Object.freeze({
  // Finished at the seat-map cap without reaching every showtime.
  Capped: "capped",
  // Finished after checking every showtime.
  Complete: "complete",
  // Stopped by an error; `error` says why.
  Failed: "failed",
  Streaming: "streaming",
});

/*
 * Params a later search may narrow and still be answered locally; any other change to the
 * params needs a new search.
 */
const NARROWABLE_PARAMS = new Set([
  "endTime",
  "excludeTheatre",
  "format",
  "includeTheatre",
  "startTime",
]);

function chronology(match) {
  return Number(`${match.date.replaceAll("-", "")}${match.time.replace(":", "")}`);
}

/** The same order as the server's search_sort_key. */
function compareMatches(sort) {
  return (a, b) => {
    const byTime = chronology(a) - chronology(b);
    const byDistance = a.theatre.distanceMiles - b.theatre.distanceMiles;
    const byName = a.theatre.name.localeCompare(b.theatre.name);

    if (sort === "nearest") {
      return byDistance || byTime || byName;
    }

    if (sort === "latest") {
      return -byTime || byDistance || byName;
    }

    return byTime || byDistance || byName;
  };
}

function lowerNames(params, name) {
  return new Set(params.getAll(name).map((value) => value.toLowerCase()));
}

function theatreAllowed(params) {
  const included = lowerNames(params, "includeTheatre");
  const excluded = lowerNames(params, "excludeTheatre");

  return (name) => {
    const lower = name.toLowerCase();

    return (included.size === 0 || included.has(lower)) && !excluded.has(lower);
  };
}

/** Whether every theatre the new filter allows was also allowed by the old one. */
function theatresNarrowed(before, after) {
  const beforeIncluded = lowerNames(before, "includeTheatre");
  const afterIncluded = lowerNames(after, "includeTheatre");

  if (afterIncluded.size > 0) {
    const allowedBefore = theatreAllowed(before);

    return [...afterIncluded].every((name) => allowedBefore(name));
  }

  if (beforeIncluded.size > 0) {
    // "All except some" can't be checked against an include list without the full theatre list.
    return false;
  }

  const afterExcluded = lowerNames(after, "excludeTheatre");

  return [...lowerNames(before, "excludeTheatre")].every((name) => afterExcluded.has(name));
}

function formatsNarrowed(before, after) {
  if (before.get("format") === "any") {
    return true;
  }

  const allowed = new Set(before.get("format").split(","));

  return (
    after.get("format") !== "any" &&
    after
      .get("format")
      .split(",")
      .every((format) => allowed.has(format))
  );
}

function sameOtherParams(before, after) {
  const keys = new Set([...before.keys(), ...after.keys()]);

  return [...keys].every(
    (key) =>
      NARROWABLE_PARAMS.has(key) ||
      before.getAll(key).join("\u0000") === after.getAll(key).join("\u0000"),
  );
}

/** A finished, fully covered result set derived locally from another one. */
function derived(results, overrides) {
  const finished = {
    ...results,
    ...overrides,
  };

  finished.until = () => Promise.resolve(finished);
  return finished;
}

/** Open a streamed search. Its state updates as events arrive; `until` waits on it. */
export function streamSearch(params, sort) {
  const controller = new AbortController();
  const waiters = new Set();
  const results = {
    abort: () => controller.abort(),
    accessibleSeatsExcluded: true,
    checkedSeatMaps: 0,
    checkedShowtimes: 0,
    error: null,
    failedSeatMaps: 0,
    matches: [],
    params: params,
    sort: sort,
    state: StreamState.Streaming,
    until: until,
    zipCode: "",
  };

  function notify() {
    waiters.forEach((waiter) => waiter());
  }

  /** Resolve once `condition(results)` holds or the stream ends; reject on a stream error. */
  function until(condition) {
    return new Promise((resolve, reject) => {
      function check() {
        if (results.state === StreamState.Failed) {
          waiters.delete(check);
          reject(results.error);
        } else if (results.state !== StreamState.Streaming || condition(results)) {
          waiters.delete(check);
          resolve(results);
        }
      }

      waiters.add(check);
      check();
    });
  }

  function apply(event) {
    if (event.type === "start") {
      results.zipCode = event.zipCode;
      results.checkedShowtimes = event.checkedShowtimes;
      results.accessibleSeatsExcluded = event.accessibleSeatsExcluded;
    } else {
      results.checkedSeatMaps = event.checkedSeatMaps;
      results.failedSeatMaps = event.failedSeatMaps;
      if (event.type === "match") {
        results.matches.push(event.match);
      } else {
        results.state = event.complete ? StreamState.Complete : StreamState.Capped;
      }
    }
  }

  async function read() {
    const query = new URLSearchParams(params);

    query.set("sort", sort);
    const response = await fetch(`/api/search/stream?${query}`, {
      signal: controller.signal,
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "The search service is temporarily unavailable.");
    }

    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";

    for (;;) {
      // Events must be applied in order, so each read waits for the previous one.
      // oxlint-disable-next-line no-await-in-loop
      const { done, value } = await reader.read();

      if (done) {
        break;
      }

      buffer += value;
      const lines = buffer.split("\n");

      buffer = lines.pop();
      lines.forEach((line) => apply(JSON.parse(line)));
      notify();
    }

    if (results.state === StreamState.Streaming) {
      throw new Error("The search stopped early. Please try again.");
    }
  }

  read().catch((error) => {
    if (error.name !== "AbortError") {
      results.error = error;
      results.state = StreamState.Failed;
    }

    notify();
  });
  return results;
}

/** The same results in another order, if they cover every showtime; otherwise null. */
export function resortedResults(results, sort) {
  if (results.state !== StreamState.Complete) {
    return null;
  }

  return derived(results, {
    matches: results.matches.toSorted(compareMatches(sort)),
    sort: sort,
  });
}

/**
 * The results a new search would return, if it only narrows theatres, formats or the time
 * window of a fully covered search; otherwise null. An identical search is null, so it fetches
 * live availability again.
 */
export function narrowedResults(results, params) {
  const before = results.params;

  if (
    results.state !== StreamState.Complete ||
    before.toString() === params.toString() ||
    !sameOtherParams(before, params) ||
    !theatresNarrowed(before, params) ||
    !formatsNarrowed(before, params) ||
    params.get("startTime") < before.get("startTime") ||
    params.get("endTime") > before.get("endTime")
  ) {
    return null;
  }

  const allowedTheatre = theatreAllowed(params);
  let formats = null;

  if (params.get("format") !== "any") {
    formats = new Set(params.get("format").split(","));
  }

  const startTime = params.get("startTime");
  const endTime = params.get("endTime");

  return derived(results, {
    matches: results.matches.filter(
      (match) =>
        allowedTheatre(match.theatre.name) &&
        (!formats || formats.has(match.format)) &&
        match.time >= startTime &&
        match.time <= endTime,
    ),
    params: params,
  });
}

/** One page in the shape /api/search returns. */
export function pageOf(results, page, pageSize) {
  const end = page * pageSize;

  return {
    accessibleSeatsExcluded: results.accessibleSeatsExcluded,
    checkedSeatMaps: results.checkedSeatMaps,
    checkedShowtimes: results.checkedShowtimes,
    failedSeatMaps: results.failedSeatMaps,
    hasNextPage: results.matches.length > end,
    hasPreviousPage: page > 1,
    matches: results.matches.slice(end - pageSize, end),
    page: page,
    pageSize: pageSize,
    zipCode: results.zipCode,
  };
}
