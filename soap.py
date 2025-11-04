import discord
import asyncio
from perms import command_with_perms
from exceptions import CategoryNotFound
from log import log_to_soaper_log
from discord.ext import commands
from constants import (
    SOAP_CHANNEL_SUFFIX,
    BOOM_EMOTE_ID,
    SOAP_CHANNEL_CATEGORY_ID,
    MANUAL_SOAP_CATEGORY_ID,
)


class SoapCog(commands.Cog):  # SOAP commands
    def __init__(self, bot):
        self.bot = bot

    async def create_soap_channel_for_user(
        self,
        guild: discord.Guild,
        user: discord.Member,
        requester: discord.Member = None,
        ctx: commands.Context | discord.Interaction = None,
    ):
        """
        Helper function to create a SOAP channel.
        Returns tuple: (success: bool, channel: discord.TextChannel | None, message: str)
        """
        # strip leading/trailing periods and then replace remaining periods with dashes
        safe_user_name = user.name.lstrip(".").rstrip(".").lower().replace(".", "-")
        channel_name = safe_user_name + SOAP_CHANNEL_SUFFIX
        existing_channel = None

        # oly check channels in the SOAP categories
        for channel in guild.text_channels:
            if channel.name == channel_name:
                # check if it's in either SOAP category
                if channel.category and channel.category.id in [
                    SOAP_CHANNEL_CATEGORY_ID,
                    MANUAL_SOAP_CATEGORY_ID,
                ]:
                    existing_channel = channel
                    break

        if existing_channel:
            return (
                False,
                existing_channel,
                f"Soap channel already made for `{user.name}`",
            )

        category = discord.utils.get(guild.categories, id=SOAP_CHANNEL_CATEGORY_ID)
        if not category:
            return False, None, "SOAP category not found"

        try:
            new_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                topic=f"This is the SOAP channel for <@{user.id}>, please follow all provided instructions.",
            )

            await new_channel.set_permissions(user, read_messages=True)

            soap_automation_cog = self.bot.get_cog("SOAPAutomationCog")
            if soap_automation_cog:
                await soap_automation_cog.create_soap_interface(new_channel, user)
            else:
                await new_channel.send(
                    f"{user.mention}\n"
                    "# Welcome!\n\n\n"
                    "This is where we'll perform your SOAP transfer. Please follow the instructions below\n\n"
                    "1. Ensure your SD card is in your console\n"
                    "2. Hold START while powering on your console. This will boot you into GM9\n"
                    "3. Navigate to `SysNAND Virtual`\n"
                    "4. Select `essential.exefs`\n"
                    "5. Select `copy to 0:/gm9/out` (select `Overwrite file(s)` if prompted)\n"
                    "6. Power off your console\n"
                    "7. Insert your SD card into your PC or connect to your console via FTPD\n"
                    "8. Navigate to `/gm9/out` on your SD, where `essential.exefs` should be located\n"
                    "9. Send the `essential.exefs` file to this chat as well as your serial number from your console. The serial number should be a three-letter prefix followed by nine numbers.\n"
                    "10. Please wait for a Soaper to assist you\n"
                )

            if ctx:
                try:
                    await log_to_soaper_log(ctx, "Created SOAP Channel")
                except Exception:
                    pass

            return True, new_channel, "Channel created successfully"

        except Exception as e:
            return False, None, f"Error creating channel: {str(e)}"

    async def deletesoap(
        self, channel: discord.TextChannel, ctx: commands.Context | discord.Interaction = None
    ):
        """Helper method to delete a SOAP channel with boom effect"""
        await channel.send("Self-destruct sequence initiated!")
        await channel.send(f"<a:boomparrot:{BOOM_EMOTE_ID}>")
        await asyncio.sleep(2.75)
        await channel.delete()
        if ctx:
            try:
                await log_to_soaper_log(ctx, "Removed SOAP Channel")
            except Exception:
                pass

    # Leaving this for Manual SOAPs.
    @command_with_perms(
        min_role="Soaper",
        name="createsoap",
        aliases=["soup", "setupsoap", "soap", "siap", "setupsoup", "createsoup"],
        help="Sets up SOAP channel",
    )
    async def createsoap(
        self, ctx: commands.Context, user: discord.Member | int | str
    ):  # Creates soup channel
        if not isinstance(user, discord.Member):
            await ctx.send("User not in server or does not exist!")
            return

        channel_name = (
            user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX
        )  # channels can't have periods
        channel = discord.utils.get(ctx.guild.channels, name=channel_name)

        if channel:
            await ctx.send(
                f"Soap channel already made for `{user.name}` at {channel.jump_url}"
            )
        else:
            category = discord.utils.get(
                ctx.guild.categories, id=MANUAL_SOAP_CATEGORY_ID
            )
            if category:
                new = await ctx.guild.create_text_channel(
                    name=channel_name,
                    category=category,
                    topic=f"This is the SOAP channel for <@{user.id}>, please follow all provided instructions.",
                )
            else:
                raise CategoryNotFound(MANUAL_SOAP_CATEGORY_ID)

            await new.set_permissions(user, read_messages=True)
            await new.send(
                f"{user.mention}\n"
                "# Welcome!\n\n\n"
                "Make sure your console is modded and region changed first.\n\n"
                "1. Ensure your SD card is in your console\n"
                "2. Hold START while powering on your console. This will boot you into GM9\n"
                "3. Navigate to `SysNAND Virtual`\n"
                "4. Select `essential.exefs`\n"
                "5. Select `copy to 0:/gm9/out` (select `Overwrite file(s)` if prompted)\n"
                "6. Power off your console\n"
                "7. Insert your SD card into your PC\n"
                "8. Navigate to `/gm9/out` on your SD, where `essential.exefs` should be located\n"
                "9. Send the `essential.exefs` file to this chat as well as your serial number from your console. The serial number should be a three-letter prefix followed by nine numbers.\n"
                "10. Please wait for further instructions\n"
            )
            await ctx.send(new.jump_url)
            await log_to_soaper_log(ctx, "Created SOAP Channel")

    @command_with_perms(
        min_role="Soaper",
        name="deletesoap",
        aliases=["water", "desoap", "unsoup", "spoon", "unsoap", "boom", "desoup"],
        help="Deletes SOAP channel",
    )
    async def deletesoap_command(
        self,
        ctx: commands.Context,
        user: discord.Member
        | discord.TextChannel
        | discord.VoiceChannel
        | int
        | str = None,
    ):  # you're never gonna guess what this one does
        match user:
            case discord.Member():
                channel_name = user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX
                channel = discord.utils.get(ctx.guild.channels, name=channel_name)
            case discord.TextChannel():
                channel = user
            case discord.VoiceChannel():
                channel = user
            case None:
                channel = ctx.channel
            case _:
                await ctx.send("User not in server or does not exist!")
                return

        if not channel:
            return await ctx.send(f"SOAP channel not found for `{user.name}`")
        elif not (
            channel.category.id == SOAP_CHANNEL_CATEGORY_ID
            and channel.name.endswith(SOAP_CHANNEL_SUFFIX)
            or channel.category.id == MANUAL_SOAP_CATEGORY_ID
        ):
            return await ctx.send(f"{channel.mention} is not a SOAP channel!")

        await self.deletesoap(channel, ctx)


def setup(bot):
    return bot.add_cog(SoapCog(bot))
