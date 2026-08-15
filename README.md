<p align="center">
  <img src="branding/movie-seat-finder-dark-4k.png" alt="Movie Seat Finder search form in dark mode" width="100%">
</p>

<h1 align="center">Movie Seat Finder</h1>

<p align="center">
  <em>Find the perfect movie seats before you buy.</em>
</p>

<p align="center">
  <a href="https://github.com/ivan-grebe/movieseatfinder/actions/workflows/tests.yml"><img src="https://github.com/ivan-grebe/movieseatfinder/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
</p>

Movie Seat Finder searches live Fandango showtimes and seat maps, then finds adjacent available seats in the part of the auditorium you choose.

## Features

- Search by ZIP code or your precise browser location.
- Filter by theatre, movie, multiple formats, dates, and times.
- Find adjacent seats in a custom auditorium region.
- Preview normalized seat maps and exclude accessible seats when needed.
- Use guarded API routes with validation, rate limiting, and safe ticket URLs.
- Connect messaging assistants through an authenticated MCP movie-seat search tool.

## Run locally

Requirements: Python 3.12+ and Node.js 22+.

```bash
git clone https://github.com/ivan-grebe/movieseatfinder.git
cd movieseatfinder
pip install -e ".[test]"
npm ci
npm run build:frontend
uvicorn backend.server:app --reload --host 127.0.0.1 --port 4173
```

Open [http://127.0.0.1:4173/](http://127.0.0.1:4173/) to use the app.

## Poke / MCP integration

The production app exposes a stateless Streamable HTTP MCP server at `/mcp`. It uses a discovery-first flow:

- `get_location_and_movie_info` finds live theatres, titles, dates, and formats.
- `find_movie_seats` searches an exact discovered title and format for a party, time window, and optional 15x15 `row:column` seat region.
- `show_movie_seat_map` refreshes a selected showtime with a signed five-minute token.

Agents should discover and reuse exact titles and formats before searching, then offer compact nearest-first results with a seat map or ticket link.

For a public Poke Recipe, use:

```text
URL: https://movieseatfinder.com/mcp
Authentication: None
```

Public Recipes need no key. Private integrations may use `MCP_API_KEY`; set `MCP_SELECTION_SECRET` to a stable random deployment secret. `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` optionally override the production allowlists.

## Testing

```bash
python -m unittest discover -s tests -v
npm run check:frontend-bundle
npm run test:frontend
npm run test:mobile
```

GitHub Actions runs these checks on every pull request, each push to `main`, and daily at 09:17 UTC.

## Support the project

If Movie Seat Finder is useful, consider giving the repository a ⭐. It helps others discover it and supports continued improvements.

## License

Released under the [MIT License](LICENSE).
