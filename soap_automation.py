import discord
import re
import asyncio
from discord.ext import commands
from log import log_to_soaper_log
from constants import (
    BOTS_ONLY_CHANNEL_ID,
    SOAP_CHANNEL_CATEGORY_ID,
    MANUAL_SOAP_CATEGORY_ID,
    LOADING_EMOTE_ID,
    SOAP_COMPLETION_AUTO_CLOSE_MINUTES,
    is_late_night_hours,
)


class CompletionFollowUpView(discord.ui.View):
    """View for the follow-up questions after eShop verification"""

    def __init__(self, channel_id=None, show_close_button=True, auto_close_task=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.show_close_button = show_close_button
        self.auto_close_task = auto_close_task

        # Remove I'm good if manual SOAP
        if not show_close_button:
            for item in list(self.children):
                if (
                    isinstance(item, discord.ui.Button)
                    and item.custom_id == "completion_no_thanks"
                ):
                    self.remove_item(item)

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
            # Send SOAP helper embed with dropdown
            from soap_helper import SoapHelperView
            embed = discord.Embed(
                title="🧼 SOAP Helper",
                description=(
                    f"{interaction.user.mention} has some questions. "
                    "Select a topic from the dropdown below to get answers to common questions.\n\n"
                    "If you can't find what you're looking for, select **'Still Need Help'** to request assistance from a Soaper."
                ),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Select an option from the dropdown menu below")
            view = SoapHelperView()
            await channel.send(embed=embed, view=view)


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
        is_manual_soap = channel and channel.category and channel.category.id == MANUAL_SOAP_CATEGORY_ID
        
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
            
            # Create auto-close task first so we can pass it to the view
            # Capture references before the async sleep
            guild = interaction.guild
            bot = interaction.client
            
            async def auto_close():
                try:
                    await asyncio.sleep(SOAP_COMPLETION_AUTO_CLOSE_MINUTES * 60)
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        return
                    # Re-check that this is still a SOAP channel and not in the manual category
                    if (
                        not channel.category
                        or channel.category.id == MANUAL_SOAP_CATEGORY_ID
                    ):
                        return
                    
                    # Extract user ID from channel topic for logging
                    user_id = None
                    if channel.topic:
                        # Topic format: "This is the SOAP channel for <@user_id>, ..."
                        match = re.search(r'<@!?(\d+)>', channel.topic)
                        if match:
                            user_id = int(match.group(1))
                    
                    soap_cog = bot.get_cog("SoapCog")
                    if soap_cog:
                        # Delete the channel (pass None for ctx since this is auto-close)
                        await soap_cog.deletesoap(channel, None)
                        
                        # Log the auto-close with user ID
                        if user_id:
                            try:
                                user = guild.get_member(user_id)
                                if user:
                                    ctx = type('Context', (), {
                                        'guild': guild,
                                        'message': type('Message', (), {
                                            'author': user,
                                            'content': 'Completion timeout'
                                        })()
                                    })()
                                    await log_to_soaper_log(ctx, "Removed SOAP Channel")
                            except Exception:
                                pass
                except asyncio.CancelledError:
                    # Task was cancelled (user clicked a button)
                    pass
                except Exception:
                    # Fail silently; auto-close is best-effort
                    pass

            auto_close_task = asyncio.create_task(auto_close())
            view = CompletionFollowUpView(channel_id, show_close_button=True, auto_close_task=auto_close_task)

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

        # Send SOAP helper embed with dropdown
        from soap_helper import SoapHelperView
        embed = discord.Embed(
            title="🧼 SOAP Helper",
            description=(
                "We're here to help. Select a topic from the dropdown below.\n\n"
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        view = SoapHelperView()

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)


class SOAPAutomationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _generate_progress_bar(self, percentage: int) -> str:
        """Generate an ASCII progress bar based on percentage (wider version)"""
        bar_width = 25
        filled = (percentage * bar_width) // 100
        empty = bar_width - filled
        return f"`[{'#' * filled}{' ' * empty}] {percentage}%` <a:loading:{LOADING_EMOTE_ID}>"

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
        progress_message = None
        async for msg in target_channel.history(limit=50):
            if msg.author == self.bot.user and msg.embeds:
                if (
                    msg.embeds[0].author
                    and msg.embeds[0].author.name == "🧼 SOAP Transfer - In Progress"
                ):
                    progress_message = msg
                    break

        if progress_message:
            try:
                await progress_message.edit(embed=embed)
                return True
            except Exception:
                # If edit fails, send a new message
                await target_channel.send(embed=embed)
                return False
        else:
            # If no progress message found, send a new one
            await target_channel.send(embed=embed)
            return False

    async def _find_progress_message(self, target_channel: discord.TextChannel):
        """Find the progress message in the channel. Returns the message or None."""
        async for msg in target_channel.history(limit=50):
            if msg.author == self.bot.user and msg.embeds:
                if (
                    msg.embeds[0].author
                    and msg.embeds[0].author.name
                    == "🧼 SOAP Transfer - In Progress"
                ):
                    return msg
        return None

    async def _delete_progress_message(self, target_channel: discord.TextChannel):
        """Delete the progress message from the channel asynchronously."""
        async def delete_progress():
            progress_message = await self._find_progress_message(target_channel)
            if progress_message:
                try:
                    await progress_message.delete()
                except Exception:
                    pass

        # Run deletion in background without blocking
        asyncio.create_task(delete_progress())

    async def create_soap_interface(self, channel, user):
        """Create the welcome embed for new SOAP channels"""
        # Welcome embed
        embed = discord.Embed(
            title="🧼 Welcome to your SOAP Channel!",
            description="This is where we'll perform your SOAP transfer. To get started, please follow the instructions below:\n\n",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📋 Step-by-Step Instructions",
            value="1. Ensure your SD card is in your console\n"
            "2. Hold **START** while powering on → this will boot you into GodMode9\n"
            "3. Navigate to `SysNAND Virtual`\n"
            "4. Select `essential.exefs`\n"
            "5. Select `Copy to 0:/gm9/out` (select Overwrite field(s) if prompted)\n"
            "6. Power off your console\n"
            "7. Insert your SD card into your PC or connect to your console via FTPD\n"
            "8. Navigate to `/gm9/out/`, where essential.exefs should be located\n"
            "9. Upload the `essential.exefs` file and provide your serial number below\n"
            "10. Please wait for a Soaper to assist you",
            inline=False,
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

    @commands.Cog.listener()
    async def on_ready(self):
        """Register persistent views on bot startup"""
        self.bot.add_view(EshopVerificationView())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for status updates in the processing channel and respond in the user's SOAP channel."""
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

        user_id = int(match.group(1))
        status_text = match.group(2).upper()
        status_detail = match.group(3).upper() if match.group(3) else None

        serial_number = status_detail if status_text in ["SUCCESS", "LOTTERY"] else None

        try:
            await message.channel.send(f"RESPONSE_ACK {user_id} {status_text}")
        except Exception:
            pass

        target_channel = None
        mention_plain = f"<@{user_id}>"
        mention_nick = f"<@!{user_id}>"
        for ch in message.guild.text_channels:
            if ch.category and ch.category.id in [
                SOAP_CHANNEL_CATEGORY_ID,
                MANUAL_SOAP_CATEGORY_ID,
            ]:
                topic = getattr(ch, "topic", None)
                if topic and (mention_plain in topic or mention_nick in topic):
                    target_channel = ch
                    break

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
                # Send initial progress message
                progress_bar = self._generate_progress_bar(0)
                embed = discord.Embed(
                    title=f"{progress_bar}", color=discord.Color.blue()
                )
                embed.set_footer(text=progress_footers.get("START", ""))
                embed.set_author(name="🧼 SOAP Transfer - In Progress")
                await target_channel.send(embed=embed)
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
            user_mention = f"<@{user_id}>"
            await target_channel.send(content=user_mention, embed=embed, view=view)

            # Delete progress message asynchronously after sending success message
            await self._delete_progress_message(target_channel)

        if status_text == "LOTTERY" and target_channel:
            # Send LOTTERY message immediately
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
            user_mention = f"<@{user_id}>"
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
                progress_message = await self._find_progress_message(target_channel)
                if progress_message:
                    try:
                        await progress_message.delete()
                    except Exception:
                        pass

            # Run update and deletion in background without blocking
            asyncio.create_task(update_and_delete_progress())

        if status_text == "ERROR" and target_channel:
            # Delete progress message when error occurs
            await self._delete_progress_message(target_channel)
            
            # Check if it's a serial mismatch error
            is_serial_error = status_detail and "SERIAL_MISMATCH" in status_detail.upper()
            
            if is_serial_error:
                # Send findserial instructions
                embed = discord.Embed(
                    title="⚠️ Serial Number Mismatch",
                    description=(
                        "The serial number you provided does not match the serial number in your `essentials.exefs` file. Please ensure you have entered the serial number correctly. If you're still having trouble, follow these instructions to find your console's serial number.\n"
                        "To find your console's serial number:\n"
                        "- Hold START while powering on your console. This will boot you into GodMode9.\n"
                        "- Go to `SYSNAND TWLNAND` -> `sys` -> `log` -> `inspect.log`\n"
                        "- Select `Open in Textviewer`.\n\n"
                        "The correct serial number (three-letter prefix followed by nine numbers) should be in the file. "
                        "You may also send us a picture if you're unsure."
                    ),
                    color=discord.Color.yellow(),
                )
                user_mention = f"<@{user_id}>"
                embed.set_footer(text="Once you've found the serial number and send it here, we will resume your SOAP Transfer.")
                await target_channel.send(content=user_mention, embed=embed)
                
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
