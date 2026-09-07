from datetime import date, timedelta

from rail_api import _seat_count, booking_link, validate_journey_date


def test_seat_count_shapes():
    assert _seat_count({"seat_counts": {"online": 3}}) == 3
    assert _seat_count({"available_seats": 5}) == 5
    assert _seat_count({}) == 0


def test_booking_link():
    url = booking_link("Dhaka", "Cox's Bazar", "2026-09-15", "AC_S")
    assert "fromcity=Dhaka" in url
    assert "tocity=Cox%27s+Bazar" in url
    assert "doj=15-Sep-2026" in url


def test_date_validation():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert validate_journey_date(tomorrow) == tomorrow

