import discord
from discord.ext import commands
from perms import command_with_perms
from constants import REQUEST_NNID_CHANNEL_ID, NNID_CHANNEL_SUFFIX, NNID_CHANNEL_CATEGORY_ID


class FilesCheckView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="Do you have the required files?",
        options=[
            discord.SelectOption(
                label="Yes, I have one of the three file options listed above", value="yes", emoji="✅"
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
                description="We need to have one of the following sets of files **from your source console** to perform an NNID transfer.\n\n"
                "- `essential.exefs`\n"
                "- a NAND backup\n"
                "- `OTP.bin`\n\n"
                "Please locate one of these files from your previous console before requesting.",
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=embed, view=None)

        elif files_answer == "unsure":
            embed = discord.Embed(
                title="❓ What Files Do I Need?",
                description="To perform an NNID transfer, you need files **from your source console** (the console you originally had the NNID on). You were asked to back up your NAND when you originally modded your console, look at all of your backups to see if you can find one of these files.",
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
            # ask about target console CFW
            embed = discord.Embed(
                title="🔍 Pre-NNID Transfer Check",
                description="Great, one more thing to check:",
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Question 2 of 2",
                value="Is your **target console** (the console you want to transfer to) on custom firmware?",
                inline=False,
            )
            embed.set_footer(text="Questions? Drop us a line in #soap-help")
            view = CFWCheckView(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)


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
                description="Your target console must be on custom firmware to receive an NNID transfer.\n\n"
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
                description=f"An NNID channel already exists for {interaction.user.mention}\n\n"
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
        # check if user already has an NNID channel
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
                description=f"You already have an NNID channel!\n\n"
                f"Please go to: {existing_channel.mention}",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # no existing channel, proceed with the form
        embed = discord.Embed(
            title="🔍 Pre-NNID Transfer Check",
            description="Let's ensure you have everything ready for your NNID transfer.",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Question 1 of 2",
            value="Do you have the required files (see above) from your *source console*?",
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
            description="This is where you can request an NNID Transfer, which allows you to transfer your Nintendo Network ID from one console to another without a system transfer.\n\n"
            "**Before requesting:**\n"
            "- Ensure you have one of the following files from your *source console* (where the NNID currently is):\n"
            "  - `essential.exefs`\n"
            "  - NAND backup\n"
            "  - `OTP.bin`\n"
            "- Be ready to get files off your *target console* (where you want to transfer to)\n"
            "- Have both the serial numbers of your source and target consoles ready",
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Click the button below to request an NNID transfer.")
        view = NNIDRequestView()
        
        # Try to load the image file
        file = None
        try:
            file = discord.File("assets/NNIDTransfer.webp", filename="image.png")
            embed.set_image(url="attachment://image.png")
        except FileNotFoundError:
            pass
        
        return embed, view, file

    # This fixes broken embeds if the bot stops.
    @commands.Cog.listener()
    async def on_ready(self):
        """Clear the request NNID channel and post the embed on startup"""
        self.bot.add_view(NNIDRequestView())

        channel = self.bot.get_channel(REQUEST_NNID_CHANNEL_ID)
        if channel:
            try:
                await channel.purge(limit=None)
            except discord.Forbidden:
                print("No permission to clear messages in request NNID channel")
            except Exception as e:
                print(f"Error clearing request NNID channel: {e}")

            embed, view, file = self._create_nnid_request_embed_and_view()
            if file:
                await channel.send(embed=embed, view=view, file=file)
            else:
                await channel.send(embed=embed, view=view)

    @command_with_perms(
        name="requestnnid", help="Creates an embed with a button for NNID transfer requests"
    )
    async def requestnnid(self, ctx: commands.Context):
        embed, view, file = self._create_nnid_request_embed_and_view()
        if file:
            await ctx.send(embed=embed, view=view, file=file)
        else:
            await ctx.send(embed=embed, view=view)


def setup(bot):
    return bot.add_cog(NNIDRequestCog(bot))

