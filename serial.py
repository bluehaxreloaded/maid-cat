import discord
import re
from dataclasses import dataclass
from perms import command_with_perms
from discord.ext import commands

# 3DS serial: model letter, 1-2 region letters, 8 digits, then an optional check digit
# (the 9th digit on the console's sticker, which isn't stored in secinfo)
SERIAL_RE = re.compile(r"^([A-Z])([A-Z]{1,2})(\d{8})(\d)?$")

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
    """Serial as typed, uppercased with spaces removed (e.g. "CWH12345678 9")."""
    return re.sub(r"\s+", "", text).upper()


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


class SerialCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @command_with_perms(
        min_role="Soaper",
        name="validateserial",
        aliases=["checkserial", "serialvalidate"],
        help="Checks a 3DS serial number",
    )
    async def validateserial(self, ctx, *, serial: str):
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

        if not info.check_digit_ok:
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
        await ctx.respond(embed=embed)


def setup(bot):
    return bot.add_cog(SerialCog(bot))
