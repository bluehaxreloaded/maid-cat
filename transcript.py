"""HTML transcripts of archived SOAP/NNID channels, posted with a summary embed right before the channel is deleted."""

import asyncio
import base64
import html
import io
import re
from collections import Counter
from datetime import datetime, timezone

import discord

import constants
from constants import SOAP_CHANNEL_SUFFIX, NNID_CHANNEL_SUFFIX, SOAPER_ROLE_ID, ERROR_LOG_ID
from helpee import case_notes, REOPENED_TITLE
from perms import _has_role_or_higher

# Optional: leave ARCHIVE_TRANSCRIPT_CHANNEL_ID unset in constants.py to skip transcripts
TRANSCRIPT_CHANNEL_ID = getattr(constants, "ARCHIVE_TRANSCRIPT_CHANNEL_ID", None)

try:
    from PIL import Image, ImageOps
except ImportError:  # without Pillow, only images that are already small get saved into the page
    Image = None

MAX_FILE_BYTES = 9_500_000  # Discord's upload limit for bots is 10 MB
MAX_IMAGE_DOWNLOAD = 25_000_000  # bigger attachments are linked instead of saved into the page
# Images are saved into the page, since attachment links expire. Each (longest side, WebP quality)
# is tried in turn until the page fits under the upload limit.
IMAGE_LEVELS = [(1600, 75), (1024, 60), (640, 45), (320, 35)]
UNCOMPRESSED_MAX = 1_000_000  # without Pillow, images up to this size are saved as they are

DONE_TITLES = {"🎉 SOAP Transfer Complete", "🔄 NNID Transfer Complete"}
DESTRUCT_TEXT = "Self-destruct sequence initiated!"
INACTIVITY_TITLE = "💤 Closed Due to Inactivity"
ERROR_CODE_TITLE = re.compile(r"^(\d{3}-\d{4})\b")
# Bot embeds that point to something going wrong, mapped to how they show up under Issues
ISSUE_TITLES = {
    "🛑 Something went wrong...": "SOAP failed",
    "⚠️ Serial Number Mismatch": "Serial number didn't match the essential.exefs",
    "🆘 Assistance Requested": "Helpee asked for help",
    "⚠️ Moving to Manual": "Moved to manual",
    "🆘 Unknown Error Code": "Helpee entered an unknown error code",
    "Unknown Error Code": "Helpee entered an unknown error code",
    INACTIVITY_TITLE: "Closed due to inactivity",
    REOPENED_TITLE: "Reopened after the helpee rejoined",
}


def _ts(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt else None


def _channel_kind(name: str) -> str:
    base = re.sub(r"-cya$", "", name)
    if base.endswith(SOAP_CHANNEL_SUFFIX):
        return "SOAP"
    if base.endswith(NNID_CHANNEL_SUFFIX):
        return "NNID"
    return "Channel"


def _helpee_id(topic: str) -> int | None:
    m = re.search(r"<@!?(\d+)>", topic or "")
    return int(m.group(1)) if m else None


def _clip_lines(lines: list[str], limit: int = 1024, empty: str = "None") -> str:
    """Join lines into one embed field value, cutting off with 'and N more' if it's too long."""
    if not lines:
        return empty
    out = []
    for i, line in enumerate(lines):
        more = f"...and {len(lines) - i} more"
        if len("\n".join(out + [line])) + len(more) + 1 > limit:
            out.append(more)
            break
        out.append(line)
    return "\n".join(out)


class _Summary:
    """What happened in a channel, read from its messages."""

    def __init__(self, bot, channel: discord.TextChannel, messages: list[discord.Message]):
        guild = channel.guild
        bot_id = bot.user.id
        by_id = {m.id: m for m in messages}
        self.helpee_id = _helpee_id(channel.topic or "")
        self.kind = _channel_kind(channel.name)
        self.completions = []  # (message, label)
        self.issues: dict[str, list] = {}  # text -> [count, first message id or None]
        self.flagged: dict[int, str] = {}  # message id -> "done" / "issue"
        self.closed_by = None
        self.closed_at = None

        soaper_role = guild.get_role(SOAPER_ROLE_ID) if SOAPER_ROLE_ID else None
        speakers = Counter()
        self.people = {}
        for m in messages:
            if m.author.bot or m.is_system():
                continue
            speakers[m.author.id] += 1
            self.people[m.author.id] = m.author
        self.soapers, self.others = [], []
        for uid, count in speakers.most_common():
            if uid == self.helpee_id:
                continue
            member = guild.get_member(uid)
            if member and soaper_role and _has_role_or_higher(member, soaper_role):
                self.soapers.append((self.people[uid], count))
            else:
                self.others.append((self.people[uid], count))
        self.soaper_ids = {u.id for u, _ in self.soapers}
        self.helpee_messages = speakers.get(self.helpee_id, 0)

        for note in case_notes(channel):
            self._issue(note, None)

        for i, m in enumerate(messages):
            if m.author.id != bot_id:
                continue
            for e in m.embeds:
                title = (e.title or "").strip()
                if title in DONE_TITLES:
                    self.completions.append((m, self._completion_label(m, e, by_id)))
                    self.flagged[m.id] = "done"
                elif title in ISSUE_TITLES:
                    text = ISSUE_TITLES[title]
                    footer = getattr(e.footer, "text", None) or ""
                    if title.startswith("🛑") and footer.startswith("Error code:"):
                        text += f" ({footer.removeprefix('Error code:').strip()})"
                    self._issue(text, m.id)
                    self.flagged[m.id] = "issue"
                elif ERROR_CODE_TITLE.match(title):
                    self._issue(f"Helpee hit error {ERROR_CODE_TITLE.match(title).group(1)}", m.id)
                    self.flagged[m.id] = "issue"
            if m.content == DESTRUCT_TEXT:
                self.closed_at = m.created_at
                self.closed_by = None
                # The command that closed it comes right before, unless it was a slash command or button
                for prev in reversed(messages[:i]):
                    if (m.created_at - prev.created_at).total_seconds() > 60:
                        break
                    if not prev.author.bot and prev.content.startswith("."):
                        self.closed_by = prev.author
                        break
            if any((e.title or "") == INACTIVITY_TITLE for e in m.embeds):
                self.closed_by = "inactivity"

    def _issue(self, text: str, message_id: int | None):
        if text in self.issues:
            self.issues[text][0] += 1
        else:
            self.issues[text] = [1, message_id]

    @staticmethod
    def _completion_label(m: discord.Message, e: discord.Embed, by_id: dict) -> str:
        lottery = "Lottery" in (e.description or "")
        who = None
        meta = getattr(m, "interaction_metadata", None)
        if meta and getattr(meta, "user", None):
            who = meta.user
        elif m.reference and m.reference.message_id in by_id:
            who = by_id[m.reference.message_id].author
        if (e.title or "").startswith("🔄"):
            kind = "NNID transfer"
        else:
            kind = "Lottery" if lottery else "Transfer"
        how = f"manual, by {who.name}" if who else "automatic"
        return f"{kind} ({how})"


# ---------- Discord markdown to HTML ----------

class _Resolver:
    def __init__(self, bot, guild: discord.Guild, messages: list[discord.Message]):
        self.bot = bot
        self.guild = guild
        self.users = {}
        for m in messages:
            self.users[m.author.id] = m.author
            for u in m.mentions:
                self.users[u.id] = u

    def user(self, uid: int) -> str:
        u = self.users.get(uid) or self.guild.get_member(uid) or self.bot.get_user(uid)
        return f"@{u.display_name}" if u else f"@unknown-user ({uid})"

    def role(self, rid: int) -> tuple[str, str | None]:
        r = self.guild.get_role(rid)
        if not r:
            return "@deleted-role", None
        color = str(r.color) if r.color.value else None
        return f"@{r.name}", color

    def channel(self, cid: int) -> str:
        c = self.guild.get_channel(cid)
        return f"#{c.name}" if c else "#deleted-channel"


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _time_span(unix: int, style: str = "f") -> str:
    dt = datetime.fromtimestamp(unix, tz=timezone.utc)
    return f'<span class="ts" data-ts="{unix}" data-f="{style}">{dt.strftime("%Y-%m-%d %H:%M UTC")}</span>'


URL_RE = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,:;!?)\]]")


def render_markdown(text: str, res: _Resolver) -> str:
    """A safe subset of Discord markdown. Everything is escaped before any HTML is added."""
    text = (text or "").replace("\x00", "")
    stash, blocks = [], set()

    def keep(fragment: str, block: bool = False) -> str:
        stash.append(fragment)
        if block:
            blocks.add(len(stash) - 1)
        return f"\x00{len(stash) - 1}\x00"

    def code_block(m):
        body = m.group(2)
        body = body[1:] if body.startswith("\n") else body
        return keep(f"<pre><code>{_esc(body.rstrip())}</code></pre>", block=True)

    text = re.sub(r"```(?:([\w+#.-]+)(?=\n))?(.*?)```", code_block, text, flags=re.S)
    text = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{_esc(m.group(1))}</code>"), text)

    def emoji(m):
        ext = "gif" if m.group(1) else "png"
        return keep(
            f'<img class="emoji" alt=":{_esc(m.group(2))}:" title=":{_esc(m.group(2))}:" '
            f'src="https://cdn.discordapp.com/emojis/{m.group(3)}.{ext}?size=48">'
        )

    def role(m):
        name, color = res.role(int(m.group(1)))
        style = f' style="color:{color};background:{color}22"' if color else ""
        return keep(f'<span class="mention"{style}>{_esc(name)}</span>')

    def timestamp(m):
        return keep(_time_span(int(m.group(1)), m.group(2) or "f"))

    text = re.sub(r"<(a?):(\w+):(\d+)>", emoji, text)
    text = re.sub(r"<@&(\d+)>", role, text)
    text = re.sub(r"<@!?(\d+)>", lambda m: keep(f'<span class="mention">{_esc(res.user(int(m.group(1))))}</span>'), text)
    text = re.sub(r"<#(\d+)>", lambda m: keep(f'<span class="mention">{_esc(res.channel(int(m.group(1))))}</span>'), text)
    text = re.sub(r"<t:(-?\d+)(?::([tTdDfFR]))?>", timestamp, text)
    text = re.sub(r"<(https?://[^\s>]+)>", lambda m: keep(f'<a href="{_esc(m.group(1))}" target="_blank" rel="noopener">{_esc(m.group(1))}</a>'), text)
    text = re.sub(
        r"\[([^\]\n]+)\]\(<?(https?://[^\s)>]+)>?\)",
        lambda m: keep(f'<a href="{_esc(m.group(2))}" target="_blank" rel="noopener">{_inline(_esc(m.group(1)))}</a>'),
        text,
    )
    text = URL_RE.sub(lambda m: keep(f'<a href="{_esc(m.group(0))}" target="_blank" rel="noopener">{_esc(m.group(0))}</a>'), text)

    text = _inline(_esc(text))

    out = []
    lines = text.split("\n")
    for n, line in enumerate(lines):
        is_block = True
        if line.startswith("-# "):
            frag = f'<span class="subtext">{line[3:]}</span>'
        elif m := re.match(r"(#{1,3}) (.+)", line):
            level = len(m.group(1))
            frag = f'<span class="h h{level}">{m.group(2)}</span>'
        elif line.startswith("&gt; "):
            frag = f"<span class=\"quote\">{line[5:]}</span>"
        elif re.match(r"\s*[-*] ", line):
            indent = len(line) - len(line.lstrip())
            frag = f'<span class="li" style="margin-left:{8 + indent * 8}px">{line.lstrip()[2:]}</span>'
        else:
            frag = line
            stripped = line.strip()
            is_block = bool(re.fullmatch(r"\x00(\d+)\x00", stripped)) and int(stripped.strip("\x00")) in blocks
        out.append(frag)
        if n < len(lines) - 1 and not is_block:
            out.append("\n")
    text = "".join(out)

    for _ in range(3):  # stashed links can hold stashed fragments
        if "\x00" not in text:
            break
        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text


def _inline(s: str) -> str:
    s = re.sub(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", r"<strong><em>\1</em></strong>", s)
    s = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__(?!\s)(.+?)(?<!\s)__", r"<u>\1</u>", s)
    s = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])", r"<em>\1</em>", s)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"\|\|(.+?)\|\|", r'<span class="spoiler" onclick="this.classList.add(\'shown\')">\1</span>', s)
    return s


# ---------- HTML page ----------

CSS = """
:root{--bg:#313338;--bg2:#2b2d31;--bg3:#1e1f22;--text:#dbdee1;--muted:#949ba4;--head:#f2f3f5;--link:#00a8fc;
--mention:#5865f233;--mention-text:#c9cdfb;--hover:#2e3035;--line:#3f4147;--code:#2b2d31;--done:#23a55a;--issue:#f0b232;--embed:#2b2d31}
@media (prefers-color-scheme: light){:root{--bg:#ffffff;--bg2:#f2f3f5;--bg3:#e3e5e8;--text:#313338;--muted:#5c5e66;
--head:#060607;--link:#006ce7;--mention:#5865f226;--mention-text:#4752c4;--hover:#f7f7f8;--line:#e1e2e4;--code:#f2f3f5;--embed:#f2f3f5}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.375 "gg sans","Noto Sans","Helvetica Neue",Helvetica,Arial,sans-serif}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--bg2);border-bottom:1px solid var(--line);padding:24px 16px}
.wrap{max-width:1000px;margin:0 auto}
h1{margin:0 0 4px;font-size:22px;color:var(--head)}
.sub{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:18px}
.card{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 14px;min-width:0}
.card h2{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.card .big{font-size:16px;color:var(--head);font-weight:600;overflow-wrap:anywhere}
.card ul{margin:0;padding-left:18px}.card li{margin:2px 0;overflow-wrap:anywhere}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600}
.pill.done{background:#23a55a33;color:var(--done)}.pill.no{background:#f0b23233;color:var(--issue)}
main{padding:8px 0 40px}
.msg{display:flex;gap:16px;padding:2px 16px 2px 16px;position:relative}
.msg.first{margin-top:14px;padding-top:4px}
.msg:hover{background:var(--hover)}
.msg.flag-done{box-shadow:inset 3px 0 var(--done)}.msg.flag-issue{box-shadow:inset 3px 0 var(--issue)}
.msg:target{background:#faa61a22}
.gutter{width:40px;flex:none;text-align:right}
.avatar{width:40px;height:40px;border-radius:50%;background:var(--bg3)}
.gutter .t{visibility:hidden;font-size:11px;color:var(--muted);line-height:22px}
.msg:hover .gutter .t{visibility:visible}
.body{min-width:0;flex:1}
.name{font-weight:600;color:var(--head)}
.tag{font-size:10px;font-weight:700;padding:1px 5px;border-radius:4px;margin-left:6px;vertical-align:2px;color:#fff;background:#5865f2}
.tag.soaper{background:#23a55a}.tag.helpee{background:#e67e22}
.meta{color:var(--muted);font-size:12px;margin-left:6px}
.content{white-space:pre-wrap;overflow-wrap:anywhere}
.edited{color:var(--muted);font-size:10px;margin-left:4px}
.reply{font-size:13px;color:var(--muted);margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.reply b{color:var(--text);font-weight:600}
.system{color:var(--muted);font-style:italic}
code{background:var(--code);border-radius:4px;padding:1px 4px;font-size:85%;font-family:Consolas,"Andale Mono",monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:4px;padding:8px;margin:4px 0;white-space:pre-wrap;overflow-wrap:anywhere}
pre code{background:none;padding:0}
.mention{background:var(--mention);color:var(--mention-text);border-radius:3px;padding:0 2px;font-weight:500}
.emoji{width:22px;height:22px;vertical-align:-5px;object-fit:contain}
.h{display:block;font-weight:700;color:var(--head);margin:6px 0 2px}.h1{font-size:22px}.h2{font-size:19px}.h3{font-size:16px}
.quote{display:block;border-left:4px solid var(--line);padding-left:10px}
.subtext{display:block;font-size:12px;color:var(--muted)}
.li{display:block}.li::before{content:"• ";}
.spoiler{background:#1e1f22;color:transparent;border-radius:3px;cursor:pointer}.spoiler.shown{background:var(--mention);color:inherit}
.ts{background:var(--mention);border-radius:3px;padding:0 2px}
.embed{display:flex;max-width:520px;margin-top:4px;background:var(--embed);border-radius:4px;border-left:4px solid var(--line);overflow:hidden}
.embed .in{padding:8px 16px 12px 12px;min-width:0;flex:1}
.embed .author{font-size:13px;font-weight:600;margin-top:4px;color:var(--head);display:flex;gap:6px;align-items:center}
.embed .author img{width:22px;height:22px;border-radius:50%}
.embed .title{font-weight:600;color:var(--head);margin-top:6px}
.embed .desc{font-size:14px;margin-top:6px;white-space:pre-wrap;overflow-wrap:anywhere}
.embed .fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:8px}
.embed .field{grid-column:1/-1;font-size:14px}.embed .field.inline{grid-column:auto}
.embed .field .fn{font-weight:600;color:var(--head);margin-bottom:2px}
.embed .field .fv{white-space:pre-wrap;overflow-wrap:anywhere}
.embed .img{max-width:100%;border-radius:4px;margin-top:12px;display:block}
.embed .thumb{width:80px;height:80px;object-fit:contain;border-radius:4px;margin:12px 12px 0 0;flex:none}
.embed .foot{font-size:12px;color:var(--muted);margin-top:8px;display:flex;gap:6px;align-items:center;white-space:pre-wrap}
.embed .foot img{width:20px;height:20px;border-radius:50%}
.att{margin-top:4px}
.att img{max-width:min(400px,100%);max-height:350px;border-radius:8px;display:block}
.file{display:inline-flex;gap:10px;align-items:center;background:var(--bg2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;max-width:100%}
.file .fsize{color:var(--muted);font-size:12px}
.buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.btn{font-size:13px;font-weight:500;padding:3px 14px;border-radius:4px;color:#fff;background:#4e5058;opacity:.85}
.btn.primary{background:#5865f2}.btn.success{background:#248046}.btn.danger{background:#da373c}.btn.link{background:#4e5058}
.btn.select{background:var(--bg3);color:var(--muted);min-width:200px}
.reactions{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.reaction{background:var(--bg2);border:1px solid var(--line);border-radius:8px;padding:1px 6px;font-size:13px}
.sticker{width:160px;height:160px;object-fit:contain}
footer{color:var(--muted);font-size:12px;text-align:center;padding:16px}
@media (max-width:600px){.gutter{width:32px}.avatar{width:32px;height:32px}.msg{gap:10px;padding:2px 8px}.embed .fields{grid-template-columns:1fr}}
"""

JS = """
const pad=n=>String(n).padStart(2,"0");
const rel=s=>{const d=s-Date.now()/1000,a=Math.abs(d),u=[[31536000,"year"],[2592000,"month"],[86400,"day"],[3600,"hour"],[60,"minute"],[1,"second"]];
for(const [n,w] of u){if(a>=n||n===1){const v=Math.round(a/n);return d<0?`${v} ${w}${v===1?"":"s"} ago`:`in ${v} ${w}${v===1?"":"s"}`}}};
document.querySelectorAll("[data-ts]").forEach(el=>{const s=+el.dataset.ts,d=new Date(s*1000),f=el.dataset.f;
const date=d.toLocaleDateString(undefined,{year:"numeric",month:"long",day:"numeric"}),time=d.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"});
el.textContent=f==="t"?time:f==="T"?d.toLocaleTimeString():f==="d"?d.toLocaleDateString():f==="D"?date:f==="F"?d.toLocaleDateString(undefined,{weekday:"long"})+", "+date+" "+time:f==="R"?rel(s):f==="short"?time:date+" "+time;
el.title=d.toString();});
"""


def _avatar(user) -> str:
    try:
        return user.display_avatar.with_size(64).url
    except Exception:
        return ""


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _emoji_html(emoji) -> str:
    if emoji is None:
        return ""
    if isinstance(emoji, str):
        return _esc(emoji)
    if getattr(emoji, "id", None):
        ext = "gif" if getattr(emoji, "animated", False) else "png"
        return f'<img class="emoji" alt=":{_esc(emoji.name)}:" src="https://cdn.discordapp.com/emojis/{emoji.id}.{ext}?size=48">'
    return _esc(getattr(emoji, "name", "") or "")


def _render_embed(e: discord.Embed, res: _Resolver) -> str:
    color = f"#{e.color.value:06x}" if e.color else None
    parts = []
    author = getattr(e.author, "name", None)
    if author:
        icon = getattr(e.author, "icon_url", None)
        icon_html = f'<img src="{_esc(icon)}" alt="">' if icon else ""
        parts.append(f'<div class="author">{icon_html}{_esc(author)}</div>')
    if e.title:
        title = render_markdown(e.title, res)
        if e.url:
            title = f'<a href="{_esc(e.url)}" target="_blank" rel="noopener">{title}</a>'
        parts.append(f'<div class="title">{title}</div>')
    if e.description:
        parts.append(f'<div class="desc">{render_markdown(e.description, res)}</div>')
    if e.fields:
        fields = "".join(
            f'<div class="field{" inline" if f.inline else ""}"><div class="fn">{render_markdown(f.name, res)}</div>'
            f'<div class="fv">{render_markdown(f.value, res)}</div></div>'
            for f in e.fields
        )
        parts.append(f'<div class="fields">{fields}</div>')
    image = getattr(e.image, "url", None)
    if image:
        parts.append(f'<img class="img" src="{_esc(image)}" alt="" loading="lazy">')
    footer = getattr(e.footer, "text", None)
    if footer or e.timestamp:
        icon = getattr(e.footer, "icon_url", None)
        icon_html = f'<img src="{_esc(icon)}" alt="">' if icon else ""
        stamp = _time_span(_ts(e.timestamp)) if e.timestamp else ""
        sep = " • " if footer and stamp else ""
        parts.append(f'<div class="foot">{icon_html}<span>{_esc(footer)}{sep}{stamp}</span></div>')
    thumb = getattr(e.thumbnail, "url", None)
    thumb_html = f'<img class="thumb" src="{_esc(thumb)}" alt="" loading="lazy">' if thumb else ""
    border = f' style="border-left-color:{color}"' if color else ""
    return f'<div class="embed"{border}><div class="in">{"".join(parts)}</div>{thumb_html}</div>'


def _render_components(components) -> str:
    rows = []
    for row in components or []:
        items = getattr(row, "children", None) or getattr(row, "items", None) or []
        buttons = []
        for c in items:
            if isinstance(c, discord.Button) or hasattr(c, "style") and hasattr(c, "label"):
                style = getattr(getattr(c, "style", None), "name", "secondary")
                style = {"blurple": "primary", "green": "success", "red": "danger", "url": "link"}.get(style, style)
                label = _emoji_html(getattr(c, "emoji", None)) + " " + _esc(getattr(c, "label", "") or "")
                if getattr(c, "url", None):
                    buttons.append(f'<a class="btn link" href="{_esc(c.url)}" target="_blank" rel="noopener">{label.strip()} ↗</a>')
                else:
                    buttons.append(f'<span class="btn {_esc(style)}">{label.strip()}</span>')
            elif hasattr(c, "placeholder"):
                buttons.append(f'<span class="btn select">{_esc(c.placeholder or "Select...")} ▾</span>')
        if buttons:
            rows.append(f'<div class="buttons">{"".join(buttons)}</div>')
    return "".join(rows)


def _is_image(a: discord.Attachment) -> bool:
    return (a.content_type or "").startswith("image/") and a.size <= MAX_IMAGE_DOWNLOAD


async def _download_images(messages) -> dict[int, tuple[str, bytes]]:
    """Attachment ID -> (content type, bytes) for every image attachment that could be downloaded."""
    images = {}
    for m in messages:
        for a in m.attachments:
            if _is_image(a):
                try:
                    images[a.id] = (a.content_type, await a.read())
                except Exception:
                    pass
    return images


def _compress(data: bytes, max_side: int, quality: int) -> tuple[str, bytes]:
    """Shrink an image to fit max_side and re-encode it as WebP. Animated images keep their first frame."""
    with Image.open(io.BytesIO(data)) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        im.thumbnail((max_side, max_side))
        transparent = im.mode in ("RGBA", "LA", "PA", "P") or "transparency" in im.info
        im = im.convert("RGBA" if transparent else "RGB")
        out = io.BytesIO()
        im.save(out, "WEBP", quality=quality, method=4)
    return "image/webp", out.getvalue()


async def _image_sources(raw: dict[int, tuple[str, bytes]], level: tuple[int, int] | None) -> dict[int, str]:
    """Attachment ID -> data URI, compressed to the given level (or left as is without Pillow)."""
    sources = {}
    for aid, (ctype, data) in raw.items():
        if level is None:
            if len(data) > UNCOMPRESSED_MAX:
                continue
            out_type, out = ctype, data
        else:
            try:
                out_type, out = await asyncio.to_thread(_compress, data, *level)
            except Exception:
                continue  # not an image Pillow can read, so it's linked instead
            if len(data) <= len(out) and ctype in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                out_type, out = ctype, data  # already smaller than the compressed copy
        sources[aid] = f"data:{out_type};base64,{base64.b64encode(out).decode()}"
    return sources


def _render_attachments(m: discord.Message, images: dict[int, str]) -> str:
    out = []
    for a in m.attachments:
        src = images.get(a.id)
        if src:
            out.append(
                f'<div class="att"><img src="{src}" alt="{_esc(a.filename)}" '
                f'title="{_esc(a.filename)} ({_human_size(a.size)} original)"></div>'
            )
            continue
        out.append(
            f'<div class="att"><a class="file" href="{_esc(a.url)}" target="_blank" rel="noopener">📄 '
            f'<span>{_esc(a.filename)}<br><span class="fsize">{_human_size(a.size)} (link may have expired)</span></span></a></div>'
        )
    return "".join(out)


async def build_html(bot, channel: discord.TextChannel, messages, summary: _Summary, reason: str,
                     archived_at: datetime | None, helpee, images: dict[int, str]) -> str:
    res = _Resolver(bot, channel.guild, messages)
    by_id = {m.id: m for m in messages}
    now = datetime.now(timezone.utc)

    def who(uid, user):
        name = user.display_name if user else f"Unknown ({uid})"
        return _esc(name)

    # Summary cards
    helpee_name = f"{_esc(helpee.display_name)} <span class=\"sub\">@{_esc(helpee.name)}</span>" if helpee else "Unknown"
    done_word = "NNID transfer" if summary.kind == "NNID" else "SOAP"
    status = (
        f'<span class="pill done">{done_word} completed</span>'
        if summary.completions
        else f'<span class="pill no">{done_word} not completed</span>'
    )
    completions = "".join(
        f'<li><a href="#m{m.id}">{_time_span(_ts(m.created_at))}</a> · {_esc(label)}</li>'
        for m, label in summary.completions
    ) or "<li>None</li>"
    issue_items = []
    for text, (n, mid) in summary.issues.items():
        item = _esc(text)
        if mid:
            item = f'<a href="#m{mid}">{item}</a>'
        if n > 1:
            item += f" ×{n}"
        issue_items.append(f"<li>{item}</li>")
    issues = "".join(issue_items) or "<li>None</li>"
    soapers = "".join(f"<li>{_esc(u.display_name)} <span class=\"sub\">{u.id} · {c} msg{'s' if c != 1 else ''}</span></li>" for u, c in summary.soapers) or "<li>None</li>"
    others = "".join(f"<li>{_esc(u.display_name)} <span class=\"sub\">{u.id} · {c} msg{'s' if c != 1 else ''}</span></li>" for u, c in summary.others)
    closed_by = summary.closed_by
    closed_text = "Inactivity" if closed_by == "inactivity" else (_esc(closed_by.display_name) if closed_by else None)
    timeline = [f"<li>Opened {_time_span(_ts(channel.created_at))}</li>"]
    if archived_at or summary.closed_at:
        closed_line = f"<li>Archived {_time_span(_ts(archived_at or summary.closed_at))}"
        if closed_text:
            closed_line += f" by {closed_text}"
        timeline.append(closed_line + "</li>")
    timeline.append(f"<li>Deleted {_time_span(_ts(now))} ({_esc(reason)})</li>")

    cards = f"""
<div class="grid">
  <div class="card"><h2>Helpee</h2><div class="big">{helpee_name}</div><div class="sub">ID {summary.helpee_id or "unknown"}</div></div>
  <div class="card"><h2>Status</h2><div class="big">{status}</div><div class="sub">{len(messages)} messages, {summary.helpee_messages} from the helpee</div></div>
  <div class="card"><h2>Timeline</h2><ul>{"".join(timeline)}</ul></div>
  <div class="card"><h2>{done_word} done</h2><ul>{completions}</ul></div>
  <div class="card"><h2>Soapers who spoke</h2><ul>{soapers}</ul></div>
  {f'<div class="card"><h2>Others who spoke</h2><ul>{others}</ul></div>' if others else ""}
  <div class="card"><h2>Issues</h2><ul>{issues}</ul></div>
</div>"""

    # Messages
    rows = []
    prev = None
    for m in messages:
        author = m.author
        flag = summary.flagged.get(m.id)
        reply_html = ""
        if m.reference and m.reference.message_id and m.type == discord.MessageType.reply:
            ref = by_id.get(m.reference.message_id)
            if ref:
                snippet = (ref.content or (ref.embeds[0].title if ref.embeds else "") or "Attachment")[:120]
                reply_html = f'<div class="reply">↪ <b>{_esc(ref.author.display_name)}</b> <a href="#m{ref.id}">{_esc(snippet)}</a></div>'
            else:
                reply_html = '<div class="reply">↪ <i>Original message was deleted</i></div>'
        meta = getattr(m, "interaction_metadata", None)
        if meta and getattr(meta, "user", None) and not reply_html:
            reply_html = f'<div class="reply">↪ <b>{_esc(meta.user.display_name)}</b> used a command</div>'

        grouped = (
            prev is not None
            and not reply_html
            and prev.author.id == author.id
            and (m.created_at - prev.created_at).total_seconds() < 420
            and not m.is_system()
        )
        stamp = _ts(m.created_at)
        if m.is_system():
            body = f'<div class="system">{_esc(m.system_content)}</div>'
        else:
            body = ""
            if m.content:
                edited = f'<span class="edited" title="{_esc(m.edited_at)}">(edited)</span>' if m.edited_at else ""
                body += f'<div class="content">{render_markdown(m.content, res)}{edited}</div>'
            body += "".join(_render_embed(e, res) for e in m.embeds)
            body += _render_attachments(m, images)
            for s in m.stickers:
                body += f'<div class="att"><img class="sticker" src="{_esc(s.url)}" alt="{_esc(s.name)}" title="{_esc(s.name)}"></div>'
            body += _render_components(m.components)
            if m.reactions:
                body += '<div class="reactions">' + "".join(
                    f'<span class="reaction">{_emoji_html(r.emoji)} {r.count}</span>' for r in m.reactions
                ) + "</div>"

        cls = "msg" + ("" if grouped else " first") + (f" flag-{flag}" if flag else "")
        if grouped:
            gutter = f'<div class="gutter"><span class="t">{_time_span(stamp, "short")}</span></div>'
            head = ""
        else:
            tag = ""
            if author.bot:
                tag = '<span class="tag">BOT</span>'
            elif author.id == summary.helpee_id:
                tag = '<span class="tag helpee">HELPEE</span>'
            elif author.id in summary.soaper_ids:
                tag = '<span class="tag soaper">SOAPER</span>'
            color = getattr(author, "color", None)
            style = f' style="color:{color}"' if color is not None and getattr(color, "value", 0) else ""
            gutter = f'<div class="gutter"><img class="avatar" src="{_esc(_avatar(author))}" alt="" loading="lazy"></div>'
            head = (
                f'<div><span class="name"{style} title="{_esc(author.name)} ({author.id})">{_esc(author.display_name)}</span>'
                f'{tag}<span class="meta">{_time_span(stamp)}</span></div>'
            )
        rows.append(f'<div class="{cls}" id="m{m.id}">{gutter}<div class="body">{reply_html}{head}{body}</div></div>')
        prev = m

    title = f"#{channel.name}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} transcript</title><style>{CSS}</style></head>
<body>
<header><div class="wrap">
<h1>{_esc(title)}</h1>
<div class="sub">{summary.kind} channel · Channel ID {channel.id} · Transcript saved {_time_span(_ts(now))}</div>
{cards}
</div></header>
<main class="wrap">{"".join(rows) or '<p class="sub" style="padding:16px">No messages.</p>'}</main>
<footer>Saved by {_esc(bot.user.name)} before the channel was deleted. Times are shown in your local time zone.</footer>
<script>{JS}</script>
</body></html>"""


def build_embed(channel: discord.TextChannel, messages, summary: _Summary, reason: str,
                archived_at: datetime | None, helpee) -> discord.Embed:
    done_word = "NNID Transfer" if summary.kind == "NNID" else "SOAP"
    if summary.completions:
        color = discord.Color.green()
    elif any(t.startswith("SOAP failed") for t in summary.issues):
        color = discord.Color.red()
    else:
        color = discord.Color.orange()

    embed = discord.Embed(
        title=f"📜 {summary.kind} Channel Transcript" if summary.kind != "Channel" else "📜 Channel Transcript",
        description=f"`#{channel.name}` was deleted ({reason}). The full transcript is attached.",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if helpee:
        embed.set_thumbnail(url=_avatar(helpee))
    uid = summary.helpee_id
    embed.add_field(
        name="👤 Helpee",
        value=f"<@{uid}>\n`{helpee.name}`" if helpee else (f"<@{uid}>" if uid else "Unknown"),
        inline=True,
    )
    embed.add_field(name="🆔 User ID", value=f"`{uid}`" if uid else "Unknown", inline=True)
    embed.add_field(
        name="📊 Status",
        value=f"✅ {done_word} completed" if summary.completions else f"❌ {done_word} not completed",
        inline=True,
    )

    timeline = [f"Opened <t:{_ts(channel.created_at)}:f>"]
    archived = archived_at or summary.closed_at
    if archived:
        line = f"Archived <t:{_ts(archived)}:f>"
        if summary.closed_by == "inactivity":
            line += " for inactivity"
        elif summary.closed_by:
            line += f" by <@{summary.closed_by.id}>"
        timeline.append(line)
    timeline.append(f"Deleted <t:{_ts(datetime.now(timezone.utc))}:f>")
    embed.add_field(name="🕒 Timeline", value="\n".join(timeline), inline=False)

    embed.add_field(
        name=f"🎉 {done_word} Done" + (f" ({len(summary.completions)} times)" if len(summary.completions) > 1 else ""),
        value=_clip_lines([f"<t:{_ts(m.created_at)}:f> · {label}" for m, label in summary.completions], empty="Never"),
        inline=False,
    )
    embed.add_field(
        name="🧼 Soapers Who Spoke",
        value=_clip_lines([f"<@{u.id}> `{u.name}` · {c} msg{'s' if c != 1 else ''}" for u, c in summary.soapers]),
        inline=False,
    )
    if summary.others:
        embed.add_field(
            name="💬 Others Who Spoke",
            value=_clip_lines([f"<@{u.id}> `{u.name}` · {c} msg{'s' if c != 1 else ''}" for u, c in summary.others]),
            inline=False,
        )
    embed.add_field(
        name="⚠️ Issues",
        value=_clip_lines([f"• {text}" + (f" (×{n})" if n > 1 else "") for text, (n, _) in summary.issues.items()]),
        inline=False,
    )
    attachments = sum(len(m.attachments) for m in messages)
    embed.add_field(
        name="📝 Messages",
        value=f"{len(messages)} total, {summary.helpee_messages} from the helpee"
        + (f", {attachments} attachment{'s' if attachments != 1 else ''}" if attachments else ""),
        inline=False,
    )
    embed.set_footer(text=f"Channel ID: {channel.id}")
    return embed


def _filename(channel: discord.TextChannel) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "", channel.name.lower().replace("🧼", "").replace("🔑", "")).strip("-_")
    return f"{base or 'channel'}-{channel.id}.html"


async def send_transcript(bot, channel: discord.TextChannel, reason: str, archived_at: datetime | None = None) -> bool:
    """Post an HTML transcript and summary of a channel to the transcript channel. Returns True if sent.
    Never raises, so a failure here never stops the channel from being deleted."""
    if not TRANSCRIPT_CHANNEL_ID:
        return False
    try:
        target = bot.get_channel(TRANSCRIPT_CHANNEL_ID) or await bot.fetch_channel(TRANSCRIPT_CHANNEL_ID)
        messages = [m async for m in channel.history(limit=None, oldest_first=True)]
        summary = _Summary(bot, channel, messages)
        helpee = None
        if summary.helpee_id:
            helpee = channel.guild.get_member(summary.helpee_id) or bot.get_user(summary.helpee_id)
            if helpee is None:
                try:
                    helpee = await bot.fetch_user(summary.helpee_id)
                except discord.HTTPException:
                    pass

        raw = await _download_images(messages)
        # Try each compression level until the page fits, then fall back to linking the images
        levels = (IMAGE_LEVELS if Image else [None]) if raw else []
        data = b""
        for level in levels + ["links"]:
            images = {} if level == "links" else await _image_sources(raw, level)
            page = await build_html(bot, channel, messages, summary, reason, archived_at, helpee, images)
            data = page.encode("utf-8")
            if len(data) <= MAX_FILE_BYTES:
                break
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"transcript is {len(data) / 1_000_000:.1f} MB, over Discord's upload limit")

        embed = build_embed(channel, messages, summary, reason, archived_at, helpee)
        await target.send(
            embed=embed,
            file=discord.File(io.BytesIO(data), filename=_filename(channel)),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except Exception as e:
        print(f"Could not save transcript for #{channel.name}: {e!r}")
        log = channel.guild.get_channel(ERROR_LOG_ID) if ERROR_LOG_ID else None
        if log:
            try:
                await log.send(
                    embed=discord.Embed(
                        title="Transcript failed",
                        description=f"Couldn't save a transcript of `#{channel.name}` before deleting it.\n`{e!r}`"[:4000],
                        color=discord.Color.red(),
                    )
                )
            except discord.HTTPException:
                pass
        return False
