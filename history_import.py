"""Read the old log channels back into the database: past SOAPs, helpees, Soapers and restrictions.

Used by .importhistory, and can also run on its own:
    python history_import.py dump history.json   read the log channels over Discord's API into a file (uses KEY)
    python history_import.py load history.json   write a dumped file into the database in constants.DATABASE

Where things come from:
- #bot-logs (soap-cat): "doing soap for <Soaper> (<id>) [with Start SOAP] in <channel link> (<channel name>)",
  then "soap for <Soaper> ... succeeded", then "Debug info" with the helpee's user ID and whether it was a lottery.
  "soap result for ..." says which donor was used. maidy's "Created SOAP Channel" embeds give usernames.
- #skynet (bots only): "SOAP_STATUS <channel> SUCCESS|LOTTERY <serial>" gives the serial, and its message ID is
  the same one live recording uses, so nothing is counted twice. "SOAP_REQUEST" gives the helpee for a channel.
- #moderating-logs: "⛔ Member Restricted" and "✅ Restriction Removed" embeds.
Manual completions (.soapnormal) and NNID transfers were never logged, so they can't be imported.

Running it again only adds what's missing.
"""

import asyncio
import json
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

DOING_RE = re.compile(r"^doing soap for (?P<who>.+?) in https://discord(?:app)?\.com/channels/\d+/(?P<chan>\d+) \((?P<name>.*?)\)")
SUCCEEDED_RE = re.compile(r"^soap for (?P<who>.+?) succeeded\s*$")
RESULT_RE = re.compile(r"^soap result for (?P<who>.+?):\n", re.S)
DONOR_RE = re.compile(r"`([^`\n]{1,24})` is now on cooldown")
DEBUG_USER_RE = re.compile(r"user_id is (\d+)")
DEBUG_LOTTERY_RE = re.compile(r"lottery is (True|False)")
DEBUG_MEMBER_RE = re.compile(r"member_obj is (.+?)(?: \((.*)\))?$", re.M)
DEBUG_NAME_RE = re.compile(r"member_name is (.+)$", re.M)
WHO_ID_RE = re.compile(r"^(?P<name>.+?) \((?P<id>\d{15,25})\)")
STATUS_RE = re.compile(r"^SOAP_STATUS (\d+) (SUCCESS|LOTTERY)(?: ([A-Z0-9]+))?")
REQUEST_RE = re.compile(r"^SOAP_REQUEST (\d+) (\S+) \S+(?: (\d+)(?: (\d+))?)?")
NAME_ID_RE = re.compile(r"^(.+?) - (\d{15,25})$")
MENTION_RE = re.compile(r"<@!?(\d+)>")
CHANNEL_SUFFIX_RE = re.compile(r"-+(?:needs-cleaning|soap|nnid)\b|-*🧼|-*🔑")

STATUS_WINDOW = timedelta(minutes=5)  # SOAP_STATUS comes within seconds of soap-cat logging success
RESULT_WINDOW = timedelta(seconds=60)
MAX_SOAP_LENGTH = timedelta(hours=2)  # a start older than this when a soap succeeds was one that failed
DEBUG_WINDOW = timedelta(seconds=30)


# ---------- reading messages ----------

def message_dict(m) -> dict:
    """A discord.Message as the plain dict the parser works on."""
    return {
        "id": m.id,
        "at": m.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "author_id": m.author.id,
        "author_bot": m.author.bot,
        "content": m.content or "",
        "embeds": [
            {
                "title": e.title or "",
                "description": e.description or "",
                "author": getattr(e.author, "name", None) or "",
                "fields": [[f.name, f.value] for f in e.fields],
            }
            for e in m.embeds
        ],
    }


def rest_message_dict(m: dict) -> dict:
    """A message from Discord's REST API as the same plain dict."""
    return {
        "id": int(m["id"]),
        "at": m["timestamp"][:19],
        "author_id": int(m["author"]["id"]),
        "author_bot": bool(m["author"].get("bot")),
        "content": m.get("content") or "",
        "embeds": [
            {
                "title": e.get("title") or "",
                "description": e.get("description") or "",
                "author": (e.get("author") or {}).get("name") or "",
                "fields": [[f.get("name", ""), f.get("value", "")] for f in e.get("fields", [])],
            }
            for e in m.get("embeds", [])
        ],
    }


def _time(m: dict) -> datetime:
    return datetime.strptime(m["at"], "%Y-%m-%dT%H:%M:%S")


# ---------- parsing ----------

def parse(bot_logs: list[dict], skynet: list[dict], mod_log: list[dict]) -> dict:
    """Turn the log channels (each oldest first) into transfers, names, channels and restrictions."""
    names: dict[int, dict] = {}  # user ID -> {"username", "display_name"}
    soaper_names: dict[int, str] = {}
    stats = defaultdict(int)

    def remember(uid: int, username: str | None = None, display: str | None = None):
        entry = names.setdefault(uid, {"username": None, "display_name": None})
        if username:
            entry["username"] = username
        if display:
            entry["display_name"] = display

    # skynet: serials by channel, and the helpee each SOAP_REQUEST was for
    statuses = defaultdict(list)  # channel ID -> [(time, outcome, serial, message ID)]
    request_users: dict[int, int] = {}  # channel ID -> helpee ID
    skynet_results = []  # (time, donor) from soap results posted in skynet
    for m in skynet:
        content = m["content"]
        if match := STATUS_RE.match(content):
            statuses[int(match.group(1))].append(
                (_time(m), "lottery" if match.group(2) == "LOTTERY" else "transfer", match.group(3), m["id"])
            )
        elif match := REQUEST_RE.match(content):
            if match.group(3):
                request_users[int(match.group(3))] = int(match.group(1))
        elif "is now on cooldown" in content and (donor := DONOR_RE.search(content)):
            skynet_results.append((_time(m), donor.group(1)))

    # bot-logs: soap-cat's soaps, in order
    def status_near(ids, at):
        """The SOAP_STATUS result sent right after a soap succeeded. Older soap-cat sent the helpee's user ID
        instead of the channel ID, so either can match."""
        for key in ids:
            for status in statuses.get(key, []) if key else []:
                if at - timedelta(seconds=5) <= status[0] <= at + STATUS_WINDOW:
                    return status
        return None

    pending = defaultdict(list)  # who -> soaps started and not finished yet
    awaiting_debug: list[dict] = []  # succeeded soaps waiting for their Debug info
    soaps: list[dict] = []
    for m in bot_logs:
        content = m["content"]
        for e in m["embeds"]:
            for field_name, value in e["fields"]:
                if field_name.startswith("Action made by") and (match := NAME_ID_RE.match(value.strip())):
                    if e["title"].startswith("Created"):
                        remember(int(match.group(2)), match.group(1))
        if not content:
            continue
        if match := DOING_RE.match(content):
            who = match.group("who").removesuffix(" with maidy credentials")
            pending[who].append({"channel_id": int(match.group("chan")), "name": match.group("name"), "who": who, "start": m})
            stats["started"] += 1
        elif match := SUCCEEDED_RE.match(content):
            who, at = match.group("who"), _time(m)
            # Soaps that failed never say succeeded, so drop starts that are too old to be this one
            starts = [p for p in pending[who] if at - _time(p["start"]) <= MAX_SOAP_LENGTH]
            stats["started but never succeeded"] += len(pending[who]) - len(starts)
            if starts:
                # The one soap-cat sent a result for, or else the most recent
                soap = next((p for p in reversed(starts) if status_near([p["channel_id"]], at)), starts[-1])
                starts.remove(soap)
            else:
                soap = {"channel_id": None, "name": "", "who": who}
                stats["succeeded without a start"] += 1
            pending[who] = starts
            soap["done"] = m
            awaiting_debug.append(soap)
        elif match := RESULT_RE.match(content):
            donor = DONOR_RE.search(content)
            for soap in reversed(awaiting_debug):
                if soap["who"] == match.group("who"):
                    soap["donor"] = donor.group(1) if donor else None
                    break
        elif content.startswith("Debug info:"):
            user = DEBUG_USER_RE.search(content)
            member_name = (DEBUG_NAME_RE.search(content) or [None, None])[1]
            at = _time(m)
            # Debug info comes a second after its soap succeeded; anything older lost its Debug info
            awaiting_debug[:] = [a for a in awaiting_debug if at - _time(a["done"]) <= DEBUG_WINDOW]
            if not awaiting_debug:
                stats["debug info without a soap"] += 1
                continue
            # Normally the latest success, but when soaps overlap the channel name gives it away
            soap = awaiting_debug[-1]
            if member_name and member_name != "None":
                prefix = member_name.lower().lstrip(".").rstrip(".").replace(".", "-")
                for candidate in reversed(awaiting_debug):
                    if candidate["name"].startswith(prefix):
                        soap = candidate
                        break
            awaiting_debug.remove(soap)
            lottery = DEBUG_LOTTERY_RE.search(content)
            soap["lottery"] = bool(lottery and lottery.group(1) == "True")
            if user:
                soap["user_id"] = int(user.group(1))
                obj = DEBUG_MEMBER_RE.search(content)
                if obj and obj.group(1) != "None":
                    remember(soap["user_id"], obj.group(1), obj.group(2))
            soaps.append(soap)

    # Usernames as they appear at the start of channel names, for soaps whose Debug info had no user ID
    by_channel_name = {}
    for uid, n in names.items():
        if n.get("username"):
            key = n["username"].lower().lstrip(".").rstrip(".").replace(".", "-")
            by_channel_name[key] = None if key in by_channel_name and by_channel_name[key] != uid else uid

    def from_channel_name(name: str) -> int | None:
        base = CHANNEL_SUFFIX_RE.split(name or "", maxsplit=1)[0]
        return by_channel_name.get(base) if base else None

    transfers, channels = [], {}
    for soap in soaps:
        done_at = _time(soap["done"])
        channel_id = soap.get("channel_id")
        user_id = soap.get("user_id") or (request_users.get(channel_id) if channel_id else None)
        if not user_id and (user_id := from_channel_name(soap["name"])):
            stats["helpee found from the channel name"] += 1
        if not user_id:
            stats["skipped, helpee unknown"] += 1
            continue
        soaper_id = None
        if (who := WHO_ID_RE.match(soap["who"])) and not soap["who"].startswith("user "):
            soaper_id = int(who.group("id"))
            soaper_names[soaper_id] = who.group("name")
        outcome = "lottery" if soap.get("lottery") else "transfer"
        serial, message_id = None, soap["done"]["id"]
        status = status_near([channel_id, user_id], done_at)
        if status:
            _, outcome, serial, message_id = status
        else:
            stats["no SOAP_STATUS (no serial)"] += 1
        donor = soap.get("donor")
        if donor is None and outcome == "transfer":
            for at, name in skynet_results:
                if abs((at - done_at).total_seconds()) <= RESULT_WINDOW.total_seconds():
                    donor = name
                    break
        transfers.append({
            "user_id": user_id, "kind": "SOAP", "outcome": outcome, "method": "automatic",
            "serial": serial if serial and serial != "SKIP" else None, "donor": donor if outcome == "transfer" else None,
            "soaper_id": soaper_id, "channel_id": channel_id, "completed_at": soap["done"]["at"], "message_id": message_id,
        })
        if channel_id:
            channels[channel_id] = {
                "channel_id": channel_id, "user_id": user_id, "name": soap["name"], "serial": serial,
                "soaper_id": soaper_id, "donor": donor, "completed_at": soap["done"]["at"],
            }

    # moderating-logs: restrictions
    restrictions = []
    for m in mod_log:
        for e in m["embeds"]:
            if e["title"] not in ("⛔ Member Restricted", "✅ Restriction Removed"):
                continue
            user = None
            reason = None
            for field_name, value in e["fields"]:
                if not user and (match := MENTION_RE.search(value)):
                    user = int(match.group(1))
                if field_name == "Reason":
                    reason = value
            if user:
                restrictions.append({
                    "action": "restrict" if e["title"].startswith("⛔") else "unrestrict",
                    "user_id": user, "moderator": (e["author"] or "").split(" (")[0] or None,
                    "reason": reason, "at": m["at"], "message_id": m["id"],
                })

    stats["soaps found"] = len(transfers)
    return {
        "transfers": transfers,
        "channels": list(channels.values()),
        "restrictions": restrictions,
        "names": {str(k): v for k, v in names.items()},
        "soaper_names": {str(k): v for k, v in soaper_names.items()},
        "stats": dict(stats),
    }


# ---------- writing to the database ----------

async def load(data: dict, guild=None, bot=None) -> dict:
    """Write parsed history into the database. Returns counts of what was added."""
    import db
    import records

    await db.ensure_schema()
    names = {int(k): v for k, v in data["names"].items()}
    soaper_names = {int(k): v for k, v in data["soaper_names"].items()}
    transfers = data["transfers"]

    def member(uid):
        return guild.get_member(uid) if guild else None

    # Users: current name and roles if they're still here, otherwise the name from the logs
    user_ids = {t["user_id"] for t in transfers} | {r["user_id"] for r in data["restrictions"]}
    rows = []
    for uid in user_ids:
        mem = member(uid)
        if mem:
            rows.append((uid, mem.name, mem.display_name, records.role_names(mem), 1))
        else:
            n = names.get(uid) or {}
            rows.append((uid, n.get("username") or f"unknown-{uid}", n.get("display_name"), None, 0 if guild else 1))
    await db.executemany(
        """INSERT INTO users (user_id, username, display_name, roles, in_server) VALUES (%s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE username = IF(VALUES(username) LIKE 'unknown-%%', username, VALUES(username)),
               display_name = COALESCE(VALUES(display_name), display_name), roles = COALESCE(VALUES(roles), roles),
               in_server = VALUES(in_server)""",
        rows,
    )

    # Soapers
    rows = []
    for sid in {t["soaper_id"] for t in transfers if t["soaper_id"]}:
        mem = member(sid)
        if mem:
            rows.append((sid, mem.name, mem.display_name, int(records.is_soaper(mem))))
        else:
            rows.append((sid, soaper_names.get(sid) or f"unknown-{sid}", soaper_names.get(sid), 0))
    await db.executemany(
        """INSERT INTO soapers (user_id, username, display_name, is_soaper) VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE display_name = COALESCE(VALUES(display_name), display_name),
               is_soaper = GREATEST(is_soaper, VALUES(is_soaper))""",
        rows,
    )

    # Transfers (INSERT IGNORE on the message ID, so running it again adds nothing twice). A soap the bot already
    # recorded live has a different message ID when its SOAP_STATUS couldn't be matched, so skip those by channel and time.
    live = defaultdict(list)
    for row in await db.fetchall("SELECT channel_id, completed_at FROM transfers WHERE source = 'live' AND channel_id IS NOT NULL"):
        live[row["channel_id"]].append(row["completed_at"])

    def recorded_live(t):
        at = datetime.strptime(t["completed_at"], "%Y-%m-%dT%H:%M:%S")
        return any(abs((at - other).total_seconds()) < 900 for other in live.get(t["channel_id"], []))

    before = (await db.fetchone("SELECT COUNT(*) AS n FROM transfers"))["n"]
    await db.executemany(
        """INSERT IGNORE INTO transfers
               (user_id, kind, outcome, method, serial, serial_base, donor, soaper_id, channel_id, completed_at, source, message_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'import', %s)""",
        [
            (t["user_id"], t["kind"], t["outcome"], t["method"], t["serial"], records.serial_base(t["serial"]), t["donor"],
             t["soaper_id"], t["channel_id"], t["completed_at"].replace("T", " "), t["message_id"])
            for t in transfers
            if not recorded_live(t)
        ],
    )
    added = (await db.fetchone("SELECT COUNT(*) AS n FROM transfers"))["n"] - before

    # Old channels, as deleted ones (channels that still exist are already tracked and left alone)
    from discord.utils import snowflake_time

    await db.executemany(
        """INSERT IGNORE INTO channels
               (channel_id, user_id, kind, name, status, serial, soaper_id, donor, completed_at, created_at, deleted_at)
           VALUES (%s, %s, 'SOAP', %s, 'deleted', %s, %s, %s, %s, %s, %s)""",
        [
            (c["channel_id"], c["user_id"], (c["name"] or str(c["channel_id"]))[:100], c["serial"], c["soaper_id"],
             c["donor"], c["completed_at"].replace("T", " "),
             snowflake_time(c["channel_id"]).replace(tzinfo=None), None)
            for c in data["channels"]
            if not (guild and guild.get_channel(c["channel_id"]))
        ],
    )

    # Restrictions, in order, so a removal ends the restriction before it
    restricted = 0
    for r in sorted(data["restrictions"], key=lambda r: r["at"]):
        mod = None
        if guild and r["moderator"]:
            found = guild.get_member_named(r["moderator"])
            mod = found.id if found else None
        at = r["at"].replace("T", " ")
        if r["action"] == "restrict":
            exists = await db.fetchone("SELECT 1 FROM restrictions WHERE message_id = %s", (r["message_id"],))
            if not exists:
                await db.execute(
                    "UPDATE restrictions SET ended_at = %s, end_reason = 'replaced' WHERE user_id = %s AND ended_at IS NULL",
                    (at, r["user_id"]),
                )
                await db.execute(
                    """INSERT INTO restrictions (user_id, moderator_id, reason, created_at, source, message_id)
                       VALUES (%s, %s, %s, %s, 'import', %s)""",
                    (r["user_id"], mod, r["reason"], at, r["message_id"]),
                )
                restricted += 1
        else:
            await db.execute(
                """UPDATE restrictions SET ended_at = %s, ended_by = %s, end_reason = 'unrestricted'
                   WHERE user_id = %s AND ended_at IS NULL AND created_at <= %s""",
                (at, mod, r["user_id"], at),
            )
    for uid in {r["user_id"] for r in data["restrictions"]}:
        await records.sync_user_restriction(uid)

    # Counts for everyone at once
    await db.execute(
        """UPDATE users u LEFT JOIN (
               SELECT user_id, SUM(kind = 'SOAP') AS s, SUM(kind = 'NNID') AS n, MIN(completed_at) AS f, MAX(completed_at) AS l
               FROM transfers GROUP BY user_id) t ON t.user_id = u.user_id
           SET u.soap_count = COALESCE(t.s, 0), u.nnid_count = COALESCE(t.n, 0), u.first_transfer_at = t.f, u.last_transfer_at = t.l"""
    )
    await db.execute(
        """UPDATE soapers s LEFT JOIN (
               SELECT soaper_id, SUM(kind = 'SOAP') AS c, SUM(kind = 'NNID') AS n, MIN(completed_at) AS f, MAX(completed_at) AS l
               FROM transfers WHERE soaper_id IS NOT NULL GROUP BY soaper_id) t ON t.soaper_id = s.user_id
           SET s.soap_count = COALESCE(t.c, 0), s.nnid_count = COALESCE(t.n, 0), s.first_soap_at = t.f, s.last_soap_at = t.l"""
    )
    return {"transfers added": added, "transfers found": len(transfers), "users": len(user_ids),
            "restrictions added": restricted}


# ---------- in the bot ----------

async def _read(channel, progress=None) -> list[dict]:
    out = []
    async for m in channel.history(limit=None, oldest_first=True):
        out.append(message_dict(m))
        if progress and len(out) % 5000 == 0:
            await progress(f"Read {len(out):,} messages from #{channel.name}...")
    return out


async def run_import(bot, guild, progress=None):
    """Read the log channels in this server and load them. Returns a summary embed."""
    import discord
    import constants

    def channel(name):
        cid = getattr(constants, name, None)
        return guild.get_channel(cid) if cid else None

    bot_logs, skynet, mod_log = channel("SOAP_LOG_ID"), channel("BOTS_ONLY_CHANNEL_ID"), channel("BAN_LOG_ID")
    data = parse(
        await _read(bot_logs, progress) if bot_logs else [],
        await _read(skynet, progress) if skynet else [],
        await _read(mod_log, progress) if mod_log else [],
    )
    result = await load(data, guild, bot)
    tracking = bot.get_cog("TrackingCog")
    if tracking:
        await tracking._sync_restrictions(guild)
    embed = discord.Embed(title="📥 History Imported", color=discord.Color.green())
    embed.add_field(name="SOAPs found", value=f"{result['transfers found']:,}", inline=True)
    embed.add_field(name="New SOAPs added", value=f"{result['transfers added']:,}", inline=True)
    embed.add_field(name="Helpees", value=f"{result['users']:,}", inline=True)
    embed.add_field(name="Restrictions added", value=str(result["restrictions added"]), inline=True)
    skipped = {k: v for k, v in data["stats"].items() if k not in ("started", "soaps found")}
    if skipped:
        embed.add_field(name="Couldn't use", value="\n".join(f"{v:,} {k}" for k, v in skipped.items()), inline=False)
    embed.set_footer(text="Manual completions and NNID transfers were never logged, so they aren't included.")
    return embed


# ---------- on its own ----------

def _dump(path: str):
    """Read the log channels over Discord's REST API (no second gateway login) and save the parsed result."""
    import time
    import urllib.error
    import urllib.request

    import constants

    headers = {"Authorization": f"Bot {constants.KEY}", "User-Agent": "DiscordBot (maid-cat history import, 1.0)"}

    def get(url):
        while True:
            try:
                with urllib.request.urlopen(urllib.request.Request("https://discord.com/api/v10" + url, headers=headers)) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(float(json.load(e).get("retry_after", 1)) + 0.25)
                    continue
                raise

    def read(cid):
        out, after = [], "0"
        while True:
            batch = get(f"/channels/{cid}/messages?limit=100&after={after}")
            if not batch:
                break
            batch.sort(key=lambda m: int(m["id"]))
            out += [rest_message_dict(m) for m in batch]
            after = batch[-1]["id"]
            if len(out) % 10000 < 100:
                print(f"  {cid}: {len(out):,} messages", flush=True)
        return out

    raw = {
        "bot_logs": [m for m in read(constants.SOAP_LOG_ID) if relevant(m)],
        "skynet": [m for m in read(constants.BOTS_ONLY_CHANNEL_ID) if relevant(m)],
        "mod_log": read(constants.BAN_LOG_ID),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    print(json.dumps(parse(raw["bot_logs"], raw["skynet"], raw["mod_log"])["stats"], indent=2))


def relevant(m: dict) -> bool:
    """Only the messages parse() reads, so a dump stays small."""
    c = m["content"]
    if m["embeds"]:
        return any(e["title"].startswith("Created") for e in m["embeds"])
    if c.startswith("SOAP_STATUS"):
        return " PROGRESS " not in c and " ERROR " not in c
    return c.startswith(("doing soap for", "soap for", "soap result for", "Debug info:", "SOAP_REQUEST")) or "is now on cooldown" in c


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "dump":
        _dump(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] in ("load", "parse"):
        with open(sys.argv[2], encoding="utf-8") as f:
            raw = json.load(f)
        data = parse(raw["bot_logs"], raw["skynet"], raw["mod_log"])
        print(json.dumps(data["stats"], indent=2))
        if sys.argv[1] == "load":
            print(asyncio.run(load(data)))
    else:
        print(__doc__)
