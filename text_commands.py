import discord
import perms
from discord.ext import commands
from functools import wraps
from constants import SOAP_CHANNEL_SUFFIX


def ping_before_mes():  # i didn't feel like writing the same line multiple times so i did the harder option of writing an entire decorator to write one single line
    def decorator(func):
        @wraps(func)
        async def send_ping(self, ctx: commands.Context, *args, **kwargs):
            member_name = ctx.channel.name.removesuffix(SOAP_CHANNEL_SUFFIX)
            member_obj = ctx.guild.get_member_named(member_name)
            if member_obj:
                await ctx.send(
                    f"{member_obj.mention}\n\n{'\n\n'.join(await func(self, ctx, *args, **kwargs))}"
                )
            else:
                await ctx.send(f"User `{member_name}` left.")

        return send_ping

    return decorator


class TextCommandsCog(commands.Cog):  # temp until dynamic stuff is ready
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @perms.command_with_perms(
        min_role="Soaper",
        name="soapnormal",
        aliases=["normalsoap", "normal"],
        help="Displays normal SOAP completion message",
    )
    @perms.soap_channels_only()
    @ping_before_mes()
    async def soapnormal(self, ctx: commands.Context):
        return [
            "The SOAP Transfer has completed! Please boot up your console normally with the SD card inserted. Then go to `System Settings -> Other Settings -> Profile -> Region Settings` and ensure the desired country is selected. If using Pretendo, you must first open the Nimbus app and switch to Nintendo.\n\n",
            "Then try opening the eShop.",
            "A system transfer was required to do this SOAP. If you want to do a system transfer from your old console to this one, you must wait a week.\n\n",
            "Please let us know if the eshop functions or not.",
        ]

    @perms.command_with_perms(
        min_role="Soaper",
        name="soaplottery",
        aliases=["lotterysoap", "lottery"],
        help='Displays "lottery" SOAP completion message',
    )
    @perms.soap_channels_only()
    @ping_before_mes()
    async def soaplottery(self, ctx: commands.Context):
        return [
            "The SOAP Transfer has completed! Please boot up your console normally with the SD card inserted. Then go to `System Settings -> Other Settings -> Profile -> Region Settings` and ensure the desired country is selected. If using Pretendo, you must first open the Nimbus app and switch to Nintendo.\n\n",
            "Then try opening the eShop.\n\n",
            "You hit the SOAP lottery! No system transfer was needed for this SOAP. If you want to do a system transfer from your old console to this one, you can do it right away.\n\n",
            "Please let us know if the eshop functions or not.",
        ]

    @perms.command_with_perms(
        min_role="Soaper",
        name="findserial",
        aliases=["serial", "serialmismatch"],
        help="Explains how to find a serial number in GM9",
    )
    @perms.soap_channels_only()
    @ping_before_mes()
    async def findserial(self, ctx: commands.Context):
        return [
            "To find the serial number, hold START while powering on your console. This will boot you into GodMode9.\n\n",
            "Go to `SYSNAND TWLNAND` -> `sys` -> `log` -> `inspect.log`\n\n",
            "Select `Open in Textviewer`. Send a picture of the serial number contained in the file. It should be a three-letter prefix followed by nine numbers.",
        ]

    @perms.command_with_perms(min_role="Soaper", name="soapwait", aliases=["wait"])
    @perms.soap_channels_only()
    @ping_before_mes()
    async def soapwait(self, ctx: commands.Context):
        blobsoap = discord.utils.get(ctx.guild.emojis, name="blobsoap")
        return [
            f"{blobsoap} the SOAP process has begun and will take up to 5 minutes. Please wait. {blobsoap}"
        ]

    @perms.command_with_perms(name="removennid", aliases=["nnidremove"])
    async def removennid(self, ctx: commands.Context):
        await ctx.send()

    @perms.command_with_perms(name="hacksguide", aliases=["guide"])
    async def hacksguide(self, ctx: commands.Context):
        await ctx.send(
            "For modding help and 3DS support please visit 3DS Hacks Guide:\n\n"
            "<https://3ds.hacks.guide/>"
        )

    @perms.command_with_perms(name="regionchange")
    async def regionchange(self, ctx: commands.Context):
        await ctx.send(
            "Region changing guide:\n\n<https://3ds.hacks.guide/region-changing.html>"
        )

    @perms.command_with_perms(name="nandbackup", aliases=["backupnand"])
    async def nandbackup(self, ctx: commands.Context):
        await ctx.send(
            "How to create a nand backup:\n\n"
            "<https://3ds.hacks.guide/godmode9-usage.html#creating-a-nand-backup>"
        )

    @perms.command_with_perms(name="cleaninty")
    async def cleaninty(self, ctx: commands.Context):
        await ctx.send(
            "See the following for an overview on how SOAP Transfers work:\n\n"
            "https://wiki.hacks.guide/wiki/3DS:Cleaninty"
        )

    @perms.command_with_perms(min_role="Soaper", name="nodonors")
    @perms.soap_channels_only()
    @ping_before_mes()
    async def nodonors(self, ctx: commands.Context):
        return [
            "Whoops, all of our donors are on cooldown, we’ll get back to you as soon as possible."
        ]


def setup(bot):
    bot.add_cog(TextCommandsCog(bot))
