"""Health checks: .status, and alerts in the error log when soap-cat or the database stops responding.

soap-cat writes a heartbeat to the bot_heartbeats table every minute while it's connected to Discord,
so a stale heartbeat means soap-cat is down, disconnected or stuck.
"""

import asyncio
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

import db
from constants import ERROR_LOG_ID, SOAP_CHANNEL_CATEGORY_ID, MANUAL_SOAP_CATEGORY_ID, NNID_CHANNEL_CATEGORY_ID
from perms import command_with_perms

CHECK_SECONDS = 60
SOAP_CAT_STALE_SECONDS = 180  # no heartbeat for this long and soap-cat counts as down
DB_FAILS_BEFORE_ALERT = 2  # one slow query isn't an outage
DB_TIMEOUT_SECONDS = 10


def _ago(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} second{'' if seconds == 1 else 's'}"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


class StatusCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.started = time.time()
        self._task = None
        self.db_ok: bool | None = None
        self.db_fails = 0
        self.db_down_since: float | None = None
        self.soap_cat_ok: bool | None = None  # None until soap-cat has written a heartbeat at least once
        self.soap_cat_down_since: float | None = None

    def cog_unload(self):
        if self._task:
            self._task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._task is None and db.enabled():
            self._task = asyncio.create_task(self._loop())

    # ---------- checks ----------

    async def check_db(self) -> float | None:
        """Milliseconds a trivial query took, or None when the database didn't answer."""
        start = time.monotonic()
        try:
            await asyncio.wait_for(db.fetchone("SELECT 1 AS ok"), DB_TIMEOUT_SECONDS)
        except Exception:
            return None
        return (time.monotonic() - start) * 1000

    async def soap_cat_heartbeat(self) -> dict | None:
        """soap-cat's row with its age in seconds, or None if it's never written one or the database is down."""
        try:
            row = await asyncio.wait_for(
                db.fetchone(
                    """SELECT beat_at, latency_ms, TIMESTAMPDIFF(SECOND, beat_at, UTC_TIMESTAMP()) AS age
                       FROM bot_heartbeats WHERE bot = 'soap-cat'"""
                ),
                DB_TIMEOUT_SECONDS,
            )
        except Exception:
            return None
        return row

    async def _loop(self):
        while True:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Health check failed: {e!r}")
            await asyncio.sleep(CHECK_SECONDS)

    async def _check_once(self):
        db_ms = await self.check_db()
        if db_ms is None:
            self.db_fails += 1
            if self.db_fails == 1:
                self.db_down_since = time.time()
            if self.db_fails >= DB_FAILS_BEFORE_ALERT and self.db_ok is not False:
                self.db_ok = False
                await self._alert(
                    "🔴 Database Not Responding",
                    "maid-cat can't reach the database. Nothing is being recorded, and restriction, "
                    "limit and history checks are being skipped.",
                    discord.Color.red(),
                )
            return  # soap-cat can't be checked without the database
        if self.db_ok is False:
            await self._alert(
                "🟢 Database Back",
                f"The database is answering again after {_ago(time.time() - self.db_down_since)}.",
                discord.Color.green(),
            )
        self.db_ok, self.db_fails = True, 0

        beat = await self.soap_cat_heartbeat()
        if beat is None:
            return  # soap-cat hasn't written a heartbeat yet, so there's nothing to compare
        stale = beat["age"] is None or beat["age"] > SOAP_CAT_STALE_SECONDS
        if stale and self.soap_cat_ok is not False:
            self.soap_cat_ok = False
            self.soap_cat_down_since = time.time() - (beat["age"] or 0)
            await self._alert(
                "🔴 soap-cat Not Responding",
                f"soap-cat's last heartbeat was {_ago(beat['age'] or 0)} ago. "
                "Start SOAP and auto-approve won't run SOAPs until it's back.",
                discord.Color.red(),
            )
        elif not stale:
            if self.soap_cat_ok is False:
                await self._alert(
                    "🟢 soap-cat Back",
                    f"soap-cat is responding again after {_ago(time.time() - self.soap_cat_down_since)}.",
                    discord.Color.green(),
                )
            self.soap_cat_ok = True

    async def _alert(self, title: str, description: str, color: discord.Color):
        for guild in self.bot.guilds:
            channel = guild.get_channel(ERROR_LOG_ID) if ERROR_LOG_ID else None
            if channel is None:
                continue
            embed = discord.Embed(
                title=title, description=description, color=color, timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Run .status for details")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException as e:
                print(f"Could not send health alert: {e}")

    # ---------- .status ----------

    @command_with_perms(
        min_role="Soaper",
        name="status",
        aliases=["health", "botstatus"],
        help="Check maid-cat, soap-cat and the database",
    )
    async def status(self, ctx):
        if hasattr(ctx, "defer"):
            await ctx.defer()
        guild = getattr(ctx, "guild", None)
        problems = False

        latency = self.bot.latency * 1000
        bot_line = f"✅ Up {_ago(time.time() - self.started)}"
        if latency == latency and latency != float("inf"):  # NaN or inf before the first heartbeat
            bot_line += f" · {latency:.0f} ms to Discord"

        if not db.enabled():
            db_line, problems = "⚠️ Not set up", True
            db_ms = None
        else:
            db_ms = await self.check_db()
            if db_ms is None:
                db_line, problems = "❌ Not responding", True
            else:
                db_line = f"✅ Responding · {db_ms:.0f} ms"

        if db_ms is None:
            soap_line = "❔ Unknown, needs the database"
        else:
            beat = await self.soap_cat_heartbeat()
            if beat is None:
                soap_line = "❔ No heartbeat yet, soap-cat may need updating"
            elif beat["age"] is not None and beat["age"] <= SOAP_CAT_STALE_SECONDS:
                ms = f" · {beat['latency_ms']} ms to Discord" if beat["latency_ms"] is not None else ""
                soap_line = f"✅ Last heartbeat {_ago(beat['age'])} ago{ms}"
            else:
                soap_line, problems = f"❌ No heartbeat for {_ago(beat['age'] or 0)}", True

        def count(category_id):
            category = guild.get_channel(category_id) if guild and category_id else None
            return len(category.text_channels) if isinstance(category, discord.CategoryChannel) else 0

        last = None
        if db_ms is not None:
            try:
                last = await db.fetchone(
                    "SELECT completed_at FROM transfers WHERE kind = 'SOAP' ORDER BY completed_at DESC LIMIT 1"
                )
            except Exception:
                last = None

        embed = discord.Embed(
            title="Bot Status",
            color=discord.Color.red() if problems else discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="maid-cat", value=bot_line, inline=False)
        embed.add_field(name="soap-cat", value=soap_line, inline=False)
        embed.add_field(name="Database", value=db_line, inline=False)
        embed.add_field(name="SOAP Channels", value=f"**{count(SOAP_CHANNEL_CATEGORY_ID)}**", inline=True)
        embed.add_field(name="Manual", value=f"**{count(MANUAL_SOAP_CATEGORY_ID)}**", inline=True)
        embed.add_field(name="NNID Channels", value=f"**{count(NNID_CHANNEL_CATEGORY_ID)}**", inline=True)
        if last and last["completed_at"]:
            ts = int(last["completed_at"].replace(tzinfo=timezone.utc).timestamp())  # stored as naive UTC
            embed.add_field(name="Last SOAP", value=f"<t:{ts}:R>", inline=True)
        embed.set_footer(text="Problems are also posted to the error log")
        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(StatusCog(bot))
