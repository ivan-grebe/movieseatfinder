# Movie Seat Finder

[![Tests](https://github.com/ivan-grebe/movieseatfinder/actions/workflows/tests.yml/badge.svg)](https://github.com/ivan-grebe/movieseatfinder/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Movie Seat Finder is a local web app for finding movie showtimes by the seats
that are actually available.

Pick a ZIP code, date range, movie, format, time window, and preferred seating
area. The app checks live Fandango showtimes and seat maps, then shows matching
performances with an auditorium preview.

## Features

- Search nearby Fandango theatres by ZIP code and radius.
- Filter by movie, date range, format, and showtime window.
- Draw a preferred seating zone on a 15x15 auditorium grid.
- Exclude accessible and wheelchair seats from matches.
- Preview live seat maps with the real auditorium background when available.
- Page through results 20 at a time.
- Open matching showtimes directly on Fandango.

## How It Works

The Python server calls Fandango web endpoints used by the public site:

- `/napi/theaterswithshowtimes` for theatres, movies, formats, and showtimes.
- `/napi/seatMap/{showtimeHashCode}` for seat availability and auditorium layout.

The browser talks only to the local server under `/api/*`. Seat matching happens
locally after the server fetches each candidate seat map.

This is not an official Fandango partner API integration, so endpoint shapes
may change.

## Run Locally

Requirements:

- Python 3.10+
- No third-party Python packages

```bash
python run.py
```

Then open `http://127.0.0.1:4173/`.

## Project Structure

```text
.
|-- backend/       # HTTP server, upstream integrations, and seat matching
|-- frontend/      # HTML, CSS, JavaScript, and static assets
|-- tests/         # Offline unit tests
|-- .github/       # GitHub Actions workflow
`-- run.py         # Local entry point
```

## Tests

The tests use Python's standard-library `unittest` runner and make no live
network requests:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions runs the suite on Python 3.10 through 3.13 for every pull
request and push to `main`.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the development workflow.

## License

Released under the [MIT License](LICENSE).
