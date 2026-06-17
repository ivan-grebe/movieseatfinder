# Movie Seat Finder FastAPI

FastAPI/Vercel-ready version of Movie Seat Finder. It serves the static UI from `static/` and exposes the same API routes under `/api/*`.

## Local run

```bash
pip install -e .
uvicorn app:app --reload --host 127.0.0.1 --port 4173
```

Then open:

```text
http://127.0.0.1:4173/
```

## Vercel

Vercel's Python runtime detects the top-level FastAPI `app` in `app.py`. The Python version is pinned with `.python-version`, and dependencies live in `pyproject.toml`.

Deploy the repo root as the Vercel project.

Set `SITE_URL` in Vercel once the production URL is known, for example:

```text
SITE_URL=https://movieseatfinder.com
```

That value is used for canonical tags, Open Graph URLs, `robots.txt`, and `sitemap.xml`.
