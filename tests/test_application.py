import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import application
from backend import seat_matching


class DateAndValidationTests(unittest.TestCase):
    def test_date_range_is_inclusive(self):
        self.assertEqual(
            list(application.date_range("2026-06-01", "2026-06-03")),
            ["2026-06-01", "2026-06-02", "2026-06-03"],
        )

    def test_date_range_rejects_backwards_dates(self):
        with self.assertRaisesRegex(ValueError, "on or after"):
            list(application.date_range("2026-06-03", "2026-06-01"))

    def test_date_range_rejects_more_than_two_weeks(self):
        with self.assertRaisesRegex(ValueError, "14 days"):
            list(application.date_range("2026-06-01", "2026-06-16"))

    def test_radius_and_time_validation(self):
        self.assertEqual(application.validate_radius(25), 25)
        self.assertEqual(application.validate_time("23:59", "Time"), "23:59")
        with self.assertRaises(ValueError):
            application.validate_radius(0)
        with self.assertRaises(ValueError):
            application.validate_time("25:00", "Time")

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
    def test_movie_matching_is_case_and_punctuation_insensitive(self):
        self.assertTrue(application.movie_matches("Spider-Man: Homecoming", "spider man"))

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

        showtime = application.normalize_showtimes({}, [movie], "2026-07-20")[0]

        self.assertEqual(showtime["format"], "IMAX")
        self.assertEqual(showtime["screenReaderTime"], "6:00 PM")
        self.assertNotIn("o'clock", showtime["screenReaderTime"])


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
        blocks = seat_matching.adjacent_blocks(self.seats, 2, "any")
        self.assertIn(["A1", "A2"], [block["seats"] for block in blocks])
        self.assertNotIn(["A2", "A3"], [block["seats"] for block in blocks])

    def test_accessible_seats_can_be_excluded(self):
        blocks = seat_matching.adjacent_blocks(
            self.seats,
            1,
            "any",
            exclude_accessible=True,
        )
        seat_ids = {seat_id for block in blocks for seat_id in block["seats"]}
        self.assertNotIn("B1", seat_ids)
        self.assertNotIn("B3", seat_ids)
        self.assertIn("B2", seat_ids)


class CacheTests(unittest.TestCase):
    def setUp(self):
        application.SEAT_MAP_CACHE.clear()
        application.THEATRES_CACHE.clear()

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
        first = application.fandango_theatres("10001", 25)
        second = application.fandango_theatres("10001", 25)

        self.assertIs(first, second)
        fetch_theatres.assert_called_once()
        logger_info.assert_called_once()
        self.assertEqual(
            logger_info.call_args.args[:2],
            ("event=cache_hit cache=%s age_ms=%d", "theatres"),
        )

    @patch("backend.application._fetch_fandango_theatres", return_value=[])
    def test_large_radius_uses_fandangos_full_supported_range(self, fetch_theatres):
        application.fandango_theatres("10001", 100, origin=(40.75, -73.99))

        self.assertEqual(fetch_theatres.call_args.args[1], 100)

    @patch("backend.application.fandango_theatres", side_effect=TimeoutError("upstream down"))
    def test_all_date_failures_are_propagated(self, fandango_theatres):
        with self.assertRaisesRegex(TimeoutError, "upstream down"):
            application.fandango_theatres_by_date(
                "10001", 25, ["2026-07-22", "2026-07-23"]
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
        application.RATE_LIMIT_HISTORY.clear()

    def test_homepage_renders_context_and_security_headers(self):
        response = self.client.get("/", headers={"host": "example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Movie Seat Finder", response.text)
        self.assertNotIn("__SITE_", response.text)
        self.assertNotIn("__INLINE_STYLES__", response.text)
        self.assertIn(f"<style>{application.INLINE_STYLES}</style>", response.text)
        self.assertNotIn('rel="stylesheet"', response.text)
        self.assertIn(
            'src="app.bundle.js?v=20260801-performance"',
            response.text,
        )
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        content_security_policy = response.headers["content-security-policy"]
        self.assertIn("default-src 'self'", content_security_policy)
        self.assertIn(
            f"'sha256-{application.INLINE_STYLE_HASH}'",
            content_security_policy,
        )

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

    def test_entry_animation_uses_only_composited_properties(self):
        enter_rule = application.INLINE_STYLES.split(".enter-item {", 1)[1].split("}", 1)[0]
        enter_keyframes = application.INLINE_STYLES.split("@keyframes enter-up {", 1)[1].split("}", 2)[0]

        self.assertNotIn("filter:", enter_rule)
        self.assertNotIn("filter:", enter_keyframes)

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

    @patch("backend.application.movies_from_dated_theatre_payloads", side_effect=TimeoutError("upstream down"))
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

    def test_missing_radius_returns_a_clear_error(self):
        response = self.client.get("/api/theatres", params={"zip": "10001"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Enter a search radius."})

    @patch("backend.application.LOGGER.exception")
    @patch("backend.application.fandango_theatres", side_effect=RuntimeError("unexpected upstream shape"))
    @patch("backend.application.resolve_search_location", return_value=("00000", (40.0, -75.0), "Testville"))
    def test_unexpected_api_errors_are_returned_as_json(
        self, resolve_search_location, fandango_theatres, logger_exception
    ):
        client = TestClient(application.app, raise_server_exceptions=False)

        response = client.get("/api/theatres", params={"zip": "00000", "radius": 25})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(response.json(), {"error": "We could not complete that search. Please try again."})
        logger_exception.assert_called_once()

    @patch("backend.application.fandango_json")
    @patch("backend.location.geocode_zip")
    def test_five_mile_zip_search_excludes_theatres_outside_the_radius(self, geocode_zip, fandango_json):
        application.THEATRES_CACHE.clear()
        geocode_zip.return_value = {"label": "Testville, TS 00000", "lat": 40.0, "lon": -75.0}
        fandango_json.return_value = {
            "theaters": [
                {"name": "Nearby Cinema", "distance": 0, "geo": {"latitude": 40.03, "longitude": -75.0}},
                {"name": "Too Far Cinema", "distance": 0, "geo": {"latitude": 40.10, "longitude": -75.0}},
            ]
        }

        response = self.client.get("/api/theatres", params={"zip": "00000", "radius": 5})

        self.assertEqual(response.status_code, 200)
        theatres = response.json()["theatres"]
        self.assertEqual([theatre["name"] for theatre in theatres], ["Nearby Cinema"])
        self.assertTrue(all(theatre["distanceMiles"] <= 5 for theatre in theatres))

    @patch("backend.application.fandango_json")
    @patch("backend.location.reverse_geocode_zip", return_value="00000")
    def test_location_search_integration_uses_precise_coordinates_for_radius_filtering(self, reverse_geocode_zip, fandango_json):
        application.THEATRES_CACHE.clear()
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
                "website": "https://www.fandango.com/test/theater-page",
                "source": "Fandango",
                "rawMovies": [{
                    "id": "1",
                    "title": "Test Movie",
                    "variants": [{
                        "filmFormatHeader": "Standard",
                        "amenityGroups": [{
                            "hasReservedSeating": True,
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
