import discord
import asyncio
import re
from datetime import datetime, timezone, timedelta
from discord.ext import commands
from perms import command_with_perms, soap_channels_only
from log import log_to_soaper_log
from helpee import REOPENED_TITLE
from constants import (
    SOAP_CHANNEL_SUFFIX,
    SOAP_CHANNEL_CATEGORY_ID,
    NNID_CHANNEL_SUFFIX,
    NNID_CHANNEL_CATEGORY_ID,
    SOAP_LOG_ID,
    SOAPER_ROLE_ID,
    INACTIVITY_REMINDER_HOURS,
    INACTIVITY_REMINDERS,
    INACTIVITY_CHECK_MINUTES,
)

# Open channel with no reply: remind every INACTIVITY_REMINDER_HOURS, then close after the last reminder.
# Each step waits a full interval after the previous one, so a restart never fires two steps back to back.
# The timer is cancelled for good once anyone (not a bot) sends a message or presses a button in the channel.
INACTIVITY_STEP = timedelta(hours=INACTIVITY_REMINDER_HOURS)
REMINDER_FOOTER_PREFIX = "Inactivity reminder "  # used to find reminders the bot already sent
KEEPOPEN_TITLE = "🔓 Inactivity Timer Disabled"  # used to find .keepopen in the channel
KEEPOPEN_FOOTER = "Inactivity timer disabled"  # older .keepopen messages used this footer instead

MENTION_RE = re.compile(r"<@!?(\d+)>")


class InactivityCog(commands.Cog):
    """Reminds helpees who never reply in their SOAP/NNID channel, then closes the channel."""

    def __init__(self, bot):
        self.bot = bot
        self._task = None
        self._pressed = set()  # channel IDs where someone pressed a button since startup

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Button presses and form submissions count as replies."""
        if interaction.type in (discord.InteractionType.component, discord.InteractionType.modal_submit):
            if interaction.channel_id and interaction.user and not interaction.user.bot:
                self._pressed.add(interaction.channel_id)

    def cog_load(self):
        self._start()

    @commands.Cog.listener()
    async def on_ready(self):
        self._start()

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()

    def _start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._check_channels()
            except asyncio.CancelledError:
                break
            except Exception as e:
                for g in self.bot.guilds:
                    ch = g.get_channel(SOAP_LOG_ID)
                    if ch:
                        try:
                            await ch.send(f"[Inactivity checker] Error: {e!r}")
                            break
                        except Exception:
                            pass
            await asyncio.sleep(INACTIVITY_CHECK_MINUTES * 60)

    async def _check_channels(self):
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                if not channel.category:
                    continue
                is_soap = (
                    channel.category.id == SOAP_CHANNEL_CATEGORY_ID
                    and channel.name.endswith(SOAP_CHANNEL_SUFFIX)
                )
                is_nnid = (
                    channel.category.id == NNID_CHANNEL_CATEGORY_ID
                    and channel.name.endswith(NNID_CHANNEL_SUFFIX)
                )
                if not (is_soap or is_nnid):
                    continue
                # Nothing can be due before the first step, so skip reading history
                if now - channel.created_at < INACTIVITY_STEP:
                    continue
                try:
                    await self._check_channel(channel, is_soap, now)
                except discord.NotFound:
                    pass

    async def _check_channel(self, channel: discord.TextChannel, is_soap: bool, now: datetime):
        if channel.id in self._pressed:
            return  # someone pressed a button, so the timer is cancelled
        soaper = channel.guild.get_role(SOAPER_ROLE_ID)
        if soaper and channel.overwrites_for(soaper).send_messages is False:
            return  # .lock is on, so the helpee can't reply
        reminders = []  # send times of reminders already posted, newest first
        started = channel.created_at
        async for message in channel.history(limit=None):
            if (
                message.author.id == self.bot.user.id
                and message.embeds
                and message.embeds[0].title == REOPENED_TITLE
            ):
                started = message.created_at  # reopened for a helpee who rejoined, so start over from here
                break
            if not message.author.bot:
                return  # someone replied, so the timer is cancelled
            # Bot replies to a button or form keep who pressed it, which covers presses from before a restart
            meta = getattr(message, "interaction_metadata", None)
            if meta and meta.user and not meta.user.bot:
                return
            if message.author.id == self.bot.user.id and message.embeds:
                footer = message.embeds[0].footer.text if message.embeds[0].footer else None
                if message.embeds[0].title == KEEPOPEN_TITLE or footer == KEEPOPEN_FOOTER:
                    return  # .keepopen was used, so the timer is off for good
                if footer and footer.startswith(REMINDER_FOOTER_PREFIX):
                    reminders.append(message.created_at)

        last_step = reminders[0] if reminders else started
        if now - last_step < INACTIVITY_STEP:
            return

        if len(reminders) < INACTIVITY_REMINDERS:
            await self._send_reminder(channel, is_soap, len(reminders) + 1, now)
        else:
            await self._close(channel, is_soap)

    async def _send_reminder(self, channel: discord.TextChannel, is_soap: bool, number: int, now: datetime):
        close_time = now + INACTIVITY_STEP * (INACTIVITY_REMINDERS - number + 1)
        transfer = "SOAP" if is_soap else "NNID"
        if number < INACTIVITY_REMINDERS:
            embed = discord.Embed(
                title="⏰ Are you still there?",
                description=(
                    "We haven't heard from you in a while. Please follow the instructions above and send a message in this channel once you're ready.\n\n"
                    f"If we don't hear back, this channel will be closed <t:{int(close_time.timestamp())}:R>."
                ),
                color=discord.Color.orange(),
            )
        else:
            embed = discord.Embed(
                title="⏰ Final Reminder",
                description=(
                    f"This channel will be closed <t:{int(close_time.timestamp())}:R> if we don't hear back.\n\n"
                    f"If it gets closed, you can request another {transfer} transfer at any time."
                ),
                color=discord.Color.red(),
            )
        embed.set_footer(text=f"{REMINDER_FOOTER_PREFIX}{number}/{INACTIVITY_REMINDERS}")

        m = MENTION_RE.search(channel.topic or "")
        await channel.send(content=f"<@{m.group(1)}>" if m else None, embed=embed)

    async def _close(self, channel: discord.TextChannel, is_soap: bool):
        name = channel.name
        if is_soap:
            cog = self.bot.get_cog("SoapCog")
            if not cog:
                return
            await cog.deletesoap(channel, None)
        else:
            cog = self.bot.get_cog("NNIDCog")
            if not cog:
                return
            await cog.deletennid(channel, None)

        # Explain the closure in the archived channel
        embed = discord.Embed(
            title="💤 Closed Due to Inactivity",
            description=f"This channel was closed because we didn't hear back after {INACTIVITY_REMINDERS} reminders.",
            color=discord.Color.orange(),
        )
        try:
            archived = await self.bot.fetch_channel(channel.id)
            await archived.send(embed=embed)
        except (discord.NotFound, discord.Forbidden):
            pass

        # Same format as the other SOAP log entries
        log = channel.guild.get_channel(SOAP_LOG_ID)
        if log:
            log_embed = discord.Embed(
                title="Archived SOAP Channel" if is_soap else "Archived NNID Channel"
            )
            log_embed.add_field(
                name="Action made by:",
                value=f"{self.bot.user.name} - {self.bot.user.id}",
                inline=False,
            )
            log_embed.add_field(
                name="Action: ",
                value=f"Closed #{name} due to inactivity",
                inline=False,
            )
            try:
                await log.send(embed=log_embed)
            except Exception:
                pass

    @command_with_perms(
        min_role="Soaper",
        name="keepopen",
        help="Stops the inactivity timer from closing this channel",
    )
    @soap_channels_only()
    async def keepopen(self, ctx):
        embed = discord.Embed(
            title=KEEPOPEN_TITLE,
            description="This channel will no longer be closed automatically for inactivity.",
            color=discord.Color.blue(),
        )
        await ctx.respond(embed=embed)
        try:
            await log_to_soaper_log(ctx, "Disabled Inactivity Timer")
        except Exception:
            pass


def setup(bot):
    bot.add_cog(InactivityCog(bot))
