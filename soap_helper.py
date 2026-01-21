import discord
import json
import re
from pathlib import Path
from discord.ext import commands
from perms import command_with_perms
from constants import (
    SOAPER_ROLE_ID,
    AWAITING_EMOTE_ID,
    SOAP_COMPLETION_AUTO_CLOSE_MINUTES,
)


def _load_error_info(error_code: str) -> dict | None:
    """Load a single error definition from error_codes.json."""
    error_codes_path = Path(__file__).parent / "error_codes.json"
    try:
        with open(error_codes_path, "r", encoding="utf-8") as f:
            raw_db = json.load(f)

        error_codes_db: dict[str, dict] = {}

        for key, value in raw_db.items():
            if key == "groups" and isinstance(value, dict):
                for group in value.values():
                    codes = group.get("codes", [])
                    template = {k: v for k, v in group.items() if k != "codes"}
                    for code in codes:
                        if isinstance(code, str):
                            error_codes_db[code] = template
            elif isinstance(value, dict):
                error_codes_db[key] = value

        return error_codes_db.get(error_code)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

class ErrorResolutionView(discord.ui.View):
    """View with buttons to confirm if error was resolved"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(
        label="Yes, my issue is resolved",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="error_resolved_yes",
    )
    async def resolved_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Handle when user confirms issue is resolved"""
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(view=self)
    
    @discord.ui.button(
        label="No, I still need help",
        style=discord.ButtonStyle.danger,
        emoji="❕",
        custom_id="error_resolved_no",
    )
    async def not_resolved_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Handle when user still needs help"""
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(view=self)
        
        # Show SOAP helper again
        embed = discord.Embed(
            title="🔍 SOAP Helper",
            description=(
                "Need help with your SOAP transfer? Select the issue you're having from the dropdown below.\n\n"
                "If you can't find what you're looking for, select **'My option is not listed here.'** to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        view = SoapHelperView()
        await interaction.followup.send(embed=embed, view=view)


class EshopResolutionView(discord.ui.View):
    """View shown after helping a user who reported eShop not working."""

    def __init__(self, channel_id=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(
        label="Yes, it works now!",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="eshop_resolution_yes",
    )
    async def eshop_works_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """eShop is working - show the completion follow-up flow"""
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        from soap_automation import CompletionFollowUpView

        channel_id = self.channel_id or interaction.channel_id
        completion_embed = discord.Embed(
            title="❔ Do you have any further questions?",
            description=(
                "Services such as Pokemon Bank, Nintendo Network IDs, System Transfers, "
                "and the Nintendo eShop should now be working. You'll also be able to create "
                "a new Nintendo Network ID for your new region.\n\n"
                "**Please click one of the buttons below.**"
            ),
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
        await interaction.followup.send(embed=completion_embed, view=view)

    @discord.ui.button(
        label="No, I still need help",
        style=discord.ButtonStyle.danger,
        emoji="❕",
        custom_id="eshop_resolution_no",
    )
    async def eshop_still_broken_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """eShop still not working - show helper again"""
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        embed = discord.Embed(
            title="🔍 SOAP Helper",
            description=(
                "Let's try again. Select the issue you're having from the dropdown below.\n\n"
                "If you can't find what you're looking for, select **'My option is not listed here.'** "
                "to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        view = SoapHelperView(context="eshop_issue")
        await interaction.followup.send(embed=embed, view=view)


class IssueResolutionView(discord.ui.View):
    """View shown after helping a user who had other questions."""

    def __init__(self, channel_id=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(
        label="Yes, close the channel",
        style=discord.ButtonStyle.success,
        emoji="👋",
        custom_id="issue_resolution_close",
    )
    async def close_channel_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Issue resolved - close the channel"""
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        channel = None
        if self.channel_id:
            channel = interaction.guild.get_channel(self.channel_id)
        if not channel:
            channel = interaction.channel

        if not channel:
            await interaction.followup.send("Channel not found.", ephemeral=True)
            return

        soap_cog = interaction.client.get_cog("SoapCog")
        if soap_cog:
            try:
                await soap_cog.deletesoap(channel, interaction)
            except Exception:
                pass
        else:
            await interaction.followup.send("Error: SoapCog not found.", ephemeral=True)

    @discord.ui.button(
        label="No, I still need help",
        style=discord.ButtonStyle.danger,
        emoji="❕",
        custom_id="issue_resolution_no",
    )
    async def still_need_help_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Issue not resolved - show helper again"""
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        embed = discord.Embed(
            title="🔍 SOAP Helper",
            description=(
                "Let's try again. Select the issue you're having from the dropdown below.\n\n"
                "If you can't find what you're looking for, select **'My option is not listed here.'** "
                "to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        view = SoapHelperView(context="other_questions")
        await interaction.followup.send(embed=embed, view=view)


class ErrorCodeModal(discord.ui.Modal):
    """Modal for submitting error codes"""

    def __init__(self, context=None):
        super().__init__(title="🆘 Error Code Help")
        self.context = context

        # Create text inputs using the proper pycord syntax
        self.error_code_input = discord.ui.InputText(
            label="What is the error code displayed?",
            placeholder="001-1001",
            required=True,
            max_length=10,
        )
        self.add_item(self.error_code_input)

    def _get_followup_embed_and_view(self, interaction: discord.Interaction):
        """Get the context-aware follow-up embed and view."""
        if self.context == "eshop_issue":
            embed = discord.Embed(
                title="❓ Does the eShop work now?",
                description="After following the steps in the message above, please let us know if this resolved your issue.",
                color=discord.Color.red(),
            )
            view = EshopResolutionView(channel_id=interaction.channel_id)
            return embed, view
        elif self.context == "other_questions":
            embed = discord.Embed(
                title="❓ Was your issue/question resolved?",
                description="After reviewing the instructions/explanation in the message above, please let us know if this resolved your issue.",
                color=discord.Color.red(),
            )
            view = IssueResolutionView(channel_id=interaction.channel_id)
            return embed, view
        return None, None

    async def callback(self, interaction: discord.Interaction):
        """Handle modal submission"""
        error_code = self.error_code_input.value.strip()

        # Validate basic format XXX-XXXX (three digits, dash, four digits)
        if not re.fullmatch(r"\d{3}-\d{4}", error_code):
            invalid_embed = discord.Embed(
                title="🆘 Invalid Error Code Format",
                description=(
                    f"{interaction.user.mention}, error codes are in the format **XXX-XXXX**.\n\n"
                    "Please double-check the error screen on your 3DS. The code is shown at the top as:\n"
                    "**Error Code: XXX-XXXX**.\n\n"
                    "If you don't see an error code or you're unsure, you can go back to the SOAP helper menu."
                ),
                color=discord.Color.orange(),
            )
            invalid_embed.set_image(
                url="https://hacksguidewiki.sfo3.digitaloceanspaces.com/hacksguidewiki/3DS_error_code.jpg"
            )

            target_message = getattr(self, "target_message", None)
            view = InvalidErrorCodeView(target_message=target_message, context=self.context)

            if target_message is not None:
                # Edit the existing awaiting message
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
                try:
                    await target_message.edit(embed=invalid_embed, view=view)
                except Exception:
                    # If editing fails, fall back to sending a new message
                    await interaction.followup.send(embed=invalid_embed, view=view)
            else:
                # No known target message; send a fresh one
                await interaction.response.send_message(embed=invalid_embed, view=view)
            return

        # Load error definition from database
        error_info = _load_error_info(error_code)
        
        if error_info:
            steps_text = "\n".join([f"**{i+1}.** {step}" for i, step in enumerate(error_info['steps'])])
            
            embed = discord.Embed(
                title=f"{error_code} - {error_info['title']}",
                description=(
                    f"{error_info['description']}\n\n"
                    f"**Steps to resolve:**\n{steps_text}"
                ),
                color=discord.Color.blue(),
            )
            
            embed.set_footer(text="Try these steps and let us know if the issue is resolved.")

            target_message = getattr(self, "target_message", None)

            followup_embed, followup_view = self._get_followup_embed_and_view(interaction)

            if target_message is not None:
                # Edit the original 'Awaiting' message with the resolution embed
                try:
                    await target_message.edit(content=None, embed=embed, view=None)
                except Exception:
                    # Fallback to normal behavior if editing fails
                    await interaction.response.send_message(embed=embed)
                    if followup_embed and followup_view:
                        await interaction.followup.send(embed=followup_embed, view=followup_view)
                else:
                    # Use the modal response for the follow-up question
                    if followup_embed and followup_view:
                        await interaction.response.send_message(embed=followup_embed, view=followup_view)
                    else:
                        await interaction.response.defer()
            else:
                # No target message, behave like the original flow
                await interaction.response.send_message(embed=embed)
                if followup_embed and followup_view:
                    await interaction.followup.send(embed=followup_embed, view=followup_view)
        else:
            # Error code not found in our database
            unknown_embed = discord.Embed(
                title="🆘 Unknown Error Code",
                description=(
                    f"{interaction.user.mention} is reporting experiencing an error that is not in our database.\n\n"
                    f"**Error Code:** `{error_code}`\n\n"
                ),
                color=discord.Color.orange(),
            )
            unknown_embed.set_footer(text="Please wait for a Soaper to assist you.")

            # Delete the old awaiting/invalid message
            target_message = getattr(self, "target_message", None)
            if target_message is not None:
                try:
                    await target_message.delete()
                except Exception:
                    pass

            # Ensure the modal interaction is acknowledged
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass

            soaper_ping = f"<@&{SOAPER_ROLE_ID}>"
            await interaction.channel.send(
                content=soaper_ping,
                embed=unknown_embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )


class AwaitingErrorCodeView(discord.ui.View):
    """View that provides a button to open the error code modal."""

    def __init__(self, context=None):
        super().__init__(timeout=None)
        self.context = context

    @discord.ui.button(
        label="🔢 Enter Error Code",
        style=discord.ButtonStyle.primary,
        custom_id="error_code_input_button",
    )
    async def input_error_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Open the error code modal, tied to this awaiting message."""
        modal = ErrorCodeModal(context=self.context)
        modal.target_message = interaction.message
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="⚠️ I need something else",
        style=discord.ButtonStyle.danger,
        custom_id="awaiting_error_no_code",
    )
    async def awaiting_no_code_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """User doesn't have / want to enter an error code – go back to SOAP helper."""
        # Disable buttons on the original awaiting message
        for child in self.children:
            child.disabled = True

        try:
            if interaction.response.is_done():
                await interaction.message.edit(view=self)
            else:
                await interaction.response.edit_message(view=self)
        except Exception:
            pass

        embed = discord.Embed(
            title="🔍 SOAP Helper",
            description=(
                "Need help with your SOAP transfer? Select the issue you're having from the dropdown below.\n\n"
                "If you can't find what you're looking for, select **'My option is not listed here.'** "
                "to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        view = SoapHelperView(context=self.context)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)


class InvalidErrorCodeView(discord.ui.View):
    """Shown when the user enters an incorrectly-formatted error code."""

    def __init__(self, target_message: discord.Message | None, context=None):
        super().__init__(timeout=None)
        self.target_message = target_message
        self.context = context

    @discord.ui.button(
        label="🔢 Enter Error Code",
        style=discord.ButtonStyle.primary,
        custom_id="invalid_error_reenter",
    )
    async def reenter_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Re-open the error code modal."""
        modal = ErrorCodeModal(context=self.context)
        if self.target_message is not None:
            modal.target_message = self.target_message
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="⚠️ I need something else",
        style=discord.ButtonStyle.danger,
        custom_id="invalid_error_no_code",
    )
    async def no_code_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        """Return the user to the main SOAP helper menu."""
        # First disable these buttons on the original message
        for child in self.children:
            child.disabled = True

        try:
            if interaction.response.is_done():
                await interaction.message.edit(view=self)
            else:
                await interaction.response.edit_message(view=self)
        except Exception:
            pass

        embed = discord.Embed(
            title="🔍 SOAP Helper",
            description=(
                "Need help with your SOAP transfer? Select the issue you're having from the dropdown below.\n\n"
                "If you can't find what you're looking for, select **'My option is not listed here.'** "
                "to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        view = SoapHelperView(context=self.context)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)


class SoapHelperDropdown(discord.ui.Select):
    """Dropdown menu for common SOAP questions"""

    def __init__(self, context: str | None = None):
        """
        Initialize the dropdown.

        Args:
            context: The context this helper was triggered from:
                - "eshop_issue": User clicked "No, eShop doesn't work" in automation
                - "other_questions": User clicked "I have more questions" in automation
                - None: Standalone /soaphelp command (no follow-up)
        """
        self.context = context
        options = [
            discord.SelectOption(
                label="The eShop still doesn't work",
                description="Getting an error when opening the eShop",
                emoji="🛒",
                value="eshop_not_working",
            ),
            discord.SelectOption(
                label="Pokèmon Bank still doesn't work",
                description="Getting an error when opening Pokèmon Bank",
                emoji="📦",
                value="pokemon_bank_not_working",
            ),
            discord.SelectOption(
                label="Switching from Pretendo to Nintendo Network?",
                description="Information regarding using Nimbus",
                emoji="🌐",
                value="pretendo_switch",
            ),
            discord.SelectOption(
                label="Where's my serial number?",
                description="How to quickly find your serial number",
                emoji="📂",
                value="serial_number",
            ),
            discord.SelectOption(
                label="What is a SOAP lottery?",
                description="Info regarding lottery SOAPs",
                emoji="🎉",
                value="region_settings",
            ),
            discord.SelectOption(
                label="When can I system transfer?",
                description="Info regarding the seven day cooldown",
                emoji="⏳",
                value="nand_backup",
            ),
            discord.SelectOption(
                label="My option is not listed here.",
                description="Request additional assistance from a Soaper",
                emoji="🆘",
                value="need_help",
            ),
        ]
        super().__init__(
            placeholder="Select a topic to get help...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="soap_helper_dropdown",
        )

    async def callback(self, interaction: discord.Interaction):
        """Handle dropdown selection"""
        value = self.values[0]

        # Disable the dropdown after use to prevent spam
        if self.view is not None:
            for child in self.view.children:
                if isinstance(child, discord.ui.Select):
                    child.disabled = True
            try:
                await interaction.message.edit(view=self.view)
            except Exception:
                pass

        # Create response embed based on selection
        if value in ("eshop_not_working", "pokemon_bank_not_working"):
            # Shared flow for both eShop and Pokémon Bank error-code paths
            awaiting_emoji = discord.utils.get(
                interaction.guild.emojis, id=AWAITING_EMOTE_ID
            )
            title_prefix = f"{awaiting_emoji} " if awaiting_emoji else ""
            awaiting_embed = discord.Embed(
                title=f"{title_prefix}Awaiting Error Code",
                description=(
                    f"{interaction.user.mention}, enter the code shown on your console in the form that just opened.\n"
                    "If you closed it by accident, you can click **Enter Error Code** below to reopen it."
                ),
                color=discord.Color.orange(),
            )
            awaiting_msg = await interaction.channel.send(
                embed=awaiting_embed,
                view=AwaitingErrorCodeView(context=self.context),
            )

            modal = ErrorCodeModal(context=self.context)
            modal.target_message = awaiting_msg
            await interaction.response.send_modal(modal)
            return

        elif value == "pretendo_switch":
            embed = discord.Embed(
                title="🌐 Switching Between Pretendo and Nintendo Network",
                description=(
                    "If you're using Pretendo and need to access Nintendo services:\n\n"
                    "**1.** Open the **Nimbus** app on your 3DS.\n"
                    "**2.** Select **Switch to Nintendo Network**.\n"
                    "**3.** Your console will reboot.\n"
                    "**4.** You can now access Nintendo services like the eShop.\n\n"
                    "To switch back to Pretendo, use Nimbus again and select **Switch to Pretendo**."
                ),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="You'll need to reboot each time you switch.")

        elif value == "serial_number":
            embed = discord.Embed(
                title="📂 Finding Your Serial Number",
                description=(
                    "Follow these instructions to find your console's serial number.\n\n"
                    "**To find your console's serial number:**\n"
                    "- Hold START while powering on your console. This will boot you into GodMode9.\n"
                    "- Go to `SYSNAND TWLNAND` -> `sys` -> `log` -> `inspect.log`\n"
                    "- Select `Open in Textviewer`.\n\n"
                    "The correct serial number (three-letter prefix followed by nine numbers) should be in the file."
                ),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="You may also send us a picture if you're unsure.")
            
        elif value == "region_settings":  # "What is a SOAP lottery?"
            embed = discord.Embed(
                title="🎉 What is a SOAP Lottery?",
                description=(
                    "A **SOAP lottery** occurs when your SOAP transfer doesn't require a system transfer to complete.\n\n"
                    "**Normal SOAP:**\n"
                    "Most SOAP transfers require a system transfer from a donor console, which means you'll need to "
                    "wait **7 days** before you can do another system transfer from your old console to this one.\n\n"
                    "**SOAP Lottery:**\n"
                    "If you win the SOAP lottery, no system transfer was needed! This means:\n"
                    "• You can do a system transfer from another 3DS right away if you want\n"
                    "• No waiting period required\n"
                    "• Your SOAP transfer completed successfully without needing a donor console\n\n"
                    "You'll know if you won the lottery because the completion message will mention it!"
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text="Winning the lottery is random and depends on your console's state.")
            
        elif value == "nand_backup":  # "Do I have to wait 7 days?"
            embed = discord.Embed(
                title="⏳ Post-SOAP System Transfer",
                description=(
                    "If you don't want to system transfer to or from another 3DS, you're free to use your newly SOAPed console as normal. If you do want to system transfer:\n\n"
                    "**After a normal SOAP transfer:**\n"
                    "If a system transfer was required for your SOAP, you must wait **7 days** before "
                    "you can do another system transfer from another 3DS to this console or vice versa.\n\n"
                    "**After a SOAP lottery:**\n"
                    "If you won the SOAP lottery (SOAP complete message was yellow), you can do a system "
                    "transfer *right away* - no waiting required.\n\n"
                    "**To perform a system transfer:**\n"
                    "Use the System Transfer feature in System Settings -> Other Settings -> System Transfer. Make sure both consoles are "
                    "charged and connected to WiFi."
                ),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Again, if you don't want to system transfer from your old console to this one, you're free to use your console as normal.")
            
        elif value == "need_help":
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
            # Send with Soaper ping
            await interaction.response.send_message(
                content=soaper_ping,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            return
        
        # Send response
        await interaction.response.send_message(embed=embed)

        # Send context-aware follow-up (or none for standalone /soaphelp)
        if self.context == "eshop_issue":
            followup_embed = discord.Embed(
                title="❓ Does the eShop work now?",
                description=f"{interaction.user.mention}, please let us know if this resolved your issue.",
                color=discord.Color.red(),
            )
            view = EshopResolutionView(channel_id=interaction.channel_id)
            await interaction.followup.send(embed=followup_embed, view=view)
        elif self.context == "other_questions":
            followup_embed = discord.Embed(
                title="❓ Is your issue resolved?",
                description=f"{interaction.user.mention}, please let us know if this resolved your issue.",
                color=discord.Color.red(),
            )
            view = IssueResolutionView(channel_id=interaction.channel_id)
            await interaction.followup.send(embed=followup_embed, view=view)
        # No follow-up for standalone /soaphelp (context=None)


class SoapHelperView(discord.ui.View):
    """View containing the SOAP helper dropdown"""

    def __init__(self, context: str | None = None):
        """
        Initialize the view.

        Args:
            context: The context this helper was triggered from:
                - "eshop_issue": User clicked "No, eShop doesn't work" in automation
                - "other_questions": User clicked "I have more questions" in automation
                - None: Standalone /soaphelp command (no follow-up)
        """
        super().__init__(timeout=None)
        self.context = context
        self.add_item(SoapHelperDropdown(context=context))


class SoapHelperCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @command_with_perms(
        name="soaphelp",
        aliases=["soaphelper", "helpsoap"],
        help="Shows a dropdown menu with common SOAP questions and answers",
    )
    async def soaphelp(self, ctx):
        """Send the SOAP helper embed with dropdown"""
        embed = discord.Embed(
            title="🔍 SOAP Helper",
            description=(
                "Need help with your SOAP transfer? Select the issue you're having from the dropdown below.\n\n"
                "If you can't find what you're looking for, select **'My option is not listed here.'** to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        
        view = SoapHelperView()
        await ctx.respond(embed=embed, view=view)

    @command_with_perms(
        name="error",
        aliases=["err"],
        help="Look up a 3DS error code from the common error code list",
    )
    async def error_lookup(self, ctx, code: str):
        """Lookup a common error code and display its info."""
        raw = code.strip().upper()
        # Normalize plain 7-digit codes into XXX-XXXX
        if re.fullmatch(r"\d{3}-\d{4}", raw) is None:
            if re.fullmatch(r"\d{7}", raw):
                raw = f"{raw[:3]}-{raw[3:]}"

        error_info = _load_error_info(raw)

        if not error_info:
            embed = discord.Embed(
                title="Unknown Error Code",
                description=(
                    f"`{raw}` is not a registered error code in our database.\n\n"
                    "Make sure you entered the code in the format **XXX-XXXX** "
                    "and that it's a 3DS eShop / network / Pokémon Bank error.\n"
                    "If you're sure it's correct, please ask a Soaper for help."
                ),
                color=discord.Color.orange(),
            )
            await ctx.respond(embed=embed)
            return

        steps_text = "\n".join(
            f"**{i+1}.** {step}" for i, step in enumerate(error_info["steps"])
        )

        embed = discord.Embed(
            title=f"{raw} - {error_info['title']}",
            description=(
                f"{error_info['description']}\n\n"
                f"**Steps to resolve:**\n{steps_text}"
            ),
            color=discord.Color.blue(),
        )
        await ctx.respond(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        """Register persistent views on bot startup"""
        self.bot.add_view(SoapHelperView())
        self.bot.add_view(ErrorResolutionView())
        self.bot.add_view(EshopResolutionView())
        self.bot.add_view(IssueResolutionView())
        self.bot.add_view(AwaitingErrorCodeView())
        self.bot.add_view(InvalidErrorCodeView(target_message=None))


def setup(bot):
    bot.add_cog(SoapHelperCog(bot))
