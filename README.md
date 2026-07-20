# Movie Seat Finder

[![Tests](https://github.com/ivan-grebe/movieseatfinder/actions/workflows/tests.yml/badge.svg)](https://github.com/ivan-grebe/movieseatfinder/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Production-ready FastAPI and Vercel version of Movie Seat Finder. It searches
live Fandango showtimes and seat maps, then finds adjacent available seats
inside a user-selected auditorium region.

## Features

- Live theatre, movie, format, showtime, and seat-map lookup.
- Exact radius filtering from a ZIP centroid or, with permission, the browser's precise location.
- Adjacent-seat matching with custom auditorium regions.
- Accessible-seat filtering and normalized seat-map previews.
- Bounded TTL caches and parallel upstream requests.
- Input validation, API rate limiting, security headers, and ticket URL checks.
- Dynamic canonical metadata, Open Graph tags, `robots.txt`, and sitemap.

## Run Locally

Requirements:

- Python 3.12+

```bash
pip install -e .
uvicorn app:app --reload --host 127.0.0.1 --port 4173
```

Then open `http://127.0.0.1:4173/`.

## Project Structure

```text
.
|-- backend/       # FastAPI application and seat-search logic
|-- frontend/      # HTML, CSS, JavaScript, and static assets
|-- tests/         # Offline unit and route tests
|-- .github/       # GitHub Actions workflow
`-- app.py         # Vercel-compatible application entry point
```

## Tests

```bash
pip install -e ".[test]"
python -m unittest discover -s tests -v
```

GitHub Actions runs the suite on Python 3.12 and 3.13 for every pull request,
push to `main`, and every day at 09:17 UTC.

## Vercel

Vercel detects the top-level FastAPI `app` exported from `app.py`. Deploy the
repository root and set `SITE_URL` to the production origin:

```text
SITE_URL=https://movieseatfinder.com
```

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE).
