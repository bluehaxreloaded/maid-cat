import discord
import re
from dataclasses import dataclass
from perms import command_with_perms
from discord.ext import commands
from exefs import InvalidEssential, read_essential, serial_from_secinfo

# 3DS serial: model letter, 1-2 region letters, 8 digits, then an optional check digit
# (the 9th digit on the console's sticker, which isn't stored in secinfo)
SERIAL_RE = re.compile(r"^([A-Z])([A-Z]{1,2})(\d{8})(\d)?$")
# Same, but allowing letters that are easy to type instead of digits; no real prefix uses O, I or L
TYPO_SERIAL_RE = re.compile(r"^([A-Z]{2,3})([0-9OIL]{8,9})$")
DIGIT_TYPOS = str.maketrans({"O": "0", "I": "1", "L": "1"})
SERIAL_IN_TEXT_RE = re.compile(r"\b([A-Z]{2,3}\d{8,9})\b")

SERIAL_RECEIVED_TITLE = "✅ Serial number received"  # the step 1 message the entered serial is read back from

MODELS = {
    "C": "Old 3DS",
    "S": "Old 3DS XL",
    "A": "Old 2DS",
    "Y": "New 3DS",
    "Q": "New 3DS XL",
    "N": "New 2DS XL",
}
REGION_CODES = [
    "JF", "JH", "JM", "JE", "W", "B", "S",
    "EE", "EF", "EH", "EM", "EG", "AH", "AG", "AM", "UH", "KF", "KH",
    "KM", "CF", "CH", "CM", "TF", "TH", "TM",
]
REGIONS = {
    "W": "USA",
    "J": "JPN",
    "E": "EUR",
    "K": "KOR",
    "T": "TWN",
    "C": "CHN",
    "A": "AUS",
    "S": "ASI",
}
UNUSUAL_REGION_CODES = ["UH", "CF", "CH", "CM"]


def clean_serial(text: str) -> str:
    """Serial as typed, uppercased with spaces removed (e.g. "YJM12345678 4"),
    and O/I/L typed in place of 0/1 in the digits corrected."""
    serial = re.sub(r"\s+", "", text).upper()
    match = TYPO_SERIAL_RE.match(serial)
    if match:
        serial = match.group(1) + match.group(2).translate(DIGIT_TYPOS)
    return serial


def check_digit(digits: str) -> int:
    """Check digit for the 8 digits of a serial (same calculation cleaninty uses)."""
    odd_sum = sum(int(d) for d in digits[::2])  # 1st, 3rd, 5th, 7th digits
    even_sum = sum(int(d) for d in digits[1::2])  # 2nd, 4th, 6th, 8th digits
    return (10 - (3 * even_sum + odd_sum) % 10) % 10


@dataclass
class SerialInfo:
    serial: str
    model: str | None
    region: str | None
    region_code: str
    known_region_code: bool
    unusual: bool
    check_digit: int | None  # entered check digit, if any
    expected_check_digit: int

    @property
    def check_digit_ok(self) -> bool:
        """No check digit entered counts as fine, since secinfo serials don't have one."""
        return self.check_digit is None or self.check_digit == self.expected_check_digit


def read_serial(text: str) -> SerialInfo | None:
    """Read a 3DS serial. Returns None if it isn't in the serial format."""
    serial = clean_serial(text)
    match = SERIAL_RE.match(serial)
    if not match:
        return None
    model, region_code, digits, entered = match.groups()
    return SerialInfo(
        serial=serial,
        model=MODELS.get(model),
        region=REGIONS.get(region_code[0]),
        region_code=region_code,
        known_region_code=region_code in REGION_CODES,
        unusual=region_code in UNUSUAL_REGION_CODES,
        check_digit=int(entered) if entered is not None else None,
        expected_check_digit=check_digit(digits),
    )


def describe_serial(info: SerialInfo) -> str | None:
    """Short label like "New 3DS · JPN" for a serial, or None if neither is known."""
    parts = [p for p in (info.model, info.region) if p]
    return " · ".join(parts) or None


def find_serial(text: str) -> str | None:
    """First serial number in a piece of text."""
    match = SERIAL_IN_TEXT_RE.search(text or "")
    return match.group(1) if match else None


def serials_match(entered: str, from_file: str) -> bool:
    """Whether the serial the helpee entered matches the one in their file.
    The file has 8 digits, while the sticker adds a 9th check digit, so one extra digit on either side still matches."""
    entered = entered.strip().upper()
    if not entered or not from_file:
        return False
    if entered == from_file:
        return True
    longer, shorter = (entered, from_file) if len(entered) > len(from_file) else (from_file, entered)
    return len(longer) == len(shorter) + 1 and longer.startswith(shorter)


async def entered_serial(channel: discord.TextChannel) -> str | None:
    """The serial the helpee entered in step 1, read back from the "Serial number received" message."""
    async for message in channel.history(limit=100):
        if message.embeds and message.embeds[0].title == SERIAL_RECEIVED_TITLE:
            return find_serial(message.embeds[0].description or "")
    return None


async def uploaded_essential_serial(channel: discord.TextChannel) -> str | None:
    """Serial inside the most recent essential.exefs posted in the channel, if there is a valid one."""
    async for message in channel.history(limit=100):
        for attachment in message.attachments:
            if not attachment.filename.lower().endswith(".exefs"):
                continue
            try:
                essential = read_essential(await attachment.read())
            except (discord.HTTPException, InvalidEssential):
                continue
            return serial_from_secinfo(essential["secinfo"])
    return None


class SerialCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @command_with_perms(
        min_role="Soaper",
        name="validateserial",
        aliases=["checkserial", "serialvalidate"],
        help="Checks a 3DS serial number, or this channel's serial if none is given",
    )
    async def validateserial(self, ctx, *, serial: str = None):
        file_serial = None
        if not serial:
            # No serial given, so check this channel's: the one entered in step 1 and the one in its essential.exefs
            file_serial = await uploaded_essential_serial(ctx.channel)
            serial = await entered_serial(ctx.channel) or file_serial
            if not serial:
                await ctx.respond(
                    "No serial number found in this channel. Use `.validateserial <serial>` to check one.",
                    ephemeral=True,
                )
                return

        info = read_serial(serial)
        if info is None:
            embed = discord.Embed(
                title="🔢 Serial Number Check",
                description=(
                    f"`{discord.utils.escape_markdown(serial)[:40]}` isn't a valid serial number format. "
                    "Serial numbers have 2-3 letters followed by 8 or 9 digits."
                ),
                color=discord.Color.red(),
            )
            await ctx.respond(embed=embed)
            return

        if info.check_digit is None:
            check = "➖ Not entered"
        elif info.check_digit_ok:
            check = "✅ Correct"
        else:
            check = f"❌ Wrong (should be {info.expected_check_digit})"

        file_matches = file_serial is None or serials_match(info.serial, file_serial)
        if not info.check_digit_ok or not file_matches:
            color = discord.Color.red()
        elif info.model is None or not info.known_region_code or info.unusual:
            color = discord.Color.orange()
        else:
            color = discord.Color.green()

        embed = discord.Embed(
            title="🔢 Serial Number Check",
            description=f"`{info.serial}`",
            color=color,
        )
        embed.add_field(name="Model", value=info.model or "⚠️ Unknown", inline=True)
        embed.add_field(name="Region", value=info.region or "⚠️ Unknown", inline=True)
        embed.add_field(name="Check Digit", value=check, inline=True)
        embed.add_field(
            name="Region Code",
            value=f"✅ {info.region_code}" if info.known_region_code else f"⚠️ {info.region_code} isn't a region code we know",
            inline=True,
        )
        if info.unusual:
            embed.add_field(name="Unusual", value="⚠️ This region code is unusual", inline=True)
        if file_serial and file_serial != info.serial:
            embed.add_field(
                name="essential.exefs",
                value=f"✅ `{file_serial}` matches" if file_matches else f"❌ `{file_serial}` doesn't match",
                inline=False,
            )
        await ctx.respond(embed=embed)


def setup(bot):
    return bot.add_cog(SerialCog(bot))
