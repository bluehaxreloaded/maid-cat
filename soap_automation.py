import discord
import re
import asyncio
from discord.ext import commands
from constants import (
    BOTS_ONLY_CHANNEL_ID,
    SOAP_CHANNEL_CATEGORY_ID,
    MANUAL_SOAP_CATEGORY_ID,
    LOADING_EMOTE_ID,
    SOAPER_ROLE_ID,
)


class CompletionFollowUpView(discord.ui.View):
    """View for the follow-up questions after eShop verification"""

    def __init__(self, channel_id=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id

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
            # Fallback to interaction channel (for ephemeral messages, this should be the SOAP channel)
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
            # Send assistance requested embed and ping Soaper role
            soaper_ping = f"<@&{SOAPER_ROLE_ID}>"
            embed = discord.Embed(
                title="🆘 Assistance Requested",
                description=f"{interaction.user.mention} has requested additional help. Please wait for a Soaper to assist you.",
                color=discord.Color.yellow(),
            )
            embed.set_footer(
                text="Describe in detail what's happening and please include error codes if possible."
            )
            await channel.send(content=soaper_ping, embed=embed)


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

        completion_embed = discord.Embed(
            title="❔ Do you have any further questions?",
            description="Services such as Pokemon Bank, Nintendo Network IDs, System Transfers, and the Nintendo eShop should now be working, please click one of the buttons below. ",
            color=discord.Color.blurple(),
        )
        channel_id = interaction.channel_id
        view = CompletionFollowUpView(channel_id)

        # Send followup
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=completion_embed, view=view
            )
        else:
            await interaction.response.send_message(
                embed=completion_embed, view=view
            )

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

        # Send assistance requested embed
        soaper_ping = f"<@&{SOAPER_ROLE_ID}>"
        embed = discord.Embed(
            title="🆘 Assistance Requested",
            description=f"{interaction.user.mention} has requested additional help. Please wait for a Soaper to assist you.",
            color=discord.Color.yellow(),
        )
        embed.set_footer(
            text="Describe in detail what's happening and please include error codes if possible."
        )

        if interaction.response.is_done():
            await interaction.followup.send(content=soaper_ping, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
        else:
            await interaction.response.send_message(content=soaper_ping, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))


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
                text="⚠️ System transfer was required - wait 7 days before transferring from another 3DS."
            )
            view = EshopVerificationView()
            user_mention = f"<@{user_id}>"
            await target_channel.send(content=user_mention, embed=embed, view=view)

            # Delete progress message asynchronously after sending success message
            async def delete_progress():
                progress_message = None
                async for msg in target_channel.history(limit=50):
                    if msg.author == self.bot.user and msg.embeds:
                        if (
                            msg.embeds[0].author
                            and msg.embeds[0].author.name
                            == "🧼 SOAP Transfer - In Progress"
                        ):
                            progress_message = msg
                            break
                if progress_message:
                    try:
                        await progress_message.delete()
                    except Exception:
                        pass

            # Run deletion in background without blocking
            asyncio.create_task(delete_progress())

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
                text="No system transfer was needed - you can transfer from another 3DS right away if you want!"
            )
            view = EshopVerificationView()
            user_mention = f"<@{user_id}>"
            await target_channel.send(content=user_mention, embed=embed, view=view)

            # Update progress to 100% and delete asynchronously after sending lottery message
            async def update_and_delete_progress():
                progress_message = None
                async for msg in target_channel.history(limit=50):
                    if msg.author == self.bot.user and msg.embeds:
                        if (
                            msg.embeds[0].author
                            and msg.embeds[0].author.name
                            == "🧼 SOAP Transfer - In Progress"
                        ):
                            progress_message = msg
                            break
                if progress_message:
                    try:
                        # Update to 100% with LOTTERY footer
                        footer = progress_footers.get(
                            "SUCCESS", "SOAP transfer completed successfully!"
                        )
                        await self._update_progress_message(target_channel, 100, footer)
                        # Wait a moment then delete
                        await asyncio.sleep(1)
                        await progress_message.delete()
                    except Exception:
                        pass

            # Run update and deletion in background without blocking
            asyncio.create_task(update_and_delete_progress())

        if status_text == "ERROR" and target_channel:
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
