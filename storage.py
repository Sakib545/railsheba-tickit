from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(slots=True)
class Watch:
    id: int
    from_city: str
    to_city: str
    journey_date: str
    seat_class: str
    train_filter: str
    last_state: str
    seat_count: int
    adjacent_required: bool
    check_start: str
    check_end: str


class Store:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_city TEXT NOT NULL,
                    to_city TEXT NOT NULL,
                    journey_date TEXT NOT NULL,
                    seat_class TEXT NOT NULL,
                    train_filter TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    last_state TEXT NOT NULL DEFAULT ''
                )
            """)
            columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(watches)")}
            migrations = {
                "seat_count": "ALTER TABLE watches ADD COLUMN seat_count INTEGER NOT NULL DEFAULT 1",
                "adjacent_required": "ALTER TABLE watches ADD COLUMN adjacent_required INTEGER NOT NULL DEFAULT 0",
                "check_start": "ALTER TABLE watches ADD COLUMN check_start TEXT NOT NULL DEFAULT '00:00'",
                "check_end": "ALTER TABLE watches ADD COLUMN check_end TEXT NOT NULL DEFAULT '23:59'",
            }
            for name, statement in migrations.items():
                if name not in columns:
                    await db.execute(statement)
            await db.commit()

    async def add(
        self, from_city: str, to_city: str, journey_date: str, seat_class: str,
        train_filter: str, seat_count: int = 1, adjacent_required: bool = False,
        check_start: str = "00:00", check_end: str = "23:59",
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT INTO watches(
                    from_city,to_city,journey_date,seat_class,train_filter,
                    seat_count,adjacent_required,check_start,check_end
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (from_city, to_city, journey_date, seat_class, train_filter,
                 seat_count, int(adjacent_required), check_start, check_end),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def list(self) -> list[Watch]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM watches WHERE active=1 ORDER BY id")
        return [
            Watch(
                r["id"], r["from_city"], r["to_city"], r["journey_date"],
                r["seat_class"], r["train_filter"], r["last_state"],
                r["seat_count"], bool(r["adjacent_required"]),
                r["check_start"], r["check_end"],
            )
            for r in rows
        ]

    async def delete(self, watch_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("DELETE FROM watches WHERE id=?", (watch_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def set_state(self, watch_id: int, state: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE watches SET last_state=? WHERE id=?", (state, watch_id))
            await db.commit()
