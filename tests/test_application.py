import sys
import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import application, seat_matching


class DateAndValidationTests(unittest.TestCase):
    def test_date_range_is_inclusive(self):
        self.assertEqual(
            list(application.date_range(date(2026, 6, 1), date(2026, 6, 3))),
            ["2026-06-01", "2026-06-02", "2026-06-03"],
        )

    def test_date_range_rejects_backwards_dates(self):
        with self.assertRaisesRegex(ValueError, "on or after"):
            list(application.date_range(date(2026, 6, 3), date(2026, 6, 1)))

    def test_date_range_rejects_more_than_two_weeks(self):
        with self.assertRaisesRegex(ValueError, "14 days"):
            list(application.date_range(date(2026, 6, 1), date(2026, 6, 16)))

    def test_search_sort_orders_are_deterministic(self):
        showtimes = [
            {"name": "Near Late", "distance": 1, "date": "2026-08-02", "time": "21:00"},
            {"name": "Far Early", "distance": 8, "date": "2026-08-01", "time": "10:00"},
            {"name": "Near Early", "distance": 1, "date": "2026-08-01", "time": "18:00"},
        ]

        def ordered_names(sort_order):
            return [
                item["name"]
                for item in sorted(showtimes, key=lambda item: application.search_sort_key(
                    item["distance"], item["date"], item["time"], item["name"], sort_order,
                ))
            ]

        self.assertEqual(ordered_names("earliest"), ["Far Early", "Near Early", "Near Late"])
        self.assertEqual(ordered_names("latest"), ["Near Late", "Near Early", "Far Early"])
        self.assertEqual(ordered_names("nearest"), ["Near Early", "Near Late", "Far Early"])

    def test_ticket_urls_are_allowlisted(self):
        self.assertEqual(
            application.safe_fandango_url("https://tickets.fandango.com/order"),
            "https://tickets.fandango.com/order",
        )
        self.assertEqual(application.safe_fandango_url("https://example.com"), "")
        self.assertEqual(application.safe_fandango_url("javascript:alert(1)"), "")


class MovieAndFormatTests(unittest.TestCase):
    def test_movie_matching_requires_the_selected_title(self):
        self.assertTrue(application.movie_matches("Spider-Man: Homecoming", "spider man homecoming"))
        self.assertFalse(application.movie_matches("Spider-Man: Homecoming", "spider man"))
        self.assertFalse(application.movie_matches("", "spider man"))

    def test_format_matching_distinguishes_imax_variants(self):
        self.assertTrue(application.format_matches("IMAX", "", "imax"))
        self.assertFalse(application.format_matches("IMAX 70mm", "", "imax"))
        self.assertTrue(application.format_matches("IMAX 70mm", "", "imax70"))

    def test_format_matching_accepts_amenity_aliases(self):
        self.assertTrue(application.format_matches("Standard", "Dolby Cinema", "dolby"))
        self.assertTrue(application.format_matches("Special Event", "70 mm presentation", "70mm"))
        self.assertTrue(application.format_matches("IMAX", "", "dolby,imax"))
        self.assertFalse(application.format_matches("Standard", "", "dolby,imax"))

    def test_movie_metadata_is_normalized(self):
        movie = {
            "poster": {"size": {"200": "poster.jpg"}},
            "rating": "PG13",
            "runtime": 155,
            "genres": ["Drama", "Science Fiction", "Adventure"],
        }
        self.assertEqual(
            application.movie_meta(movie),
            {
                "poster": "poster.jpg",
                "rating": "PG-13",
                "runtime": "2h 35m",
                "genres": ["Drama", "Science Fiction"],
            },
        )

    def test_showtime_uses_the_specific_format_and_compact_time(self):
        movie = {
            "title": "Test Movie",
            "variants": [{
                "filmFormatHeader": "Premium Format",
                "amenityGroups": [{
                    "amenities": [{"name": "IMAX with Laser"}],
                    "showtimes": [{
                        "type": "available",
                        "ticketingDate": "2026-07-20+18:00",
                        "filmFormat": [{"filterName": "IMAX"}],
                    }],
                }],
            }],
        }

        showtime = application.normalize_showtimes([movie])[0]

        self.assertEqual(showtime["format"], "IMAX")
        self.assertEqual(showtime["displayTime"], "6:00 PM")
        self.assertNotIn("o'clock", showtime["displayTime"])


class SeatSelectionTests(unittest.TestCase):
    def setUp(self):
        self.seats = [
            {"id": "A1", "row": 0, "column": 1, "x": 10, "y": 0, "status": "A", "type": "standard"},
            {"id": "A2", "row": 0, "column": 2, "x": 20, "y": 0, "status": "A", "type": "standard"},
            {"id": "A3", "row": 0, "column": 3, "x": 30, "y": 0, "status": "U", "type": "standard"},
            {"id": "B1", "row": 1, "column": 1, "x": 10, "y": 10, "status": "A", "type": "wheelchair"},
            {"id": "B2", "row": 1, "column": 2, "x": 20, "y": 10, "status": "A", "type": "standard"},
            {"id": "B3", "row": 1, "column": 3, "x": 30, "y": 10, "status": "A", "type": "companion"},
        ]

    def test_grid_parser_filters_invalid_cells(self):
        self.assertEqual(
            seat_matching.parse_seat_grid("0:0,14:14,15:0,bad,2:x"),
            [(0, 0), (14, 14)],
        )

    def test_adjacent_blocks_require_available_contiguous_seats(self):
        blocks = seat_matching.adjacent_blocks(self.seats, 2, [], False)
        self.assertIn(["A1", "A2"], blocks)
        self.assertNotIn(["A2", "A3"], blocks)

    def test_accessible_seats_can_be_excluded(self):
        blocks = seat_matching.adjacent_blocks(self.seats, 1, [], True)
        seat_ids = {seat_id for block in blocks for seat_id in block}
        self.assertNotIn("B1", seat_ids)
        self.assertNotIn("B3", seat_ids)
        self.assertIn("B2", seat_ids)


class CacheTests(unittest.TestCase):
    def setUp(self):
        seat_cache = patch.object(
            application,
            "SEAT_MAP_CACHE",
            application.TtlCache("seat_map", ttl_seconds=300, max_entries=200),
        )
        theatres_cache = patch.object(
            application,
            "THEATRES_CACHE",
            application.TtlCache("theatres", ttl_seconds=300, max_entries=240),
        )
        seat_cache.start()
        theatres_cache.start()
        self.addCleanup(seat_cache.stop)
        self.addCleanup(theatres_cache.stop)

    def test_application_info_logs_use_stdout(self):
        self.assertTrue(any(
            getattr(handler, "stream", None) is sys.stdout
            for handler in application.LOGGER.handlers
        ))

    @patch("backend.application.LOGGER.info")
    @patch("backend.application.fandango_json")
    def test_seat_maps_are_cached(self, fandango_json, logger_info):
        fandango_json.return_value = {"seats": []}
        first = application.seat_map("showtime-1")
        second = application.seat_map("showtime-1")
        self.assertIs(first, second)
        fandango_json.assert_called_once()
        logger_info.assert_called_once()
        self.assertEqual(
            logger_info.call_args.args[:2],
            ("event=cache_hit cache=%s age_ms=%d", "seat_map"),
        )

    @patch("backend.application.LOGGER.info")
    @patch("backend.application._fetch_fandango_theatres", return_value=[])
    def test_theatre_payloads_are_cached(self, fetch_theatres, logger_info):
        origin = (40.75, -73.99)
        first = application.fandango_theatres("10001", 25, origin)
        second = application.fandango_theatres("10001", 25, origin)

        self.assertIs(first, second)
        fetch_theatres.assert_called_once()
        logger_info.assert_called_once()
        self.assertEqual(
            logger_info.call_args.args[:2],
            ("event=cache_hit cache=%s age_ms=%d", "theatres"),
        )

    @patch("backend.application._fetch_fandango_theatres", return_value=[])
    def test_large_radius_uses_fandangos_full_supported_range(self, fetch_theatres):
        application.fandango_theatres("10001", 100, (40.75, -73.99))

        self.assertEqual(fetch_theatres.call_args.args[1], 100)

    @patch(
        "backend.application.fandango_theatres",
        side_effect=application.requests.RequestException("upstream down"),
    )
    def test_all_date_failures_are_propagated(self, fandango_theatres):
        with self.assertRaisesRegex(application.requests.RequestException, "upstream down"):
            application.fandango_theatres_by_date(
                "10001", 25, ["2026-07-22", "2026-07-23"], (40.75, -73.99)
            )


class LiveFandangoIntegrationTests(unittest.TestCase):
    """Contract check for the upstream endpoint used by production searches."""

    def test_theatre_endpoint_returns_live_theatre_payloads(self):
        payload = application.fandango_json(
            "/napi/theaterswithshowtimes",
            {"zipCode": "10001", "radius": 5, "limit": 5},
            timeout=30,
        )

        self.assertIsInstance(payload.get("theaters"), list)
        self.assertGreater(len(payload["theaters"]), 0)
        theatre = payload["theaters"][0]
        self.assertTrue(theatre.get("name"))
        self.assertIn("geo", theatre)
        self.assertIn("latitude", theatre["geo"])
        self.assertIn("longitude", theatre["geo"])


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(application.app)

    def setUp(self):
        rate_limiter = patch.object(
            application,
            "RATE_LIMITER",
            application.MovingWindowRateLimiter(application.MemoryStorage()),
        )
        theatres_cache = patch.object(
            application,
            "THEATRES_CACHE",
            application.TtlCache("theatres", ttl_seconds=300, max_entries=240),
        )
        rate_limiter.start()
        theatres_cache.start()
        self.addCleanup(rate_limiter.stop)
        self.addCleanup(theatres_cache.stop)

    def test_homepage_renders_context_and_security_headers(self):
        response = self.client.get("/", headers={"host": "example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Movie Seat Finder", response.text)
        self.assertNotIn("__SITE_", response.text)
        self.assertNotIn("__INLINE_STYLES__", response.text)
        self.assertIn(f"<style>{application.INLINE_STYLES}</style>", response.text)
        self.assertNotIn('rel="stylesheet"', response.text)
        self.assertIn(
            f'src="app.bundle.js?v={application.ASSET_VERSIONS["app.bundle.js"]}"',
            response.text,
        )
        self.assertIn(
            f'href="/favicon.svg?v={application.ASSET_VERSIONS["favicon.svg"]}"',
            response.text,
        )
        self.assertNotIn("__BUNDLE_VERSION__", response.text)
        self.assertNotIn("__FAVICON_VERSION__", response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        content_security_policy = response.headers["content-security-policy"]
        self.assertIn("default-src 'self'", content_security_policy)
        self.assertIn(
            f"'sha256-{application.INLINE_STYLE_HASH}'",
            content_security_policy,
        )

    def test_raw_template_and_frontend_sources_are_not_served(self):
        redirect = self.client.get("/index.html", follow_redirects=False)
        self.assertEqual(redirect.status_code, 308)
        self.assertEqual(redirect.headers["location"], "/")
        for source_path in ("/app.js", "/ui.js", "/results.js", "/styles.css"):
            self.assertEqual(self.client.get(source_path).status_code, 404, source_path)
        for public_path in ("/app.bundle.js", "/favicon.svg", "/og-image.png"):
            self.assertEqual(self.client.get(public_path).status_code, 200, public_path)

    def test_versioned_assets_receive_immutable_browser_and_cdn_caching(self):
        response = self.client.get("/app.bundle.js?v=20260801-performance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            response.headers["cdn-cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            response.headers["vercel-cdn-cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertNotIn("import ", response.text)

    def test_unversioned_assets_are_not_cached_as_immutable(self):
        response = self.client.get("/app.bundle.js")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("immutable", response.headers.get("cache-control", ""))

    def test_missing_versioned_assets_are_not_cached_as_immutable(self):
        response = self.client.get("/missing.js?v=missing")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("immutable", response.headers.get("cache-control", ""))

    @patch("backend.application.LOGGER.info")
    def test_ticket_click_is_logged_without_request_data(self, logger_info):
        response = self.client.post("/api/events/ticket-click")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        logger_info.assert_called_once_with("event=ticket_click")

    def test_homepage_rejects_markup_in_forwarded_host(self):
        injected_host = 'attacker.example\"><meta name="injected" content="yes'
        response = self.client.get("/", headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": injected_host,
        })

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<meta name="injected"', response.text)
        self.assertIn('<link rel="canonical" href="http://testserver/">', response.text)

    @patch("backend.application.movies_from_dated_theatre_payloads", return_value=[])
    @patch("backend.application.resolve_search_location", return_value=("00000", (40.0, -75.0), "Testville"))
    def test_movie_endpoint_does_not_broaden_empty_filtered_results(
        self, resolve_search_location, movies_from_payloads
    ):
        response = self.client.get("/api/movies", params={
            "zip": "00000",
            "radius": 25,
            "startDate": "2026-07-22",
            "endDate": "2026-07-22",
            "theatre": "Selected Cinema",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"movies": []})
        self.assertEqual(movies_from_payloads.call_args.args[4], "selected cinema")

    @patch(
        "backend.application.movies_from_dated_theatre_payloads",
        side_effect=application.requests.RequestException("upstream down"),
    )
    @patch("backend.application.resolve_search_location", return_value=("00000", (40.0, -75.0), "Testville"))
    def test_movie_endpoint_reports_total_upstream_failure(
        self, resolve_search_location, movies_from_payloads
    ):
        response = self.client.get("/api/movies", params={
            "zip": "00000",
            "radius": 25,
            "startDate": "2026-07-22",
            "endDate": "2026-07-22",
        })

        self.assertEqual(response.status_code, 502)
        self.assertIn("Could not load real movie data", response.json()["error"])

    def test_invalid_zip_returns_json_error(self):
        response = self.client.get("/api/theatres", params={"zip": "abc", "radius": 25})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Enter a valid 5 digit US ZIP code or use your location."})

    @patch(
        "backend.application.resolve_search_location",
        side_effect=application.ZipNotFoundError("We couldn't find that ZIP code. Check it and try again."),
    )
    def test_unknown_zip_is_identified_as_a_location_field_error(self, resolve_search_location):
        for endpoint in ("/api/theatres", "/api/movies"):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint, params={"zip": "00000", "radius": 25})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {
                        "error": "We couldn't find that ZIP code. Check it and try again.",
                        "code": "location",
                    },
                )

    def test_missing_radius_uses_the_standard_validation_error(self):
        response = self.client.get("/api/theatres", params={"zip": "10001"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"error": "One of the search values is invalid. Adjust the form and try again."},
        )

    def test_unparseable_params_use_the_same_error_shape(self):
        response = self.client.get("/api/theatres", params={"zip": "10001", "radius": "abc"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_search_constraints_are_enforced_before_upstream_work(self):
        base_params = {"zip": "10001", "radius": 25, "movie": "Test Movie"}
        invalid_params = [
            {"startTime": "25:00"},
            {"adjacentSeats": 0},
            {"page": 0},
            {"pageSize": 51},
            {"sort": "random"},
            {"movie": "   "},
        ]

        for invalid in invalid_params:
            with self.subTest(invalid=invalid):
                response = self.client.get("/api/search", params={**base_params, **invalid})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {"error": "One of the search values is invalid. Adjust the form and try again."},
                )

    @patch("backend.application.LOGGER.info")
    def test_ticket_click_rate_limit_uses_a_moving_window(self, logger_info):
        for _ in range(60):
            self.assertEqual(self.client.post("/api/events/ticket-click").status_code, 204)

        response = self.client.post("/api/events/ticket-click")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(),
            {"error": "Too many requests. Please wait a moment and try again."},
        )
        self.assertEqual(logger_info.call_count, 60)

    @patch("backend.application.LOGGER.error")
    @patch("backend.application.fandango_theatres", side_effect=RuntimeError("unexpected upstream shape"))
    @patch("backend.application.resolve_search_location", return_value=("00000", (40.0, -75.0), "Testville"))
    def test_unexpected_api_errors_are_returned_as_json(
        self, resolve_search_location, fandango_theatres, logger_error
    ):
        client = TestClient(application.app, raise_server_exceptions=False)

        response = client.get("/api/theatres", params={"zip": "00000", "radius": 25})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(response.json(), {"error": "We could not complete that search. Please try again."})
        logger_error.assert_called_once()

    @patch("backend.application.fandango_json")
    @patch("backend.location.geocode_zip")
    def test_five_mile_zip_search_excludes_theatres_outside_the_radius(self, geocode_zip, fandango_json):
        geocode_zip.return_value = {"label": "Testville, TS 00000", "lat": 40.0, "lon": -75.0}
        fandango_json.return_value = {
            "theaters": [
                {"name": "Nearby Cinema", "distance": 0, "geo": {"latitude": 40.03, "longitude": -75.0}},
                {"name": "Too Far Cinema", "distance": 0, "geo": {"latitude": 40.10, "longitude": -75.0}},
            ]
        }

        response = self.client.get("/api/theatres", params={"zip": "00000", "radius": 5})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["theatres"], [{"name": "Nearby Cinema"}])

    @patch("backend.application.fandango_json")
    @patch("backend.location.reverse_geocode_zip", return_value="00000")
    def test_location_search_integration_uses_precise_coordinates_for_radius_filtering(self, reverse_geocode_zip, fandango_json):
        fandango_json.return_value = {
            "theaters": [
                {"name": "Nearby Cinema", "distance": 0, "geo": {"latitude": 40.03, "longitude": -75.0}},
                {"name": "Too Far Cinema", "distance": 0, "geo": {"latitude": 40.10, "longitude": -75.0}},
            ]
        }

        response = self.client.get("/api/theatres", params={"lat": 40.0, "lon": -75.0, "radius": 5})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([theatre["name"] for theatre in response.json()["theatres"]], ["Nearby Cinema"])
        self.assertEqual(fandango_json.call_args.args[1]["radius"], 25)

    @patch("backend.location.reverse_geocode_zip", side_effect=application.requests.RequestException())
    def test_location_lookup_failure_guides_the_user_to_manual_zip_entry(self, reverse_geocode_zip):
        response = self.client.get("/api/theatres", params={"lat": 40.0, "lon": -75.0, "radius": 25})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Enter a ZIP code instead", response.json()["error"])

    @patch("backend.application.seat_map")
    @patch("backend.application.fandango_theatres_by_date")
    @patch("backend.application.resolve_search_location", return_value=("00000", (40.0, -75.0), "Testville"))
    def test_search_loads_seat_maps_through_the_extracted_matching_engine(
        self, resolve_search_location, fandango_theatres_by_date, seat_map
    ):
        fandango_theatres_by_date.return_value = {
            "2026-07-20": [{
                "name": "Test Cinema",
                "address": "1 Main St",
                "distanceMiles": 1.2,
                "rawMovies": [{
                    "title": "Test Movie",
                    "variants": [{
                        "filmFormatHeader": "Standard",
                        "amenityGroups": [{
                            "showtimes": [{
                                "type": "available",
                                "ticketingDate": "2026-07-20+19:00",
                                "showtimeHashCode": "showtime-1",
                            }],
                        }],
                    }],
                }],
            }]
        }
        seat_map.return_value = {
            "seats": [
                {"id": "A1", "row": 0, "column": 1, "x": 0, "y": 0, "status": "A", "type": "standard"},
                {"id": "A2", "row": 0, "column": 2, "x": 10, "y": 0, "status": "A", "type": "wheelchair"},
                {"id": "A3", "row": 0, "column": 3, "x": 20, "y": 0, "status": "A", "type": "companion"},
            ],
            "totalAvailableSeatCount": 3,
            "totalSeatCount": 3,
        }

        response = self.client.get("/api/search", params={
            "zip": "00000", "radius": 25, "movie": "Test Movie",
            "startDate": "2026-07-20", "endDate": "2026-07-20",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["matches"]), 1)
        self.assertTrue(response.json()["accessibleSeatsExcluded"])
        layout_seats = response.json()["matches"][0]["seatMap"]["layout"]["seats"]
        matched_by_id = {seat["id"]: seat["matched"] for seat in layout_seats}
        self.assertEqual(matched_by_id, {"A1": True, "A2": False, "A3": False})
        seat_map.assert_called_once_with("showtime-1")

    def test_manifest_and_discovery_routes(self):
        self.assertEqual(self.client.get("/site.webmanifest").status_code, 200)
        self.assertIn("Sitemap:", self.client.get("/robots.txt").text)
        sitemap = self.client.get("/sitemap.xml").text
        self.assertIn("<urlset", sitemap)
        self.assertNotIn("<lastmod>", sitemap)


if __name__ == "__main__":
    unittest.main()
