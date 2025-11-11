from pathlib import Path
import discord
import re
from perms import command_with_perms, soap_channels_only
from discord.ext import commands
from functools import wraps
from constants import (
    SOAP_CHANNEL_SUFFIX,
    NNID_CHANNEL_SUFFIX,
    SOAP_USABLE_IDS,
    NNID_CHANNEL_CATEGORY_ID,
    BLOBSOAP_EMOTE_ID,
    SOAP_LOADING_ID,
)

# Regex pulls the User ID from a mention
MENTION_RE = re.compile(r"<@!?(\d{15,25})>")


def ping_before_mes():  # i didn't feel like writing the same line multiple times so i did the harder option of writing an entire decorator to write one single line
    def decorator(func):
        @wraps(func)
        async def send_ping(self, ctx: commands.Context, *args, **kwargs):
            # Get the user from the channel topic.
            member_obj = None
            topic = getattr(ctx.channel, "topic", None)
            if isinstance(ctx.channel, discord.TextChannel) and topic:
                m = MENTION_RE.search(topic)
                if m:
                    uid = int(m.group(1))
                    member_obj = ctx.guild.get_member(uid)
                    if member_obj is None:
                        try:
                            member_obj = await ctx.guild.fetch_member(uid)
                        except discord.NotFound:
                            member_obj = None

                if member_obj:
                    await ctx.send(
                        f"{member_obj.mention}\n\n{'\n\n'.join(await func(self, ctx, *args, **kwargs))}"
                    )
                    return

            # Get the user from the channel name if topic failed.
            # Try SOAP suffix first, then NNID suffix
            member_name = ctx.channel.name.removesuffix(SOAP_CHANNEL_SUFFIX)
            if member_name == ctx.channel.name:  # SOAP suffix didn't match, try NNID
                member_name = ctx.channel.name.removesuffix(NNID_CHANNEL_SUFFIX)
            
            member_obj = ctx.guild.get_member_named(member_name)
            if member_obj:
                await ctx.send(
                    f"{member_obj.mention}\n\n{'\n\n'.join(await func(self, ctx, *args, **kwargs))}"
                )
            elif ctx.channel.category and (ctx.channel.category.id in SOAP_USABLE_IDS or ctx.channel.category.id == NNID_CHANNEL_CATEGORY_ID):
                await ctx.send(
                    f"`SOAPEE MENTION HERE` (this is not a soap/nnid channel)\n\n{'\n\n'.join(await func(self, ctx, *args, **kwargs))}"
                )
            else:
                await ctx.send(f"User `{member_name}` left.")

        return send_ping

    return decorator


class TextCommandsCog(commands.Cog):  # temp until dynamic stuff is ready
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @command_with_perms(
        min_role="Soaper",
        name="soapnormal",
        aliases=["normalsoap", "normal"],
        help="Displays normal SOAP completion message",
    )
    @soap_channels_only()
    @ping_before_mes()
    async def soapnormal(self, ctx: commands.Context):
        return [
            "The SOAP Transfer has completed! Please boot up your console normally with the SD card inserted. Then go to `System Settings -> Other Settings -> Profile -> Region Settings` and ensure the desired country is selected. If using Pretendo, you must first open the Nimbus app and switch to Nintendo.\n\n",
            "Then try opening the eShop.",
            "A system transfer was required to do this SOAP. If you want to do a system transfer from your old console to this one, you must wait a week.\n\n",
            "Please let us know if the eshop functions or not.",
        ]

    @command_with_perms(
        min_role="Soaper",
        name="soaplottery",
        aliases=["lotterysoap", "lottery"],
        help='Displays "lottery" SOAP completion message',
    )
    @soap_channels_only()
    @ping_before_mes()
    async def soaplottery(self, ctx: commands.Context):
        return [
            "The SOAP Transfer has completed! Please boot up your console normally with the SD card inserted. Then go to `System Settings -> Other Settings -> Profile -> Region Settings` and ensure the desired country is selected. If using Pretendo, you must first open the Nimbus app and switch to Nintendo.\n\n",
            "Then try opening the eShop.\n\n",
            "You hit the SOAP lottery! No system transfer was needed for this SOAP. If you want to do a system transfer from your old console to this one, you can do it right away.\n\n",
            "Please let us know if the eshop functions or not.",
        ]

    @command_with_perms(
        min_role="Soaper",
        name="findserial",
        aliases=["serial", "serialmismatch"],
        help="Explains how to find a serial number in GM9",
    )
    @soap_channels_only()
    @ping_before_mes()
    async def findserial(self, ctx: commands.Context):
        return [
            "To find the serial number, hold START while powering on your console. This will boot you into GodMode9.\n\n",
            "Go to `SYSNAND TWLNAND` -> `sys` -> `log` -> `inspect.log`\n\n",
            "Select `Open in Textviewer`. Send a picture of the serial number contained in the file. It should be a three-letter prefix followed by nine numbers.",
        ]

    @command_with_perms(
        min_role="Soaper",
        name="soapwait",
        aliases=["wait"],
        help="Claiming a soap channel, for soapers",
    )
    @soap_channels_only()
    async def soapwait(self, ctx: commands.Context):
        await ctx.send(
            f"{discord.utils.get(ctx.guild.emojis, id=BLOBSOAP_EMOTE_ID)} the SOAP process has begun and will take up to 5 minutes. Please wait. {discord.utils.get(ctx.guild.emojis, id=BLOBSOAP_EMOTE_ID)}"
        )
        sticker = discord.utils.get(ctx.guild.stickers, id=SOAP_LOADING_ID)
        if sticker:
            await ctx.send(content="", stickers=[sticker])

    @command_with_perms(
        name="removennid", aliases=["nnidremove"], help="NNID Removal instructions"
    )
    async def removennid(self, ctx: commands.Context):
        await ctx.send(
            "To remove the old Nintendo Network ID (NNID) on your system, first [make a fresh nand backup](<https://3ds.hacks.guide/godmode9-usage.html#creating-a-nand-backup>) (NOTE: Your existing NAND backup is likely in the original region of your console, so you will want one of your changed region anyway).\n\n"
            "Then use GM9 to [remove your NNID](<https://3ds.hacks.guide/godmode9-usage.html#removing-an-nnid-without-formatting-your-console>)"
        )

    @command_with_perms(
        name="hacksguide", aliases=["guide"], help="Modding and 3DS help link"
    )
    async def hacksguide(self, ctx: commands.Context):
        await ctx.send(
            "For modding help and 3DS support please visit 3DS Hacks Guide:\n\n"
            "<https://3ds.hacks.guide/>"
        )

    @command_with_perms(
        name="regionchange",
        help="Directions on performing a region change on a 3DS console",
    )
    async def regionchange(self, ctx: commands.Context):
        await ctx.send(
            "Region changing guide:\n\n<https://3ds.hacks.guide/region-changing.html>"
        )

    @command_with_perms(
        name="nandbackup",
        aliases=["backupnand"],
        help="Directions on creating a nand backup",
    )
    async def nandbackup(self, ctx: commands.Context):
        await ctx.send(
            "How to create a nand backup:\n\n"
            "<https://3ds.hacks.guide/godmode9-usage.html#creating-a-nand-backup>"
        )

    @command_with_perms(name="cleaninty", help="Sends link to cleaninty article")
    async def cleaninty(self, ctx: commands.Context):
        await ctx.send(
            "See the following for an overview on how SOAP Transfers work:\n\n"
            "https://wiki.hacks.guide/wiki/3DS:Cleaninty"
        )

    @command_with_perms(
        min_role="Soaper",
        name="nodonors",
        help="Lets Soapee know they need to wait for a bit.",
    )
    @soap_channels_only()
    @ping_before_mes()
    async def nodonors(self, ctx: commands.Context):
        return [
            "All of our donors are on cooldown, you have been added to the queue, we’ll get back to you as soon as possible."
        ]
    
    @command_with_perms(
        min_role="Soaper",
        name="nocomputer",
        help="Sends essentialsubmit instructions",
    )
    @soap_channels_only()
    async def nocomputer(self, ctx: commands.Context):
        file = None
        try:
            path = Path(__file__).parent / "assets" / "essential-3dsx.webp"
            file = discord.File(fp=path, filename="essential-3dsx.webp")
            await ctx.send(file=file)
            await ctx.send(
                "## Submitting essential.exefs without a computer\n"
                "1. Open FBI and navigate to `Remote Install` → `Scan QR Code`\n"
                "2. Scan the QR code provided with the camera and press A to install.\n"
                "3. After it is installed, close FBI.\n"
                "4. Open the Homebrew Launcher.\n"
                "5. Select essentialsubmit from the list of applications.\n"
                "6. Press Y and type in your Discord username, then press OK.\n"
                "7. Select the large :soap: icon.\n"
                "8. Let us know when it has been submitted.\n"
                "\nIf you have any questions or issues, let us know.\n"
                "\nAfter we confirm that it submitted properly, you can safely delete `essentialsubmit.3dsx` from the `3ds` folder on your SD card (this is not required)."
            )
        except FileNotFoundError as e:
            print(f"Error: Could not find assets/essential-3dsx.webp - {e}")
            await ctx.send("Could not get essentialsubmit QR code.")
            pass
    
    @command_with_perms(name="newsd", help="New SD guide")
    async def newsd(self, ctx: commands.Context):
        await ctx.send(
            "How to restore CFW on a new SD card:\n\n"
            "<https://3ds.hacks.guide/restoring-updating-cfw.html>"
        )

    @command_with_perms(
        name="formatsd", aliases=["format", "sdformat"], help="SD formatting guide"
    )
    async def formatsd(self, ctx: commands.Context):
        await ctx.send(
            "How to format an SD card correctly for a 3DS:\n\n"
            "<https://3ds.hacks.guide/formatting-sd-(windows).html>"
        )

    @command_with_perms(name="donors", help="How to donate consoles for SOAPs")
    async def donors(self, ctx: commands.Context):
        await ctx.send(
            "For a console to be a donor, ideally they should:\n"
            "- be in a state where they won't be used anymore (won't turn on, bad screens, bad ram, etc),\n"
            "- have a bad wifi card, or\n"
            "- have had the eShop apps (`tiger`, `mint`) deleted off the NAND so it can't connect to the eShop (connecting a console to the eShop while it is also being used as a donor is known cause various issues)"
            "\n\n"
            "To donate a console for soaps, all we need is either:\n\n"
            "- `essential.exefs` + serial, or\n"
            "- secinfo + OTP + serial\n\n"
            "You can send this info to any Staff or Soaper. Thank you!"
        )


def setup(bot):
    bot.add_cog(TextCommandsCog(bot))
