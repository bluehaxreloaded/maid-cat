import discord
import json
from pathlib import Path
from discord.ext import commands
from perms import command_with_perms
from constants import SOAPER_ROLE_ID


class ErrorResolutionView(discord.ui.View):
    """View with buttons to confirm if error was resolved"""
    
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(
        label="Yes, it's fixed!",
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


class ErrorCodeModal(discord.ui.Modal):
    """Modal for submitting error codes"""
    
    def __init__(self, service_name: str, emoji: str):
        self.service_name = service_name
        self.emoji = emoji
        super().__init__(title=f"{emoji} {service_name} Error Report")
        
        # Create text inputs using the proper pycord syntax
        self.error_code_input = discord.ui.InputText(
            label="What is the error code displayed?",
            placeholder="001-1001",
            required=True,
            max_length=10,
        )
        self.add_item(self.error_code_input)
    
    async def callback(self, interaction: discord.Interaction):
        """Handle modal submission"""
        error_code = self.error_code_input.value.strip()
        
        # Load error codes database
        error_codes_path = Path(__file__).parent / "error_codes.json"
        error_info = None
        
        try:
            with open(error_codes_path, 'r', encoding='utf-8') as f:
                error_codes_db = json.load(f)
                error_info = error_codes_db.get(error_code)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        
        if error_info and error_info.get("service", "").lower() in self.service_name.lower():
            # Error code found in database - show resolution steps
            # Add each step as a numbered list
            steps_text = "\n".join([f"**{i+1}.** {step}" for i, step in enumerate(error_info['steps'])])
            
            embed = discord.Embed(
                title=f"{self.emoji} {error_code} - {error_info['title']}",
                description=(
                    f"{error_info['description']}\n\n"
                    f"**Steps to resolve:**\n{steps_text}"
                ),
                color=discord.Color.blue(),
            )
            
            embed.set_footer(text="Try these steps, try the eShop again, and let us know if the issue is resolved.")
            
            # Send to the channel (not ephemeral)
            await interaction.response.send_message(embed=embed)
            
            # Send follow-up embed asking if issue was resolved
            followup_embed = discord.Embed(
                title="❓ Did this resolve your issue?",
                description=f"{interaction.user.mention}, please let us know if the steps above helped or if you still need help.",
                color=discord.Color.red(),
            )
            view = ErrorResolutionView()
            await interaction.followup.send(embed=followup_embed, view=view)
        else:
            # Error code not found
            embed = discord.Embed(
                title=f"{self.emoji} {self.service_name} Error Report",
                description=(
                    f"{interaction.user.mention} is experiencing issues with {self.service_name}.\n\n"
                    f"**Error Code:** {error_code}\n\n"
                    "This error code is not in our database. A Soaper will assist you shortly."
                ),
                color=discord.Color.orange(),
            )
            
            embed.set_footer(text="Please wait for a Soaper to assist you.")
            
            # Send to the channel
            await interaction.response.send_message(embed=embed)


class SoapHelperDropdown(discord.ui.Select):
    """Dropdown menu for common SOAP questions"""

    def __init__(self):
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
                label="Switching between Pretendo & Nintendo Network?",
                description="Information regarding using Nimbus",
                emoji="🌐",
                value="pretendo_switch",
            ),
            discord.SelectOption(
                label="What is a SOAP lottery?",
                description="Information regarding lottery SOAPs",
                emoji="🎉",
                value="region_settings",
            ),
            discord.SelectOption(
                label="Do I have to wait 7 days?",
                description="Info regarding system transferring after a SOAP",
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
        
        # Create response embed based on selection
        if value == "eshop_not_working":
            # Show modal directly
            modal = ErrorCodeModal("eShop", "🛒")
            await interaction.response.send_modal(modal)
            return
            
        elif value == "pokemon_bank_not_working":
            # Show modal directly
            modal = ErrorCodeModal("Pokémon Bank", "📦")
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
                title="⏳ Do I Have to Wait 7 Days?",
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
        
        # Send response (not ephemeral) with follow-up
        await interaction.response.send_message(embed=embed)
        
        # Send follow-up embed asking if issue was resolved
        followup_embed = discord.Embed(
            title="❓ Did this resolve your issue?",
            description=f"{interaction.user.mention}, please let us know if the steps above helped or if you still need help.",
            color=discord.Color.red(),
        )
        view = ErrorResolutionView()
        await interaction.followup.send(embed=followup_embed, view=view)


class SoapHelperView(discord.ui.View):
    """View containing the SOAP helper dropdown"""
    
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SoapHelperDropdown())


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
                "If you can't find what you're looking for, select **'Other'** to request assistance from a Soaper."
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Select an option from the dropdown menu below")
        
        view = SoapHelperView()
        await ctx.respond(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        """Register persistent views on bot startup"""
        self.bot.add_view(SoapHelperView())
        self.bot.add_view(ErrorResolutionView())


def setup(bot):
    bot.add_cog(SoapHelperCog(bot))
