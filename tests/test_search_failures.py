import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import application


class SearchFailureTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(application.app)
        application.RATE_LIMITER.storage.reset()
        self.params = {"zip": "10001", "radius": 25, "movie": "Test Movie"}
        self.location = self.enterContext(
            patch.object(application, "api_search_location", return_value=("10001", (0, 0), "Test"))
        )

    def test_backwards_time_window_is_rejected_before_location_lookup(self):
        response = self.client.get(
            "/api/search", params={**self.params, "startTime": "21:00", "endTime": "17:00"}
        )
        self.assertEqual(response.status_code, 400)
        self.location.assert_not_called()

    def test_a_failed_date_is_not_silently_returned_as_an_empty_day(self):
        def theatres(_zip, _radius, _origin, show_date):
            if show_date == "2026-09-07":
                raise application.requests.Timeout("upstream timeout")
            return []

        with patch.object(application, "fandango_theatres", side_effect=theatres):
            response = self.client.get(
                "/api/search",
                params={**self.params, "startDate": "2026-09-06", "endDate": "2026-09-07"},
            )
        self.assertEqual(response.status_code, 502)

    def search_with_seat_check(self, check):
        theatre = {"name": "Cinema", "address": "Main St", "distanceMiles": 1}
        showtime = {
            "date": "2026-09-06",
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
        candidates = [
            (theatre, {**showtime, "showtimeHashCode": code}) for code in ("first", "second")
        ]
        with (
            patch.object(application, "collect_candidate_showtimes", return_value=candidates),
            patch.object(application, "showtime_seat_match", side_effect=check),
        ):
            return self.client.get("/api/search", params=self.params)

    def test_total_seat_map_failure_returns_an_error(self):
        def unavailable(*_args):
            raise application.requests.Timeout("upstream timeout")

        response = self.search_with_seat_check(unavailable)
        self.assertEqual(response.status_code, 502)

    def test_successful_checks_with_no_matches_are_not_failures(self):
        response = self.search_with_seat_check(lambda *_args: None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matches"], [])
        self.assertEqual(response.json()["failedSeatMaps"], 0)

    def test_partial_failure_preserves_confirmed_matches_and_reports_failed_checks(self):
        def check(showtime, *_args):
            if showtime["showtimeHashCode"] == "first":
                raise application.requests.Timeout("upstream timeout")
            return {"matchingGroups": [["H1"]], "bestGroup": ["H1"]}

        response = self.search_with_seat_check(check)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matches"][0]["showtimeHashCode"], "second")
        self.assertEqual(response.json()["failedSeatMaps"], 1)
