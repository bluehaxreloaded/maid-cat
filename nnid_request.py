from pathlib import Path
import discord
from discord.ext import commands
from perms import command_with_perms
from constants import REQUEST_NNID_CHANNEL_ID, NNID_CHANNEL_SUFFIX, NNID_CHANNEL_CATEGORY_ID, RESTRICTED_ROLE_ID


class FilesCheckView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="Do you have one of the required files?",
        options=[
            discord.SelectOption(
                label="Yes, I have one of the three files listed above", value="yes", emoji="✅"
            ),
            discord.SelectOption(
                label="No, I don't have any files", value="no", emoji="❌"
            ),
            discord.SelectOption(label="I'm not sure", value="unsure", emoji="❓"),
        ],
    )
    async def files_select(
        self, select: discord.ui.Select, interaction: discord.Interaction
    ):
        files_answer = select.values[0]

        if files_answer == "no":
            embed = discord.Embed(
                title="🔒 Unable to Request NNID Transfer",
                description="We need to have one of the following files **from your source console** to perform a transfer.\n\n"
                "- `essential.exefs`\n"
                "- a NAND backup\n"
                "- `OTP.bin`\n\n"
                "Please locate one of these files from your previous console before requesting.",
                color=discord.Color.red(),
            )
            embed.set_footer(text="If you modded your console, try locating the NAND backup you originally made.")
            await interaction.response.edit_message(embed=embed, view=None)

        elif files_answer == "unsure":
            embed = discord.Embed(
                title="❓ What Files Do I Need?",
                description="To perform a transfer, you need one of the folllowing files **from your source console** (the console you originally had the NNID on). You were asked to back up your NAND when you originally modded your console, look at all of your backups to see if you can find one of these files.",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="You must have one of the following files from your source console:",
                value="- `essential.exefs`\n"
                "- a NAND backup\n"
                "- `OTP.bin`",
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=None)

        else:  # yes or nand
            # ask about source console being broken/inaccessible
            embed = discord.Embed(
                title="🔍 Pre-NNID Transfer Check",
                description="Great, one more thing to check:",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Question 2 of 3",
                value="Do either of the following apply to you?\n"
                "• Your **source console** (the console where your NNID currently is) is broken or inaccessible\n"
                "• Your source console is a New 3DS/2DS and you want to transfer to an Old 3DS/2DS",
                inline=False,
            )
            embed.set_footer(text="Questions? Drop us a line in #soap-help")
            view = BrokenConsoleCheckView(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)


class BrokenConsoleCheckView(discord.ui.View):
    def __init__(self, user: discord.Member):
        super().__init__(timeout=180)
        self.user = user

    @discord.ui.select(
        placeholder="Do either of the following apply to you?",
        options=[
            discord.SelectOption(
                label="My source console is broken/inaccessible", value="broken", emoji="✅"
            ),
            discord.SelectOption(
                label="I'm transferring from a New 3DS/2DS to an Old 3DS/2DS", value="new_to_old", emoji="✅"
            ),
            discord.SelectOption(
                label="No, neither applies to me", value="no", emoji="❌"
            ),
            discord.SelectOption(label="I'm not sure", value="unsure", emoji="❓"),
        ],
    )
    async def broken_console_select(
        self, select: discord.ui.Select, interaction: discord.Interaction
    ):
        broken_answer = select.values[0]

        if broken_answer in ["broken", "new_to_old"]:
            # Both "yes" options proceed to CFW check
            # ask about target console CFW
            embed = discord.Embed(
                title="🔍 Pre-NNID Transfer Check",
                description="Great, one more thing to check:",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Question 3 of 3",
                value="Is your **target console** (the console you want to transfer to) on custom firmware?",
                inline=False,
            )
            embed.set_footer(text="Questions? Drop us a line in #soap-help")
            view = CFWCheckView(self.user)
            await interaction.response.edit_message(embed=embed, view=view)

        elif broken_answer == "no":
            embed = discord.Embed(
                title="🔒 Unable to Request NNID Transfer",
                description="For safety reasons, we only perform NNID transfers if:\n"
                "• Your source console is broken or inaccessible, OR\n"
                "• You're transferring from a New 3DS/2DS to an Old 3DS/2DS\n\n"
                "If your source console still works and you're not doing a New 3DS/2DS to Old 3DS/2DS transfer, you can perform a **system transfer** directly on your console to transfer your NNID. This is the official method and doesn't require our assistance.\n\n"
                "**To perform a system transfer:**\n"
                "1. Go to System Settings -> Other Settings on both consoles\n"
                "2. Select 'System Transfer'\n"
                "3. Follow the on-screen instructions",
                color=discord.Color.red(),
            )
            embed.set_footer(text="If anything happens to your console, you can request a transfer here.")
            await interaction.response.edit_message(embed=embed, view=None)

        elif broken_answer == "unsure":
            embed = discord.Embed(
                title="❓ What Applies to Me?",
                description="We can help with NNID transfers if either of the following applies:",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Broken or Inaccessible Console",
                value="Your source console is considered broken or inaccessible if:\n"
                "• Won't power on\n"
                "• Has a broken screen\n"
                "• Has hardware damage preventing normal use\n"
                "• Has a brick (software issue preventing boot)\n"
                "• Lost or stolen\n"
                "• Sold or given away\n"
                "• No longer in your possession",
                inline=False,
            )
            embed.add_field(
                name="New 3DS/2DS to Old 3DS/2DS Transfer",
                value="If your source console is a New 3DS or New 2DS and you want to transfer to an Old 3DS or Old 2DS, we can help with that transfer.",
                inline=False,
            )
            embed.add_field(
                name="Neither Applies?",
                value="If your console still works normally and you're not doing a New 3DS/2DS to Old 3DS/2DS transfer, you should perform a **system transfer** instead. This is the official method and doesn't require our assistance.",
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=None)


class CFWCheckView(discord.ui.View):
    def __init__(self, user: discord.Member):
        super().__init__(timeout=180)
        self.user = user

    @discord.ui.select(
        placeholder="Is your target console on custom firmware?",
        options=[
            discord.SelectOption(
                label="Yes, my target console is on custom firmware", value="yes", emoji="✅"
            ),
            discord.SelectOption(
                label="No, my target console is not on custom firmware", value="no", emoji="❌"
            ),
            discord.SelectOption(label="I'm not sure", value="unsure", emoji="❓"),
        ],
    )
    async def cfw_select(
        self, select: discord.ui.Select, interaction: discord.Interaction
    ):
        cfw_answer = select.values[0]

        if cfw_answer == "no":
            embed = discord.Embed(
                title="🔒 Unable to Request NNID Transfer",
                description="Your target console must be on custom firmware to receive a NNID transfer.\n\n"
                "Please visit the guide below to mod your console:",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="3DS Hacks Guide", value="https://3ds.hacks.guide/", inline=False
            )
            await interaction.response.edit_message(embed=embed, view=None)

        elif cfw_answer == "unsure":
            embed = discord.Embed(
                title="❓ How to Check for CFW",
                description="Follow these steps to check if your 3DS is modded:",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Steps",
                value="• Hold SELECT while powering on your console\n"
                "• If the Luma3DS configuration menu appears, you have CFW\n"
                "• If it boots normally, you don't have CFW installed",
                inline=False,
            )
            embed.add_field(
                name="No CFW?",
                value="Visit https://3ds.hacks.guide/ to mod your console",
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=None)

        else:
            # create NNID channel automatically
            await self.create_nnid_channel(interaction)

    async def create_nnid_channel(self, interaction: discord.Interaction):
        # defer the interaction first to prevent timeout
        await interaction.response.defer(ephemeral=True)

        nnid_cog = interaction.client.get_cog("NNIDCog")

        if not nnid_cog:
            embed = discord.Embed(
                title="❌ Error",
                description="NNID module not loaded. Please contact a Staff Member.",
                color=discord.Color.red(),
            )
            await interaction.followup.edit_message(
                interaction.message.id, embed=embed, view=None
            )
            return

        # call the nnid function
        success, channel, message = await nnid_cog.create_nnid_channel_for_user(
            interaction.guild, self.user, interaction.user, ctx=interaction
        )

        if success:
            embed = discord.Embed(
                title="✅ Ready to Proceed!",
                description=f"We'll perform your NNID transfer in {channel.mention}.\n\n",
                color=discord.Color.green(),
            )

            embed.set_footer(
                text="Please go there and follow the instructions to get started."
            )

            # button to go to nnid channel
            view = discord.ui.View()
            link_button = discord.ui.Button(
                label="Go to NNID Channel",
                style=discord.ButtonStyle.link,
                emoji="🔄",
                url=channel.jump_url,
            )
            view.add_item(link_button)
        elif channel:  # channel already exists
            embed = discord.Embed(
                title="⚠️ Channel Already Exists",
                description=f"A NNID channel already exists for {interaction.user.mention}\n\n"
                f"Channel: {channel.mention}",
                color=discord.Color.orange(),
            )
            view = None
        else:  # error occurred
            embed = discord.Embed(
                title="❌ Error", description=message, color=discord.Color.red()
            )
            view = None

        await interaction.followup.edit_message(
            interaction.message.id, embed=embed, view=view
        )


class NNIDRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request NNID Transfer",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        custom_id="nnid_request_button",
    )
    async def request_nnid_button(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        # check if user already has a NNID channel
        channel_name = (
            interaction.user.name.lower().replace(".", "-") + NNID_CHANNEL_SUFFIX
        )
        existing_channel = None

        # only check channels in the NNID category
        for channel in interaction.guild.text_channels:
            if channel.name == channel_name:
                if channel.category and channel.category.id == NNID_CHANNEL_CATEGORY_ID:
                    existing_channel = channel
                    break

        if existing_channel:
            embed = discord.Embed(
                title="⚠️ Channel Already Exists",
                description=f"You already have a NNID channel!\n\n"
                f"Please go to: {existing_channel.mention}",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # check if user has the restricted role (set role in constants.py)
        restricted_role = discord.utils.get(interaction.guild.roles, id=RESTRICTED_ROLE_ID)
        if restricted_role in interaction.user.roles:
            embed = discord.Embed(
                title="⛔ Restricted from Bluehax Services",
                description="You are unable to request a new NNID transfer. This restriction may be temporary or permanent, depending on the reason.\n\nYou may still receive help with previously completed NNID transfers in #soap-help.",
                color=discord.Color.red(),
            )
            embed.set_footer(text="If you believe this is a mistake, please contact a Soaper or Staff Member.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # no existing channel, proceed with the form
        embed = discord.Embed(
            title="🔍 Pre-NNID Transfer Check",
            description="Let's ensure you have everything ready for your NNID transfer.",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Question 1 of 3",
            value="Do you have one of the required files (see above) from your *source console*?",
            inline=False,
        )
        embed.set_footer(text="Questions? Drop us a line in #soap-help")

        view = FilesCheckView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class NNIDRequestCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    def _create_nnid_request_embed_and_view(self):
        """Helper method to create the NNID request embed and view"""
        embed = discord.Embed(
            title="🔄 NNID Transfer Request",
            description="This is where you can request a NNID Transfer, which allows you to transfer your Nintendo Network ID from one console to another console.\n\n"
            "**Before requesting:**\n"
            "- Ensure you have one of the following files from your *source console* (where the NNID currently is):\n"
            "  - `essential.exefs`\n"
            "  - NAND backup\n"
            "  - `OTP.bin`\n"
            "- Be ready to get files off your *target console* (where you want to transfer to)\n"
            "- Have both the serial numbers of your source and target consoles ready",
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Click the button below to request a NNID transfer.")
        view = NNIDRequestView()
        
        # Try to load the image file
        file = None
        try:
            path = Path(__file__).parent / "assets" / "NNIDTransfer.webp"
            file = discord.File(fp=path, filename="NNIDTransfer.webp")
            embed.set_image(url="attachment://NNIDTransfer.webp")
        except FileNotFoundError as e:
            print(f"Error: Could not find assets/NNIDTransfer.webp - {e}")
            pass
        
        return embed, view, file

    # This fixes broken embeds if the bot stops.
    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(NNIDRequestView())

    @command_with_perms(
        name="requestnnid", help="Creates an embed with a button for NNID transfer requests"
    )
    async def requestnnid(self, ctx):
        if ctx.channel.id == REQUEST_NNID_CHANNEL_ID:
            try:
                await ctx.channel.purge(limit=None)
            except discord.Forbidden:
                print("No permission to clear messages in request NNID channel")
            except Exception as e:
                print(f"Error clearing request NNID channel: {e}")
        embed, view, file = self._create_nnid_request_embed_and_view()
        if file:
            await ctx.respond(embed=embed, view=view, file=file)
        else:
            await ctx.respond(embed=embed, view=view)


def setup(bot):
    return bot.add_cog(NNIDRequestCog(bot))

