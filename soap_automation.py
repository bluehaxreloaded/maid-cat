import discord
import hashlib
import io
import time
import re
import asyncio
from datetime import datetime, timezone
from discord.ext import commands, tasks
import constants
from perms import _has_role_or_higher, command_with_perms
from log import log_to_soaper_log
from constants import (
    BOTS_ONLY_CHANNEL_ID,
    SOAP_CHANNEL_CATEGORY_ID,
    SOAP_CHANNEL_SUFFIX,
    MANUAL_SOAP_CATEGORY_ID,
    LOADING_EMOTE_ID,
    AWAITING_EMOTE_ID,
    SOAP_COMPLETION_AUTO_CLOSE_MINUTES,
    SOAPER_ROLE_ID,
    is_late_night_hours,
)
from soap_helper import SoapHelperView
from inactivity import AFTER_HOURS_TITLE
from exefs import (
    MAX_ESSENTIAL_SIZE,
    InvalidEssential,
    read_essential,
    rebuild_essential,
    secinfo_signed,
    serial_from_secinfo,
)
from essential_store import delete_essential, load_essential, save_essential, sweep_essentials
from serial import (
    SERIAL_RECEIVED_TITLE,
    describe_serial,
    entered_serial,
    find_serial,
    read_serial,
    serials_match,
    uploaded_essential_serial,
)
from helpee import (
    add_case_note,
    case_notes,
    complete_case_setup,
    helpee_id,
    member_from_topic,
    reset_case_setup,
    safe_note_text,
)


STEP2_TITLE = "2️⃣ Upload your essential.exefs file"

# essential.exefs files that passed every check except the serial, held here until the helpee corrects it.
# Channel ID -> (helpee ID, file)
pending_essentials: dict[int, tuple[int, bytes]] = {}
SOAP_REQUEST_COOLDOWN = 15  # seconds before the same essential.exefs can be sent to soap-cat again
_last_soap_requests: dict[str, tuple[float, discord.abc.User]] = {}  # essential.exefs hash -> when and by who it was last sent


def forget_essential(channel: discord.abc.GuildChannel):
    """Wipe a channel's essential.exefs once the channel is deleted (kept while it's archived as -cya)."""
    pending_essentials.pop(channel.id, None)
    try:
        delete_essential(channel.id)
    except OSError as e:
        print(f"Could not wipe essential.exefs for #{channel.name}: {e}")


def store_essential(channel_id: int, user_id: int, data: bytes):
    """Save a helpee's fully checked essential.exefs to disk for their channel."""
    save_essential(channel_id, user_id, data)
    pending_essentials.pop(channel_id, None)


async def reject_non_helpee(interaction: discord.Interaction) -> bool:
    """Only the helpee named in the channel topic can upload their essential.exefs.
    Tells anyone else so and returns True. In a channel with no helpee in its topic (e.g. testing with .reset), anyone can."""
    helpee = helpee_id(interaction.channel)
    if helpee is None or interaction.user.id == helpee:
        return False
    await interaction.response.send_message(
        "Only the person this channel was opened for can upload their `essential.exefs`.", ephemeral=True
    )
    return True


AWAITING_APPROVAL_TITLE = "Your SOAP is awaiting approval"  # the inactivity timer also matches on this


def awaiting_approval_embed(guild: discord.Guild | None) -> discord.Embed:
    """Sent once steps 1 and 2 are done and the request is in soap-queue."""
    awaiting_emoji = discord.utils.get(guild.emojis, id=AWAITING_EMOTE_ID) if guild else None
    embed = discord.Embed(
        title=f"{awaiting_emoji} {AWAITING_APPROVAL_TITLE}" if awaiting_emoji else AWAITING_APPROVAL_TITLE,
        description=(
            "Please wait for our Soapers to review your case. Once approved, your SOAP Transfer will begin "
            "automatically and you will be notified upon its completion."
        ),
        color=discord.Color.yellow(),
    )
    embed.set_footer(text="Please don't use your 3DS until your SOAP is complete.")
    return embed


def after_hours_embed() -> discord.Embed:
    """Sent after the awaiting approval message when it's late night for most of the staff."""
    embed = discord.Embed(
        title=AFTER_HOURS_TITLE,
        description=(
            "It is currently after-hours for most of the staff members of this server, therefore it may take longer "
            "than usual for your SOAP Transfer to be approved, or for us to provide help or answer questions.\n\n"
            "You don't need to do anything else for now. We'll get to your request as soon as possible."
        ),
        color=discord.Color(0xD50032),
    )
    embed.set_footer(text="We appreciate your patience and understanding!")
    return embed


async def send_awaiting_approval(interaction: discord.Interaction):
    """The awaiting approval message, plus the after hours notice if it's late night."""
    await interaction.followup.send(embed=awaiting_approval_embed(interaction.guild))
    if is_late_night_hours():
        await interaction.followup.send(embed=after_hours_embed())


APPROVED_TITLE = "✅ Your SOAP is approved"  # the inactivity timer also matches on this


async def _find_awaiting_message(channel: discord.TextChannel) -> discord.Message | None:
    """The awaiting approval message in a SOAP channel, whether or not it's been marked approved yet."""
    async for message in channel.history(limit=50):
        if message.author.id != channel.guild.me.id or not message.embeds:
            continue
        title = message.embeds[0].title or ""
        if title.endswith(AWAITING_APPROVAL_TITLE) or title == APPROVED_TITLE:
            return message
    return None


async def mark_soap_approved(channel: discord.TextChannel):
    """Change the awaiting approval message to say the SOAP is approved (on Start SOAP or when the transfer starts)."""
    message = await _find_awaiting_message(channel)
    if message is None or message.embeds[0].title == APPROVED_TITLE:
        return  # already approved (Start SOAP and the transfer starting both call this)
    embed = discord.Embed(
        title=APPROVED_TITLE,
        description="Transfer will begin momentarily...",
        color=discord.Color.green(),
    )
    try:
        await message.edit(embed=embed)
    except discord.HTTPException:
        pass


async def delete_awaiting_message(channel: discord.TextChannel):
    """Remove the awaiting/approved message (when the transfer finishes or the request goes manual)."""
    message = await _find_awaiting_message(channel)
    if message is not None:
        try:
            await message.delete()
        except discord.HTTPException:
            pass


def serial_mismatch_embed() -> discord.Embed:
    """Sent when the serial the helpee entered doesn't match the one in their essential.exefs."""
    embed = discord.Embed(
        title="⚠️ Serial Number Mismatch",
        description=(
            "The serial number you provided does not match the serial number in your `essential.exefs` file. Please ensure you have entered the serial number correctly. If you're still having trouble, follow these instructions to find your console's serial number.\n"
            "To find your console's serial number:\n"
            "- Hold START while powering on your console. This will boot you into GodMode9.\n"
            "- Go to `[2:] SYSNAND TWLN` -> `sys` -> `log` -> `inspect.log`\n"
            "- Select `Open in Textviewer`.\n\n"
            "The correct serial number (two or three-letter prefix followed by eight numbers) should be in the file. "
            "You may also send us a picture if you're unsure."
        ),
        color=discord.Color.yellow(),
    )
    embed.set_footer(
        text="Once you've found your serial number, press the button below to enter it."
    )
    return embed


class SerialMismatchView(discord.ui.View):
    """Button on the serial mismatch message to enter the serial number again"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Enter serial number",
        style=discord.ButtonStyle.success,
        emoji="🔢",
        custom_id="serial_mismatch_enter",
    )
    async def serial_mismatch_enter_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        modal = SerialNumberModal(
            prompt_message_id=interaction.message.id,
            prompt_view_class=SerialMismatchView,
            correction=True,
        )
        await interaction.response.send_modal(modal)


async def send_soap_request(channel: discord.TextChannel, soaper: discord.abc.User) -> str | None:
    """Ask soap-cat to soap this channel's helpee using their stored essential.exefs.
    Returns why it couldn't be sent, or None if it was."""
    member = await member_from_topic(channel)
    if member is None:
        return "Could not find the helpee for this channel."
    serial = await entered_serial(channel)
    if not serial:
        return "No serial number was entered in this channel."
    data = load_essential(channel.id, member.id)
    if data is None:
        return "There's no essential.exefs saved for this helpee."
    bots_only = channel.guild.get_channel(BOTS_ONLY_CHANNEL_ID)
    if bots_only is None:
        return "Could not find the bots only channel."
    # Cancel duplicates, e.g. two Soapers pressing Start SOAP at the same time
    file_hash = hashlib.sha256(data).hexdigest()
    now = time.monotonic()
    sent_at, sent_by = _last_soap_requests.get(file_hash, (0, None))
    if now - sent_at < SOAP_REQUEST_COOLDOWN:
        seconds = int(now - sent_at)
        return (
            f"This SOAP is on cooldown, {sent_by.mention} already approved this SOAP "
            f"{seconds} second{'' if seconds == 1 else 's'} ago."
        )
    _last_soap_requests[file_hash] = (now, soaper)
    # soap-cat reads the file straight from the essentials folder, so it never goes through Discord
    # The channel ID tells soap-cat which channel this is, so it doesn't have to find it by topic
    await bots_only.send(f"SOAP_REQUEST {member.id} {serial} STORED {channel.id}")
    add_case_note(channel, "SOAP approved and sent to soap-cat")
    return None


class SoapQueueView(discord.ui.View):
    """Start SOAP / Hold for Review buttons on a soap-queue request. Pressing either disables both.
    Start SOAP sends the request to soap-cat in the bots only channel; Hold for Review moves the channel
    to manual (same as .manual)."""

    def __init__(self):
        super().__init__(timeout=None)

    @staticmethod
    async def _is_soaper(interaction: discord.Interaction) -> bool:
        """Start SOAP and Hold for Review are Soaper or higher only."""
        soaper = discord.utils.get(interaction.guild.roles, name="Soaper")
        if soaper is None or not _has_role_or_higher(interaction.user, soaper):
            await interaction.response.send_message("You must be a Soaper or higher to use this.", ephemeral=True)
            return False
        return True

    @staticmethod
    def _request_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
        """The SOAP channel this request is for; the request's embed says "<helpee> in <#channel>"."""
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        match = re.search(r"<#(\d+)>", embed.description or "") if embed else None
        return interaction.guild.get_channel(int(match.group(1))) if match else None

    @discord.ui.button(
        label="Start SOAP",
        style=discord.ButtonStyle.success,
        emoji="🧼",
        custom_id="soap_queue_approve",
    )
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._is_soaper(interaction):
            return
        channel = self._request_channel(interaction)
        if channel is None:
            await interaction.response.send_message("That SOAP channel doesn't exist anymore.", ephemeral=True)
            return

        await interaction.response.defer()
        error = await send_soap_request(channel, interaction.user)
        if error:
            # Buttons stay enabled so it can be tried again or held for review
            await interaction.followup.send(error, ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        await interaction.followup.send(f"Started SOAP in {channel.mention}.", ephemeral=True)
        await mark_soap_approved(channel)

    @discord.ui.button(
        label="Hold for Review",
        style=discord.ButtonStyle.secondary,
        emoji="🛠️",
        custom_id="soap_queue_manual",
    )
    async def manual_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not await self._is_soaper(interaction):
            return
        channel = self._request_channel(interaction)
        if channel is None:
            await interaction.response.send_message("That SOAP channel doesn't exist anymore.", ephemeral=True)
            return

        # Same as .manual, which replies to the Soaper with how it went
        soap_cog = interaction.client.get_cog("SoapCog")
        if not await soap_cog._move_soap_category(
            interaction,
            None,
            channel,
            MANUAL_SOAP_CATEGORY_ID,
            "manual",
            moved_message="Holding for review, moved {channel} to Manual SOAPs.",
        ):
            return

        manual_embed = discord.Embed(
            title="⚠️ Moving to Manual",
            description="This SOAP Request has been selected to be performed manually by a Soaper.",
            color=discord.Color.orange(),
        )
        manual_embed.set_footer(text="Please wait for assistance and prepare to answer any questions.")
        await delete_awaiting_message(channel)
        await channel.send(embed=manual_embed)

        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)


def essential_signed(channel_id: int, user_id: int | None) -> bool | None:
    """Whether the stored essential.exefs is signed by Nintendo, or None if it can't be checked."""
    data = load_essential(channel_id, user_id)
    if data is None:
        return None
    try:
        return secinfo_signed(read_essential(data)["secinfo"])
    except InvalidEssential:
        return None


def soap_queue_embed(
    member: discord.abc.User,
    channel: discord.TextChannel | None,
    serial: str | None,
    notes: list[str],
    signed: bool | None = None,
) -> discord.Embed:
    """The soap-queue request for a helpee who finished steps 1 and 2."""
    info = read_serial(serial) if serial else None
    embed = discord.Embed(
        title="🧼 New SOAP Request",
        description=f"{member.mention} in {channel.mention}" if channel else member.mention,
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Username", value=member.name, inline=True)
    embed.add_field(name="Serial Number", value=f"`{serial}`" if serial else "Not found", inline=True)
    region = info.region if info and info.region else "Unknown"
    embed.add_field(name="Region", value=f"{region} ({info.model})" if info and info.model else region, inline=True)
    embed.add_field(
        name="Signed by Nintendo",
        value={True: "✅ Yes", False: "❌ No"}.get(signed, "➖ Not checked"),
        inline=True,
    )
    notes_text = "\n".join(f"- {n}" for n in notes) or "None"
    if len(notes_text) > 1024:
        notes_text = notes_text[:1021] + "..."
    embed.add_field(name="Case Notes", value=notes_text, inline=False)
    return embed


async def post_to_soap_queue(channel: discord.TextChannel, member: discord.abc.User):
    """Post a helpee's request to soap-queue once steps 1 and 2 are done."""
    queue_id = getattr(constants, "SOAP_QUEUE_CHANNEL_ID", None)  # not every server has one set yet
    queue = channel.guild.get_channel(queue_id) if queue_id else None
    if queue is None:
        return
    embed = soap_queue_embed(
        member,
        channel,
        await entered_serial(channel),
        case_notes(channel),
        essential_signed(channel.id, member.id),
    )
    try:
        await queue.send(embed=embed, view=SoapQueueView())
    except discord.HTTPException as e:
        print(f"Could not post to soap-queue for #{channel.name}: {e}")


class CompletionFollowUpView(discord.ui.View):
    """View for the follow-up questions after eShop verification"""

    def __init__(self, channel_id=None, show_close_button=True, bot=None, guild=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.show_close_button = show_close_button
        self.auto_close_task = None

        # Remove I'm good if manual SOAP
        if not show_close_button:
            for item in list(self.children):
                if (
                    isinstance(item, discord.ui.Button)
                    and item.custom_id == "completion_no_thanks"
                ):
                    self.remove_item(item)

        # Start auto-close timer if we have the necessary references
        if show_close_button and bot and guild and channel_id:
            self._start_auto_close(bot, guild, channel_id)

    def _start_auto_close(self, bot, guild, channel_id):
        """Start the auto-close timer for this channel."""

        async def auto_close():
            try:
                await asyncio.sleep(SOAP_COMPLETION_AUTO_CLOSE_MINUTES * 60)
                channel = guild.get_channel(channel_id)
                if not channel:
                    return
                if (
                    not channel.category
                    or channel.category.id == MANUAL_SOAP_CATEGORY_ID
                ):
                    return

                # Extract user ID from channel topic for logging
                user_id = None
                if channel.topic:
                    match = re.search(r"<@!?(\d+)>", channel.topic)
                    if match:
                        user_id = int(match.group(1))

                soap_cog = bot.get_cog("SoapCog")
                if soap_cog:
                    await soap_cog.deletesoap(channel, None)

                    if user_id:
                        try:
                            user = guild.get_member(user_id)
                            if user:
                                ctx = type(
                                    "Context",
                                    (),
                                    {
                                        "guild": guild,
                                        "message": type(
                                            "Message",
                                            (),
                                            {
                                                "author": user,
                                                "content": "Completion timeout",
                                            },
                                        )(),
                                    },
                                )()
                                await log_to_soaper_log(ctx, "Removed SOAP Channel")
                        except Exception:
                            pass
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self.auto_close_task = asyncio.create_task(auto_close())

    @discord.ui.button(
        label="I'm good, thanks!",
        style=discord.ButtonStyle.primary,
        emoji="👋",
        custom_id="completion_no_thanks",
    )
    async def no_thanks_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Trigger boom command to delete the channel"""
        # Cancel auto-close task if it exists
        if self.auto_close_task and not self.auto_close_task.done():
            self.auto_close_task.cancel()

        # Disable all buttons in this view
        for item in self.children:
            item.disabled = True

        # Disable buttons before responding
        await interaction.response.edit_message(view=self)

        # Get channel from stored ID or from interaction
        channel = None
        if self.channel_id:
            channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            # Fallback to interaction channel
            channel = interaction.channel

        if not channel:
            await interaction.followup.send("Channel not found.", ephemeral=True)
            return

        # Use the deletesoap helper from SoapCog
        soap_cog = interaction.client.get_cog("SoapCog")
        if soap_cog:
            try:
                await soap_cog.deletesoap(channel, interaction)
            except Exception:
                pass
        else:
            await interaction.followup.send("Error: SoapCog not found.", ephemeral=True)

    @discord.ui.button(
        label="I have more questions",
        style=discord.ButtonStyle.danger,
        emoji="❔",
        custom_id="completion_more_questions",
    )
    async def more_questions_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Send assistance requested embed to channel"""
        # Cancel auto-close task if it exists
        if self.auto_close_task and not self.auto_close_task.done():
            self.auto_close_task.cancel()

        # Disable all buttons in this view
        for item in self.children:
            item.disabled = True

        # Disable buttons before responding
        await interaction.response.edit_message(view=self)

        # Get channel from stored ID or from interaction
        channel = None
        if self.channel_id:
            channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            # For ephemeral interactions, we need to find the SOAP channel differently
            # Since ephemeral interactions don't have the channel context, we'll use the message reference
            # or find it by user mention in topic
            pass

        # If we can't get channel from stored ID, try to find it from user's SOAP channels
        if not channel:
            user_id = interaction.user.id
            mention_plain = f"<@{user_id}>"
            mention_nick = f"<@!{user_id}>"
            for ch in interaction.guild.text_channels:
                if ch.category and ch.category.id in [
                    SOAP_CHANNEL_CATEGORY_ID,
                    MANUAL_SOAP_CATEGORY_ID,
                ]:
                    topic = getattr(ch, "topic", None)
                    if topic and (mention_plain in topic or mention_nick in topic):
                        channel = ch
                        break

        if channel:
            # Show SOAP helper with context for follow-up questions
            embed = discord.Embed(
                title="🔍 SOAP Helper",
                description=(
                    "Select the issue you're having from the dropdown below.\n\n"
                    "If you can't find what you're looking for, select **'My option is not listed here.'** "
                    "to request assistance from a Soaper."
                ),
                color=discord.Color.red(),
            )
            embed.set_footer(text="Select an option from the dropdown menu below")
            view = SoapHelperView(context="other_questions")
            await channel.send(content=interaction.user.mention, embed=embed, view=view)


class CopySerialView(discord.ui.View):
    """View with a copy button that sends the serial as an ephemeral message."""

    def __init__(self, serial: str = None):
        super().__init__(timeout=None)
        self.serial = serial

    @discord.ui.button(
        label="",
        style=discord.ButtonStyle.secondary,
        emoji="📋",
        custom_id="copy_serial",
    )
    async def copy_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        serial = None
        if interaction.message and interaction.message.embeds:
            # Always derive from the clicked message to avoid stale serials
            # from persistent view instances.
            serial = find_serial(interaction.message.embeds[0].description or "")

        # Backward-compatible fallback for older message formats.
        if not serial and self.serial:
            serial = self.serial
        if serial:
            await interaction.response.send_message(serial, ephemeral=True)
        else:
            await interaction.response.send_message(
                "Could not find serial number.", ephemeral=True
            )


class SerialNumberModal(discord.ui.Modal):
    """Modal for submitting serial number (2–3 letters + 8-9 digits)."""

    def __init__(self, prompt_message_id: int = None, prompt_view_class=None, correction: bool = False):
        super().__init__(title="🔢 Serial Number", timeout=None)
        self.prompt_message_id = prompt_message_id
        self.prompt_view_class = prompt_view_class or SerialNumberCheckView
        # correction: re-entering the serial after a mismatch, so check it against the file instead of asking for it
        self.correction = correction
        self.serial_input = discord.ui.InputText(
            label="Please enter your console's serial number.",
            placeholder="e.g. YJM123456784 or QW12345678",
            required=True,
            max_length=14,  # room for a space, e.g. YJM12345678 4
        )
        self.add_item(self.serial_input)

    async def callback(self, interaction: discord.Interaction):
        serial_raw = self.serial_input.value.strip().upper()
        info = read_serial(serial_raw)
        if info is None:
            add_case_note(
                interaction.channel,
                f"Entered a serial number in the wrong format: `{safe_note_text(serial_raw, 20)}`",
            )
            await interaction.response.send_message(
                "Invalid format. Serial numbers have 2-3 letters followed by 8 or 9 digits (e.g. YJM123456784 or QW12345678). Please try again.",
                ephemeral=True,
            )
            return
        # The 9th digit on the sticker is a check digit, so a wrong one means the serial was mistyped
        if not info.check_digit_ok:
            add_case_note(
                interaction.channel,
                f"Entered a serial number with the wrong check digit: `{info.serial}`",
            )
            await interaction.response.send_message(
                "That serial number doesn't look right. Please check each character against the sticker on your console and try again.",
                ephemeral=True,
            )
            return
        serial = info.serial
        if info.unusual:
            add_case_note(interaction.channel, f"Serial number has an unusual region code: `{info.region_code}`")

        await interaction.response.defer()
        # The file from the mismatched upload; if it's gone (e.g. after a restart) they'll be asked for it again
        pending = pending_essentials.get(interaction.channel.id) if interaction.channel else None
        if self.correction and interaction.channel:
            if pending:
                file_serial = serial_from_secinfo(read_essential(pending[1])["secinfo"])
            else:
                file_serial = await uploaded_essential_serial(interaction.channel)
            if file_serial and not serials_match(serial, file_serial):
                add_case_note(
                    interaction.channel,
                    f"Re-entered serial number still didn't match their essential.exefs: `{serial}`",
                )
                await interaction.followup.send(
                    "That serial number still doesn't match the one in your `essential.exefs`. "
                    "Please check it again, or send us a picture if you're unsure.",
                    ephemeral=True,
                )
                return
            if pending:
                try:
                    store_essential(interaction.channel.id, *pending)
                except OSError as e:
                    print(f"Could not store essential.exefs for #{interaction.channel.name}: {e}")
                    pending = None  # ask for the file again below

        serial_embed = discord.Embed(
            title=SERIAL_RECEIVED_TITLE,
            description=serial,  # keep this just the serial; the file check and soap-cat read it back
            color=discord.Color.green(),
        )
        label = describe_serial(info)
        if label:
            serial_embed.set_footer(text=label)
        await interaction.followup.send(embed=serial_embed)

        # Disable buttons on the serial prompt message
        if self.prompt_message_id and interaction.channel:
            try:
                prompt_msg = await interaction.channel.fetch_message(
                    self.prompt_message_id
                )
                view = self.prompt_view_class()
                for item in view.children:
                    item.disabled = True
                await prompt_msg.edit(view=view)
            except Exception:
                pass

        if self.correction and (pending or load_essential(interaction.channel.id, helpee_id(interaction.channel))):
            # The file is already in, so there's no step 2 this time
            wait_embed = discord.Embed(
                title="✅ Serial number updated",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=wait_embed)
            await send_awaiting_approval(interaction)
            complete_case_setup(interaction.channel)  # step 1 and step 2 are done now
            await post_to_soap_queue(interaction.channel, interaction.user)
            return

        # Step 2: Ask for essential.exefs
        exefs_embed = discord.Embed(
            title=STEP2_TITLE,
            description=(
                "**To get your essential.exefs file:**\n"
                "1. Ensure your SD card is in your console\n"
                "2. Hold **START** while powering on → this will boot you into GodMode9\n"
                "3. Navigate to `[S:] SYSNAND Virtual`\n"
                "4. Select `essential.exefs`\n"
                "5. Select `Copy to 0:/gm9/out` (select Overwrite if prompted)\n"
                "6. Power off your console\n"
                "7. Insert your SD card into your PC or connect to your console via [FTPD](<https://wiki.hacks.guide/wiki/3DS:FTP>). If you do not have a PC available, ask us about a solution\n"
                "8. Navigate to `/gm9/out/` on your SD, where `essential.exefs` should be located\n\n"
                "Were you able to get your essential.exefs file?"
            ),
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=exefs_embed, view=EssentialUploadView())


class SerialNumberCheckView(discord.ui.View):
    """View for serial number prompt buttons in new SOAP channels"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Yes, I have my serial number.",
        style=discord.ButtonStyle.success,
        emoji="🔢",
        custom_id="serial_yes",
    )
    async def serial_yes_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        modal = SerialNumberModal(prompt_message_id=interaction.message.id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="I need help.",
        style=discord.ButtonStyle.red,
        emoji="❔",
        custom_id="serial_help",
    )
    async def serial_help_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        # Disable the original buttons
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            await interaction.response.defer()

        # Send findserial instructions
        instructions_embed = discord.Embed(
            title="📂 Finding Your Serial Number",
            description=(
                "Follow these instructions to find your console's serial number.\n\n"
                "**To find your console's serial number:**\n"
                "- Hold START while powering on your console. This will boot you into GodMode9.\n"
                "- Go to `[2:] SYSNAND TWLN` -> `sys` -> `log` -> `inspect.log`\n"
                "- Select `Open in Textviewer`.\n\n"
                "The correct serial number (two or three-letter prefix followed by eight numbers) should be in the file."
            ),
            color=discord.Color.blue(),
        )
        instructions_embed.set_footer(
            text="You may also send us a picture if you're unsure."
        )
        await interaction.followup.send(embed=instructions_embed)

        # Send follow-up with Yes / No, I need further assistance
        followup_embed = discord.Embed(
            title="Were you able to find your serial number?",
            description="**After following the instructions above,** please select an option below.",
            color=discord.Color.blue(),
        )
        await interaction.followup.send(
            embed=followup_embed,
            view=SerialNumberFollowUpView(),
        )


class SerialNumberFollowUpView(discord.ui.View):
    """View for follow-up after serial number instructions"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Yes, I have my serial number",
        style=discord.ButtonStyle.success,
        emoji="🔢",
        custom_id="serial_followup_yes",
    )
    async def serial_followup_yes_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        modal = SerialNumberModal(
            prompt_message_id=interaction.message.id,
            prompt_view_class=SerialNumberFollowUpView,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="No, I need more help",
        style=discord.ButtonStyle.danger,
        emoji="❔",
        custom_id="serial_followup_assistance",
    )
    async def serial_followup_assistance_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            await interaction.response.defer()

        soaper_ping = f"<@&{SOAPER_ROLE_ID}>"
        embed = discord.Embed(
            title="🆘 Assistance Requested",
            description=(
                f"{interaction.user.mention} has requested additional help. "
                "Please wait for a Soaper to assist you."
            ),
            color=discord.Color.yellow(),
        )
        embed.set_footer(
            text="Describe in detail what's happening and please include error codes if possible."
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                content=soaper_ping,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        else:
            await interaction.response.send_message(
                content=soaper_ping,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )


class EssentialUploadModal(discord.ui.DesignerModal):
    """Modal for uploading essential.exefs."""

    def __init__(self, prompt_message_id: int = None, prompt_view_class=None):
        self.prompt_message_id = prompt_message_id
        self.prompt_view_class = prompt_view_class or EssentialUploadView
        self.file_upload = discord.ui.FileUpload(
            custom_id="essential_file",
            max_values=1,
            required=True,
        )
        super().__init__(
            discord.ui.Label(
                "essential.exefs",
                self.file_upload,
                description="The essential.exefs file from /gm9/out/ on your SD card.",
            ),
            title="📁 Upload essential.exefs",
            timeout=None,
        )

    async def callback(self, interaction: discord.Interaction):
        if await reject_non_helpee(interaction):
            return
        files = self.file_upload.values or []
        if not files or not files[0].filename.lower().endswith(".exefs"):
            name = safe_note_text(files[0].filename) if files else "no file"
            add_case_note(interaction.channel, f"Uploaded a file that isn't an .exefs: `{name}`")
            await interaction.response.send_message(
                "That isn't an `.exefs` file. Please upload the `essential.exefs` file from `/gm9/out/` on your SD card.",
                ephemeral=True,
            )
            return
        attachment = files[0]

        # Files uploaded through a modal aren't posted anywhere; once it passes every check it's stored on disk
        # (Soapers get it with /essential)
        await interaction.response.defer()
        try:
            # Too big to be an essential.exefs, so don't download it at all
            if attachment.size > MAX_ESSENTIAL_SIZE:
                raise InvalidEssential("file is too big")
            data = await attachment.read()
        except discord.HTTPException:
            await interaction.followup.send(
                "Could not read your file. Please try uploading it again.", ephemeral=True
            )
            return
        except InvalidEssential:
            data = b""  # reported below like any other invalid file

        # Make sure it's a real essential.exefs and not just a file with that name
        try:
            essential = read_essential(data)
        except InvalidEssential as e:
            add_case_note(interaction.channel, f"Uploaded an invalid essential.exefs ({e})")
            await interaction.followup.send(
                "That file isn't a valid `essential.exefs`. It may be damaged or incomplete. "
                "Please copy `essential.exefs` from your console again and upload the new file.",
                ephemeral=True,
            )
            return

        # Not required, but Soapers should know if the file wasn't signed by Nintendo
        if secinfo_signed(essential["secinfo"]) is False:
            add_case_note(interaction.channel, "Uploaded an essential.exefs that isn't signed by Nintendo")

        # Only the known files are kept, so nothing else that was in the upload gets stored
        clean = rebuild_essential(essential)
        entered = await entered_serial(interaction.channel) if interaction.channel else None
        if entered and not serials_match(entered, serial_from_secinfo(essential["secinfo"])):
            add_case_note(
                interaction.channel,
                f"Serial number didn't match their essential.exefs: entered `{safe_note_text(entered, 20)}`",
            )
            # Not stored yet: it's held in memory until the serial is corrected.
            # It still replaces any earlier upload, so the old file isn't used by mistake.
            pending_essentials[interaction.channel.id] = (interaction.user.id, clean)
            try:
                delete_essential(interaction.channel.id)
            except OSError as e:
                print(f"Could not wipe old essential.exefs for #{interaction.channel.name}: {e}")
            await interaction.followup.send(
                content=interaction.user.mention,
                embed=serial_mismatch_embed(),
                view=SerialMismatchView(),
            )
        else:
            try:
                store_essential(interaction.channel.id, interaction.user.id, clean)
            except OSError as e:
                print(f"Could not store essential.exefs for #{interaction.channel.name}: {e}")
                await interaction.followup.send(
                    "Could not save your file. Please try uploading it again.", ephemeral=True
                )
                return
            name = attachment.filename.replace("`", "'")
            received_embed = discord.Embed(
                title="✅ essential.exefs received",
                description=f"📎 `{name}` | {len(clean) / 1024:.1f} KB",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=received_embed)
            await send_awaiting_approval(interaction)
            complete_case_setup(interaction.channel)  # step 1 and step 2 are done now
            await post_to_soap_queue(interaction.channel, interaction.user)

        # Disable buttons on the upload prompt message
        if self.prompt_message_id and interaction.channel:
            try:
                prompt_msg = await interaction.channel.fetch_message(
                    self.prompt_message_id
                )
                view = self.prompt_view_class()
                for item in view.children:
                    item.disabled = True
                await prompt_msg.edit(view=view)
            except Exception:
                pass


class EssentialUploadView(discord.ui.View):
    """View for essential.exefs upload prompt buttons"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Yes, upload my essential.exefs.",
        style=discord.ButtonStyle.success,
        emoji="📁",
        custom_id="essential_upload",
    )
    async def essential_upload_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        if await reject_non_helpee(interaction):
            return
        modal = EssentialUploadModal(prompt_message_id=interaction.message.id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="I need help.",
        style=discord.ButtonStyle.red,
        emoji="❔",
        custom_id="essential_help",
    )
    async def essential_help_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        # Disable the original buttons
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            await interaction.response.defer()

        # Send essential.exefs troubleshooting
        instructions_embed = discord.Embed(
            title="📂 Finding Your essential.exefs",
            description=(
                "If you're having trouble getting your `essential.exefs` file, check the following.\n\n"
                "- If you reach the Luma3DS chainloader when holding START, select GodMode9 to continue (the red text is the selected option).\n"
                "- If you reach the HOME menu or GodMode9 is not listed in the chainloader, GodMode9 is not installed. Please redo [Finalizing Setup](<https://3ds.hacks.guide/finalizing-setup>).\n"
                "- The file is copied to the `gm9/out` folder on the root of your SD card.\n"
                "- If there is more than one `essential.exefs` in `gm9/out`, delete all of them and copy it again."
            ),
            color=discord.Color.blue(),
        )
        instructions_embed.set_footer(
            text="You may also send us a picture if you're unsure."
        )
        await interaction.followup.send(embed=instructions_embed)

        # Send follow-up with Yes / No, I need further assistance
        followup_embed = discord.Embed(
            title="Were you able to get your essential.exefs file?",
            description="**After following the instructions above,** please select an option below.",
            color=discord.Color.blue(),
        )
        await interaction.followup.send(
            embed=followup_embed,
            view=EssentialFollowUpView(),
        )


class EssentialFollowUpView(discord.ui.View):
    """View for follow-up after essential.exefs troubleshooting"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Yes, upload my essential.exefs",
        style=discord.ButtonStyle.success,
        emoji="📁",
        custom_id="essential_followup_upload",
    )
    async def essential_followup_upload_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        if await reject_non_helpee(interaction):
            return
        modal = EssentialUploadModal(
            prompt_message_id=interaction.message.id,
            prompt_view_class=EssentialFollowUpView,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="No, I need more help",
        style=discord.ButtonStyle.danger,
        emoji="❔",
        custom_id="essential_followup_assistance",
    )
    async def essential_followup_assistance_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            await interaction.response.defer()

        soaper_ping = f"<@&{SOAPER_ROLE_ID}>"
        embed = discord.Embed(
            title="🆘 Assistance Requested",
            description=(
                f"{interaction.user.mention} has requested additional help. "
                "Please wait for a Soaper to assist you."
            ),
            color=discord.Color.yellow(),
        )
        embed.set_footer(
            text="Describe in detail what's happening and please include error codes if possible."
        )
        await interaction.followup.send(
            content=soaper_ping,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )


class EshopVerificationView(discord.ui.View):
    """View for eShop verification buttons"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Yes, the eShop works",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="eshop_success",
    )
    async def eshop_success_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Send completion ephemeral with follow-up questions"""
        # Disable all buttons
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            await interaction.response.defer()

        channel_id = interaction.channel_id
        channel = interaction.channel

        # Check if channel is in manual SOAP category
        is_manual_soap = (
            channel
            and channel.category
            and channel.category.id == MANUAL_SOAP_CATEGORY_ID
        )

        if is_manual_soap:
            completion_embed = discord.Embed(
                title="✅ You're all set!",
                description="Services such as Pokemon Bank, Nintendo Network IDs, System Transfers, and the Nintendo eShop should now be working. You'll also be able to create a new Nintendo Network ID for your new region.\n\nSince this channel was created manually, **please let a Soaper know to close this channel** if you don't have any further questions.",
                color=discord.Color.blurple(),
            )
            view = None  # Don't show any buttons for manual SOAP channels
        else:
            completion_embed = discord.Embed(
                title="❔ Do you have any further questions?",
                description="Services such as Pokemon Bank, Nintendo Network IDs, System Transfers, and the Nintendo eShop should now be working. You'll also be able to create a new Nintendo Network ID for your new region. \n\n**Please click one of the buttons below.**",
                color=discord.Color.blurple(),
            )
            completion_embed.set_footer(
                text=(
                    f"Otherwise, this channel will automatically close in "
                    f"{SOAP_COMPLETION_AUTO_CLOSE_MINUTES} minutes."
                )
            )

            view = CompletionFollowUpView(
                channel_id=channel_id,
                show_close_button=True,
                bot=interaction.client,
                guild=interaction.guild,
            )

        # Send followup
        if interaction.response.is_done():
            if view is not None:
                await interaction.followup.send(embed=completion_embed, view=view)
            else:
                await interaction.followup.send(embed=completion_embed)
        else:
            if view is not None:
                await interaction.response.send_message(
                    embed=completion_embed, view=view
                )
            else:
                await interaction.response.send_message(embed=completion_embed)

    @discord.ui.button(
        label="No, I need help",
        style=discord.ButtonStyle.danger,
        emoji="❕",
        custom_id="eshop_error",
    )
    async def eshop_error_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Send assistance requested embed"""
        # Disable buttons
        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            await interaction.response.defer()

        # Show SOAP helper with context for eShop issues
        embed = discord.Embed(
            title="🔍 SOAP Helper",
            description=(
                "Select the issue you're having from the dropdown below.\n\n"
                "If you can't find what you're looking for, select **'My option is not listed here.'** "
                "to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        view = SoapHelperView(context="eshop_issue")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)


class SOAPAutomationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._status_locks: dict[int, asyncio.Lock] = {}  # SOAP channel ID -> lock for its status updates
        self._progress_messages: dict[int, discord.Message] = {}  # SOAP channel ID -> its progress bar

    def cog_unload(self):
        self.essential_sweeper.cancel()

    def _generate_progress_bar(self, percentage: int) -> str:
        """Generate an ASCII progress bar based on percentage (wider version)"""
        # The percentage is padded to 3 digits so the bar is the same length at every percentage,
        # and short enough that the loading emoji stays on the same line on mobile
        bar_width = 23
        filled = (percentage * bar_width) // 100
        empty = bar_width - filled
        return f"`[{'#' * filled}{' ' * empty}] {percentage:>3}%` <a:loading:{LOADING_EMOTE_ID}>"

    async def _update_progress_message(
        self, target_channel: discord.TextChannel, percentage: int, footer: str = None
    ) -> bool:
        """Update or create a progress message. Returns True if message was found and updated."""
        progress_bar = self._generate_progress_bar(percentage)
        embed = discord.Embed(title=f"{progress_bar}", color=discord.Color.blue())
        embed.set_author(name="🧼 SOAP Transfer - In Progress")
        if footer:
            embed.set_footer(text=footer)

        # Try to find and edit the existing progress message
        progress_message = await self._find_progress_message(target_channel)
        if progress_message:
            try:
                await progress_message.edit(embed=embed)
                return True
            except discord.HTTPException:
                pass  # it's gone, so post a new one
        # No progress message yet, so post one and remember it for the next update
        self._progress_messages[target_channel.id] = await target_channel.send(embed=embed)
        return False

    def _is_progress_message(self, msg: discord.Message) -> bool:
        return (
            msg.author == self.bot.user
            and bool(msg.embeds)
            and msg.embeds[0].author is not None
            and msg.embeds[0].author.name == "🧼 SOAP Transfer - In Progress"
        )

    async def _find_progress_message(self, target_channel: discord.TextChannel):
        """Find the progress message in the channel. Returns the message or None."""
        remembered = self._progress_messages.get(target_channel.id)
        if remembered is not None:
            return remembered
        # Not remembered (e.g. after a restart), so look for it
        async for msg in target_channel.history(limit=50):
            if self._is_progress_message(msg):
                self._progress_messages[target_channel.id] = msg
                return msg
        return None

    async def _delete_progress_message(self, target_channel: discord.TextChannel):
        """Delete the progress message from the channel asynchronously."""
        self._progress_messages.pop(target_channel.id, None)

        async def delete_progress():
            # Remove every progress bar, including any duplicates
            async for msg in target_channel.history(limit=50):
                if self._is_progress_message(msg):
                    try:
                        await msg.delete()
                    except discord.HTTPException:
                        pass

        # Run deletion in background without blocking
        asyncio.create_task(delete_progress())

    async def create_soap_interface(self, channel, user):
        """Create the welcome embed for new SOAP channels"""
        # Welcome embed
        welcome_embed = discord.Embed(
            title="🧼 Welcome to your SOAP Channel!",
            description="We're excited to assist you! To get started, please read the following:\n\n",
            color=discord.Color.blue(),
        )
        await channel.send(content=user.mention, embed=welcome_embed)

        # Step-by-step instructions in a separate embed
        steps_embed = discord.Embed(
            title="1️⃣ Please provide your serial number",
            description=(
                "To find your console's serial number, refer to the sticker on the back of your console. On a New 3DS, the sticker is on the front of the console underneath the faceplate. The serial number has a two or three-letter prefix followed by nine numbers.\n\n"
                "Were you able to find your serial number?"
            ),
            color=discord.Color.blue(),
        )
        await channel.send(embed=steps_embed, view=SerialNumberCheckView())

        # steps_embed = discord.Embed(
        #     title="📁 Step-by-Step Instructions",
        #     description=(
        #         "1. Ensure your SD card is in your console\n"
        #         "2. Hold **START** while powering on → this will boot you into GodMode9\n"
        #         "3. Navigate to `SysNAND Virtual`\n"
        #         "4. Select `essential.exefs`\n"
        #         "5. Select `Copy to 0:/gm9/out` (select Overwrite field(s) if prompted)\n"
        #         "6. Power off your console\n"
        #         "7. Insert your SD card into your PC or connect to your console via FTPD\n"
        #         "8. Navigate to `/gm9/out/`, where essential.exefs should be located\n"
        #         "9. Upload the `essential.exefs` file and provide your serial number below\n"
        #         "10. Please wait for a Soaper to assist you"
        #     ),
        #     color=discord.Color.blue(),
        # )
        # await channel.send(embed=steps_embed)
        # The after hours notice is sent once steps 1 and 2 are done, see send_awaiting_approval

    @command_with_perms(
        min_role="Developer",
        name="testqueue",
        aliases=["queuetest", "queuepreview"],
        help="Shows what this channel's soap-queue request looks like (Developer only)",
    )
    async def testqueue(self, ctx):
        """Post the soap-queue embed here, using this channel's data if it's a helpee channel."""
        member = await member_from_topic(ctx.channel) if getattr(ctx.channel, "topic", None) else None
        if member is not None:
            embed = soap_queue_embed(
                member,
                ctx.channel,
                await entered_serial(ctx.channel),
                case_notes(ctx.channel),
                essential_signed(ctx.channel.id, member.id),
            )
        else:
            # Not a helpee channel, so show sample data
            author = ctx.author if hasattr(ctx, "author") else ctx.user
            embed = soap_queue_embed(
                author,
                ctx.channel,
                "YJM123456784",
                ["Entered a serial number in the wrong format: `YJM1234`", "Uploaded a file that isn't an .exefs: `photo.jpg`"],
                True,
            )
        await ctx.respond(embed=embed, view=SoapQueueView())

    @command_with_perms(
        min_role="Soaper",
        name="reset",
        aliases=["resetchannel", "testsoap", "soaptest", "testsoapflow"],
        help="Restarts the SOAP channel setup in the current channel (Soaper+ only)",
    )
    async def reset(self, ctx):
        """Run create_soap_interface in the current channel, for the helpee named in its topic."""
        member = await member_from_topic(ctx.channel) if getattr(ctx.channel, "topic", None) else None
        if member is None:
            # Not a helpee channel (e.g. testing), so run it for whoever used the command
            member = ctx.author if hasattr(ctx, "author") else getattr(ctx, "user", None)
        if not isinstance(member, discord.Member):
            await ctx.respond("Could not get user.", ephemeral=True)
            return
        await ctx.respond("Restarting the channel setup here...", ephemeral=True)
        reset_case_setup(ctx.channel)
        await self.create_soap_interface(ctx.channel, member)

    @commands.Cog.listener()
    async def on_ready(self):
        """Register persistent views on bot startup"""
        self.bot.add_view(EshopVerificationView())
        self.bot.add_view(SerialNumberCheckView())
        self.bot.add_view(SerialNumberFollowUpView())
        self.bot.add_view(CopySerialView())  # new serial messages don't have it, but older ones still do
        self.bot.add_view(EssentialUploadView())
        self.bot.add_view(EssentialFollowUpView())
        self.bot.add_view(SerialMismatchView())
        self.bot.add_view(SoapQueueView())
        if not self.essential_sweeper.is_running():
            self.essential_sweeper.start()

    @tasks.loop(hours=1)
    async def essential_sweeper(self):
        """Wipe stored essentials whose channel is gone (e.g. deleted while the bot was offline) or that are too old."""
        # A server that hasn't loaded would look like it has no channels, so wait for the next run
        if not self.bot.guilds or any(guild.unavailable for guild in self.bot.guilds):
            return
        open_ids = {channel.id for guild in self.bot.guilds for channel in guild.text_channels}
        try:
            wiped = sweep_essentials(open_ids)
        except OSError as e:
            print(f"Could not sweep stored essentials: {e}")
            return
        if wiped:
            print(f"Wiped {wiped} stored essential.exefs file(s)")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """A deleted channel's essential.exefs isn't needed anymore."""
        forget_essential(channel)

    @discord.slash_command(
        name="essential",
        description="Sends you this channel's essential.exefs (only you can see it)",
    )
    async def essential(self, ctx: discord.ApplicationContext):
        """The helpee's essential.exefs isn't posted in the channel, so Soapers get it from here.
        Slash only, since only a slash command can reply with a message just the sender can see."""
        soaper = discord.utils.get(ctx.guild.roles, name="Soaper") if ctx.guild else None
        if soaper is None or not _has_role_or_higher(ctx.author, soaper):
            await ctx.respond("You must be a Soaper or higher to use this.", ephemeral=True)
            return
        data = load_essential(ctx.channel.id, helpee_id(ctx.channel))
        if data is None:
            await ctx.respond("There's no essential.exefs saved for this channel's helpee.", ephemeral=True)
            return
        await ctx.respond(file=discord.File(io.BytesIO(data), filename="essential.exefs"), ephemeral=True)

    @commands.Cog.listener("on_message")
    async def block_chat_essential(self, message: discord.Message):
        """Helpees must upload essential.exefs with the button, so remove any sent in the chat."""
        channel = message.channel
        if (
            message.author.bot
            or not isinstance(channel, discord.TextChannel)
            or not channel.category
            or channel.category.id != SOAP_CHANNEL_CATEGORY_ID
            or not channel.name.endswith(SOAP_CHANNEL_SUFFIX)
        ):
            return
        if not any(a.filename.lower().endswith(".exefs") for a in message.attachments):
            return
        # Only the helpee; Soapers can still share files
        m = re.search(r"<@!?(\d+)>", channel.topic or "")
        if not m or int(m.group(1)) != message.author.id:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            return
        add_case_note(channel, "Sent essential.exefs in the chat instead of using the upload button")

        # Only offer the button once step 2 has been reached
        step2_reached = False
        async for msg in channel.history(limit=100):
            if msg.author.id == self.bot.user.id and msg.embeds and msg.embeds[0].title == STEP2_TITLE:
                step2_reached = True
                break

        embed = discord.Embed(
            title="📁 Please use the upload button",
            color=discord.Color.orange(),
        )
        if step2_reached:
            embed.description = (
                "Please don't send your `essential.exefs` file in the chat. "
                "Use the button above to upload it instead."
            )
            await channel.send(content=message.author.mention, embed=embed)
        else:
            embed.description = (
                "Please don't send your `essential.exefs` file in the chat. "
                "Enter your serial number above first, and you'll be able to upload it after."
            )
            await channel.send(content=message.author.mention, embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """soap-cat's status updates arrive seconds apart and would otherwise be handled at the same time,
        so a later update could miss the progress bar an earlier one was still posting and post a second one.
        Updates for each SOAP channel are handled one at a time, in the order they arrive."""
        if not message.guild or message.channel.id != BOTS_ONLY_CHANNEL_ID or message.author.id == self.bot.user.id:
            return
        match = re.match(r"^SOAP_STATUS\s+(\d{15,25})\b", (message.content or "").strip(), re.IGNORECASE)
        lock = self._status_locks.setdefault(int(match.group(1)), asyncio.Lock()) if match else asyncio.Lock()
        # Nothing is awaited before taking the lock, so updates can't swap order on the way in
        async with lock:
            await self._handle_status_message(message)

    async def _handle_status_message(self, message: discord.Message):
        """Respond in the user's SOAP channel to a status update from soap-cat."""
        # Bots only channel only
        if not message.guild or message.channel.id != BOTS_ONLY_CHANNEL_ID:
            return

        # Ignore self messages
        if message.author.id == self.bot.user.id:
            return

        content = (message.content or "").strip()
        match = re.match(
            r"^SOAP_STATUS\s+(\d{15,25})\s+([A-Z_]+)(?:\s+([A-Z0-9_]+))?\s*$",
            content,
            re.IGNORECASE,
        )
        if not match:
            print(f"No match found for {content}")
            return

        channel_id = int(match.group(1))
        status_text = match.group(2).upper()
        status_detail = match.group(3).upper() if match.group(3) else None

        serial_number = status_detail if status_text in ["SUCCESS", "LOTTERY"] else None

        # Get the SOAP channel by ID
        target_channel = message.guild.get_channel(channel_id)

        try:
            # Just in case channel is missing, send warning
            if target_channel is None:
                await message.channel.send(
                    f"RESPONSE_ACK {channel_id} {status_text} [WARN: CHANNEL NOT FOUND]"
                )
            else:
                await message.channel.send(f"RESPONSE_ACK {channel_id} {status_text}")
        except Exception:
            pass

        # If we don't have a channel, we can't route anything further.
        if target_channel is None:
            return

        # Progress status mapping
        progress_percentages = {
            "START": 0,
            "SERIAL_CHECK_ATTEMPT": 5,
            "QUEUED": 10,
            "CLEANINTY_INIT": 10,
            "CLEANINTY_SERIAL_CHECK": 15,
            "ESHOP_REGION_CHANGE_ATTEMPT": 25,
            "ESHOP_REGION_CHANGE_SUCCESS": 60,
            "SYSTEM_TRANSFER_ATTEMPT": 65,
            "ESHOP_DELETE_SUCCESS": 90,
            "SYSTEM_TRANSFER_SUCCESS": 90,
            "SUCCESS": 100,
        }

        # Footer messages for each progress status
        progress_footers = {
            "START": "Initializing SOAP transfer...",
            "QUEUED": "Your request is in the queue, please wait...",
            "CLEANINTY_INIT": "Initializing cleaninty...",
            "CLEANINTY__INIT_SUCCESS": "Cleaninty initialized...",
            "SERIAL_CHECK_ATTEMPT": "Verifying serial number...",
            "ESHOP_REGION_CHANGE_ATTEMPT": "Attempting eShopRegionChange on source...",
            "SYSTEM_TRANSFER_ATTEMPT": "Sticky titles are sticking, performing system transfer...",
            "ESHOP_REGION_CHANGE_SUCCESS": "Sticky titles aren't sticking (soap lottery), deleting eShop account...",
            "ESHOP_DELETE_SUCCESS": "eShop account deleted successfully...",
            "SYSTEM_TRANSFER_SUCCESS": "System transfer completed successfully...",
            "SUCCESS": "SOAP transfer completed successfully!",
        }

        if status_text == "PROGRESS" and target_channel:
            if status_detail == "START":
                # The transfer started, so it's approved even if nobody pressed Start SOAP
                await mark_soap_approved(target_channel)
                # Send initial progress message (or reset the existing one, e.g. if the transfer is retried)
                await self._update_progress_message(target_channel, 0, progress_footers.get("START", ""))
            elif status_detail and status_detail in progress_percentages:
                # Update existing progress message
                footer = progress_footers.get(status_detail, "")
                await self._update_progress_message(
                    target_channel, progress_percentages[status_detail], footer
                )

        if status_text == "SUCCESS" and target_channel:
            # Increment SOAP count when SUCCESS is received
            tracker_cog = self.bot.get_cog("TrackerCog")
            if tracker_cog:
                tracker_cog.increment_soap_count()

            # Send SUCCESS message immediately
            # Try to recover the user ID from the channel topic so we can mention them.
            user_id = None
            topic = getattr(target_channel, "topic", None)
            if topic:
                m = re.search(r"<@!?(\d+)>", topic)
                if m:
                    try:
                        user_id = int(m.group(1))
                    except ValueError:
                        user_id = None

            boot_instruction = (
                f"Boot the console with the serial {serial_number} normally (with the SD inserted into the console)"
                if serial_number != "SKIP"
                else "Boot the console normally (with the SD inserted into the console)"
            )
            embed = discord.Embed(
                title="🎉 SOAP Transfer Complete",
                description="Please follow the following steps to verify that everything is working correctly:\n\n"
                f"**1.** {boot_instruction}\n"
                "**2.** Then go to: **System Settings** → **Other Settings** → **Profile** → **Region Settings**\n"
                "and ensure the desired country is selected.\n"
                "**3.** If using Pretendo, switch to Nintendo Network with Nimbus.\n"
                "**4.** Then try opening the eShop.\n"
                "**5.** Does the eShop launch successfully?",
                color=discord.Color.green(),
            )
            embed.set_footer(
                text="⚠️ If you want to system transfer to/from another 3DS, you must wait 7 days.\nOtherwise, you're free to use your console as normal."
            )
            view = EshopVerificationView()
            user_mention = f"<@{user_id}>" if user_id else None
            await target_channel.send(content=user_mention, embed=embed, view=view)

            # Delete progress message asynchronously after sending success message
            await self._delete_progress_message(target_channel)
            await delete_awaiting_message(target_channel)

        if status_text == "LOTTERY" and target_channel:
            # Send LOTTERY message immediately
            # Try to recover the user ID from the channel topic so we can mention them.
            user_id = None
            topic = getattr(target_channel, "topic", None)
            if topic:
                m = re.search(r"<@!?(\d+)>", topic)
                if m:
                    try:
                        user_id = int(m.group(1))
                    except ValueError:
                        user_id = None

            boot_instruction = (
                f"Boot the console with the serial {serial_number} normally (with the SD inserted into the console)"
                if serial_number != "SKIP"
                else "Boot the console normally (with the SD inserted into the console)"
            )
            embed = discord.Embed(
                title="🎉 SOAP Transfer Complete",
                description="You won the Soap Lottery! Please follow the following steps to verify that everything is working correctly:\n\n"
                f"**1.** {boot_instruction}\n"
                "**2.** Then go to: **System Settings** → **Other Settings** → **Profile** → **Region Settings**\n"
                "and ensure the desired country is selected.\n"
                "**3.** If using Pretendo, switch to Nintendo Network with Nimbus.\n"
                "**4.** Then try opening the eShop.\n"
                "**5.** Does the eShop launch successfully?",
                color=discord.Color.yellow(),
            )
            embed.set_footer(
                text="No system transfer was needed - you can transfer to/from another 3DS right away if you want!"
            )
            view = EshopVerificationView()
            user_mention = f"<@{user_id}>" if user_id else None
            await target_channel.send(content=user_mention, embed=embed, view=view)

            # Update progress to 100% and delete asynchronously after sending lottery message
            async def update_and_delete_progress():
                # Update to 100% with LOTTERY footer
                footer = progress_footers.get(
                    "SUCCESS", "SOAP transfer completed successfully!"
                )
                await self._update_progress_message(target_channel, 100, footer)
                # Wait a moment then delete
                await asyncio.sleep(1)
                await self._delete_progress_message(target_channel)

            # Run update and deletion in background without blocking
            asyncio.create_task(update_and_delete_progress())
            await delete_awaiting_message(target_channel)

        if status_text == "ERROR" and target_channel:
            # Delete progress message when error occurs
            await self._delete_progress_message(target_channel)
            await delete_awaiting_message(target_channel)

            # Check if it's a serial mismatch error
            is_serial_error = (
                status_detail and "SERIAL_MISMATCH" in status_detail.upper()
            )

            if is_serial_error:
                # Try to recover the user ID from the channel topic so we can mention them.
                user_id = None
                topic = getattr(target_channel, "topic", None)
                if topic:
                    m = re.search(r"<@!?(\d+)>", topic)
                    if m:
                        try:
                            user_id = int(m.group(1))
                        except ValueError:
                            user_id = None

                add_case_note(
                    target_channel, "Serial number didn't match their essential.exefs (found during the SOAP)"
                )
                # Send findserial instructions
                user_mention = f"<@{user_id}>" if user_id else None
                await target_channel.send(
                    content=user_mention, embed=serial_mismatch_embed(), view=SerialMismatchView()
                )

            else:
                # Error - requires Soaper intervention
                embed = discord.Embed(
                    title="🛑 Something went wrong...",
                    description="Soapers, please check the error log for more information.",
                    color=discord.Color.red(),
                )
                if status_detail:
                    embed.set_footer(text=f"Error code: {status_detail}")
                await target_channel.send(embed=embed)


def setup(bot):
    return bot.add_cog(SOAPAutomationCog(bot))
