from pathlib import Path
import discord
import re
from perms import command_with_perms, soap_channels_only, nnid_channels_only
from discord.ext import commands
from discord.ext.bridge import BridgeOption
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
        async def send_ping(self, ctx, *args, **kwargs):
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
                    await ctx.respond(
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
                await ctx.respond(
                    f"{member_obj.mention}\n\n{'\n\n'.join(await func(self, ctx, *args, **kwargs))}"
                )
            elif ctx.channel.category and (ctx.channel.category.id in SOAP_USABLE_IDS or ctx.channel.category.id == NNID_CHANNEL_CATEGORY_ID):
                await ctx.respond(
                    f"`HELPEE MENTION HERE` (This is not a working channel)\n\n{'\n\n'.join(await func(self, ctx, *args, **kwargs))}"
                )
            else:
                await ctx.respond(f"User `{member_name}` left.")

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
    async def soapnormal(self, ctx):
        embed = discord.Embed(
            title="🎉 SOAP Transfer Complete",
            description=(
                "Please follow the following steps to verify that everything is working correctly:\n\n"
                "**1.** Boot the console normally (with the SD inserted into the console)\n"
                "**2.** Then go to: **System Settings** → **Other Settings** → **Profile** → **Region Settings**\n"
                "and ensure the desired country is selected.\n"
                "**3.** If using Pretendo, switch to Nintendo Network with Nimbus.\n"
                "**4.** Then try opening the eShop.\n"
                "**5.** Check whether the eShop launches successfully. If so, you're done!"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=(
                "⚠️ If you want to system transfer from another 3DS, you must wait 7 days.\n"
                "Otherwise, you're free to use your console as normal."
            )
        )

        await ctx.respond(embed=embed)

    @command_with_perms(
        min_role="Soaper",
        name="soaplottery",
        aliases=["lotterysoap", "lottery"],
        help='Displays "lottery" SOAP completion message',
    )
    @soap_channels_only()
    async def soaplottery(self, ctx):
        embed = discord.Embed(
            title="🎉 SOAP Transfer Complete",
            description=(
                "You won the Soap Lottery! Please follow the following steps to verify that everything is working correctly:\n\n"
                "**1.** Boot the console normally (with the SD inserted into the console)\n"
                "**2.** Then go to: **System Settings** → **Other Settings** → **Profile** → **Region Settings**\n"
                "and ensure the desired country is selected.\n"
                "**3.** If using Pretendo, switch to Nintendo Network with Nimbus.\n"
                "**4.** Then try opening the eShop.\n"
                "**5.** Check whether the eShop launches successfully. If so, you're done!"
            ),
            color=discord.Color.yellow(),
        )
        embed.set_footer(
            text="No system transfer was needed - you can transfer from another 3DS right away if you want!"
        )

        await ctx.respond(embed=embed)

    @command_with_perms(
        min_role="Soaper",
        name="findserial",
        aliases=["serialmismatch"],
        help="Explains how to find a serial number in GM9",
    )
    async def findserial(self, ctx):
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
                "- Go to `[2:] SYSNAND TWLN` -> `sys` -> `log` -> `inspect.log`\n"
                "- Select `Open in Textviewer`.\n\n"
                "The correct serial number (three-letter prefix followed by nine numbers) should be in the file. "
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="You may also send us a picture if you're unsure.")
        
        # Send with user mention if found
        if member_obj:
            await ctx.respond(content=member_obj.mention, embed=embed)
        elif ctx.channel.category and (ctx.channel.category.id in SOAP_USABLE_IDS or ctx.channel.category.id == NNID_CHANNEL_CATEGORY_ID):
            await ctx.respond(content="`HELPEE MENTION HERE` (This is not a working channel)", embed=embed)
        else:
            await ctx.respond(embed=embed)

    @command_with_perms(
        min_role="Soaper",
        name="soapwait",
        aliases=["wait", 'aait'],
        help="Claiming a soap channel, for soapers",
    )
    @soap_channels_only()
    async def soapwait(self, ctx):
        from discord.ext import commands as _commands_mod

        if not isinstance(ctx, _commands_mod.Context) and hasattr(ctx, "respond"):
            try:
                await ctx.respond("✅ Sent to channel!", ephemeral=True)
            # Better safe than sorry
            except TypeError:
                await ctx.respond("✅ Sent to channel!")

        await ctx.channel.send(
            f"{discord.utils.get(ctx.guild.emojis, id=BLOBSOAP_EMOTE_ID)} the SOAP process has begun and will take up to 5 minutes. Please wait. {discord.utils.get(ctx.guild.emojis, id=BLOBSOAP_EMOTE_ID)}"
        )
        sticker = discord.utils.get(ctx.guild.stickers, id=SOAP_LOADING_ID)
        if sticker:
            await ctx.channel.send(content="", stickers=[sticker])

    @command_with_perms(
        min_role="Soaper",
        name="nnidcomplete",
        aliases=["nniddone", "nnidtransfercomplete"],
        help="Displays NNID transfer completion message",
    )
    @nnid_channels_only()
    async def nnidcomplete(self, ctx):
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
        if not member_obj:
            member_name = ctx.channel.name.removesuffix(NNID_CHANNEL_SUFFIX)
            member_obj = ctx.guild.get_member_named(member_name)

        embed = discord.Embed(
            title="🔄 NNID Transfer Complete",
            description=(
                "Your Nintendo Network ID has been successfully transferred to your target console. "
                "Please follow these steps to verify everything is working:\n\n"
                "**1.** Boot the target console normally\n"
                "**2.** Go to System Settings → Nintendo Network ID Settings\n"
                "**3.** Let us know if you can log into your Nintendo Network ID.\n"
                "**4.** Try opening the eShop and confirm your titles are available in Redownloadable Software."
                "**5.** If everything is working correctly, you're done!"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Let a Soaper know if you're all set or if you have any questions.")
        if member_obj:
            await ctx.respond(content=member_obj.mention, embed=embed)
        else:
            await ctx.respond(embed=embed)

        # Increment NNID counter
        tracker_cog = self.bot.get_cog("TrackerCog")
        if tracker_cog:
            tracker_cog.increment_nnid_count()
            await tracker_cog.update_trackers(ctx.guild)

    @command_with_perms(
        name="removennid", aliases=["nnidremove"], help="NNID Removal instructions"
    )
    async def removennid(self, ctx):
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
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="hacksguide", aliases=["guide"], help="Modding and 3DS help link"
    )
    async def hacksguide(self, ctx):
        embed = discord.Embed(
            title="📚 3DS Hacks Guide",
            description="For modding help and 3DS support, please visit the 3DS Hacks Guide:\n\n"
            "[**3ds.hacks.guide**](<https://3ds.hacks.guide/>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="This guide contains comprehensive instructions for modding your 3DS console.")
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="regionchange",
        help="Directions on performing a region change on a 3DS console",
    )
    async def regionchange(self, ctx):
        embed = discord.Embed(
            title="🌍 Region Changing Guide",
            description="Learn how to perform a region change on your 3DS console:\n\n"
            "[**Region Changing Guide**](<https://3ds.hacks.guide/region-changing.html>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Follow the guide carefully to change your console's region.")
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="nandbackup",
        aliases=["backupnand"],
        help="Directions on creating a nand backup",
    )
    async def nandbackup(self, ctx):
        embed = discord.Embed(
            title="💾 Creating a NAND Backup",
            description="Learn how to create a NAND backup using GodMode9:\n\n"
            "[**NAND Backup Guide**](<https://3ds.hacks.guide/godmode9-usage.html#creating-a-nand-backup>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Always create a NAND backup before making significant changes to your console.")
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="freshinstall",
        aliases=["minty", "mintyfresh", "fresh", "cleaninstall"],
        help="Instructions for a fresh CFW install",
    )
    async def freshinstall(self, ctx):
        embed = discord.Embed(
            title="🌿 Minty-Fresh CFW Install",
            description=(
                "Follow the steps below to make your console feel like new (with the latest CFW)!\n\n"
                "**1.** Verify your Luma version and [follow these directions to properly upgrade your CFW install.](<https://3ds.hacks.guide/checking-for-cfw>)\n"
                "**2.** [Format your SD card](<https://wiki.hacks.guide/wiki/Formatting_an_SD_card>).\n"
                "**3.** System format your 3DS: System Settings → Other Settings → Format System Memory\n"
                "**4.** Go through the console's initial setup process again.\n"
                "**5.** [Restore/upgrade your Luma installation](<https://3ds.hacks.guide/restoring-updating-cfw>).\n"
                "**6.** Complete the instructions in [Finalizing Setup](<https://3ds.hacks.guide/finalizing-setup>)."
            ),
            color=discord.Color.green(),
        )
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="transfer",
        aliases=["homebrewtransfer","systransfer", "keephomebrewapps"],
        help="Instructions to keep Homebrew apps after system transfer",
    )
    async def homebrewaftertransfer(self, ctx):
        embed = discord.Embed(
            title="📱 Keeping Homebrew Apps after System Transfer",
            description=(
                "**1.** Install CFW on the new console using [3ds.hacks.guide](<https://3ds.hacks.guide/>)\n"
                "**2.** Do a system transfer normally. Choose \"Don't use the guide\" then \"PC-based transfer\" if asked.\n"
                "**3.** On the new console, download faketik and place faketik.3dsx in the `/3ds` folder on your SD root.\n"
                "**4.** To access the Homebrew Launcher on the new console, follow [Manually entering Homebrew Launcher](<https://wiki.hacks.guide/wiki/3DS:Troubleshooting/manually_entering_homebrew_launcher>) under Other troubleshooting on the troubleshooting page.\n"
                "**5.** Once you are in the Homebrew Launcher, run faketik.\n"
                "**6.** Your Homebrew apps should appear on the homescreen!"
            ),
            color=discord.Color.red(),
        )
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="movesd",
        aliases=["movesdcard", "sdtransfer", "newsd"],
        help="Instructions to move data to a new SD card",
    )
    async def movesd(self, ctx):
        embed = discord.Embed(
            title="💾 Moving SD Cards",
            description="Moving SD cards on a 3DS is easy.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="1. Format the new SD card",
            value=(
                "First, ensure the new SD card is in the **FAT32** format.\n"
                "If it is not, follow the instructions here to format it:\n"
                "[Formatting an SD card](<https://wiki.hacks.guide/wiki/Formatting_an_SD_card>)"
            ),
            inline=False,
        )
        embed.add_field(
            name="2. Move your data",
            value=(
                "Once the new card is FAT32, move **all** your content from the old SD to the new SD.\n\n"
                "⚠️ **IMPORTANT:** Do not put the new SD card in the console before moving all your data to it."
            ),
            inline=False,
        )
        await ctx.respond(embed=embed)

    @command_with_perms(name="cleaninty", help="Sends link to cleaninty article")
    async def cleaninty(self, ctx):
        embed = discord.Embed(
            title="🧼 SOAP Transfers Overview",
            description="Learn about how SOAP Transfers work:\n\n"
            "[**Cleaninty Article**](<https://wiki.hacks.guide/wiki/3DS:Cleaninty>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="This article provides an overview of the SOAP transfer process.")
        await ctx.respond(embed=embed)

    @command_with_perms(
        min_role="Soaper",
        name="nodonors",
        help="Lets Helpee know they need to wait for a bit.",
    )
    @soap_channels_only()
    async def nodonors(self, ctx):
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
            await ctx.respond(content=member_obj.mention, embed=embed)
        elif ctx.channel.category and (ctx.channel.category.id in SOAP_USABLE_IDS or ctx.channel.category.id == NNID_CHANNEL_CATEGORY_ID):
            await ctx.respond(content="`HELPEE MENTION HERE` (This is not a working channel)", embed=embed)
        else:
            await ctx.respond(embed=embed)
    
    @command_with_perms(
        min_role="Soaper",
        name="nocomputer",
        help="Sends essentialsubmit instructions",
    )
    @soap_channels_only()
    async def nocomputer(self, ctx):
        try:
            path = Path(__file__).parent / "assets" / "essential-3dsx.webp"
            file = discord.File(fp=path, filename="essential-3dsx.webp")
            embed = discord.Embed(
                title="💻 Submitting essential.exefs without a computer",
                description=(
                    "**1.** Open FBI and navigate to `Remote Install` → `Scan QR Code`\n"
                    "**2.** Scan the QR code below with the camera and press A to install.\n"
                    "**3.** After it is installed, close FBI.\n"
                    "**4.** Open the Homebrew Launcher.\n"
                    "**5.** Select essentialsubmit from the list of applications.\n"
                    "**6.** Press Y and type in your Discord username, then press OK.\n"
                    "**7.** Select the large :soap: icon.\n"
                    "**8.** Let us know when it has been submitted.\n"
                    "**9.** After we confirm you submitted properly, you can safely delete essentialsubmit.3dsx from the 3ds folder on your SD card."
                ),
                color=discord.Color.blue(),
            )
            embed.set_image(url="attachment://essential-3dsx.webp")
            embed.set_footer(
                text="If you have questions or issues, let us know. "
            )
            await ctx.respond(file=file, embed=embed)
        except FileNotFoundError as e:
            print(f"Error: Could not find assets/essential-3dsx.webp - {e}")
            await ctx.respond("Could not get essentialsubmit QR code.")

    @command_with_perms(
        name="cfwupdate",
        aliases=["cfwrestore", "update", "emptysd"],
        help="Restoring or updating CFW / lost SD card contents",
    )
    async def cfwupdate(self, ctx):
        embed = discord.Embed(
            title="🔄 Restoring / Updating CFW",
            description=(
                "If you need to update your 3DS CFW installation, or you have lost the contents of your SD card, "
                "please follow the directions on the 3DS Hacks Guide "
                "[Restoring / Updating CFW](<https://3ds.hacks.guide/restoring-updating-cfw.html>) page."
            ),
            color=discord.Color.blue(),
        )
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="macaddress",
        aliases=["mac", "3dsmac"],
        help="Instructions to find the 3DS MAC address",
    )
    async def mac(self, ctx):
        embed = discord.Embed(
            title="📶 3DS MAC Address Location",
            description=(
                "**System Settings** → **Internet Settings** → **Other Information** → **Confirm MAC Address**"
            ),
            color=discord.Color.blue(),
        )
        await ctx.respond(embed=embed)

    @command_with_perms(
        name="formatsd", aliases=["format", "sdformat"], help="SD formatting guide"
    )
    async def formatsd(self, ctx):
        embed = discord.Embed(
            title="💾 Formatting SD Card for 3DS",
            description="Learn how to format an SD card correctly for your 3DS console:\n\n"
            "[**SD Card Formatting Guide**](<https://3ds.hacks.guide/formatting-sd-(windows).html>)",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Proper formatting ensures your SD card works correctly with your 3DS.")
        await ctx.respond(embed=embed)

    @command_with_perms(name="donors", help="How to donate consoles for SOAPs")
    async def donors(self, ctx):
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
        await ctx.respond(embed=embed)

    @command_with_perms(
        allowed_roles=["Developer", "Staff"],
        name="say",
        aliases=["embed"],
        help="Send a custom embed. Use \\n in text fields for newlines.",
    )
    async def say(
        self,
        ctx,
        title: BridgeOption(str, "Embed title", required=False) = None,
        description: BridgeOption(str, "Embed description. Use \\n for newlines.", required=False) = None,
        color: BridgeOption(str, "Color: blue, green, red, orange, yellow, blurple", required=False) = None,
        footer: BridgeOption(str, "Footer text", required=False) = None,
        image: BridgeOption(str, "Image URL (large image at bottom)", required=False) = None,
        thumbnail: BridgeOption(str, "Thumbnail URL (small image top-right)", required=False) = None,
        author: BridgeOption(str, "Author name (top of embed)", required=False) = None,
        url: BridgeOption(str, "URL to make title clickable", required=False) = None,
        timestamp: BridgeOption(bool, "Add timestamp", required=False) = False,
    ):
        """Send a formatted embed. At least one of title or description is required."""
        if not title and not description:
            await ctx.respond("Provide at least a title or description.", ephemeral=True)
            return

        color_map = {
            "blue": discord.Color.blue(),
            "green": discord.Color.green(),
            "red": discord.Color.red(),
            "orange": discord.Color.orange(),
            "yellow": discord.Color.yellow(),
            "blurple": discord.Color.blurple(),
        }
        embed_color = discord.Color.blue()
        if color:
            color_lower = color.strip().lower()
            if color_lower in color_map:
                embed_color = color_map[color_lower]
            elif color_lower.startswith("#") and len(color_lower) == 7:
                try:
                    embed_color = discord.Color(int(color_lower[1:], 16))
                except ValueError:
                    pass
            elif color_lower.startswith("0x") and len(color_lower) <= 10:
                try:
                    embed_color = discord.Color(int(color_lower, 16))
                except ValueError:
                    pass

        def _nl(s: str) -> str:
            """Convert \\n in user input to actual newlines."""
            return s.replace("\\n", "\n") if s else s

        embed = discord.Embed(color=embed_color)
        if title:
            embed.title = _nl(title)
        if description:
            embed.description = _nl(description)
        if url:
            embed.url = url
        if footer:
            embed.set_footer(text=_nl(footer))
        if image:
            embed.set_image(url=image)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if author:
            embed.set_author(name=author)
        if timestamp:
            embed.timestamp = discord.utils.utcnow()

        try:
            await ctx.defer(ephemeral=True)
        except Exception:
            pass
        await ctx.channel.send(embed=embed)

    @command_with_perms(name="nnidwarning", help="Warning message for NNIDTransfers")
    async def nnidwarning(self, ctx):
        embed = discord.Embed(
            title="⚠️ NNID Transfer Warning",
            description=(
                "Nintendo of America no longer assists with unlinking NNIDs. Due to this, we provide NNID Transfers for consoles that cannot perform a system transfer normally.\n\n"
                "Performing NNID transfers via the process used by us in this server is experimental.\n\n"
                "While there have no been no proven cases of any NNIDs being lost during this process, using our services for transferring NNIDs should nevertheless be considered a **last resort** and you should perform a system transfer if able.\n\n"
                "__By continuing, you acknowledge that there may be a chance that you will lose access to your NNID and/or other eShop services.__\n\n"
                "Please tell us if you would like to continue."
            ),
            color=discord.Color.yellow(),
        )
        await ctx.respond(embed=embed)
    
    @command_with_perms(
        min_role="Soaper",
        name="updateessential",
        help="How to update embedded essential.exefs backup"
    )
    async def updateessential(self, ctx):
        embed = discord.Embed(
            title="💾 Update essential.exefs",
            description=(
                "1. Delete (or backup to your PC and then delete) all copies of `essential.exefs` from the `/gm9/out` folder on your SD card\n"
                "2. Insert the SD card back into the console.\n"
                "3. Hold START while powering on your console. This will boot you into GodMode9.\n"
                "4. Navigate to `[S:] SYSNAND VIRTUAL`, press A on `nand.bin` and select `NAND image options...` -> `Update embedded backup`\n"
                "5. Then go back to `[S:] SYSNAND VIRTUAL`, press A on `essential.exefs` and select `Copy to 0:/gm9/out`\n"
                "6. Power off your console\n"
                "7. Insert your SD card into your PC or connect to your console via FTPD\n"
                "8. Navigate to /gm9/out/, where essential.exefs should be located\n"
                "9. Upload the essential.exefs file and provide your serial number below\n"
                "10. Please wait for a Soaper to assist you"
            ),
            color=discord.Color.blue(),
        )
        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(TextCommandsCog(bot))
