import discord
import asyncio
from perms import command_with_perms
from exceptions import CategoryNotFound
from log import log_to_soaper_log
from discord.ext import commands
from discord.ext.bridge import BridgeOption
from constants import (
    NNID_CHANNEL_SUFFIX,
    BOOM_EMOTE_ID,
    NNID_CHANNEL_CATEGORY_ID,
    is_late_night_hours,
)


class NNIDCog(commands.Cog):  # NNID commands
    def __init__(self, bot):
        self.bot = bot

    async def create_nnid_interface(self, channel, user):
        """Create the welcome embed for new NNID channels"""
        # Welcome embed
        embed = discord.Embed(
            title="🔄 Welcome to your NNID Channel!",
            description="This is where we'll perform your NNID transfer. To get started, please follow the instructions below:\n\n"
            "**📋 Step-by-Step Instructions**\n"
            "1. Ensure your SD card is in your target console (the console you want to transfer to)\n"
            "2. Hold START while powering on the console. This should boot you into GodMode9.\n"
            "   - If you reach the Luma3DS chainloader, select GodMode9 to continue (the red text is the selected option)\n"
            "   - If you reach the HOME menu or GodMode9 is not listed in the chainloader, GodMode9 is not installed. Please redo [Finalizing Setup](https://3ds.hacks.guide/finalizing-setup)\n"
            "3. Navigate to `[S:] SYSNAND Virtual` → `essential.exefs` → `Copy to 0:/gm9/out` (select `Overwrite file(s)` if prompted)\n"
            "4. Power off your console\n"
            "5. Insert your SD card into your PC or connect to your console via FTPD\n"
            "6. Navigate to `/gm9/out/`, where `essential.exefs` should be located\n"
            "7. Rename the `essential.exefs` file to `TARGET_essential.exefs` and upload it to this channel\n"
            "8. Provide your source console's serial number below if possible\n"
            "9. Provide your target console's serial number below\n"
            "10. Locate your previous console's `essential.exefs`\n"
            "    - If you only have a NAND backup or a `SecureInfo_A` or `SecureInfo_B` file with an OTP file, let us know and we will provide further instructions.\n"
            "    - If you cannot find an essential.exefs, NAND backup, or SecureInfo file with an OTP file, we unfortunately cannot perform the NNID transfer. Please try your best to find one of these files, let us know if you need help.\n"
            "11. Rename the file to `SOURCE_essential.exefs` and upload it to this channel\n"
            "12. Please wait for someone to assist you",
            color=discord.Color.orange(),
        )
        # Send with mention
        await channel.send(content=user.mention, embed=embed)
        
        # Send late night delay warning if applicable
        if is_late_night_hours():
            late_night_embed = discord.Embed(
                title="🌕 After Hours Notice",
                description="It's currently late at night in North America, so most of our Soapers are offline. Response times may be slower than usual. Please follow the instructions above and we'll assist you as soon as possible.\n\n",
                color=discord.Color(0xD50032),
            )
            late_night_embed.set_footer(text="Thank you for your patience!"),
            await channel.send(embed=late_night_embed)

    async def create_nnid_channel_for_user(
        self,
        guild: discord.Guild,
        user: discord.Member,
        requester: discord.Member = None,
        ctx: commands.Context | discord.Interaction = None,
    ):
        """
        Helper function to create a NNID channel.
        Returns tuple: (success: bool, channel: discord.TextChannel | None, message: str)
        """
        # strip leading/trailing periods and then replace remaining periods with dashes
        safe_user_name = user.name.lstrip(".").rstrip(".").lower().replace(".", "-")
        channel_name = safe_user_name + NNID_CHANNEL_SUFFIX
        existing_channel = None

        # only check channels in the NNID categories
        for channel in guild.text_channels:
            if channel.name == channel_name:
                # check if it's in either NNID category
                if channel.category and channel.category.id in [
                    NNID_CHANNEL_CATEGORY_ID,
                ]:
                    existing_channel = channel
                    break

        if existing_channel:
            return (
                False,
                existing_channel,
                f"NNID channel already made for `{user.name}`",
            )

        category = discord.utils.get(guild.categories, id=NNID_CHANNEL_CATEGORY_ID)
        if not category:
            return False, None, "NNID category not found"

        try:
            new_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                topic=f"This is the NNID channel for <@{user.id}>, please follow all provided instructions.",
            )

            await new_channel.set_permissions(user, read_messages=True)

            await self.create_nnid_interface(new_channel, user)

            if ctx:
                try:
                    await log_to_soaper_log(ctx, "Created NNID Channel")
                except Exception:
                    pass

            return True, new_channel, "Channel created successfully"

        except Exception as e:
            return False, None, f"Error creating channel: {str(e)}"

    async def deletennid(
        self,
        channel: discord.TextChannel,
        ctx: commands.Context | discord.Interaction = None,
    ):
        """Helper method to delete a NNID channel with boom effect"""
        await channel.send("Self-destruct sequence initiated!")
        await channel.send(f"<a:boomparrot:{BOOM_EMOTE_ID}>")
        await asyncio.sleep(2.75)
        await channel.delete()
        if ctx:
            try:
                await log_to_soaper_log(ctx, "Removed NNID Channel")
            except Exception:
                pass

    @command_with_perms(
        min_role="Soaper",
        name="creatennid",
        aliases=["nnid", "setupnnid", "createnn"],
        help="Sets up NNID channel",
    )
    async def creatennid(
        self,
        ctx,
        user: BridgeOption(discord.Member, "User to create an NNID channel for"),
    ):
        channel_name = (
            user.name.lower().replace(".", "-") + NNID_CHANNEL_SUFFIX
        )  # channels can't have periods
        channel = discord.utils.get(ctx.guild.channels, name=channel_name)

        if channel:
            await ctx.respond(
                f"NNID channel already made for `{user.name}` at {channel.jump_url}"
            )
        else:
            category = discord.utils.get(
                ctx.guild.categories, id=NNID_CHANNEL_CATEGORY_ID
            )
            if category:
                new = await ctx.guild.create_text_channel(
                    name=channel_name,
                    category=category,
                    topic=f"This is the NNID channel for <@{user.id}>, please follow all provided instructions.",
                )
            else:
                raise CategoryNotFound(NNID_CHANNEL_CATEGORY_ID)

            await new.set_permissions(user, read_messages=True)
            await self.create_nnid_interface(new, user)
            await ctx.respond(new.jump_url)
            await log_to_soaper_log(ctx, "Created NNID Channel")


def setup(bot):
    return bot.add_cog(NNIDCog(bot))
