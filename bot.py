import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # opsional: isi untuk sync command instan di server uji coba
TZ = ZoneInfo("Asia/Jakarta")
EMBED_COLOR = 0x2B6CB0


class GundamBot(commands.Bot):
    def __init__(self) -> None:
        # Slash command tidak butuh privileged intents
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())

    async def setup_hook(self) -> None:
        await db.init()
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Login sebagai {self.user} (id: {self.user.id})")


bot = GundamBot()


def today_wib() -> tuple[str, str]:
    now = datetime.now(TZ).date()
    return now.isoformat(), (now - timedelta(days=1)).isoformat()


def to_wib(utc_string: str) -> str:
    dt = datetime.strptime(utc_string, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).strftime("%d %b %Y %H:%M")


def make_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------- Perintah member ----------

@bot.tree.command(name="absen", description="Absen harian untuk dapat poin")
@app_commands.guild_only()
async def absen(interaction: discord.Interaction) -> None:
    today, yesterday = today_wib()
    result = await db.checkin(interaction.guild_id, interaction.user.id, today, yesterday)
    if result is None:
        await interaction.response.send_message(
            "Kamu sudah absen hari ini. Reset setiap pukul 00:00 WIB.", ephemeral=True
        )
        return

    gained, streak, total = result
    msg = f"Absen berhasil. +{gained} poin, streak {streak} hari. Total poin: {total}."
    if streak % db.STREAK_BONUS_EVERY == 0:
        msg += f" Bonus streak {db.STREAK_BONUS} poin sudah termasuk."
    await interaction.response.send_message(msg)


@bot.tree.command(name="poin", description="Cek poin kamu atau member lain")
@app_commands.guild_only()
@app_commands.describe(member="Member yang mau dicek (kosongkan untuk dirimu sendiri)")
async def poin(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    target = member or interaction.user
    data = await db.get_user(interaction.guild_id, target.id)
    if data is None:
        await interaction.response.send_message(
            f"{target.display_name} belum punya poin.", ephemeral=True
        )
        return

    points, streak, last_checkin = data
    rank = await db.get_rank(interaction.guild_id, points)
    embed = discord.Embed(title=f"Poin {target.display_name}", color=EMBED_COLOR)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Total poin", value=str(points))
    embed.add_field(name="Peringkat", value=f"#{rank}")
    embed.add_field(name="Streak absen", value=f"{streak} hari")
    embed.set_footer(text=f"Absen terakhir: {last_checkin or '-'}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="Top 10 member dengan poin terbanyak")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction) -> None:
    rows = await db.leaderboard(interaction.guild_id, 10)
    if not rows:
        await interaction.response.send_message("Belum ada data poin.", ephemeral=True)
        return

    lines = [f"**{i}.** <@{user_id}> - {points} poin" for i, (user_id, points) in enumerate(rows, 1)]
    embed = discord.Embed(
        title="Leaderboard Komunitas", description="\n".join(lines), color=EMBED_COLOR
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="riwayat", description="Lihat 10 riwayat poin terakhir")
@app_commands.guild_only()
@app_commands.describe(member="Member yang mau dilihat (kosongkan untuk dirimu sendiri)")
async def riwayat(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    target = member or interaction.user
    rows = await db.history(interaction.guild_id, target.id, 10)
    if not rows:
        await interaction.response.send_message("Belum ada riwayat poin.", ephemeral=True)
        return

    lines = [f"`{to_wib(created)}` **{amount:+d}** - {reason}" for amount, reason, created in rows]
    embed = discord.Embed(
        title=f"Riwayat poin {target.display_name}",
        description="\n".join(lines),
        color=EMBED_COLOR,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="hadir", description="Klaim poin gathering/event dengan kode dari panitia")
@app_commands.guild_only()
@app_commands.describe(kode="Kode event dari panitia")
async def hadir(interaction: discord.Interaction, kode: str) -> None:
    status, name, points, total = await db.claim_event(
        interaction.guild_id, interaction.user.id, kode
    )
    if status == "invalid":
        await interaction.response.send_message(
            "Kode tidak valid atau event sudah ditutup.", ephemeral=True
        )
    elif status == "duplicate":
        await interaction.response.send_message(
            f"Kamu sudah tercatat hadir di **{name}**.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Kehadiran di **{name}** tercatat. +{points} poin, total sekarang {total}."
        )


# ---------- Perintah staff (butuh izin Manage Server) ----------

staff_only = app_commands.checks.has_permissions(manage_guild=True)


@bot.tree.command(name="event_buat", description="[Staff] Buat event/gathering dan dapatkan kode kehadiran")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@staff_only
@app_commands.describe(nama="Nama event", poin="Poin untuk yang hadir")
async def event_buat(
    interaction: discord.Interaction,
    nama: app_commands.Range[str, 1, 80],
    poin: app_commands.Range[int, 1, 1000],
) -> None:
    code = make_code()
    event_id = await db.create_event(interaction.guild_id, nama, poin, code, interaction.user.id)
    await interaction.response.send_message(
        f"Event **{nama}** dibuat (ID {event_id}, {poin} poin).\n"
        f"Kode kehadiran: `{code}`\n"
        "Bagikan kode ini ke peserta di lokasi, mereka klaim lewat `/hadir`. "
        f"Tutup dengan `/event_tutup {event_id}` setelah acara selesai.",
        ephemeral=True,
    )


@bot.tree.command(name="event_tutup", description="[Staff] Tutup event supaya kode tidak bisa dipakai lagi")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@staff_only
@app_commands.describe(event_id="ID event (lihat di /event_daftar)")
async def event_tutup(interaction: discord.Interaction, event_id: int) -> None:
    ok = await db.close_event(interaction.guild_id, event_id)
    msg = f"Event ID {event_id} ditutup." if ok else "Event tidak ditemukan atau sudah ditutup."
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="event_daftar", description="[Staff] Daftar event aktif beserta kodenya")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@staff_only
async def event_daftar(interaction: discord.Interaction) -> None:
    rows = await db.list_events(interaction.guild_id)
    if not rows:
        await interaction.response.send_message("Tidak ada event aktif.", ephemeral=True)
        return
    lines = [
        f"**{eid}.** {name} - {pts} poin - kode `{code}` - {count} hadir"
        for eid, name, pts, code, _active, count in rows
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="poin_tambah", description="[Staff] Tambah poin member secara manual")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@staff_only
@app_commands.describe(member="Member penerima", jumlah="Jumlah poin", alasan="Alasan pemberian")
async def poin_tambah(
    interaction: discord.Interaction,
    member: discord.Member,
    jumlah: app_commands.Range[int, 1, 10000],
    alasan: app_commands.Range[str, 1, 100] = "Penyesuaian oleh staff",
) -> None:
    total = await db.add_points(interaction.guild_id, member.id, jumlah, alasan)
    await interaction.response.send_message(
        f"+{jumlah} poin untuk {member.mention} ({alasan}). Total: {total}."
    )


@bot.tree.command(name="poin_kurang", description="[Staff] Kurangi poin member secara manual")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@staff_only
@app_commands.describe(member="Member yang dikurangi", jumlah="Jumlah poin", alasan="Alasan pengurangan")
async def poin_kurang(
    interaction: discord.Interaction,
    member: discord.Member,
    jumlah: app_commands.Range[int, 1, 10000],
    alasan: app_commands.Range[str, 1, 100] = "Penyesuaian oleh staff",
) -> None:
    total = await db.add_points(interaction.guild_id, member.id, -jumlah, alasan)
    await interaction.response.send_message(
        f"-{jumlah} poin dari {member.mention} ({alasan}). Total: {total}."
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        msg = "Perintah ini hanya untuk staff (izin Manage Server)."
    else:
        print(f"Error pada command: {error!r}")
        msg = "Terjadi kesalahan. Coba lagi sebentar."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN belum diisi. Salin .env.example menjadi .env dulu.")
    bot.run(TOKEN)
