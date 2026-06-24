import unittest
from unittest.mock import patch

from backend import server


class DateRangeTests(unittest.TestCase):
    def test_date_range_is_inclusive(self):
        self.assertEqual(
            list(server.date_range("2026-06-01", "2026-06-03")),
            ["2026-06-01", "2026-06-02", "2026-06-03"],
        )

    def test_date_range_rejects_backwards_dates(self):
        with self.assertRaisesRegex(ValueError, "on or after"):
            list(server.date_range("2026-06-03", "2026-06-01"))


class MovieAndFormatTests(unittest.TestCase):
    def test_movie_matching_is_case_and_punctuation_insensitive(self):
        self.assertTrue(server.movie_matches("Spider-Man: Homecoming", "spider man"))

    def test_movie_matching_rejects_unrelated_titles(self):
        self.assertFalse(server.movie_matches("Dune: Part Two", "Superman"))

    def test_format_matching_distinguishes_imax_variants(self):
        self.assertTrue(server.format_matches("IMAX", "", "imax"))
        self.assertFalse(server.format_matches("IMAX 70mm", "", "imax"))
        self.assertTrue(server.format_matches("IMAX 70mm", "", "imax70"))

    def test_format_matching_accepts_amenity_aliases(self):
        self.assertTrue(server.format_matches("Standard", "Dolby Cinema", "dolby"))
        self.assertTrue(server.format_matches("Special Event", "70 mm presentation", "70mm"))

    def test_movie_metadata_is_normalized(self):
        movie = {
            "poster": {"size": {"200": "poster.jpg"}},
            "rating": "PG13",
            "runtime": 155,
            "genres": ["Drama", "Science Fiction", "Adventure"],
        }
        self.assertEqual(
            server.movie_meta(movie),
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
            server.parse_seat_grid("0:0,14:14,15:0,bad,2:x"),
            [(0, 0), (14, 14)],
        )

    def test_adjacent_blocks_require_contiguous_available_seats(self):
        blocks = server.adjacent_blocks(self.seats, 2, "any")
        self.assertIn(["A1", "A2"], [block["seats"] for block in blocks])
        self.assertNotIn(["A2", "A3"], [block["seats"] for block in blocks])

    def test_accessible_seats_can_be_excluded(self):
        blocks = server.adjacent_blocks(
            self.seats,
            1,
            "any",
            exclude_accessible=True,
        )
        seat_ids = {seat_id for block in blocks for seat_id in block["seats"]}
        self.assertNotIn("B1", seat_ids)
        self.assertIn("B2", seat_ids)

    def test_layout_marks_matching_seats(self):
        data = {
            "backgroundSvg": "<svg />",
            "backgroundWidth": 100,
            "backgroundHeight": 50,
            "seats": [
                {"id": "A1", "x": 10, "y": 10, "width": 5, "height": 5},
                {"id": "A2", "x": 20, "y": 10, "width": 5, "height": 5},
            ],
        }
        layout = server.normalized_seat_layout(data, [{"seats": ["A2"]}])
        self.assertFalse(layout["seats"][0]["matched"])
        self.assertTrue(layout["seats"][1]["matched"])


class CacheTests(unittest.TestCase):
    def setUp(self):
        server.SEAT_MAP_CACHE.clear()

    @patch("backend.server.fandango_json")
    def test_seat_maps_are_cached(self, fandango_json):
        fandango_json.return_value = {"seats": []}

        first = server.seat_map("showtime-1")
        second = server.seat_map("showtime-1")

        self.assertIs(first, second)
        fandango_json.assert_called_once()

    def test_missing_showtime_hash_skips_fetch(self):
        self.assertIsNone(server.seat_map(""))


class TitleTests(unittest.TestCase):
    def test_title_from_slug_removes_release_year(self):
        self.assertEqual(
            server.title_from_slug("the-princess-bride-2026"),
            "The Princess Bride",
        )


if __name__ == "__main__":
    unittest.main()
