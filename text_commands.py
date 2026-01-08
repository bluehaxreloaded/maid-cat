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
                    f"`HELPEE MENTION HERE` (This is not a working channel)\n\n{'\n\n'.join(await func(self, ctx, *args, **kwargs))}"
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
    async def findserial(self, ctx: commands.Context):
        # Get the user from the channel topic or name for mention
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

        # Get the user from the channel name if topic failed
        if not member_obj:
            member_name = ctx.channel.name.removesuffix(SOAP_CHANNEL_SUFFIX)
            if member_name == ctx.channel.name:  # SOAP suffix didn't match, try NNID
                member_name = ctx.channel.name.removesuffix(NNID_CHANNEL_SUFFIX)
            member_obj = ctx.guild.get_member_named(member_name)

        # Create embed matching the Serial Number Mismatch embed format
        embed = discord.Embed(
            title="📂 Finding Your Serial Number",
            description=(
                "Follow these instructions to find your console's serial number.\n\n"
                "**To find your console's serial number:**\n"
                "- Hold START while powering on your console. This will boot you into GodMode9.\n"
                "- Go to `SYSNAND TWLNAND` -> `sys` -> `log` -> `inspect.log`\n"
                "- Select `Open in Textviewer`.\n\n"
                "The correct serial number (three-letter prefix followed by nine numbers) should be in the file. "
            ),
            color=discord.Color.yellow(),
        )
        embed.set_footer(text="You may also send us a picture if you're unsure.")
        
        # Send with user mention if found
        if member_obj:
            await ctx.send(content=member_obj.mention, embed=embed)
        elif ctx.channel.category and (ctx.channel.category.id in SOAP_USABLE_IDS or ctx.channel.category.id == NNID_CHANNEL_CATEGORY_ID):
            await ctx.send(content="`HELPEE MENTION HERE` (This is not a working channel)", embed=embed)
        else:
            await ctx.send(embed=embed)

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
        embed = discord.Embed(
            title="🔧 Removing Previous Nintendo Network ID",
            description=(
                "You'll need to remove the old Nintendo Network ID from your system. \n\nTo do so, follow these steps:\n"
                "**1.** [Make a new NAND backup](<https://3ds.hacks.guide/godmode9-usage.html#creating-a-nand-backup>) and save it somewhere safe.\n"
                "**2.** Use GodMode9 to [remove your NNID](<https://3ds.hacks.guide/godmode9-usage.html#removing-an-nnid-without-formatting-your-console>) "
                "without having to format your console."
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="If you need help with any of these steps, feel free to ask!")
        await ctx.send(embed=embed)

    @command_with_perms(
        name="hacksguide", aliases=["guide"], help="Modding and 3DS help link"
    )
    async def hacksguide(self, ctx: commands.Context):
        embed = discord.Embed(
            title="📚 3DS Hacks Guide",
            description="For modding help and 3DS support, please visit the 3DS Hacks Guide:\n\n"
            "[**3ds.hacks.guide**](<https://3ds.hacks.guide/>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="This guide contains comprehensive instructions for modding your 3DS console.")
        await ctx.send(embed=embed)

    @command_with_perms(
        name="regionchange",
        help="Directions on performing a region change on a 3DS console",
    )
    async def regionchange(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🌍 Region Changing Guide",
            description="Learn how to perform a region change on your 3DS console:\n\n"
            "[**Region Changing Guide**](<https://3ds.hacks.guide/region-changing.html>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Follow the guide carefully to change your console's region.")
        await ctx.send(embed=embed)

    @command_with_perms(
        name="nandbackup",
        aliases=["backupnand"],
        help="Directions on creating a nand backup",
    )
    async def nandbackup(self, ctx: commands.Context):
        embed = discord.Embed(
            title="💾 Creating a NAND Backup",
            description="Learn how to create a NAND backup using GodMode9:\n\n"
            "[**NAND Backup Guide**](<https://3ds.hacks.guide/godmode9-usage.html#creating-a-nand-backup>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Always create a NAND backup before making significant changes to your console.")
        await ctx.send(embed=embed)

    @command_with_perms(name="cleaninty", help="Sends link to cleaninty article")
    async def cleaninty(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🧼 SOAP Transfers Overview",
            description="Learn about how SOAP Transfers work:\n\n"
            "[**Cleaninty Article**](<https://wiki.hacks.guide/wiki/3DS:Cleaninty>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="This article provides an overview of the SOAP transfer process.")
        await ctx.send(embed=embed)

    @command_with_perms(
        min_role="Soaper",
        name="nodonors",
        help="Lets Helpee know they need to wait for a bit.",
    )
    @soap_channels_only()
    async def nodonors(self, ctx: commands.Context):
        # Get the user from the channel topic or name for mention
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

        # Get the user from the channel name if topic failed
        if not member_obj:
            member_name = ctx.channel.name.removesuffix(SOAP_CHANNEL_SUFFIX)
            if member_name == ctx.channel.name:  # SOAP suffix didn't match, try NNID
                member_name = ctx.channel.name.removesuffix(NNID_CHANNEL_SUFFIX)
            member_obj = ctx.guild.get_member_named(member_name)

        # Create embed
        embed = discord.Embed(
            title="⏳ Donors on Cooldown",
            description="All of our donors are currently on cooldown. You have been added to the queue, and we'll get back to you as soon as possible.",
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Thank you for your patience!")
        
        # Send with user mention if found
        if member_obj:
            await ctx.send(content=member_obj.mention, embed=embed)
        elif ctx.channel.category and (ctx.channel.category.id in SOAP_USABLE_IDS or ctx.channel.category.id == NNID_CHANNEL_CATEGORY_ID):
            await ctx.send(content="`HELPEE MENTION HERE` (This is not a working channel)", embed=embed)
        else:
            await ctx.send(embed=embed)
    
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
        embed = discord.Embed(
            title="💾 Formatting SD Card for 3DS",
            description="Learn how to format an SD card correctly for your 3DS console:\n\n"
            "[**SD Card Formatting Guide**](<https://3ds.hacks.guide/formatting-sd-(windows).html>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Proper formatting ensures your SD card works correctly with your 3DS.")
        await ctx.send(embed=embed)

    @command_with_perms(name="donors", help="How to donate consoles for SOAPs")
    async def donors(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🎁 Donating Consoles for SOAPs",
            description=(
                "**Ideal donor consoles should:**\n"
                "• Be in a state where they won't be used anymore (won't turn on, bad screens, bad RAM, etc.), or\n"
                "• Have a bad WiFi card, or\n"
                "• Have had the eShop apps (`tiger`, `mint`) deleted off the NAND so it can't connect to the eShop\n\n"
                "⚠️ **Note:** Connecting a console to the eShop while it is also being used as a donor is known to cause various issues.\n\n"
                "**To donate a console for SOAPs, we need either:**\n"
                "• `essential.exefs` + serial, or\n"
                "• secinfo + OTP + serial\n\n"
                "You can send this information to any Staff or Soaper. Thank you for your contribution! 🙏"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Donor consoles help make SOAP transfers possible for others.")
        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(TextCommandsCog(bot))
