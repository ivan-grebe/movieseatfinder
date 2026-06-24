import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import application


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
            application.parse_seat_grid("0:0,14:14,15:0,bad,2:x"),
            [(0, 0), (14, 14)],
        )

    def test_adjacent_blocks_require_available_contiguous_seats(self):
        blocks = application.adjacent_blocks(self.seats, 2, "any")
        self.assertIn(["A1", "A2"], [block["seats"] for block in blocks])
        self.assertNotIn(["A2", "A3"], [block["seats"] for block in blocks])

    def test_accessible_seats_can_be_excluded(self):
        blocks = application.adjacent_blocks(
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
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])

    def test_invalid_zip_returns_json_error(self):
        response = self.client.get("/api/theatres", params={"zip": "abc"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Enter a valid 5 digit US ZIP code."})

    def test_manifest_and_discovery_routes(self):
        self.assertEqual(self.client.get("/site.webmanifest").status_code, 200)
        self.assertIn("Sitemap:", self.client.get("/robots.txt").text)
        self.assertIn("<urlset", self.client.get("/sitemap.xml").text)


if __name__ == "__main__":
    unittest.main()
