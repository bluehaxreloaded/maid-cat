"""What maid-cat records in the database: users, transfers, Soapers, restrictions and channels.

Every function here is safe to call from anywhere: when the database is off they do nothing, and when it
fails they print the error and carry on, so recording can never break a SOAP.
"""

import asyncio
import functools
import re
import traceback
from datetime import datetime, timedelta, timezone

import discord

import db
from constants import (
    SOAP_CHANNEL_SUFFIX,
    NNID_CHANNEL_SUFFIX,
    SOAP_CHANNEL_CATEGORY_ID,
    MANUAL_SOAP_CATEGORY_ID,
    NNID_CHANNEL_CATEGORY_ID,
    TEMP_ARCHIVE_CATEGORY_ID,
    SOAPER_ROLE_ID,
)

MENTION_RE = re.compile(r"<@!?(\d+)>")
SERIAL_BASE_RE = re.compile(r"^([A-Z]{1,3})(\d{8})\d?$")


def serial_base(serial: str | None) -> str | None:
    """The serial without its check digit: the sticker has 9 digits, the console's own records 8."""
    if not serial:
        return None
    serial = serial.strip().upper()
    match = SERIAL_BASE_RE.match(serial)
    return match.group(1) + match.group(2) if match else serial
# Same format soap.py writes into archived channel topics
ARCHIVE_DELETION_RE = re.compile(r"Archived\.\s*Deletion scheduled:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*UTC\.")
ARCHIVE_DAYS = 3
CHANNEL_CATEGORIES = (SOAP_CHANNEL_CATEGORY_ID, MANUAL_SOAP_CATEGORY_ID, NNID_CHANNEL_CATEGORY_ID, TEMP_ARCHIVE_CATEGORY_ID)


def quiet(func):
    """Skip when the database is off, and never let a database error escape."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not db.enabled():
            return None
        try:
            await db.ensure_schema()
            return await func(*args, **kwargs)
        except Exception:
            print(f"Database error in {func.__name__}:")
            traceback.print_exc()
            return None

    return wrapper


def utc(dt: datetime | None) -> datetime | None:
    """A naive UTC datetime for the database."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def role_names(member) -> str | None:
    roles = getattr(member, "roles", None)
    if roles is None:
        return None
    names = [r.name for r in sorted(roles, key=lambda r: r.position, reverse=True) if not r.is_default()]
    return ", ".join(names)[:1024]


def is_soaper(member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    role = member.guild.get_role(SOAPER_ROLE_ID) if SOAPER_ROLE_ID else None
    if role is None:
        return False
    return role in member.roles or member.top_role.position > role.position


async def resolve(guild: discord.Guild | None, user_id: int, bot=None):
    """The member, or else the user, for an ID. None if Discord doesn't know them."""
    if guild:
        member = guild.get_member(user_id)
        if member:
            return member
    if bot:
        user = bot.get_user(user_id)
        if user:
            return user
        try:
            return await bot.fetch_user(user_id)
        except discord.HTTPException:
            return None
    return None


# ---------- users ----------

@quiet
async def save_user(user, in_server: bool | None = None):
    """Add or refresh someone's name, roles and whether they're in the server."""
    if user is None:
        return
    member = isinstance(user, discord.Member)
    in_server = member if in_server is None else in_server
    await db.execute(
        """INSERT INTO users (user_id, username, display_name, roles, in_server)
           VALUES (%s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE username = VALUES(username), display_name = VALUES(display_name),
               roles = COALESCE(VALUES(roles), roles), in_server = VALUES(in_server)""",
        (user.id, user.name, getattr(user, "display_name", None), role_names(user), int(in_server)),
    )


@quiet
async def save_placeholder_user(user_id: int):
    """A row for someone Discord no longer knows (e.g. a deleted account), so their history still counts."""
    await db.execute(
        "INSERT IGNORE INTO users (user_id, username, in_server) VALUES (%s, %s, 0)", (user_id, f"unknown-{user_id}")
    )


@quiet
async def update_tracked_member(member: discord.Member, in_server: bool = True):
    """Keep names and roles current, only for people already in the users or soapers table."""
    tracked = await db.fetchone(
        """SELECT (SELECT 1 FROM users WHERE user_id = %s) AS u, (SELECT 1 FROM soapers WHERE user_id = %s) AS s""",
        (member.id, member.id),
    )
    if tracked and tracked["u"]:
        await save_user(member, in_server=in_server)
    if tracked and tracked["s"]:
        await save_soaper(member, in_server=in_server)


@quiet
async def refresh_user_counts(user_id: int):
    await db.execute(
        """UPDATE users u SET
               soap_count = (SELECT COUNT(*) FROM transfers WHERE user_id = u.user_id AND kind = 'SOAP'),
               nnid_count = (SELECT COUNT(*) FROM transfers WHERE user_id = u.user_id AND kind = 'NNID'),
               first_transfer_at = (SELECT MIN(completed_at) FROM transfers WHERE user_id = u.user_id),
               last_transfer_at = (SELECT MAX(completed_at) FROM transfers WHERE user_id = u.user_id)
           WHERE user_id = %s""",
        (user_id,),
    )


# ---------- Soapers ----------

@quiet
async def save_soaper(user, in_server: bool = True):
    if user is None:
        return
    await db.execute(
        """INSERT INTO soapers (user_id, username, display_name, is_soaper)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE username = VALUES(username), display_name = VALUES(display_name),
               is_soaper = VALUES(is_soaper)""",
        (user.id, user.name, getattr(user, "display_name", None), int(in_server and is_soaper(user))),
    )


@quiet
async def refresh_soaper_counts(soaper_id: int):
    await db.execute(
        """UPDATE soapers s SET
               soap_count = (SELECT COUNT(*) FROM transfers WHERE soaper_id = s.user_id AND kind = 'SOAP'),
               nnid_count = (SELECT COUNT(*) FROM transfers WHERE soaper_id = s.user_id AND kind = 'NNID'),
               first_soap_at = (SELECT MIN(completed_at) FROM transfers WHERE soaper_id = s.user_id),
               last_soap_at = (SELECT MAX(completed_at) FROM transfers WHERE soaper_id = s.user_id)
           WHERE user_id = %s""",
        (soaper_id,),
    )


# ---------- transfers ----------

@quiet
async def record_transfer(
    guild: discord.Guild | None,
    *,
    user_id: int,
    kind: str,
    method: str,
    completed_at: datetime,
    outcome: str = "transfer",
    serial: str | None = None,
    soaper_id: int | None = None,
    channel_id: int | None = None,
    donor: str | None = None,
    message_id: int | None = None,
    source: str = "live",
    bot=None,
    refresh: bool = True,
) -> bool:
    """Record a finished SOAP or NNID transfer. Returns True if it's new (the same message is only counted once)."""
    # Fill in what the channel already knows: the serial, the Soaper and, from soap-cat, the donor
    if channel_id:
        row = await db.fetchone("SELECT serial, exefs_serial, soaper_id, donor FROM channels WHERE channel_id = %s", (channel_id,))
        if row:
            serial = serial or row["serial"] or row["exefs_serial"]
            soaper_id = soaper_id or row["soaper_id"]
            donor = donor or (row["donor"] if outcome == "transfer" and kind == "SOAP" else None)
    if serial and serial.upper() == "SKIP":
        serial = None

    helpee = await resolve(guild, user_id, bot)
    if helpee:
        await save_user(helpee)
    else:
        await save_placeholder_user(user_id)
    soaper = await resolve(guild, soaper_id, bot) if soaper_id else None
    # A SOAP maid-cat approved by itself keeps its ID on the transfer, but the bot isn't a Soaper
    by_bot = bool(soaper and soaper.bot)
    if soaper and not by_bot:
        await save_soaper(soaper, in_server=isinstance(soaper, discord.Member))
    elif soaper_id:
        await db.execute(
            "INSERT IGNORE INTO soapers (user_id, username) VALUES (%s, %s)", (soaper_id, f"unknown-{soaper_id}")
        )

    added = await db.execute(
        """INSERT IGNORE INTO transfers
               (user_id, kind, outcome, method, serial, serial_base, donor, soaper_id, channel_id, completed_at, source, message_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (user_id, kind, outcome, method, serial, serial_base(serial), donor, soaper_id, channel_id,
         utc(completed_at), source, message_id),
    )
    if channel_id and source == "live":
        await db.execute(
            "UPDATE channels SET completed_at = %s, soaper_id = COALESCE(%s, soaper_id) WHERE channel_id = %s",
            (utc(completed_at), soaper_id, channel_id),
        )
    if refresh:
        await refresh_user_counts(user_id)
        if soaper_id and not by_bot:
            await refresh_soaper_counts(soaper_id)
    return bool(added)


@quiet
async def recently_recorded(channel_id: int, kind: str, minutes: int = 10) -> bool:
    """Whether this channel already had a transfer recorded in the last few minutes (e.g. .soapnormal right after soap-cat)."""
    row = await db.fetchone(
        """SELECT 1 FROM transfers WHERE channel_id = %s AND kind = %s
           AND completed_at > UTC_TIMESTAMP() - INTERVAL %s MINUTE LIMIT 1""",
        (channel_id, kind, minutes),
    )
    return row is not None


# ---------- restrictions ----------

@quiet
async def active_restriction(user_id: int) -> dict | None:
    return await db.fetchone(
        "SELECT * FROM restrictions WHERE user_id = %s AND ended_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )


@quiet
async def sync_user_restriction(user_id: int):
    """Copy the user's current restriction onto their users row."""
    active = await active_restriction(user_id)
    if active is None:
        kind, until = "none", None
    else:
        kind, until = ("temporary", active["expires_at"]) if active["expires_at"] else ("permanent", None)
    await db.execute(
        "UPDATE users SET restriction = %s, restricted_until = %s WHERE user_id = %s", (kind, until, user_id)
    )


@quiet
async def add_restriction(
    user,
    *,
    moderator_id: int | None,
    reason: str | None,
    expires_at: datetime | None,
    created_at: datetime | None = None,
    source: str = "live",
    message_id: int | None = None,
    end_reason_for_old: str = "replaced",
) -> int | None:
    """Start a restriction (ending any current one). expires_at None means permanent."""
    if isinstance(user, int):
        await save_placeholder_user(user)
        user_id = user
    else:
        await save_user(user)
        user_id = user.id
    created_at = utc(created_at) or now()
    await db.execute(
        """UPDATE restrictions SET ended_at = %s, ended_by = %s, end_reason = %s
           WHERE user_id = %s AND ended_at IS NULL""",
        (created_at, moderator_id, end_reason_for_old, user_id),
    )
    new_id = await db.insert(
        """INSERT IGNORE INTO restrictions (user_id, moderator_id, reason, created_at, expires_at, source, message_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (user_id, moderator_id, (reason or None) and reason[:512], created_at, utc(expires_at), source, message_id),
    )
    await sync_user_restriction(user_id)
    return new_id


# Called with (restriction row, ended_by, end_reason) when a restriction really ends, see TrackingCog
restriction_end_listeners: list = []
LISTENED_END_REASONS = ("expired", "unrestricted", "role removed")  # not "replaced" or "role could not be added"


@quiet
async def end_restriction(user_id: int, *, ended_by: int | None, end_reason: str, ended_at: datetime | None = None) -> bool:
    active = await active_restriction(user_id)
    changed = await db.execute(
        """UPDATE restrictions SET ended_at = %s, ended_by = %s, end_reason = %s
           WHERE user_id = %s AND ended_at IS NULL""",
        (utc(ended_at) or now(), ended_by, end_reason, user_id),
    )
    await sync_user_restriction(user_id)
    if changed and active and end_reason in LISTENED_END_REASONS:
        for listener in restriction_end_listeners:
            asyncio.create_task(listener(active, ended_by, end_reason))
    return bool(changed)


@quiet
async def count_restricted_attempt(user_id: int, kind: str):
    """Add one blocked SOAP or NNID attempt to the user's active restriction."""
    column = "soap_attempts" if kind == "SOAP" else "nnid_attempts"
    await db.execute(
        f"UPDATE restrictions SET {column} = {column} + 1 WHERE user_id = %s AND ended_at IS NULL", (user_id,)
    )


@quiet
async def expired_restrictions() -> list[dict]:
    return await db.fetchall(
        """SELECT * FROM restrictions
           WHERE ended_at IS NULL AND expires_at IS NOT NULL AND expires_at <= UTC_TIMESTAMP()"""
    ) or []


# ---------- channels ----------

def channel_kind(channel: discord.abc.GuildChannel) -> str | None:
    name = re.sub(r"-cya$", "", channel.name)
    category_id = channel.category.id if channel.category else None
    if name.endswith(NNID_CHANNEL_SUFFIX) or category_id == NNID_CHANNEL_CATEGORY_ID:
        return "NNID"
    if name.endswith(SOAP_CHANNEL_SUFFIX) or category_id in (SOAP_CHANNEL_CATEGORY_ID, MANUAL_SOAP_CATEGORY_ID):
        return "SOAP"
    return None


def is_tracked_channel(channel) -> bool:
    """A helpee's SOAP/NNID channel: in one of the SOAP/NNID/archive categories, with the helpee in its topic."""
    return (
        isinstance(channel, discord.TextChannel)
        and channel.category is not None
        and channel.category.id in CHANNEL_CATEGORIES
        and MENTION_RE.search(channel.topic or "") is not None
        and channel_kind(channel) is not None
    )


@quiet
async def sync_channel(channel: discord.TextChannel):
    """Save a SOAP/NNID channel's current state: who it's for, open/manual/archived, and archive times."""
    if not is_tracked_channel(channel):
        return
    topic = channel.topic or ""
    category_id = channel.category.id
    status = {TEMP_ARCHIVE_CATEGORY_ID: "archived", MANUAL_SOAP_CATEGORY_ID: "manual"}.get(category_id, "open")
    delete_at = archived_at = None
    match = ARCHIVE_DELETION_RE.search(topic)
    if status == "archived" and match:
        from datetime import timedelta

        delete_at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        archived_at = delete_at - timedelta(days=ARCHIVE_DAYS)
    user_id = int(MENTION_RE.search(ARCHIVE_DELETION_RE.sub("", topic)).group(1))
    try:
        from helpee import case_notes

        notes = "\n".join(case_notes(channel)) or None
    except Exception:
        notes = None
    await db.execute(
        """INSERT INTO channels (channel_id, user_id, kind, name, status, case_notes, created_at, archived_at, delete_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), kind = VALUES(kind), name = VALUES(name),
               status = VALUES(status), case_notes = COALESCE(VALUES(case_notes), case_notes),
               archived_at = CASE WHEN VALUES(status) = 'archived'
                   THEN COALESCE(VALUES(archived_at), archived_at, UTC_TIMESTAMP()) ELSE NULL END,
               delete_at = CASE WHEN VALUES(status) = 'archived' THEN COALESCE(VALUES(delete_at), delete_at) ELSE NULL END,
               deleted_at = NULL""",
        (channel.id, user_id, channel_kind(channel), channel.name[:100], status, notes,
         utc(channel.created_at), archived_at, delete_at),
    )


@quiet
async def update_channel(channel: discord.TextChannel, **fields):
    """Set fields on a channel's row (saving the channel first if it isn't there yet)."""
    if not is_tracked_channel(channel) or not fields:
        return
    await sync_channel(channel)
    columns = ", ".join(f"{k} = %s" for k in fields)
    await db.execute(
        f"UPDATE channels SET {columns} WHERE channel_id = %s",
        tuple(utc(v) if isinstance(v, datetime) else v for v in fields.values()) + (channel.id,),
    )


@quiet
async def channel_deleted(channel_id: int):
    await db.execute(
        "UPDATE channels SET status = 'deleted', deleted_at = UTC_TIMESTAMP() WHERE channel_id = %s AND status <> 'deleted'",
        (channel_id,),
    )


@quiet
async def sync_all_channels(guilds: list[discord.Guild]):
    """Bring the channels table in line with Discord: save every SOAP/NNID channel and mark missing ones deleted.
    Takes every server the bot is in, so one server's channels aren't marked deleted while syncing another."""
    existing = set()
    for guild in guilds:
        for channel in guild.text_channels:
            existing.add(channel.id)
            if is_tracked_channel(channel):
                await sync_channel(channel)
    rows = await db.fetchall("SELECT channel_id FROM channels WHERE status <> 'deleted'") or []
    for row in rows:
        if row["channel_id"] not in existing:
            await channel_deleted(row["channel_id"])


async def unused_pair_code() -> str:
    """A 4 digit Bluehax Transporter pair code that no open channel, and nothing in the last week, is using.
    Two channels sharing one would send one helpee's essential.exefs to the other."""
    import random

    taken = set()
    if db.enabled():
        try:
            rows = await asyncio.wait_for(
                db.fetchall(
                    """SELECT pair_code FROM channels WHERE pair_code IS NOT NULL
                       AND (status IN ('open', 'manual') OR pair_code_at > UTC_TIMESTAMP() - INTERVAL 7 DAY)"""
                ),
                timeout=3,
            )
            taken = {r["pair_code"] for r in rows}
        except Exception as e:
            print(f"Could not check pair codes in use: {e!r}")
    free = [f"{n:04d}" for n in range(10000) if f"{n:04d}" not in taken]
    return random.choice(free or [f"{random.randint(0, 9999):04d}"])


# ---------- soap-queue ----------

@quiet
async def helpee_history(user_id: int, serial: str | None) -> dict | None:
    """What soap-queue shows about a helpee: past transfers, serial reuse and restrictions."""
    counts = await db.fetchone(
        """SELECT SUM(kind = 'SOAP') AS soaps, SUM(kind = 'NNID') AS nnids FROM transfers WHERE user_id = %s""",
        (user_id,),
    )
    last = await db.fetchone(
        """SELECT completed_at, serial, outcome, method, soaper_id FROM transfers
           WHERE user_id = %s AND kind = 'SOAP' ORDER BY completed_at DESC LIMIT 1""",
        (user_id,),
    )
    serial_rows = []
    if serial:
        serial_rows = await db.fetchall(
            """SELECT user_id, completed_at FROM transfers
               WHERE kind = 'SOAP' AND serial_base = %s ORDER BY completed_at DESC""",
            (serial_base(serial),),
        ) or []
    restrictions = await db.fetchone(
        """SELECT COUNT(*) AS n, SUM(ended_at IS NULL) AS active, MAX(created_at) AS last FROM restrictions WHERE user_id = %s""",
        (user_id,),
    )
    return {
        "soaps": int(counts["soaps"] or 0) if counts else 0,
        "nnids": int(counts["nnids"] or 0) if counts else 0,
        "last": last,
        "serial_mine": [r for r in serial_rows if r["user_id"] == user_id],
        "serial_others": [r for r in serial_rows if r["user_id"] != user_id],
        "restrictions": int(restrictions["n"] or 0) if restrictions else 0,
        "restricted_now": bool(restrictions and restrictions["active"]),
        "last_restricted": restrictions["last"] if restrictions else None,
    }


# ---------- refusing restricted users ----------

BLOCKED_FOOTER = "If you believe this is in error, please ask in #soap-help."

async def restriction_for(user) -> dict | None:
    """The user's restriction if they're restricted (by the role, or an active one in the database), else None."""
    import constants

    try:
        # A button has 3 seconds to answer, so don't wait long on the database; the role still counts
        active = await asyncio.wait_for(active_restriction(user.id), timeout=2)
    except asyncio.TimeoutError:
        active = None
    if active:
        return active
    role_id = getattr(constants, "RESTRICTED_ROLE_ID", None)
    role = user.guild.get_role(role_id) if role_id and isinstance(user, discord.Member) else None
    if role is not None and role in user.roles:
        return {"expires_at": None, "reason": None, "moderator_id": None, "created_at": None}
    return None


def restricted_text(kind: str, restriction: dict) -> str:
    """What the helpee is told when they're restricted. kind is SOAP or NNID."""
    if restriction.get("expires_at"):
        ends = int(restriction["expires_at"].replace(tzinfo=timezone.utc).timestamp())
        length = f"This restriction ends <t:{ends}:f> (<t:{ends}:R>)."
    else:
        length = "This restriction may be temporary or permanent, depending on the reason."
    return (
        f"You are unable to request a new {kind} transfer. {length}\n\n"
        f"You may still receive help with previously completed {kind} transfers in #soap-help."
    )


def restricted_embed(kind: str, restriction: dict) -> discord.Embed:
    embed = discord.Embed(
        title="⛔ Restricted from Bluehax Services",
        description=restricted_text(kind, restriction),
        color=discord.Color.red(),
    )
    embed.set_footer(text=BLOCKED_FOOTER)
    return embed


async def log_restricted_attempt(guild: discord.Guild, user, kind: str, how: str, restriction: dict, by=None):
    """Tell Staff a restricted user tried to get a SOAP/NNID channel opened."""
    import constants

    if by is None or by.id == user.id:
        await count_restricted_attempt(user.id, kind)  # Staff trying .createsoap for them doesn't count

    log_id = getattr(constants, "BAN_LOG_ID", None) or getattr(constants, "SOAP_LOG_ID", None)
    log = guild.get_channel(log_id) if log_id else None
    if log is None:
        return
    embed = discord.Embed(
        title=f"⛔ Restricted User Tried to Open a {kind} Channel",
        description=f"{user.mention} {user.name}",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="How", value=how, inline=False)
    if by is not None and by.id != user.id:
        embed.add_field(name="Tried by", value=f"{by.mention} {by.name}", inline=False)
    if restriction.get("expires_at"):
        ends = int(restriction["expires_at"].replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="Restriction", value=f"Temporary, ends <t:{ends}:f> (<t:{ends}:R>)", inline=False)
    else:
        embed.add_field(name="Restriction", value="Permanent", inline=False)
    if restriction.get("moderator_id"):
        embed.add_field(name="Restricted by", value=f"<@{restriction['moderator_id']}>", inline=True)
    if restriction.get("created_at"):
        since = int(restriction["created_at"].replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="Restricted", value=f"<t:{since}:f>", inline=True)
    if restriction.get("reason"):
        embed.add_field(name="Reason", value=restriction["reason"][:1024], inline=False)
    embed.set_footer(text=f"ID: {user.id}")
    try:
        await log.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        print(f"Could not log a restricted user's channel attempt: {e}")


# ---------- SOAP limits ----------

# Each limit is a rolling window, e.g. week is any 7 days in a row
LIMIT_PERIODS = {"day": 1, "week": 7, "month": 30, "year": 365}


@quiet
async def soap_limits() -> dict[str, int]:
    """The limits Staff have set, as period -> most SOAPs allowed in it."""
    rows = await db.fetchall("SELECT period, max_soaps FROM soap_limits")
    return {r["period"]: int(r["max_soaps"]) for r in rows}


@quiet
async def set_soap_limit(period: str, max_soaps: int | None, by_id: int | None) -> bool:
    """Set a limit, or remove it when max_soaps is None. period None with max_soaps None removes them all."""
    if max_soaps is None:
        if period is None:
            await db.execute("DELETE FROM soap_limits")
        else:
            await db.execute("DELETE FROM soap_limits WHERE period = %s", (period,))
    else:
        await db.execute(
            """INSERT INTO soap_limits (period, max_soaps, set_by) VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE max_soaps = VALUES(max_soaps), set_by = VALUES(set_by)""",
            (period, max_soaps, by_id),
        )
    return True


@quiet
async def _soap_limit_hit(user_id: int) -> dict | None:
    limits = await soap_limits()
    if not limits:
        return None
    current = now()
    longest = max(LIMIT_PERIODS[p] for p in limits)
    rows = await db.fetchall(
        """SELECT completed_at FROM transfers
           WHERE user_id = %s AND kind = 'SOAP' AND completed_at >= %s
           ORDER BY completed_at DESC""",
        (user_id, current - timedelta(days=longest)),
    )
    times = [r["completed_at"] for r in rows]  # newest first
    hit = None
    for period, most in limits.items():
        window = timedelta(days=LIMIT_PERIODS[period])
        inside = [t for t in times if t >= current - window]
        if len(inside) >= most:
            # Another is allowed once the oldest of the newest `most` SOAPs leaves the window
            until = inside[most - 1] + window
            if hit is None or until > hit["until"]:
                hit = {"period": period, "max": most, "count": len(inside), "until": until}
    return hit


async def soap_limit_for(user) -> dict | None:
    """The limit the user has hit, or None. Like restriction_for, it gives up after 2 seconds and lets them through."""
    try:
        return await asyncio.wait_for(_soap_limit_hit(user.id), timeout=2)
    except asyncio.TimeoutError:
        return None


def limit_text(limit: dict) -> str:
    most, period = limit["max"], limit["period"]
    until = int(limit["until"].replace(tzinfo=timezone.utc).timestamp())
    per = "30 days" if period == "month" else period
    return (
        f"You can only receive {most} SOAP transfer{'s' if most != 1 else ''} per {per}. "
        f"You can request another one <t:{until}:f> (<t:{until}:R>)."
    )


def limit_embed(limit: dict) -> discord.Embed:
    embed = discord.Embed(
        title="⏳ SOAP Limit Reached",
        description=limit_text(limit),
        color=discord.Color.orange(),
    )
    embed.set_footer(text=BLOCKED_FOOTER)
    return embed


async def log_limit_hit(guild: discord.Guild, user, how: str, limit: dict):
    """Tell Staff someone hit the SOAP limit."""
    import constants

    log_id = getattr(constants, "SOAP_LOG_ID", None)
    log = guild.get_channel(log_id) if log_id else None
    if log is None:
        return
    until = int(limit["until"].replace(tzinfo=timezone.utc).timestamp())
    per = "30 days" if limit["period"] == "month" else limit["period"]
    embed = discord.Embed(
        title="⏳ SOAP Limit Reached",
        description=f"{user.mention} {user.name}",
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="How", value=how, inline=False)
    embed.add_field(name="Limit", value=f"{limit['max']} per {per}", inline=True)
    embed.add_field(name="SOAPs in That Time", value=str(limit["count"]), inline=True)
    embed.add_field(name="Can Request Again", value=f"<t:{until}:f> (<t:{until}:R>)", inline=False)
    embed.set_footer(text=f"ID: {user.id}")
    try:
        await log.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        print(f"Could not log a SOAP limit hit: {e}")
