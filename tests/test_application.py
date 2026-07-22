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
        self.assertIn("B2", seat_ids)


class CacheTests(unittest.TestCase):
    def setUp(self):
        application.SEAT_MAP_CACHE.clear()

    @patch("backend.application.fandango_json")
    def test_seat_maps_are_cached(self, fandango_json):
        fandango_json.return_value = {"seats": []}
        first = application.seat_map("showtime-1")
        second = application.seat_map("showtime-1")
        self.assertIs(first, second)
        fandango_json.assert_called_once()


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

    def test_homepage_renders_dynamic_seo_and_security_headers(self):
        response = self.client.get("/", headers={"host": "example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Movie Seat Finder", response.text)
        self.assertNotIn("__SITE_", response.text)
        self.assertIn('property="og:image:secure_url"', response.text)
        self.assertIn('property="og:image:width" content="1200"', response.text)
        self.assertIn('name="twitter:image:alt"', response.text)
        self.assertIn('"@type": "Organization"', response.text)
        self.assertIn('"@type": "WebSite"', response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])

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
            ],
            "totalAvailableSeatCount": 1,
            "totalSeatCount": 1,
        }

        response = self.client.get("/api/search", params={
            "zip": "00000", "radius": 25, "movie": "Test Movie",
            "startDate": "2026-07-20", "endDate": "2026-07-20",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["matches"]), 1)
        seat_map.assert_called_once_with("showtime-1")

    def test_manifest_and_discovery_routes(self):
        self.assertEqual(self.client.get("/site.webmanifest").status_code, 200)
        self.assertIn("Sitemap:", self.client.get("/robots.txt").text)
        sitemap = self.client.get("/sitemap.xml").text
        self.assertIn("<urlset", sitemap)
        self.assertNotIn("<lastmod>", sitemap)


if __name__ == "__main__":
    unittest.main()
