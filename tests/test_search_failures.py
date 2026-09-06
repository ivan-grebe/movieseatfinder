import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import application


class SearchFailureTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(application.app)
        application.RATE_LIMITER.storage.reset()
        self.params = {"zip": "10001", "radius": 25, "movie": "Test Movie"}

    @patch.object(application, "api_search_location")
    def test_backwards_time_window_is_rejected_before_upstream_work(self, location):
        response = self.client.get(
            "/api/search", params={**self.params, "startTime": "21:00", "endTime": "17:00"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Latest time", response.json()["error"])
        location.assert_not_called()

    @patch.object(application, "collect_candidate_showtimes", return_value=[])
    @patch.object(application, "api_search_location", return_value=("10001", (0, 0), "Test"))
    def test_equal_time_bounds_remain_valid(self, _location, candidates):
        response = self.client.get(
            "/api/search", params={**self.params, "startTime": "17:00", "endTime": "17:00"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["failedSeatMaps"], 0)
        candidates.assert_called_once()

    def test_a_failed_date_is_not_silently_returned_as_an_empty_day(self):
        def theatres(_zip, _radius, _origin, show_date):
            if show_date == "2026-09-07":
                raise ValueError("invalid upstream response")
            return []

        with patch.object(application, "fandango_theatres", side_effect=theatres):
            with patch.object(
                application, "api_search_location", return_value=("10001", (0, 0), "Test")
            ):
                response = self.client.get(
                    "/api/search",
                    params={**self.params, "startDate": "2026-09-06", "endDate": "2026-09-07"},
                )
        self.assertEqual(response.status_code, 502)
        self.assertIn("every selected date", response.json()["error"])

    def test_seat_failures_are_distinct_from_successful_nonmatches(self):
        theatre = {"name": "Cinema", "address": "Main St", "distanceMiles": 1}
        showtime = {
            "date": date.today().isoformat(),
            "time": "19:00",
            "displayTime": "7 PM",
            "movieTitle": "Test Movie",
            "format": "Standard",
            "amenities": "",
            "ticketUrl": "https://tickets.fandango.com/order",
            "poster": "",
            "rating": "",
            "runtime": "",
            "genres": [],
        }
        match = {"matchingGroups": [["H1"]], "bestGroup": ["H1"]}
        scenarios = [
            (["failure", "failure"], 502, 0, None),
            (["failure", "match"], 200, 1, 1),
            (["failure", "empty"], 200, 0, 1),
            (["empty", "empty"], 200, 0, 0),
        ]
        for outcomes, status, count, failed in scenarios:
            with self.subTest(outcomes=outcomes):

                def check(candidate, *_args):
                    outcome = candidate["showtimeHashCode"]
                    if outcome == "failure":
                        raise application.requests.Timeout("upstream timeout")
                    return match if outcome == "match" else None

                candidates = [
                    (theatre, {**showtime, "showtimeHashCode": item}) for item in outcomes
                ]
                with (
                    patch.object(
                        application, "api_search_location", return_value=("10001", (0, 0), "Test")
                    ),
                    patch.object(
                        application, "collect_candidate_showtimes", return_value=candidates
                    ),
                    patch.object(application, "showtime_seat_match", side_effect=check),
                ):
                    response = self.client.get("/api/search", params=self.params)
                self.assertEqual(response.status_code, status)
                if status == 502:
                    self.assertIn("availability could not be checked", response.json()["error"])
                else:
                    self.assertEqual(len(response.json()["matches"]), count)
                    self.assertEqual(response.json()["failedSeatMaps"], failed)
                    self.assertEqual(response.json()["checkedSeatMaps"], 2)
