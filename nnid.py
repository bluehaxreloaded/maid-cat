import discord
import asyncio
from perms import command_with_perms
from helpee import (
    channel_name_for,
    find_open_channel,
    restore_helpee_access,
    member_from_topic,
    sync_helpee_role,
)
from exceptions import CategoryNotFound
from log import log_to_soaper_log
from discord.ext import commands
from discord.ext.bridge import BridgeOption
from constants import (
    NNID_CHANNEL_SUFFIX,
    BOOM_EMOTE_ID,
    NNID_CHANNEL_CATEGORY_ID,
    HELPEE_ROLE_ID,
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
            "5. Insert your SD card into your PC or connect to your console via [FTPD](<https://wiki.hacks.guide/wiki/3DS:FTP>). If you do not have a PC available, ask us about a solution\n"
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
                description="It is currently after-hours for most of the staff members of this server, therefore response times may be longer than usual for initiating NNID transfers, providing help, or answering questions.\n\nIn the meantime, please follow all the instructions provided above. We'll assist you as soon as possible.",
                color=discord.Color(0xD50032),
            )
            (late_night_embed.set_footer(text="We appreciate your patience and understanding!"),)
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
        channel_name = channel_name_for(user, NNID_CHANNEL_SUFFIX)

        # Only check channels in the NNID category (exclude archived)
        existing_channel = find_open_channel(
            guild, user, [NNID_CHANNEL_CATEGORY_ID], NNID_CHANNEL_SUFFIX
        )

        if existing_channel:
            # they may have lost access by leaving and rejoining the server
            await restore_helpee_access(existing_channel, user)
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

            if HELPEE_ROLE_ID:
                try:
                    role = guild.get_role(HELPEE_ROLE_ID)
                    if role and role not in user.roles:
                        await user.add_roles(role)
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
        """Helper method to archive a NNID channel (revoke access, move to temp archive for deletion)."""
        soap_cog = self.bot.get_cog("SoapCog")
        if soap_cog:
            await soap_cog.archive_channel(channel, ctx, is_soap=False)
        else:
            # Fallback: delete immediately if SoapCog not available
            # Revoke helpee role, unless they still have another open SOAP/NNID channel
            member = await member_from_topic(channel)
            if member:
                await sync_helpee_role(member, moved={channel.id: None})
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
        min_role="Developer",
        name="creatennid",
        aliases=["nnid", "setupnnid", "createnn"],
        help="Sets up NNID channel",
    )
    async def creatennid(
        self,
        ctx,
        user: BridgeOption(discord.Member, "User to create an NNID channel for"),
    ):
        channel_name = channel_name_for(user, NNID_CHANNEL_SUFFIX)
        # Only open NNID channels count as existing (archived ones don't)
        channel = find_open_channel(
            ctx.guild, user, [NNID_CHANNEL_CATEGORY_ID], NNID_CHANNEL_SUFFIX
        )

        if channel:
            await ctx.respond(
                f"NNID channel already made for `{user.name}` at {channel.jump_url}"
            )
            # they may have lost access by leaving and rejoining the server
            await restore_helpee_access(channel, user)
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
            if HELPEE_ROLE_ID:
                try:
                    role = ctx.guild.get_role(HELPEE_ROLE_ID)
                    if role and role not in user.roles:
                        await user.add_roles(role)
                except Exception:
                    pass


def setup(bot):
    return bot.add_cog(NNIDCog(bot))
