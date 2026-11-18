"""Keeps the database in step with Discord: channels, members, Soapers and restrictions.
Also has .helpee, .note, .leaderboard, .soaplimit and .importhistory."""

import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord.ext.bridge import BridgeOption

import db
import records
from constants import RESTRICTED_ROLE_ID, BAN_LOG_ID
from perms import _has_role_or_higher, command_with_perms

EXPIRY_CHECK_SECONDS = 60
LEADERBOARD_SIZE = 15
LOOKUP_TRANSFERS = 10
LOOKUP_NOTES = 3  # newest notes shown on .helpee, .notes shows the rest
NOTES_PAGE = 25
NOTE_MAX_LENGTH = 500
LEADERBOARD_PERIODS = {  # name -> (title, days back, or None for all time)
    "all": ("All Time", None),
    "year": ("Past Year", 365),
    "month": ("Past 30 Days", 30),
    "week": ("Past 7 Days", 7),
    "today": ("Past 24 Hours", 1),
}

SOAP_LIMIT_PERIOD_WORDS = {}
for _period, _words in {
    "day": ("day", "days", "d", "daily", "24h"),
    "week": ("week", "weeks", "w", "wk", "weekly", "7d"),
    "month": ("month", "months", "mo", "m", "monthly", "30d"),
    "year": ("year", "years", "y", "yr", "yearly", "365d"),
}.items():
    for _word in _words:
        SOAP_LIMIT_PERIOD_WORDS[_word] = _period
SOAP_LIMIT_PERIOD_WORDS[""] = None


def _ts(dt: datetime | None, style: str = "f") -> str:
    if dt is None:
        return "never"
    return f"<t:{int(dt.replace(tzinfo=timezone.utc).timestamp())}:{style}>"


class TrackingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._expiry_task = None
        self._synced = False
        self._importing = False
        # Restricted role changes the bot is making itself, so on_member_update doesn't record them twice
        self.own_role_changes: set[int] = set()
        records.restriction_end_listeners.append(self._restriction_ended)

    def cog_unload(self):
        if self._expiry_task:
            self._expiry_task.cancel()
        if self._restriction_ended in records.restriction_end_listeners:
            records.restriction_end_listeners.remove(self._restriction_ended)

    # ---------- startup ----------

    @commands.Cog.listener()
    async def on_ready(self):
        if not db.enabled():
            print("DATABASE is not set in constants.py, so nothing is recorded to the database")
            return
        if self._expiry_task is None or self._expiry_task.done():
            self._expiry_task = asyncio.create_task(self._expiry_loop())
        if self._synced:
            return
        self._synced = True
        try:
            await db.ensure_schema()
        except Exception as e:
            print(f"Could not set up the database tables: {e!r}")
            return
        await records.sync_all_channels(self.bot.guilds)
        for guild in self.bot.guilds:
            await self._sync_members(guild)
            await self._sync_restrictions(guild)
        print("Database synced with the server")

    async def _sync_members(self, guild: discord.Guild):
        """Refresh names, roles and in-server for everyone already in the users and soapers tables, in bulk."""
        try:
            user_ids = {r["user_id"] for r in await db.fetchall("SELECT user_id FROM users")}
            soaper_ids = {r["user_id"] for r in await db.fetchall("SELECT user_id FROM soapers")}
            here = [m for m in guild.members if m.id in user_ids]
            await db.executemany(
                "UPDATE users SET username = %s, display_name = %s, roles = %s, in_server = 1 WHERE user_id = %s",
                [(m.name, m.display_name, records.role_names(m), m.id) for m in here],
            )
            gone = user_ids - {m.id for m in here}
            await db.executemany("UPDATE users SET in_server = 0 WHERE user_id = %s", [(uid,) for uid in gone])
            # Anyone with the Soaper role is a Soaper, even before their first SOAP
            soapers_here = [m for m in guild.members if not m.bot and records.is_soaper(m)]
            await db.executemany(
                """INSERT INTO soapers (user_id, username, display_name, is_soaper) VALUES (%s, %s, %s, 1)
                   ON DUPLICATE KEY UPDATE username = VALUES(username), display_name = VALUES(display_name), is_soaper = 1""",
                [(m.id, m.name, m.display_name) for m in soapers_here],
            )
            former = soaper_ids - {m.id for m in soapers_here}
            await db.executemany(
                "UPDATE soapers SET is_soaper = 0 WHERE user_id = %s", [(uid,) for uid in former]
            )
            for m in guild.members:  # names of former Soapers who are still around
                if m.id in former:
                    await records.save_soaper(m)
        except Exception as e:
            print(f"Could not sync members with the database: {e!r}")

    async def _sync_restrictions(self, guild: discord.Guild):
        """Match restrictions in the database with who has the restricted role, for changes made while the bot was off."""
        role = guild.get_role(RESTRICTED_ROLE_ID) if RESTRICTED_ROLE_ID else None
        if role is None:
            return
        for member in role.members:
            if await records.active_restriction(member.id) is None:
                await records.add_restriction(
                    member, moderator_id=None, reason="Already had the restricted role when tracking started", expires_at=None
                )
        rows = await db.fetchall("SELECT DISTINCT user_id FROM restrictions WHERE ended_at IS NULL") or []
        for row in rows:
            member = guild.get_member(row["user_id"])
            if member and role not in member.roles:
                # Still active here but the role is gone, unless it's run out and the expiry check will handle it
                active = await records.active_restriction(member.id)
                if active and not (active["expires_at"] and active["expires_at"] <= records.now()):
                    await records.end_restriction(member.id, ended_by=None, end_reason="role removed")

    # ---------- temporary restrictions ----------

    async def _expiry_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for row in await records.expired_restrictions():
                    await self.lift_expired(row)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Restriction expiry check failed: {e!r}")
            await asyncio.sleep(EXPIRY_CHECK_SECONDS)

    async def lift_expired(self, row: dict):
        user_id = row["user_id"]
        await records.end_restriction(user_id, ended_by=None, end_reason="expired")
        for guild in self.bot.guilds:
            role = guild.get_role(RESTRICTED_ROLE_ID) if RESTRICTED_ROLE_ID else None
            member = guild.get_member(user_id)
            if role and member and role in member.roles:
                self.own_role_changes.add(member.id)
                try:
                    await member.remove_roles(role, reason="Temporary restriction ended")
                except discord.HTTPException as e:
                    print(f"Could not remove the restricted role from {member}: {e}")
            mod = self.bot.get_cog("ModerationCog")
            user = member or await records.resolve(guild, user_id, self.bot)
            if mod and user and BAN_LOG_ID and guild.get_channel(BAN_LOG_ID):
                await mod._log_mod_action(
                    guild=guild, user=user, action="unrestrict", source="Temporary restriction ended",
                    reason=row["reason"],
                )

    async def _restriction_ended(self, row: dict, ended_by: int | None, end_reason: str):
        """Log the end in the ban log, and ghost ping the user in the request channel if they tried to get a
        SOAP or NNID channel while restricted, so they see they can request again. They aren't told anything else.
        Expiry and .unrestrict already log the end themselves, so those are only logged here when there were attempts."""
        try:
            import constants

            attempts = {"SOAP": row.get("soap_attempts") or 0, "NNID": row.get("nnid_attempts") or 0}
            tried = {kind: n for kind, n in attempts.items() if n}
            if not tried and end_reason != "role removed":
                return
            request_ids = {
                "SOAP": getattr(constants, "REQUEST_SOAP_CHANNEL_ID", None),
                "NNID": getattr(constants, "REQUEST_NNID_CHANNEL_ID", None),
            }
            for guild in self.bot.guilds:
                member = guild.get_member(row["user_id"])
                pinged = []
                for kind in tried:
                    channel = guild.get_channel(request_ids[kind]) if request_ids[kind] else None
                    if member is None or channel is None:
                        continue
                    try:
                        ping = await channel.send(member.mention, allowed_mentions=discord.AllowedMentions(users=True))
                        await ping.delete()
                        pinged.append(channel.mention)
                    except discord.HTTPException as e:
                        print(f"Could not ping {member} in #{channel.name}: {e}")
                await self._log_restriction_end(guild, row, ended_by, end_reason, tried, pinged, member)
        except Exception as e:
            print(f"Restriction end handling failed: {e!r}")

    async def _log_restriction_end(self, guild, row, ended_by, end_reason, tried, pinged, member):
        log = guild.get_channel(BAN_LOG_ID) if BAN_LOG_ID else None
        if log is None:
            return
        user = member or await records.resolve(guild, row["user_id"], self.bot)
        how = {
            "expired": "Temporary restriction ran out",
            "unrestricted": "Removed with .unrestrict",
            "role removed": "Restricted role removed by hand",
        }.get(end_reason, end_reason)
        embed = discord.Embed(
            title="✅ Restriction Ended",
            description=f"<@{row['user_id']}> {discord.utils.escape_markdown(user.name) if user else ''}".strip(),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        if user:
            embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="How", value=how, inline=False)
        if ended_by and ended_by != self.bot.user.id:
            embed.add_field(name="Ended by", value=f"<@{ended_by}>", inline=True)
        embed.add_field(name="Restricted", value=_ts(row["created_at"], "f"), inline=True)
        if tried:
            counts = ", ".join(f"{n} {kind}" for kind, n in tried.items())
            embed.add_field(name="Blocked Attempts", value=counts, inline=True)
            if pinged:
                embed.add_field(name="Pinged In", value=", ".join(pinged), inline=True)
            elif member is None:
                embed.add_field(name="Pinged In", value="Not pinged, they're not in the server", inline=True)
        if row.get("reason"):
            embed.add_field(name="Reason", value=row["reason"][:1024], inline=False)
        embed.set_footer(text=f"ID: {row['user_id']}")
        try:
            await log.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            print(f"Could not log a restriction ending: {e}")

    # ---------- keeping things current ----------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await records.sync_channel(channel)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if (before.category_id, before.name, getattr(before, "topic", None)) != (
            after.category_id, after.name, getattr(after, "topic", None)
        ):
            await records.sync_channel(after)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await records.channel_deleted(channel.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await records.update_tracked_member(member)
        # Leaving and rejoining doesn't get rid of a restriction
        active = await records.active_restriction(member.id)
        role = member.guild.get_role(RESTRICTED_ROLE_ID) if RESTRICTED_ROLE_ID else None
        if active and role and role not in member.roles:
            self.own_role_changes.add(member.id)
            try:
                await member.add_roles(role, reason="Restricted before leaving the server")
            except discord.HTTPException as e:
                print(f"Could not restrict {member} again after they rejoined: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await records.update_tracked_member(member, in_server=False)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles and before.name == after.name and before.display_name == after.display_name:
            return
        await records.update_tracked_member(after)
        if records.is_soaper(after) and not records.is_soaper(before):
            await records.save_soaper(after)

        role = after.guild.get_role(RESTRICTED_ROLE_ID) if RESTRICTED_ROLE_ID else None
        if role is None or (role in before.roles) == (role in after.roles):
            return
        if after.id in self.own_role_changes:
            self.own_role_changes.discard(after.id)
            return
        # Someone added or removed the restricted role by hand, so record it
        moderator, reason = await self._role_change_author(after)
        if moderator and moderator.id == self.bot.user.id:
            return  # the bot's own commands record their changes themselves
        if role in after.roles:
            if await records.active_restriction(after.id) is None:
                await records.add_restriction(
                    after, moderator_id=moderator.id if moderator else None,
                    reason=reason or "Restricted role added by hand", expires_at=None,
                )
        else:
            await records.end_restriction(
                after.id, ended_by=moderator.id if moderator else None, end_reason="role removed"
            )

    async def _role_change_author(self, member: discord.Member):
        try:
            async for entry in member.guild.audit_logs(action=discord.AuditLogAction.member_role_update, limit=5):
                if entry.target and entry.target.id == member.id:
                    return entry.user, entry.reason
        except discord.HTTPException:
            pass
        return None, None

    # ---------- commands ----------

    async def _reply(self, ctx, **kwargs):
        """Reply in the channel the command was used in, without pinging anyone it mentions."""
        return await ctx.respond(allowed_mentions=discord.AllowedMentions.none(), **kwargs)

    @command_with_perms(
        min_role="Soaper",
        name="helpee",
        aliases=["lookup", "inspect", "history", "userinfo"],
        help="Show a user's SOAP and NNID history, serials, restrictions and channels",
    )
    async def lookup(self, ctx, user: BridgeOption(discord.User, "User to inspect")):
        if not db.enabled():
            return await self._reply(ctx, content="The database isn't set up.")
        if hasattr(ctx, "defer"):
            await ctx.defer()
        try:
            row = await db.fetchone("SELECT * FROM users WHERE user_id = %s", (user.id,))
            totals = await db.fetchone(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT serial_base) AS serials FROM transfers WHERE user_id = %s",
                (user.id,),
            )
            transfers = await db.fetchall(
                """SELECT t.*, s.username AS soaper_name FROM transfers t
                   LEFT JOIN soapers s ON s.user_id = t.soaper_id
                   WHERE t.user_id = %s ORDER BY t.completed_at DESC LIMIT %s""",
                (user.id, LOOKUP_TRANSFERS),
            )
            restrictions = await db.fetchall(
                "SELECT * FROM restrictions WHERE user_id = %s ORDER BY created_at DESC LIMIT 3", (user.id,)
            )
            restriction_count = await db.fetchone("SELECT COUNT(*) AS n FROM restrictions WHERE user_id = %s", (user.id,))
            channels = await db.fetchall(
                "SELECT * FROM channels WHERE user_id = %s ORDER BY created_at DESC LIMIT 3", (user.id,)
            )
            soaper = await db.fetchone("SELECT * FROM soapers WHERE user_id = %s", (user.id,))
            notes = await self._notes_for(user.id, LOOKUP_NOTES)
            note_count = await db.fetchone("SELECT COUNT(*) AS n FROM user_notes WHERE user_id = %s", (user.id,))
            shared = await db.fetchall(
                """SELECT t2.user_id, MAX(u.username) AS username, MAX(t2.serial) AS serial FROM transfers t1
                   JOIN transfers t2 ON t1.serial_base = t2.serial_base AND t2.user_id <> t1.user_id
                   LEFT JOIN users u ON u.user_id = t2.user_id
                   WHERE t1.user_id = %s AND t1.serial_base IS NOT NULL
                   GROUP BY t2.user_id LIMIT 5""",
                (user.id,),
            )
        except Exception as e:
            return await self._reply(ctx, content=f"Could not read the database: {e}")
        limit = await records.soap_limit_for(user) if row and row["soap_count"] else None

        guild = getattr(ctx, "guild", None)
        member = guild.get_member(user.id) if guild else None
        now = records.now()

        # Standing decides the colour: red while restricted, orange when something needs a look
        flags = []
        if row and row["restriction"] == "permanent":
            standing, color = "⛔ Restricted, permanent", discord.Color.red()
        elif row and row["restriction"] == "temporary":
            standing = f"⛔ Restricted until {_ts(row['restricted_until'], 'D')}"
            color = discord.Color.red()
        elif limit:
            standing, color = f"⏳ SOAP limit reached until {_ts(limit['until'], 'D')}", discord.Color.orange()
        else:
            standing, color = "✅ Good standing", discord.Color.green()
        for s in shared:
            name = discord.utils.escape_markdown(s["username"] or str(s["user_id"]))
            flags.append(f"Serial `{s['serial']}` was also used by **{name}** (`{s['user_id']}`)")
        if user.created_at and datetime.now(timezone.utc) - user.created_at < timedelta(days=7):
            flags.append("Account is less than 7 days old")
        if restriction_count and restriction_count["n"] and not (row and row["restriction"] != "none"):
            n = restriction_count["n"]
            flags.append(f"Restricted {n} time{'s' if n != 1 else ''} before")
        if flags and color == discord.Color.green():
            color = discord.Color.orange()

        embed = discord.Embed(
            title="User Overview",
            description=f"{user.mention} · `{user.id}`",
            color=color if (row or soaper) else discord.Color.light_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        name = user.name if not getattr(user, "global_name", None) else f"{user.name} ({user.global_name})"
        embed.set_author(name=name, icon_url=user.display_avatar.url)
        embed.set_footer(text="Bluehax Records")

        if not row and not soaper:
            embed.add_field(name="History", value="No SOAP or NNID transfers on record.", inline=False)
            if notes:
                self._add_notes_field(embed, notes, note_count["n"] if note_count else len(notes), guild)
            return await self._reply(ctx, embed=embed)

        if row:
            embed.add_field(name="SOAPs", value=f"**{row['soap_count']}**", inline=True)
            embed.add_field(name="NNID Transfers", value=f"**{row['nnid_count']}**", inline=True)
            embed.add_field(name="Serials", value=f"**{totals['serials'] if totals else 0}**", inline=True)
            embed.add_field(name="Standing", value=standing, inline=True)
            embed.add_field(name="Account Created", value=_ts(user.created_at.replace(tzinfo=None), "D"), inline=True)
            if member and member.joined_at:
                joined = _ts(member.joined_at.replace(tzinfo=None), "D")
            else:
                joined = "Not in server"
            embed.add_field(name="Joined Server", value=joined, inline=True)

        if flags:
            embed.add_field(name="⚠️ Flags", value="\n".join(f"• {f}" for f in flags)[:1024], inline=False)

        if notes:
            self._add_notes_field(embed, notes, note_count["n"] if note_count else len(notes), guild)

        if transfers:
            total = totals["n"] if totals else len(transfers)
            lines = []
            for t in transfers:
                kind = "SOAP" if t["kind"] == "SOAP" else "NNID"
                if t["outcome"] == "lottery":
                    kind += " (Lottery)"
                bits = [_ts(t["completed_at"], "d"), f"**{kind}**"]
                if t["serial"]:
                    bits.append(f"`{t['serial']}`")
                if t["soaper_id"] == self.bot.user.id:
                    bits.append("Auto-approved")
                elif t["soaper_id"]:
                    by = guild.get_member(t["soaper_id"]) if guild else None
                    by = by.name if by else (t["soaper_name"] or str(t["soaper_id"]))
                    bits.append(f"by {discord.utils.escape_markdown(by)}")
                if t.get("eshop_works") == 0:
                    bits.append("eShop didn't work")
                lines.append(" · ".join(bits))
            heading = "Transfer History" + (f" (latest {len(transfers)} of {total})" if total > len(transfers) else "")
            embed.add_field(name=heading, value="\n".join(lines)[:1024], inline=False)

        if restrictions:
            lines = []
            for r in restrictions:
                length = f"until {_ts(r['expires_at'], 'd')}" if r["expires_at"] else "Permanent"
                state = "Active" if r["ended_at"] is None else f"Ended {_ts(r['ended_at'], 'd')}"
                line = f"{_ts(r['created_at'], 'd')} · {length} · {state}"
                if r["reason"]:
                    line += f"\n-# {discord.utils.escape_markdown(r['reason'][:120])}"
                lines.append(line)
            embed.add_field(name="Restrictions", value="\n".join(lines)[:1024], inline=False)

        if channels:
            lines = []
            for c in channels:
                where = f"<#{c['channel_id']}>" if c["status"] != "deleted" else f"`#{c['name']}`"
                line = f"{where} · {c['status'].capitalize()} · {_ts(c['created_at'], 'd')}"
                if c["pair_code"]:
                    line += f" · Pair code `{c['pair_code']}`"
                lines.append(line)
            embed.add_field(name="Recent Channels", value="\n".join(lines)[:1024], inline=False)

        if soaper and (soaper["soap_count"] or soaper["nnid_count"]):
            embed.add_field(
                name="As a Soaper",
                value=f"**{soaper['soap_count']}** SOAPs · **{soaper['nnid_count']}** NNID transfers · "
                f"last {_ts(soaper['last_soap_at'], 'R')}",
                inline=False,
            )
        await self._reply(ctx, embed=embed)

    # ---------- notes ----------

    async def _notes_for(self, user_id: int, limit: int) -> list[dict]:
        return await db.fetchall(
            """SELECT n.*, COALESCE(s.username, u.username) AS author_name FROM user_notes n
               LEFT JOIN soapers s ON s.user_id = n.author_id
               LEFT JOIN users u ON u.user_id = n.author_id
               WHERE n.user_id = %s ORDER BY n.created_at DESC, n.id DESC LIMIT %s""",
            (user_id, limit),
        )

    @staticmethod
    def _note_lines(notes: list[dict], guild: discord.Guild | None) -> list[str]:
        lines = []
        for n in notes:
            author = guild.get_member(n["author_id"]) if guild else None
            author = author.name if author else (n["author_name"] or str(n["author_id"]))
            lines.append(
                f"`#{n['id']}` {discord.utils.escape_markdown(n['note'])}\n"
                f"-# {discord.utils.escape_markdown(author)} · {_ts(n['created_at'], 'd')}"
            )
        return lines

    def _add_notes_field(self, embed: discord.Embed, notes: list[dict], total: int, guild):
        heading = "📝 Notes" + (f" (latest {len(notes)} of {total}, see .notes)" if total > len(notes) else "")
        value = ""
        for line in self._note_lines(notes, guild):
            if len(value) + len(line) + 1 > 1024:
                break
            value += ("\n" if value else "") + line
        embed.add_field(name=heading, value=value or "Too long to show here, see .notes", inline=False)

    @command_with_perms(
        min_role="Soaper",
        name="note",
        aliases=["addnote"],
        help="Leave a note on a user, shown on .helpee: .note @user text",
    )
    async def note(
        self,
        ctx,
        user: BridgeOption(discord.User, "User the note is about"),
        *,
        text: BridgeOption(str, "The note", required=False) = None,
    ):
        if not db.enabled():
            return await ctx.respond("The database isn't set up.", ephemeral=True)
        text = (text or "").strip()
        if not text:
            return await self._show_notes(ctx, user)
        if len(text) > NOTE_MAX_LENGTH:
            return await ctx.respond(f"Notes can be up to {NOTE_MAX_LENGTH} characters.", ephemeral=True)
        try:
            member = ctx.guild.get_member(user.id) if ctx.guild else None
            await records.save_user(member or user)  # so the note shows on .helpee before any transfer
            note_id = await db.insert(
                "INSERT INTO user_notes (user_id, author_id, note, created_at) VALUES (%s, %s, %s, %s)",
                (user.id, ctx.author.id, text, records.now()),
            )
        except Exception as e:
            return await ctx.respond(f"Could not save the note: {e}", ephemeral=True)
        name = discord.utils.escape_markdown(user.name)
        await ctx.respond(f"📝 Saved note `#{note_id}` on **{name}**.", allowed_mentions=discord.AllowedMentions.none())

    @command_with_perms(
        min_role="Soaper",
        name="notes",
        help="Show every note on a user: .notes @user",
    )
    async def notes(self, ctx, user: BridgeOption(discord.User, "User to show notes for")):
        if not db.enabled():
            return await ctx.respond("The database isn't set up.", ephemeral=True)
        await self._show_notes(ctx, user)

    async def _show_notes(self, ctx, user):
        try:
            notes = await self._notes_for(user.id, NOTES_PAGE)
        except Exception as e:
            return await ctx.respond(f"Could not read the database: {e}", ephemeral=True)
        embed = discord.Embed(title="📝 Notes", description=f"{user.mention} · `{user.id}`", color=discord.Color.blurple())
        embed.set_author(name=user.name, icon_url=user.display_avatar.url)
        lines = self._note_lines(notes, getattr(ctx, "guild", None))
        body = ""
        for line in lines:
            if len(body) + len(line) + 2 > 4000:
                break
            body += ("\n\n" if body else "") + line
        embed.description += "\n\n" + (body or "No notes yet. Add one with `.note @user text`.")
        embed.set_footer(text="Remove a note with .delnote <number>")
        await self._reply(ctx, embed=embed)

    @command_with_perms(
        min_role="Soaper",
        name="delnote",
        aliases=["deletenote", "removenote", "rmnote"],
        help="Remove a note by its number: .delnote 12 (your own notes, or any note if you're Staff)",
    )
    async def delnote(self, ctx, number: BridgeOption(str, "Note number, shown as #12")):
        if not db.enabled():
            return await ctx.respond("The database isn't set up.", ephemeral=True)
        number = number.strip().lstrip("#")
        if not number.isdigit():
            return await ctx.respond("Use the note's number, like `.delnote 12`.", ephemeral=True)
        row = await db.fetchone("SELECT * FROM user_notes WHERE id = %s", (int(number),))
        if row is None:
            return await ctx.respond(f"There's no note `#{number}`.", ephemeral=True)
        staff = discord.utils.get(ctx.guild.roles, name="Staff") if ctx.guild else None
        is_staff = staff is not None and isinstance(ctx.author, discord.Member) and _has_role_or_higher(ctx.author, staff)
        if row["author_id"] != ctx.author.id and not is_staff:
            return await ctx.respond("You can only remove your own notes.", ephemeral=True)
        await db.execute("DELETE FROM user_notes WHERE id = %s", (row["id"],))
        await ctx.respond(f"Removed note `#{row['id']}`.")

    @command_with_perms(
        min_role="Soaper",
        name="leaderboard",
        aliases=["lb", "topsoapers", "soaperstats", "top"],
        help="Soaper leaderboard: .leaderboard [all|year|month|week|today]",
    )
    async def leaderboard(
        self,
        ctx,
        period: BridgeOption(
            str,
            "Time period",
            choices=["all", "year", "month", "week", "today"],
            required=False,
        ) = "all",
    ):
        if not db.enabled():
            return await ctx.respond("The database isn't set up.", ephemeral=True)
        period = (period or "all").lower()
        if period not in LEADERBOARD_PERIODS:
            return await ctx.respond(
                f"`{period}` isn't a period. Use one of: {', '.join(f'`{p}`' for p in LEADERBOARD_PERIODS)}.",
                ephemeral=True,
            )
        label, days = LEADERBOARD_PERIODS[period]
        since = (records.now() - timedelta(days=days)) if days else datetime(2000, 1, 1)
        try:
            rows = await db.fetchall(
                """SELECT t.soaper_id, COUNT(*) AS soaps, SUM(t.outcome = 'lottery') AS lotteries,
                          MAX(t.completed_at) AS last_at, s.is_soaper, s.username
                   FROM transfers t LEFT JOIN soapers s ON s.user_id = t.soaper_id
                   WHERE t.kind = 'SOAP' AND t.soaper_id IS NOT NULL AND t.soaper_id != %s AND t.completed_at >= %s
                   GROUP BY t.soaper_id, s.is_soaper, s.username
                   ORDER BY soaps DESC, last_at DESC""",
                (self.bot.user.id, since),
            )
            totals = await db.fetchone(
                """SELECT COUNT(*) AS soaps, SUM(soaper_id = %s) AS auto
                   FROM transfers WHERE kind = 'SOAP' AND completed_at >= %s""",
                (self.bot.user.id, since),
            )
        except Exception as e:
            return await ctx.respond(f"Could not read the database: {e}", ephemeral=True)

        total_soaps = int(totals["soaps"] or 0) if totals else 0
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}

        def line(rank: int, r: dict) -> str:
            soaps, lotteries = int(r["soaps"] or 0), int(r["lotteries"] or 0)
            bits = [f"**{soaps:,}** SOAP{'s' if soaps != 1 else ''}"]
            if total_soaps and soaps:
                share = soaps / total_soaps
                bits.append("<1%" if share < 0.005 else f"{share:.0%}")
            if lotteries:
                bits.append(f"{lotteries} lotter{'ies' if lotteries != 1 else 'y'}")
            # The live role is always current; the stored flag is only a fallback when they aren't in the server
            guild = getattr(ctx, "guild", None)
            member = guild.get_member(r["soaper_id"]) if guild else None
            current = records.is_soaper(member) if member else bool(r["is_soaper"])
            former = "" if current else " *(former)*"
            name = member.name if member else (r["username"] or str(r["soaper_id"]))
            name = discord.utils.escape_markdown(name)
            return f"{medals.get(rank, f'**{rank}.**')} **{name}**{former} · " + " · ".join(bits)

        lines = [line(i, r) for i, r in enumerate(rows[:LEADERBOARD_SIZE], 1)]
        author_id = ctx.author.id
        mine = next((i for i, r in enumerate(rows, 1) if r["soaper_id"] == author_id), None)
        if mine and mine > LEADERBOARD_SIZE:
            lines += ["...", line(mine, rows[mine - 1])]

        embed = discord.Embed(
            title=f"🏅 Soaper Leaderboard · {label}",
            description="\n".join(lines) or "No SOAPs recorded in this period.",
            color=discord.Color.gold(),
        )
        if total_soaps:
            parts = [
                f"{total_soaps:,} SOAP{'s' if total_soaps != 1 else ''}",
                f"{len(rows)} Soaper{'s' if len(rows) != 1 else ''}",
            ]
            auto = int(totals["auto"] or 0)
            if auto:
                parts.append(f"{auto:,} auto-approved")
            footer = " · ".join(parts)
            embed.set_footer(text=footer)
        await ctx.respond(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @command_with_perms(
        min_role="Staff",
        name="soaplimit",
        aliases=["soaplimits", "setsoaplimit"],
        help="How many SOAPs one person can get: .soaplimit 2 week, .soaplimit off week, or no args to show them",
    )
    async def soaplimit(
        self,
        ctx,
        count: BridgeOption(str, "Most SOAPs allowed, or off to remove the limit", required=False) = None,
        *,
        period: BridgeOption(str, "Per day, week, month (30 days) or year", required=False) = None,
    ):
        if not db.enabled():
            return await ctx.respond("The database isn't set up.", ephemeral=True)
        if count is not None:
            period = (period or "").lower().strip()
            for word in ("per ", "a ", "every ", "/"):
                period = period.removeprefix(word).strip()  # .soaplimit 2 per week
            period_name = SOAP_LIMIT_PERIOD_WORDS.get(period)
            off = count.lower() in ("off", "none", "remove", "clear", "0")
            if period and period_name is None:
                return await ctx.respond(
                    f"`{period}` isn't a period. Use `day`, `week`, `month` or `year`.", ephemeral=True
                )
            if off:
                await records.set_soap_limit(period_name, None, ctx.author.id)
            elif not count.isdigit() or period_name is None:
                return await ctx.respond(
                    "Use `.soaplimit 2 week` to allow 2 SOAPs per week, or `.soaplimit off week` to remove that limit.",
                    ephemeral=True,
                )
            elif await records.set_soap_limit(period_name, int(count), ctx.author.id) is None:
                return await ctx.respond("Could not save the limit to the database.", ephemeral=True)

        limits = await records.soap_limits()
        if limits is None:
            return await ctx.respond("Could not read the database.", ephemeral=True)
        lines = [
            f"**{most}** SOAP{'s' if most != 1 else ''} per {'30 days' if p == 'month' else p}"
            for p, most in sorted(limits.items(), key=lambda kv: records.LIMIT_PERIODS[kv[0]])
        ]
        embed = discord.Embed(
            title="⏳ SOAP Limits",
            description="\n".join(lines) or "No limits set.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Each limit is per person and rolling, e.g. week is any 7 days. NNID transfers and SOAPs where "
            "the helpee said the eShop didn't work aren't counted.")
        await ctx.respond(embed=embed)

    @command_with_perms(
        allowed_roles=["Developer"],
        name="importhistory",
        help="Read the old log channels into the database (safe to run again)",
    )
    async def importhistory(self, ctx):
        if not db.enabled():
            return await ctx.respond("The database isn't set up.", ephemeral=True)
        from history_import import run_import

        if self._importing:
            return await ctx.respond("An import is already running.", ephemeral=True)
        self._importing = True
        await ctx.respond("Reading the log channels into the database. This can take a while, I'll post here when it's done.")
        try:
            summary = await run_import(self.bot, ctx.guild)
        except Exception as e:
            await ctx.channel.send(f"Import failed: `{e!r}`")
            raise
        finally:
            self._importing = False
        await ctx.channel.send(embed=summary)


def setup(bot):
    bot.add_cog(TrackingCog(bot))
