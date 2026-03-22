import discord
import asyncio
import re
from datetime import datetime, timezone, timedelta
from perms import command_with_perms
from exceptions import CategoryNotFound
from log import log_to_soaper_log
from discord.ext import commands
from discord.ext.bridge import BridgeOption
from constants import (
    SOAP_CHANNEL_SUFFIX,
    BOOM_EMOTE_ID,
    SOAP_CHANNEL_CATEGORY_ID,
    MANUAL_SOAP_CATEGORY_ID,
    NNID_CHANNEL_SUFFIX,
    NNID_CHANNEL_CATEGORY_ID,
    TEMP_ARCHIVE_CATEGORY_ID,
    ARCHIVE_CHANNEL_SUFFIX,
    SOAP_LOG_ID,
    ERROR_LOG_ID,
)
from perms import _has_role_or_higher

# Topic format for archived channels: "Archived. Deletion scheduled: YYYY-MM-DD HH:MM:SS UTC. " + original
ARCHIVE_PREFIX = "Archived. Deletion scheduled: "
ARCHIVE_CHECK_INTERVAL = 300  # 5 minutes
ARCHIVE_EMBED_TITLE = "🗑️Archived Channel"
ARCHIVE_DELETION_REGEX = re.compile(
    r"Archived\.\s*Deletion scheduled:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*UTC\.\s*"
)


async def _send_to_log(
    guild: discord.Guild | None,
    channel_id: int | None,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
) -> bool:
    """Send a message to a log channel. Returns True if sent."""
    if not guild or not channel_id or (not content and not embed):
        return False
    ch = guild.get_channel(channel_id)
    if not ch:
        return False
    try:
        if embed:
            await ch.send(embed=embed)
        else:
            await ch.send(content)
        return True
    except Exception:
        return False


async def _try_log_soap(ctx, title: str) -> None:
    """Try to log to SOAP log; ignore failures."""
    if not ctx:
        return
    try:
        await log_to_soaper_log(ctx, title)
    except Exception:
        pass


async def _respond_ephemeral(ctx, message: str) -> bool:
    """Send an ephemeral response to ctx (Context or Interaction). Returns True if sent."""
    if not ctx:
        return False
    try:
        inter = getattr(ctx, "interaction", ctx)
        if hasattr(inter, "followup"):
            await inter.followup.send(message, ephemeral=True)
        elif hasattr(ctx, "respond"):
            await ctx.respond(message, ephemeral=True)
        elif hasattr(ctx, "send"):
            await ctx.send(message)
        else:
            return False
        return True
    except Exception:
        return False


def _get_user_id_from_topic(topic: str) -> int | None:
    """Extract user ID from topic (looks for <@id> mention). Returns None if not found."""
    m = re.search(r"<@!?(\d+)>", topic)
    return int(m.group(1)) if m else None


async def _get_channel_topic(channel: discord.TextChannel) -> str:
    """Get channel topic, using fetch if available (discord.py 2.x) else cache."""
    if hasattr(channel, "fetch"):
        try:
            ch = await channel.fetch()
            return (ch.topic or "").strip()
        except Exception:
            return (channel.topic or "").strip()
    return (channel.topic or "").strip()


async def _notify_and_delete(channel: discord.TextChannel, message: str) -> None:
    """Send message to channel then delete it. Ignores NotFound."""
    try:
        await channel.send(message)
        await channel.delete()
    except discord.NotFound:
        pass


async def _edit_channel_with_retry(channel, *, max_attempts=3, **edit_kwargs):
    """Edit channel with retry on transient failures (rate limit, server errors)."""
    last_error = None
    for attempt in range(max_attempts):
        try:
            await channel.edit(**edit_kwargs)
            return True
        except discord.NotFound:
            raise
        except (discord.HTTPException, discord.Forbidden) as e:
            last_error = e
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                await asyncio.sleep(wait)
    raise last_error


class ArchiveConfirmView(discord.ui.View):
    """Confirmation view for Delete early action."""

    def __init__(self, channel_id: int, guild_id: int, bot, timeout=60):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.bot = bot

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.NotFound:
            pass

    @discord.ui.button(label="Yes, I'm sure", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "This button is for a different server.", ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            await interaction.response.send_message(
                "Channel already deleted.", ephemeral=True
            )
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Confirmed, deleting channel...", view=self, embed=None
        )
        await asyncio.sleep(3)
        try:
            await channel.delete()
        except discord.NotFound:
            pass
        await _try_log_soap(interaction, "Deleted archived channel (early)")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Cancelled.", view=self, embed=None
        )


class ArchiveView(discord.ui.View):
    """View with Delete early button for archived channels."""

    def __init__(self, channel_id: int, guild_id: int, bot, timeout=None):
        super().__init__(timeout=None)  # Always None - buttons must never expire for persistence
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.bot = bot

    @discord.ui.button(
        label="Delete Now",
        style=discord.ButtonStyle.danger,
        emoji="🧹",
        custom_id="archive_delete",
    )
    async def delete_early(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Show confirmation before deleting the archived channel. Staff only."""
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Could not verify your role.", ephemeral=True)
            return
        staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
        if not staff_role or not _has_role_or_higher(interaction.user, staff_role):
            await interaction.response.send_message(
                "You must be Staff or higher to delete archived channels.", ephemeral=True
            )
            return
        if self.guild_id and interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "This button is for a different server.", ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(interaction.channel_id)
        if not channel:
            await interaction.response.send_message(
                "Channel already deleted.", ephemeral=True
            )
            return
        view = ArchiveConfirmView(channel.id, interaction.guild_id, self.bot)
        await interaction.response.send_message(
            "Are you sure you want to delete this channel?",
            ephemeral=True,
            view=view,
        )


class SoapCog(commands.Cog):  # SOAP commands
    def __init__(self, bot):
        self.bot = bot
        self._archive_checker_task = None
        self._next_archive_check_time: datetime | None = None

    def cog_load(self):
        """Start the periodic archive checker when the cog loads."""
        self._start_archive_checker()

    def _start_archive_checker(self):
        """Start the archive checker task if not already running."""
        if self._archive_checker_task is None or self._archive_checker_task.done():
            self._archive_checker_task = asyncio.create_task(self._archive_checker_loop())

    @commands.Cog.listener()
    async def on_ready(self):
        """Ensure archive checker is running."""
        self._start_archive_checker()

    def cog_unload(self):
        """Cancel the archive checker when the cog unloads."""
        if self._archive_checker_task and not self._archive_checker_task.done():
            self._archive_checker_task.cancel()

    async def _archive_checker_loop(self):
        """Periodically check archived channels and delete them."""
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._check_archived_channels()
            except asyncio.CancelledError:
                break
            except Exception as e:
                for g in self.bot.guilds:
                    if await _send_to_log(g, SOAP_LOG_ID, f"[Archive checker] Error: {e!r}"):
                        break
            self._next_archive_check_time = datetime.now(timezone.utc) + timedelta(seconds=ARCHIVE_CHECK_INTERVAL)
            await asyncio.sleep(ARCHIVE_CHECK_INTERVAL)

    async def _get_archived_channels_report(self) -> list[tuple[str, str, str, bool]]:
        """Return list of (guild_name, channel_name, deletion_time_str, qualifies_for_deletion) for each channel in archive category."""
        report = []
        if not TEMP_ARCHIVE_CATEGORY_ID:
            return report
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            temp_cat = discord.utils.get(guild.categories, id=TEMP_ARCHIVE_CATEGORY_ID)
            if not temp_cat:
                continue
            for channel in temp_cat.channels:
                if not isinstance(channel, discord.TextChannel):
                    continue
                topic = await _get_channel_topic(channel)
                match = ARCHIVE_DELETION_REGEX.search(topic)
                if not match:
                    report.append((guild.name, channel.name, "(no parseable deletion time in topic)", False))
                    continue
                deletion_str = match.group(1)
                try:
                    deletion_dt = datetime.strptime(
                        deletion_str, "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                    qualifies = deletion_dt <= now
                    report.append((guild.name, channel.name, f"{deletion_str} UTC", qualifies))
                except (ValueError, TypeError) as e:
                    report.append((guild.name, channel.name, f"({deletion_str!r} parse error: {e})", False))
        return report

    async def _delete_oldest_archived_channel(self, guild: discord.Guild) -> bool:
        """Delete the archive channel closest to its deletion time. Returns True if one was deleted."""
        if not TEMP_ARCHIVE_CATEGORY_ID:
            return False
        temp_cat = discord.utils.get(guild.categories, id=TEMP_ARCHIVE_CATEGORY_ID)
        if not temp_cat:
            return False
        oldest_channel = None
        oldest_dt = None
        for ch in temp_cat.channels:
            if not isinstance(ch, discord.TextChannel):
                continue
            topic = await _get_channel_topic(ch)
            match = ARCHIVE_DELETION_REGEX.search(topic)
            if not match:
                continue
            try:
                deletion_dt = datetime.strptime(
                    match.group(1), "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                if oldest_dt is None or deletion_dt < oldest_dt:
                    oldest_dt = deletion_dt
                    oldest_channel = ch
            except (ValueError, TypeError):
                continue
        if oldest_channel:
            try:
                await oldest_channel.delete()
                embed = discord.Embed(
                    title="Early-deleted archived channel (category full)",
                    description=f"#{oldest_channel.name}",
                    color=discord.Color.orange(),
                )
                embed.add_field(
                    name="Deletion was scheduled",
                    value=f"{oldest_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC",
                    inline=False,
                )
                await _send_to_log(guild, SOAP_LOG_ID, embed=embed)
                return True
            except Exception:
                pass
        return False

    async def _update_archive_category_name(self):
        """Rename TEMP_ARCHIVE_CATEGORY to 'AEP Archive [X]' where X = channel count - 1 (archive-info)."""
        if not TEMP_ARCHIVE_CATEGORY_ID:
            return
        for guild in self.bot.guilds:
            temp_cat = discord.utils.get(guild.categories, id=TEMP_ARCHIVE_CATEGORY_ID)
            if not temp_cat:
                continue
            text_channels = [c for c in temp_cat.channels if isinstance(c, discord.TextChannel)]
            count = len(text_channels) - 1  # Exclude #archive-info
            count = max(0, count)
            new_name = f"AEP Archive [{count}]"
            if temp_cat.name != new_name:
                try:
                    await temp_cat.edit(name=new_name)
                except Exception:
                    pass

    async def _check_archived_channels(self):
        """Delete channels in TEMP_ARCHIVE_CATEGORY whose deletion time has passed."""
        if not TEMP_ARCHIVE_CATEGORY_ID:
            return
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            temp_cat = discord.utils.get(guild.categories, id=TEMP_ARCHIVE_CATEGORY_ID)
            if not temp_cat:
                continue
            for channel in temp_cat.channels:
                if not isinstance(channel, discord.TextChannel):
                    continue
                topic = await _get_channel_topic(channel)
                match = ARCHIVE_DELETION_REGEX.search(topic)
                if not match:
                    continue
                try:
                    deletion_dt = datetime.strptime(
                        match.group(1), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                    if deletion_dt <= now:
                        try:
                            await channel.delete()
                            embed = discord.Embed(
                                title="Auto-deleted archived channel",
                                description=f"#{channel.name}",
                                color=discord.Color.orange(),
                            )
                            embed.add_field(
                                name="Deletion time",
                                value=f"{match.group(1)} UTC",
                                inline=False,
                            )
                            await _send_to_log(guild, SOAP_LOG_ID, embed=embed)
                        except discord.NotFound:
                            pass
                except (ValueError, TypeError):
                    continue
        await self._update_archive_category_name()

    async def archive_channel(
        self,
        channel: discord.TextChannel,
        ctx: commands.Context | discord.Interaction | None,
        is_soap: bool,
    ):
        """Archive a SOAP or NNID channel: revoke access, move to temp archive, schedule deletion in 7 days."""
        if hasattr(channel, "fetch"):
            try:
                channel = await channel.fetch()
            except Exception:
                pass
        try:
            await channel.send("Self-destruct sequence initiated!")
            await channel.send(f"<a:boomparrot:{BOOM_EMOTE_ID}>")
            await asyncio.sleep(2.75)
        except discord.NotFound:
            return

        topic = channel.topic or ""
        user_id = _get_user_id_from_topic(topic)
        if not user_id:
            await _notify_and_delete(channel, "Could not find user in channel topic. Deleting channel.")
            return

        deletion_time = datetime.now(timezone.utc) + timedelta(days=3)
        deletion_str = deletion_time.strftime("%Y-%m-%d %H:%M:%S")
        new_topic = f"{ARCHIVE_PREFIX}{deletion_str} UTC. {topic}"

        temp_category = (
            discord.utils.get(channel.guild.categories, id=TEMP_ARCHIVE_CATEGORY_ID)
            if TEMP_ARCHIVE_CATEGORY_ID
            else None
        )
        if not temp_category:
            await _notify_and_delete(
                channel, "TEMP_ARCHIVE_CATEGORY_ID not configured. Deleting channel."
            )
            return

        try:
            target = channel.guild.get_member(user_id) or discord.Object(id=user_id)
            await channel.set_permissions(target, overwrite=None)
        except Exception:
            pass  # e.g. no override to remove; continue with move

        # Move channel and set topic
        # Append archive suffix: e.g. aidenkt-soap🧼 -> aidenkt-soap🧼-aep
        suffix = ARCHIVE_CHANNEL_SUFFIX if ARCHIVE_CHANNEL_SUFFIX.startswith("-") else "-" + ARCHIVE_CHANNEL_SUFFIX
        archive_name = channel.name.rstrip("-") + suffix

        try:
            if len(new_topic) > 1024:
                new_topic = new_topic[:1021] + "..."
            await _edit_channel_with_retry(
                channel, category=temp_category, topic=new_topic, name=archive_name
            )
        except discord.NotFound:
            return
        except Exception as e:
            err_str = str(e)
            retry_succeeded = False
            # Category full (50 channels): delete oldest archive channel and retry
            if "Maximum number of channels" in err_str or "50035" in err_str:
                if await self._delete_oldest_archived_channel(channel.guild):
                    try:
                        await _edit_channel_with_retry(
                            channel, category=temp_category, topic=new_topic, name=archive_name
                        )
                        retry_succeeded = True
                    except discord.NotFound:
                        return
                    except Exception as retry_err:
                        e = retry_err
                        err_str = str(e)
            if not retry_succeeded:
                err_msg = f"Failed to move channel to archive: {e}"
                try:
                    await channel.send(err_msg)
                except Exception:
                    pass
                await _respond_ephemeral(ctx, err_msg)
                if ERROR_LOG_ID:
                    err_embed = discord.Embed(
                        title="Archive channel failed",
                        description=str(e),
                        color=discord.Color.red(),
                    )
                    err_embed.add_field(name="Channel", value=channel.mention, inline=False)
                    await _send_to_log(channel.guild, ERROR_LOG_ID, embed=err_embed)
                return

        async def send_archive_message():
            await asyncio.sleep(2.5)  # Let category/topic edit propagate
            embed = discord.Embed(
                title=ARCHIVE_EMBED_TITLE,
                description=f"This channel has been archived and is scheduled for deletion.\n\nIt will be permanently deleted <t:{int(deletion_time.timestamp())}:R>.",
                color=discord.Color.orange(),
            )
            view = ArchiveView(channel.id, channel.guild.id, self.bot, timeout=None)
            for attempt in range(3):
                try:
                    ch = await self.bot.fetch_channel(channel.id)
                    await ch.send(embed=embed, view=view)
                    return
                except discord.NotFound:
                    return
                except (discord.HTTPException, discord.Forbidden) as e:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        await _send_to_log(
                            channel.guild, ERROR_LOG_ID,
                            f"Failed to send archive message to #{channel.name}: {e}",
                        )

        asyncio.create_task(send_archive_message())

        await self._update_archive_category_name()
        await _try_log_soap(ctx, "Archived SOAP Channel" if is_soap else "Archived NNID Channel")

    async def create_soap_channel_for_user(
        self,
        guild: discord.Guild,
        user: discord.Member,
        requester: discord.Member = None,
        ctx: commands.Context | discord.Interaction = None,
    ):
        """
        Helper function to create a manual SOAP channel.
        Returns tuple: (success: bool, channel: discord.TextChannel | None, message: str)
        """
        # strip leading/trailing periods and then replace remaining periods with dashes
        safe_user_name = user.name.lstrip(".").rstrip(".").lower().replace(".", "-")
        channel_name = safe_user_name + SOAP_CHANNEL_SUFFIX
        existing_channel = None

        # Only check channels in the SOAP categories (exclude archived)
        for channel in guild.text_channels:
            if channel.name == channel_name and channel.category:
                if channel.category.id in [
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
        """Helper method to archive a SOAP channel (revoke access, move to temp archive, delete in 7 days)."""
        await self.archive_channel(channel, ctx, is_soap=True)

    @command_with_perms(
        allowed_roles=["Developer", "Staff"],
        name="archivecheck",
        help="Report archived channels and when the next deletion pass will run",
    )
    async def archivecheck(self, ctx):
        """Developer/Staff only: manually trigger the archive checker and report all channels seen."""
        if hasattr(ctx, "defer"):
            try:
                await ctx.defer(ephemeral=True)
            except TypeError:
                await ctx.defer()
        await self._update_archive_category_name()
        report = await self._get_archived_channels_report()
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"**Archive check** (current time: {now_str} UTC)", ""]
        if self._next_archive_check_time:
            next_str = self._next_archive_check_time.strftime("%Y-%m-%d %H:%M:%S")
            delta = (self._next_archive_check_time - now).total_seconds()
            lines.append(f"Next deletion pass: {next_str} UTC (in {int(delta)}s)")
        else:
            lines.append("Next deletion pass: (checker not yet run)")
        lines.append("")
        if not report:
            lines.append("No channels in archive category.")
        else:
            for guild_name, ch_name, deletion_str, qualifies in report:
                status = "✓ QUALIFIES FOR DELETION" if qualifies else "— not yet"
                lines.append(f"• **#{ch_name}** ({guild_name}): {deletion_str} — {status}")
        msg = "\n".join(lines)
        if len(msg) > 2000:
            chunks = [msg[i : i + 1990] for i in range(0, len(msg), 1990)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    if hasattr(ctx, "respond"):
                        await ctx.respond(chunk, ephemeral=True)
                    else:
                        await ctx.send(chunk)
                else:
                    if hasattr(ctx, "followup"):
                        await ctx.followup.send(chunk, ephemeral=True)
                    else:
                        await ctx.send(chunk)
        else:
            if hasattr(ctx, "respond"):
                await ctx.respond(msg, ephemeral=True)
            else:
                await ctx.send(msg)

    @command_with_perms(
        min_role="Soaper",
        name="manualsoap",
        aliases=["manual"],
        help="Move SOAP channel to manual SOAP category",
    )
    async def manualsoap(
        self,
        ctx,
        user: BridgeOption(
            discord.Member,
            "User whose SOAP channel to move",
            required=False,
        ) = None,
        channel: BridgeOption(
            discord.TextChannel,
            "SOAP channel to move (defaults to current channel)",
            required=False,
        ) = None,
    ):
        """Move SOAP channel to manual category."""
        await self._move_soap_category(ctx, user, channel, MANUAL_SOAP_CATEGORY_ID, "manual")

    @command_with_perms(
        min_role="Soaper",
        name="autosoap",
        aliases=["auto"],
        help="Move SOAP channel to auto SOAP category",
    )
    async def autosoap(
        self,
        ctx,
        user: BridgeOption(
            discord.Member,
            "User whose SOAP channel to move",
            required=False,
        ) = None,
        channel: BridgeOption(
            discord.TextChannel,
            "SOAP channel to move (defaults to current channel)",
            required=False,
        ) = None,
    ):
        """Move SOAP channel to auto category."""
        await self._move_soap_category(ctx, user, channel, SOAP_CHANNEL_CATEGORY_ID, "auto")

    async def _move_soap_category(
        self,
        ctx,
        user: discord.Member | None,
        channel: discord.TextChannel | None,
        target_category_id: int,
        category_name: str,
    ):
        """Move a SOAP channel to the given category."""
        target_channel = channel
        if target_channel is None and user is not None:
            channel_name = user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX
            target_channel = discord.utils.get(ctx.guild.channels, name=channel_name)
        if target_channel is None:
            target_channel = ctx.channel

        if not target_channel:
            return await ctx.respond("Channel not found.", ephemeral=True)

        is_archived = (
            target_channel.category
            and TEMP_ARCHIVE_CATEGORY_ID
            and target_channel.category.id == TEMP_ARCHIVE_CATEGORY_ID
        )
        if is_archived:
            return await ctx.respond("Cannot move archived channels.", ephemeral=True)

        is_soap = (
            target_channel.category
            and (
                (target_channel.category.id == SOAP_CHANNEL_CATEGORY_ID and target_channel.name.endswith(SOAP_CHANNEL_SUFFIX))
                or target_channel.category.id == MANUAL_SOAP_CATEGORY_ID
            )
        )
        if not is_soap:
            return await ctx.respond(f"{target_channel.mention} is not a SOAP channel!", ephemeral=True)

        category = discord.utils.get(ctx.guild.categories, id=target_category_id)
        if not category:
            return await ctx.respond("Category not found.", ephemeral=True)

        if target_channel.category and target_channel.category.id == target_category_id:
            return await ctx.respond(f"Channel is already in the {category_name} category.", ephemeral=True)

        try:
            await _edit_channel_with_retry(target_channel, category=category)
            await ctx.respond(f"Moved {target_channel.mention} to {category_name} category.", ephemeral=True)
        except Exception as e:
            await ctx.respond(f"Failed to move channel: {e}", ephemeral=True)

    # Leaving this for Manual SOAPs.
    @command_with_perms(
        allowed_roles=["Developer", "Staff"],
        name="createsoap",
        aliases=["soup", "setupsoap", "soap", "siap", "setupsoup", "createsoup"],
        help="Sets up SOAP channel",
    )
    async def createsoap(
        self,
        ctx,
        user: BridgeOption(discord.Member, "User to create a SOAP channel for"),
    ):  # Creates soup channel

        channel_name = (
            user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX
        )  # channels can't have periods
        channel = discord.utils.get(ctx.guild.channels, name=channel_name)
        # Don't count archived channels as existing
        if channel and TEMP_ARCHIVE_CATEGORY_ID and channel.category and channel.category.id == TEMP_ARCHIVE_CATEGORY_ID:
            channel = None

        if channel:
            await ctx.respond(
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

            # Use the SOAPAutomationCog's interface so manual SOAPs get the same welcome embed
            soap_automation_cog = self.bot.get_cog("SOAPAutomationCog")
            if soap_automation_cog:
                await soap_automation_cog.create_soap_interface(new, user)
            else:
                # Fallback to the old text instructions if the automation cog isn't loaded
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
            await ctx.respond(new.jump_url)
            await log_to_soaper_log(ctx, "Created SOAP Channel")

    @command_with_perms(
        min_role="Soaper",
        name="deletechannel",
        aliases=["deletesoap", "desoap", "unsoup", "spoon", "unsoap", "desoup", "delnnid"],
        help="Deletes SOAP or NNID channel",
    )
    async def deletesoap_command(
        self,
        ctx,
        user: BridgeOption(
            discord.Member,
            "User whose SOAP/NNID channel should be deleted",
            required=False,
        ) = None,
        channel: BridgeOption(
            discord.TextChannel,
            "Specific SOAP/NNID channel to delete (defaults to current channel)",
            required=False,
        ) = None,
    ):
        await self._perform_deletechannel(ctx, user=user, channel=channel)

    async def _perform_deletechannel(
        self,
        ctx,
        user: discord.Member | None = None,
        channel: discord.TextChannel | None = None,
    ):
        """Shared implementation for deletechannel/boom/water."""
        # Determine target channel: explicit channel option, or derived from user, or current channel.
        target_channel = channel

        if target_channel is None and user is not None:
            # Try SOAP channel first based on user name
            soap_channel_name = user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX
            nnid_channel_name = user.name.lower().replace(".", "-") + NNID_CHANNEL_SUFFIX
            target_channel = discord.utils.get(ctx.guild.channels, name=soap_channel_name)
            if not target_channel:
                target_channel = discord.utils.get(ctx.guild.channels, name=nnid_channel_name)

        if target_channel is None:
            target_channel = ctx.channel

        channel = target_channel

        if not channel:
            return await ctx.respond("Channel not found for the given user/channel.")

        # Block .boom on archived channels first (use Delete early button instead)
        is_archived = (
            channel.category
            and TEMP_ARCHIVE_CATEGORY_ID
            and channel.category.id == TEMP_ARCHIVE_CATEGORY_ID
        )
        if is_archived:
            msg = "Cannot use .boom on an archived channel. Use the **Delete early** button in the channel instead."
            if hasattr(ctx, "respond"):
                await ctx.respond(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return
        
        # Check if it's a SOAP channel
        is_soap = (
            channel.category
            and (
                (channel.category.id == SOAP_CHANNEL_CATEGORY_ID and channel.name.endswith(SOAP_CHANNEL_SUFFIX))
                or channel.category.id == MANUAL_SOAP_CATEGORY_ID
            )
        )
        
        # Check if it's a NNID channel
        is_nnid = (
            channel.category
            and channel.category.id == NNID_CHANNEL_CATEGORY_ID
            and channel.name.endswith(NNID_CHANNEL_SUFFIX)
        )

        if not (is_soap or is_nnid):
            return await ctx.respond(f"{channel.mention} is not a SOAP or NNID channel!")
        
        # For slash/bridge invocations, acknowledge the interaction by deferring it
        if hasattr(ctx, "defer"):
            try:
                await ctx.defer(ephemeral=True)
            except TypeError:
                # Some contexts don't support 'ephemeral' kwarg; fall back to plain defer
                await ctx.defer()
        
        if is_soap:
            await self.deletesoap(channel, ctx)
        elif is_nnid:
            nnid_cog = self.bot.get_cog("NNIDCog")
            if nnid_cog:
                await nnid_cog.deletennid(channel, ctx)
            else:
                # Fall back error message in the channel if NNIDCog is missing
                await channel.send("Error: NNIDCog not found.")

    @command_with_perms(
        min_role="Soaper",
        name="boom",
        help="Deletes SOAP or NNID channel (alias of deletechannel)",
    )
    async def boom(
        self,
        ctx,
        user: BridgeOption(
            discord.Member,
            "User whose SOAP/NNID channel should be deleted",
            required=False,
        ) = None,
        channel: BridgeOption(
            discord.TextChannel,
            "Specific SOAP/NNID channel to delete (defaults to current channel)",
            required=False,
        ) = None,
    ):
        await self._perform_deletechannel(ctx, user=user, channel=channel)

    @command_with_perms(
        min_role="Soaper",
        name="water",
        help="Deletes SOAP or NNID channel (alias of deletechannel)",
    )
    async def water(
        self,
        ctx,
        user: BridgeOption(
            discord.Member,
            "User whose SOAP/NNID channel should be deleted",
            required=False,
        ) = None,
        channel: BridgeOption(
            discord.TextChannel,
            "Specific SOAP/NNID channel to delete (defaults to current channel)",
            required=False,
        ) = None,
    ):
        await self._perform_deletechannel(ctx, user=user, channel=channel)


def setup(bot):
    return bot.add_cog(SoapCog(bot))
