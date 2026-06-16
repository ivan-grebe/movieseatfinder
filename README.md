# Movie Seat Finder

Movie Seat Finder is a small local web app for finding movie showtimes by the seats that are actually available.

Pick a ZIP code, date range, movie, format, time window, and preferred seating area. The app checks live Fandango showtimes and seat maps, then shows matching performances with an auditorium preview so you can quickly see whether the seats are worth buying.

## Features

- Search nearby Fandango theatres by ZIP code and radius.
- Filter by movie, date range, format, and showtime window.
- Draw a preferred seating zone on a 15x15 auditorium grid.
- Exclude accessible and wheelchair seats from matches.
- View live Fandango seat maps with the real auditorium background when available.
- Page through results 20 at a time.
- Open matching showtimes directly on Fandango.

## How It Works

The local Python server calls Fandango web endpoints used by the public site:

- `/napi/theaterswithshowtimes` for theatres, movies, formats, and showtimes.
- `/napi/seatMap/{showtimeHashCode}` for seat availability and auditorium layout.

The browser talks only to the local server under `/api/*`. Seat matching happens locally after the server fetches each candidate seat map.

This is not an official Fandango partner API integration, so endpoint shapes may change.

## Run Locally

Requirements:

- Python 3.10+
- No third-party Python packages required

Start the server:

```bash
python server.py
```

Open the app:

```text
http://127.0.0.1:4173/index.html
```

## Notes

- The app binds to `127.0.0.1` and is intended for local use.
- Date searches are intended to stay small; the UI limits the end date to two weeks from the start date.
- Results depend on Fandango availability and reserved-seat data.
- Some theatres or formats may not expose seat maps for every showtime.

## Related

A FastAPI/Vercel-ready rewrite lives at:

```text
https://github.com/ivan-grebe/movieseatfinder-fastapi
```
