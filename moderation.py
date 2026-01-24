import discord
import asyncio
from datetime import datetime, timezone, timedelta
from discord.ext import commands
from perms import command_with_perms
from constants import JOIN_LEAVE_LOG_ID, SPAM_BOT_CHANNEL_ID, BAN_LOG_ID, MESSAGE_LOG_ID, RESTRICTED_ROLE_ID


def _format_account_age(created_at: datetime) -> str:
    """Return a rough human-readable age like '6 years, 8 months, 23 days'."""
    now = datetime.now(timezone.utc)
    delta = now - created_at

    # Approximate months/years
    days = delta.days
    years, days = divmod(days, 365)
    months, days = divmod(days, 30)

    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if days or not parts:
        parts.append(f"{days} day{'s' if days != 1 else ''}")

    return ", ".join(parts)


def _format_pst_time() -> str:
    """Format current time in PST as 'Today at 4:04PM' or '12/7/2025 at 4:04PM'."""
    pst = timezone(timedelta(hours=-8))  # PST is UTC-8
    now_pst = datetime.now(pst)
    
    # Format time (12-hour format with AM/PM)
    hour = now_pst.hour
    minute = now_pst.minute
    period = "AM" if hour < 12 else "PM"
    hour_12 = hour if hour <= 12 else hour - 12
    if hour_12 == 0:
        hour_12 = 12
    time_str = f"{hour_12}:{minute:02d}{period}"
    
    return f"Today at {time_str}"


def _format_timeout_duration(until: datetime) -> str:
    """Return a human-readable timeout length like '10 minutes' or '1 hour, 5 minutes'."""
    now = datetime.now(timezone.utc)
    # If already expired or invalid, just say expired
    if until <= now:
        return "expired"

    delta = until - now
    # Add 59 seconds so we effectively round UP to the next minute
    total_seconds = max(0, int(delta.total_seconds() + 59))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    return ", ".join(parts)


class ModerationCog(commands.Cog):
    """Basic moderation/utility events like join/leave logging and spam-bot handling."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_member_log(self, member: discord.Member, joined: bool):
        """Send a join/leave embed to the JOIN_LEAVE_LOG_ID channel."""
        guild = member.guild
        channel = guild.get_channel(JOIN_LEAVE_LOG_ID)
        if channel is None:
            return

        action = "Member Joined" if joined else "Member Left"
        color = discord.Color.green() if joined else discord.Color.red()

        embed = discord.Embed(color=color)
        embed.set_author(name=action, icon_url=member.display_avatar.url)

        # Mention + username
        embed.add_field(
            name="",
            value=f"{member.mention} {member}",
            inline=False,
        )

        # Account age
        age_str = _format_account_age(member.created_at)
        embed.add_field(name="Account Age", value=age_str, inline=False)

        # Footer with ID and timestamp (PST)
        timestamp = _format_pst_time()
        embed.set_footer(text=f"ID: {member.id} • {timestamp}")

        await channel.send(embed=embed)

    async def _log_mod_action(
        self,
        *,
        guild: discord.Guild,
        user: discord.Member | discord.User,
        action: str,
        moderator: discord.Member | discord.User | None = None,
        reason: str | None = None,
        source: str | None = None,
        timeout_until: datetime | None = None,
    ):
        """Log moderation actions (ban, kick, timeout, restrict, etc.) to the ban log channel."""
        if not BAN_LOG_ID:
            return

        log_channel = guild.get_channel(BAN_LOG_ID)
        if log_channel is None:
            return

        if action.lower() == "ban":
            title = "🔨 Member Banned"
            color = discord.Color.red()
        elif action.lower() == "unban":
            title = "✅ Member Unbanned"
            color = discord.Color.green()
        elif action.lower() == "timeout":
            title = "⏰ Member Timed Out"
            color = discord.Color.yellow()
        elif action.lower() == "untimeout":
            title = "✅ Timeout Removed"
            color = discord.Color.green()
        elif action.lower() == "restrict":
            title = "⛔ Member Restricted"
            color = discord.Color.red()
        elif action.lower() == "unrestrict":
            title = "✅ Restriction Removed"
            color = discord.Color.green()
        else:  # kick
            title = "🔨 Member Kicked"
            color = discord.Color.orange()

        embed = discord.Embed(
            title=title,
            color=color,
        )
        # Show who performed the action (or source, e.g., Honeypot)
        if source:
            author_name = source
            # For honeypot, use bot's avatar
            author_icon = self.bot.user.display_avatar.url if self.bot.user else None
        elif moderator:
            author_name = str(moderator)
            author_icon = moderator.display_avatar.url
        else:
            author_name = "Unknown"
            author_icon = self.bot.user.display_avatar.url if self.bot.user else None

        embed.set_author(name=author_name, icon_url=author_icon)
        embed.set_thumbnail(url=user.display_avatar.url)
        
        # User mention and username
        embed.add_field(
            name="",
            value=f"{user.mention} {user}",
            inline=False,
        )

        # Timeout length, if applicable
        if action.lower() == "timeout" and timeout_until is not None:
            length_str = _format_timeout_duration(timeout_until)
            embed.add_field(
                name="Timeout Length",
                value=length_str,
                inline=False,
            )
        
        # Footer with ID and timestamp (PST)
        timestamp = _format_pst_time()
        embed.set_footer(text=f"ID: {user.id} • {timestamp}")

         # Reason, if available
        if reason:
            embed.add_field(
                name="Reason",
                value=reason,
                inline=False,
            )

        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

    async def _ensure_spam_bot_info_message(self, guild: discord.Guild):
        """Ensure the spam bot channel has the info embed present."""
        if not SPAM_BOT_CHANNEL_ID:
            return

        channel = guild.get_channel(SPAM_BOT_CHANNEL_ID)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return

        info_title = "🍯 Honeypot"

        # Look for an existing info message from the bot
        message_exists = False
        try:
            async for msg in channel.history(limit=25):
                if (
                    msg.author == self.bot.user
                    and msg.embeds
                    and msg.embeds[0].title == info_title
                ):
                    message_exists = True
                    break
        except Exception:
            pass

        # If message doesn't exist, clear channel and send new one
        if not message_exists:
            # Clear the channel first
            try:
                await channel.purge()
            except Exception:
                # If purge fails, continue anyway to try sending the message
                pass

            # Send the info embed
            embed = discord.Embed(
                title=info_title,
                description=(
                    "This channel is to catch spam bots that flood the server with messages.\n\n"
                    "If you can see this channel, **do not send messages here.** "
                    "Messages sent in this channel will ban you from the server. If you are human, ignore this channel or remove it from your channel list."
                ),
                color=discord.Color.yellow(),
            )
            embed.set_footer(text="Do not type here, you will be banned.")

            try:
                await channel.send(embed=embed)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._send_member_log(member, joined=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._send_member_log(member, joined=False)
        # Also check if this was a kick and log it
        await self._maybe_log_kick(member)

    @commands.Cog.listener()
    async def on_ready(self):
        """On startup, ensure the spam bot info message exists in each guild."""
        for guild in self.bot.guilds:
            await self._ensure_spam_bot_info_message(guild)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """Log all ban actions (except honeypot, which is already logged explicitly)."""
        if not BAN_LOG_ID:
            return

        moderator = None
        reason = None

        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.ban, limit=5):
                if entry.target.id == user.id:
                    moderator = entry.user
                    reason = entry.reason
                    break
        except Exception:
            pass

        # Skip if this was the honeypot ban (already logged)
        if reason == "Spam bot auto-ban/unban":
            return

        await self._log_mod_action(
            guild=guild,
            user=user,
            action="ban",
            moderator=moderator,
            reason=reason,
            source=None,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        """Log all unban actions."""
        if not BAN_LOG_ID:
            return

        moderator = None
        reason = None

        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.unban, limit=5):
                if entry.target.id == user.id:
                    moderator = entry.user
                    reason = entry.reason
                    break
        except Exception:
            pass

        await self._log_mod_action(
            guild=guild,
            user=user,
            action="unban",
            moderator=moderator,
            reason=reason,
            source=None,
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Log timeout actions when a member's timeout status changes."""
        if not BAN_LOG_ID:
            return

        # Check if timeout status changed (Pycord uses communication_disabled_until for timeouts)
        before_timeout = getattr(before, "communication_disabled_until", None)
        after_timeout = getattr(after, "communication_disabled_until", None)

        # Timeout was applied
        if before_timeout is None and after_timeout is not None:
            moderator = None
            reason = None

            try:
                async for entry in after.guild.audit_logs(
                    action=discord.AuditLogAction.member_update, limit=10
                ):
                    if (
                        entry.target.id == after.id
                        and getattr(entry.after, "communication_disabled_until", None) is not None
                        and getattr(entry.before, "communication_disabled_until", None) is None
                    ):
                        moderator = entry.user
                        reason = entry.reason
                        break
            except Exception:
                pass

            await self._log_mod_action(
                guild=after.guild,
                user=after,
                action="timeout",
                moderator=moderator,
                reason=reason,
                source=None,
                timeout_until=after_timeout,
            )
        # Timeout was removed
        elif before_timeout is not None and after_timeout is None:
            moderator = None
            reason = None

            try:
                async for entry in after.guild.audit_logs(
                    action=discord.AuditLogAction.member_update, limit=10
                ):
                    if (
                        entry.target.id == after.id
                        and getattr(entry.after, "communication_disabled_until", None) is None
                        and getattr(entry.before, "communication_disabled_until", None)
                    ):
                        moderator = entry.user
                        reason = entry.reason
                        break
            except Exception:
                pass

            # Log as "untimeout"
            await self._log_mod_action(
                guild=after.guild,
                user=after,
                action="untimeout",
                moderator=moderator,
                reason=reason,
                source=None,
            )

    # Message edit/delete logs

    def _get_message_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """Return the message log channel, if configured."""
        if not MESSAGE_LOG_ID:
            return None
        channel = guild.get_channel(MESSAGE_LOG_ID)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Log message edits in the message log channel (Dyno-style)."""
        # Ignore DMs and bots
        if not after.guild or after.author.bot:
            return

        # No channel configured
        log_channel = self._get_message_log_channel(after.guild)
        if log_channel is None:
            return

        # Skip if content didn't actually change or we don't have old content
        if before.content == after.content:
            return

        # Build embed
        embed = discord.Embed(color=discord.Color.blurple())
        # Author is the user who edited the message
        embed.set_author(
            name=str(after.author),
            icon_url=after.author.display_avatar.url,
        )

        # One-line description with channel and jump link
        desc = f"Message edited in {after.channel.mention}"
        try:
            desc += f" • [Jump to Message]({after.jump_url})"
        except Exception:
            pass
        embed.description = desc

        before_text = before.content or "*no content*"
        after_text = after.content or "*no content*"

        # Truncate to avoid hitting field limits
        if len(before_text) > 1024:
            before_text = before_text[:1021] + "..."
        if len(after_text) > 1024:
            after_text = after_text[:1021] + "..."

        embed.add_field(name="Before", value=before_text, inline=False)
        embed.add_field(name="After", value=after_text, inline=False)

        timestamp = _format_pst_time()
        embed.set_footer(text=f"User ID: {after.author.id} • {timestamp}")

        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Log message deletions in the message log channel (Dyno-style)."""
        # Ignore DMs and bots
        if not message.guild or message.author.bot:
            return

        log_channel = self._get_message_log_channel(message.guild)
        if log_channel is None:
            return

        embed = discord.Embed(color=discord.Color.red())
        embed.set_author(
            name=str(message.author),
            icon_url=message.author.display_avatar.url,
        )

        content = message.content or "*no content*"
        if len(content) > 1024:
            content = content[:1021] + "..."

        first_line = (
            f"**Message sent by {message.author.mention} • Deleted in {message.channel.mention}**"
        )
        embed.description = f"{first_line}\n{content}"

        timestamp = _format_pst_time()
        embed.set_footer(
            text=f"Author: {message.author.id} | Message ID: {message.id} • {timestamp}"
        )

        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

    @command_with_perms(
        min_role="Developer",
        name="resethoneypot",
        help="Wipe the honeypot channel and resend the info embed. Developers only.",
    )
    async def reset_spam_bot_channel(self, ctx):
        """
        Wipe the honeypot channel and resend the info embed.
        Requires Developer role.
        """
        if not SPAM_BOT_CHANNEL_ID:
            return await ctx.respond(
                "SPAM_BOT_CHANNEL_ID is not configured in constants.py.", ephemeral=True
            )

        channel = ctx.guild.get_channel(SPAM_BOT_CHANNEL_ID)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return await ctx.respond(
                "Spam bot channel not found or is not a text channel.", ephemeral=True
            )

        # Purge all messages in the spam bot channel
        try:
            await channel.purge()
        except Exception:
            return await ctx.respond(
                "Failed to clear the spam bot channel. Check my permissions.", ephemeral=True
            )

        # Ensure info message is resent
        await self._ensure_spam_bot_info_message(ctx.guild)
        await ctx.respond(
            f"Spam bot channel {channel.mention} has been reset and the info embed was resent.",
            ephemeral=True,
        )

    @command_with_perms(
        min_role="Staff",
        name="restrict",
        aliases=["blacklist"],
        help="Restrict a user from requesting SOAP/NNID transfers by giving them the restricted role.",
    )
    async def restrict_user(self, ctx, user: discord.Member, *, reason: str = None):
        """
        Restrict a user from requesting SOAP/NNID transfers.
        Requires Staff role or higher.
        """
        if not RESTRICTED_ROLE_ID:
            embed = discord.Embed(
                title="❌ Configuration Error",
                description="RESTRICTED_ROLE_ID is not configured in constants.py.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        restricted_role = ctx.guild.get_role(RESTRICTED_ROLE_ID)
        if restricted_role is None:
            embed = discord.Embed(
                title="❌ Configuration Error",
                description="Restricted role not found. Check RESTRICTED_ROLE_ID in constants.py.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Check role hierarchy - can't restrict users with equal or higher roles
        if ctx.author.top_role.position <= user.top_role.position and ctx.author != ctx.guild.owner:
            embed = discord.Embed(
                title="❌ Permission Denied",
                description=f"You cannot restrict {user.mention} because they have an equal or higher role than you.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Check if user already has the role
        if restricted_role in user.roles:
            embed = discord.Embed(
                title="⚠️ Already Restricted",
                description=f"{user.mention} is already restricted from requesting SOAP/NNID transfers.",
                color=discord.Color.orange(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Add the role
        try:
            await user.add_roles(restricted_role, reason=reason or f"Restricted by {ctx.author}")
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Permission Error",
                description="I don't have permission to add roles. Check my permissions.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Failed to restrict user: {e}",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Log the action
        await self._log_mod_action(
            guild=ctx.guild,
            user=user,
            action="restrict",
            moderator=ctx.author,
            reason=reason,
            source=None,
        )

        embed = discord.Embed(
            title="✅ User Restricted",
            description=f"{user.mention} has been restricted from requesting SOAP/NNID transfers.",
            color=discord.Color.red(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(
            name="Moderator",
            value=ctx.author.mention,
            inline=True,
        )
        if reason:
            embed.add_field(
                name="Reason",
                value=reason,
                inline=False,
            )
        embed.set_footer(text=f"User ID: {user.id}")

        await ctx.respond(embed=embed, ephemeral=True)

    @command_with_perms(
        min_role="Staff",
        name="unrestrict",
        aliases=["unblacklist"],
        help="Remove restriction from a user by removing the restricted role.",
    )
    async def unrestrict_user(self, ctx, user: discord.Member, *, reason: str = None):
        """
        Remove restriction from a user.
        Requires Staff role or higher.
        """
        if not RESTRICTED_ROLE_ID:
            embed = discord.Embed(
                title="❌ Configuration Error",
                description="RESTRICTED_ROLE_ID is not configured in constants.py.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        restricted_role = ctx.guild.get_role(RESTRICTED_ROLE_ID)
        if restricted_role is None:
            embed = discord.Embed(
                title="❌ Configuration Error",
                description="Restricted role not found. Check RESTRICTED_ROLE_ID in constants.py.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Check role hierarchy - can't unrestrict users with equal or higher roles (unless they're already restricted)
        # But we still check hierarchy to prevent abuse
        if ctx.author.top_role.position <= user.top_role.position and ctx.author != ctx.guild.owner:
            embed = discord.Embed(
                title="❌ Permission Denied",
                description=f"You cannot modify restrictions for {user.mention} because they have an equal or higher role than you.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Check if user has the role
        if restricted_role not in user.roles:
            embed = discord.Embed(
                title="⚠️ Not Restricted",
                description=f"{user.mention} is not currently restricted.",
                color=discord.Color.orange(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Remove the role
        try:
            await user.remove_roles(restricted_role, reason=reason or f"Unrestricted by {ctx.author}")
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Permission Error",
                description="I don't have permission to remove roles. Check my permissions.",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)
        except discord.HTTPException as e:
            embed = discord.Embed(
                title="❌ Error",
                description=f"Failed to unrestrict user: {e}",
                color=discord.Color.red(),
            )
            return await ctx.respond(embed=embed, ephemeral=True)

        # Log the action
        await self._log_mod_action(
            guild=ctx.guild,
            user=user,
            action="unrestrict",
            moderator=ctx.author,
            reason=reason,
            source=None,
        )

        embed = discord.Embed(
            title="✅ Restriction Removed",
            description=f"Restriction has been removed from {user.mention}.\n\nThey can now request SOAP/NNID transfers again.",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(
            name="Moderator",
            value=ctx.author.mention,
            inline=True,
        )
        if reason:
            embed.add_field(
                name="Reason",
                value=reason,
                inline=False,
            )
        embed.set_footer(text=f"User ID: {user.id}")

        await ctx.respond(embed=embed, ephemeral=True)

    async def _maybe_log_kick(self, member: discord.Member):
        """Check recent audit logs to see if the member was kicked and log it."""
        if not BAN_LOG_ID:
            return

        guild = member.guild
        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.kick, limit=5):
                if entry.target.id == member.id:
                    # Only log if the kick is recent (within ~10 seconds)
                    if (datetime.now(timezone.utc) - entry.created_at).total_seconds() <= 10:
                        await self._log_mod_action(
                            guild=guild,
                            user=member,
                            action="kick",
                            moderator=entry.user,
                            reason=entry.reason,
                            source=None,
                        )
                    break
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle spam bot channel - auto ban/unban users without sending DMs"""
        # Only process messages in the spam bot channel
        if not message.guild or message.channel.id != SPAM_BOT_CHANNEL_ID:
            return
        
        # Ignore bot messages
        if message.author.bot:
            return

        # Delete the message
        try:
            await message.delete()
        except Exception:
            pass
        
        guild = message.guild
        user = message.author
        
        # Ban the user (clearing messages from the last hour)
        reason = "Spam bot auto-ban/unban"
        try:
            await guild.ban(user, delete_message_seconds=3600, reason=reason)
        except discord.Forbidden:
            # Bot doesn't have ban permissions
            print(f"Failed to ban {user} - missing ban permissions")
            return
        except discord.HTTPException as e:
            # Log HTTP errors for debugging
            print(f"Failed to ban {user} - HTTP error: {e}")
            return
        except Exception as e:
            # Log other errors
            print(f"Failed to ban {user} - error: {e}")
            return
        
        # Log the honeypot ban as a moderated ban
        await self._log_mod_action(
            guild=guild,
            user=user,
            action="ban",
            moderator=guild.me,
            reason=reason,
            source="Honeypot",
        )
        
        # Small delay to ensure ban is processed
        await asyncio.sleep(0.5)
        
        # Unban the user immediately
        try:
            await guild.unban(user, reason="Spam bot auto-unban")
        except discord.NotFound:
            # User wasn't banned (shouldn't happen, but handle gracefully)
            pass
        except discord.HTTPException as e:
            # Log HTTP errors for debugging
            print(f"Failed to unban {user} - HTTP error: {e}")
        except Exception as e:
            # Log other errors
            print(f"Failed to unban {user} - error: {e}")


def setup(bot: commands.Bot):
    bot.add_cog(ModerationCog(bot))

