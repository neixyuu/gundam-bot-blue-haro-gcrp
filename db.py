import aiosqlite

DB_PATH = "gundam_bot.db"

# Pengaturan poin (ubah sesuai kebutuhan komunitas)
DAILY_POINTS = 10
STREAK_BONUS_EVERY = 7   # bonus tiap kelipatan N hari berturut-turut
STREAK_BONUS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    last_checkin TEXT,
    streak INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS point_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    points INTEGER NOT NULL,
    code TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (guild_id, code)
);

CREATE TABLE IF NOT EXISTS event_attendance (
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
"""


async def init() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


async def _apply_points(conn, guild_id: int, user_id: int, amount: int, reason: str) -> int:
    """Tambah/kurangi poin tanpa commit. Poin tidak pernah di bawah 0."""
    await conn.execute(
        "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
        (guild_id, user_id),
    )
    await conn.execute(
        "UPDATE users SET points = MAX(0, points + ?) WHERE guild_id = ? AND user_id = ?",
        (amount, guild_id, user_id),
    )
    await conn.execute(
        "INSERT INTO point_log (guild_id, user_id, amount, reason) VALUES (?, ?, ?, ?)",
        (guild_id, user_id, amount, reason),
    )
    cur = await conn.execute(
        "SELECT points FROM users WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    row = await cur.fetchone()
    return row[0]


async def add_points(guild_id: int, user_id: int, amount: int, reason: str) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        total = await _apply_points(conn, guild_id, user_id, amount, reason)
        await conn.commit()
        return total


async def checkin(guild_id: int, user_id: int, today: str, yesterday: str):
    """Absen harian. Return None jika sudah absen hari ini,
    selain itu (gained, streak, total)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO users (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        cur = await conn.execute(
            "SELECT last_checkin, streak FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        last, streak = await cur.fetchone()
        if last == today:
            await conn.commit()
            return None

        new_streak = streak + 1 if last == yesterday else 1
        gained = DAILY_POINTS
        if new_streak % STREAK_BONUS_EVERY == 0:
            gained += STREAK_BONUS

        # Syarat di WHERE menjaga agar klik ganda tidak menghasilkan poin dua kali
        cur = await conn.execute(
            """UPDATE users SET last_checkin = ?, streak = ?, points = points + ?
               WHERE guild_id = ? AND user_id = ?
                 AND (last_checkin IS NULL OR last_checkin != ?)""",
            (today, new_streak, gained, guild_id, user_id, today),
        )
        if cur.rowcount == 0:
            await conn.commit()
            return None

        await conn.execute(
            "INSERT INTO point_log (guild_id, user_id, amount, reason) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, gained, f"Absen harian (streak {new_streak})"),
        )
        cur = await conn.execute(
            "SELECT points FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        total = (await cur.fetchone())[0]
        await conn.commit()
        return gained, new_streak, total


async def get_user(guild_id: int, user_id: int):
    """Return (points, streak, last_checkin) atau None kalau belum ada data."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT points, streak, last_checkin FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cur.fetchone()


async def get_rank(guild_id: int, points: int) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) + 1 FROM users WHERE guild_id = ? AND points > ?",
            (guild_id, points),
        )
        return (await cur.fetchone())[0]


async def leaderboard(guild_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT user_id, points FROM users
               WHERE guild_id = ? AND points > 0
               ORDER BY points DESC LIMIT ?""",
            (guild_id, limit),
        )
        return await cur.fetchall()


async def history(guild_id: int, user_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """SELECT amount, reason, created_at FROM point_log
               WHERE guild_id = ? AND user_id = ?
               ORDER BY id DESC LIMIT ?""",
            (guild_id, user_id, limit),
        )
        return await cur.fetchall()


# ---------- Event / gathering ----------

async def create_event(guild_id: int, name: str, points: int, code: str, created_by: int):
    """Return event id."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO events (guild_id, name, points, code, created_by) VALUES (?, ?, ?, ?, ?)",
            (guild_id, name, points, code, created_by),
        )
        await conn.commit()
        return cur.lastrowid


async def close_event(guild_id: int, event_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "UPDATE events SET active = 0 WHERE guild_id = ? AND id = ? AND active = 1",
            (guild_id, event_id),
        )
        await conn.commit()
        return cur.rowcount > 0


async def list_events(guild_id: int, active_only: bool = True):
    query = """SELECT e.id, e.name, e.points, e.code, e.active,
                      (SELECT COUNT(*) FROM event_attendance a WHERE a.event_id = e.id)
               FROM events e WHERE e.guild_id = ?"""
    if active_only:
        query += " AND e.active = 1"
    query += " ORDER BY e.id DESC LIMIT 15"
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(query, (guild_id,))
        return await cur.fetchall()


async def claim_event(guild_id: int, user_id: int, code: str):
    """Return (status, name, points, total).
    status: 'ok' | 'invalid' | 'duplicate'."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT id, name, points FROM events WHERE guild_id = ? AND code = ? AND active = 1",
            (guild_id, code.strip().upper()),
        )
        ev = await cur.fetchone()
        if not ev:
            return "invalid", None, None, None
        event_id, name, points = ev
        try:
            await conn.execute(
                "INSERT INTO event_attendance (event_id, user_id) VALUES (?, ?)",
                (event_id, user_id),
            )
        except aiosqlite.IntegrityError:
            return "duplicate", name, points, None
        total = await _apply_points(conn, guild_id, user_id, points, f"Event: {name}")
        await conn.commit()
        return "ok", name, points, total
