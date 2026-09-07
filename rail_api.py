from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import httpx


API_BASE = "https://railspaapi.shohoz.com/v1.0/app"
BOOKING_BASE = "https://eticket.railway.gov.bd/booking/train/search"


class RailAPIError(RuntimeError):
    pass


@dataclass(slots=True)
class Availability:
    train_name: str
    trip_number: str
    seat_class: str
    seats: int
    fare: str
    departure: str
    arrival: str
    booking_url: str


class RailClient:
    def __init__(self, mobile: str, password: str) -> None:
        self.mobile = mobile
        self.password = password
        self.token: str | None = None
        self.http = httpx.AsyncClient(timeout=20, headers={"Accept": "application/json"})
        self._login_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    async def login(self, force: bool = False) -> None:
        async with self._login_lock:
            if self.token and not force:
                return
            response = await self.http.post(
                f"{API_BASE}/auth/sign-in",
                json={"mobile_number": self.mobile, "password": self.password},
            )
            if response.status_code == 422:
                raise RailAPIError("Railway mobile number অথবা password ভুল।")
            response.raise_for_status()
            try:
                self.token = response.json()["data"]["token"]
            except (KeyError, TypeError, ValueError) as exc:
                raise RailAPIError("Railway login response বোঝা যায়নি।") from exc

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        await self.login()
        headers = {"Authorization": f"Bearer {self.token}"}
        response = await self.http.get(f"{API_BASE}{path}", params=params, headers=headers)
        if response.status_code == 401:
            await self.login(force=True)
            headers["Authorization"] = f"Bearer {self.token}"
            response = await self.http.get(f"{API_BASE}{path}", params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def search(
        self,
        from_city: str,
        to_city: str,
        journey_date: str,
        seat_class: str,
        train_filter: str = "",
    ) -> list[Availability]:
        payload = await self._get(
            "/bookings/search-trips-v2",
            {
                "from_city": from_city,
                "to_city": to_city,
                "date_of_journey": journey_date,
                "seat_class": seat_class,
            },
        )
        trains = payload.get("data", {}).get("trains", [])
        matches: list[Availability] = []
        needle = train_filter.casefold().strip()
        for train in trains:
            trip_number = str(train.get("trip_number", ""))
            train_name = str(train.get("train_name") or train.get("trip_name") or trip_number)
            if needle and needle not in f"{train_name} {trip_number}".casefold():
                continue
            for seat_type in train.get("seat_types", []):
                current_class = str(seat_type.get("type", ""))
                if current_class.casefold() != seat_class.casefold():
                    continue
                seats = _seat_count(seat_type)
                matches.append(
                    Availability(
                        train_name=train_name,
                        trip_number=trip_number,
                        seat_class=current_class,
                        seats=seats,
                        fare=str(seat_type.get("fare") or seat_type.get("fare_amount") or "—"),
                        departure=str(train.get("departure_date_time", "—")),
                        arrival=str(train.get("arrival_date_time", "—")),
                        booking_url=booking_link(from_city, to_city, journey_date, current_class),
                    )
                )
        return matches


def _seat_count(seat_type: dict[str, Any]) -> int:
    for key in ("seat_counts", "seat_count", "available_seats", "available"):
        value = seat_type.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, dict):
            for nested in ("online", "available", "total"):
                if isinstance(value.get(nested), (int, float)):
                    return max(0, int(value[nested]))
    return 0


def booking_link(from_city: str, to_city: str, journey_date: str, seat_class: str) -> str:
    parsed = datetime.strptime(journey_date, "%Y-%m-%d").date()
    params = {
        "fromcity": from_city,
        "tocity": to_city,
        "doj": parsed.strftime("%d-%b-%Y"),
        "class": seat_class,
    }
    return f"{BOOKING_BASE}?{urlencode(params)}"


def validate_journey_date(value: str) -> str:
    parsed = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    if parsed < date.today():
        raise ValueError("Journey date অতীতের হতে পারবে না।")
    return parsed.isoformat()

