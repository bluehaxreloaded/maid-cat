import discord
from discord.ext import commands
from perms import command_with_perms
from constants import REQUEST_SOAP_CHANNEL_ID


class CFWCheckView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(
        placeholder="Is your console modded?",
        options=[
            discord.SelectOption(label="Yes, my console is modded", value="yes", emoji="✅"),
            discord.SelectOption(label="No, my console is not modded", value="no", emoji="❌"),
            discord.SelectOption(label="I'm not sure", value="unsure", emoji="❓")
        ]
    )
    async def cfw_select(self, select: discord.ui.Select, interaction: discord.Interaction):
        cfw_answer = select.values[0]

        if cfw_answer == "no":
            embed = discord.Embed(
                title="🔒 Unable to Request SOAP",
                description="Your console must be on custom firmware to receive a SOAP transfer.\n\n"
                           "Please visit the guide below to mod your console:",
                color=discord.Color.red()
            )
            embed.add_field(name="3DS Hacks Guide", value="https://3ds.hacks.guide/", inline=False)
            await interaction.response.edit_message(embed=embed, view=None)

        elif cfw_answer == "unsure":
            embed = discord.Embed(
                title="❓ How to Check for CFW",
                description="Follow these steps to check if your 3DS is modded:",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Steps",
                value="• Hold SELECT while powering on your console\n"
                      "• If the Luma3DS configuration menu appears, you have CFW\n"
                      "• If it boots normally, you don't have CFW installed",
                inline=False
            )
            embed.add_field(
                name="No CFW?",
                value="Visit https://3ds.hacks.guide/ to mod your console",
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=None)

        else:  # yes
            # ask the second question about region change
            embed = discord.Embed(
                title="🔍 Pre-SOAP Check",
                description="Great, one more thing to check:",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Question 2 of 2",
                value="Is your console region-changed?",
                inline=False
            )
            embed.set_footer(text="Questions? Drop us a line in #soap-help")
            view = RegionChangeView(interaction.user)
            await interaction.response.edit_message(embed=embed, view=view)


class RegionChangeView(discord.ui.View):
    def __init__(self, user: discord.Member):
        super().__init__(timeout=60)
        self.user = user

    @discord.ui.select(
        placeholder="Is your console region-changed?",
        options=[
            discord.SelectOption(label="Yes, it's region-changed", value="yes", emoji="✅"),
            discord.SelectOption(label="No, it's not region-changed", value="no", emoji="❌"),
            discord.SelectOption(label="I'm not sure", value="unsure", emoji="❓")
        ]
    )
    async def region_select(self, select: discord.ui.Select, interaction: discord.Interaction):
        region_answer = select.values[0]

        if region_answer == "no":
            embed = discord.Embed(
                title="🔒 Unable to Request SOAP",
                description="Your console must be region-changed to use SOAP.\n\n"
                           "Please region-change your console first:",
                color=discord.Color.red()
            )
            embed.add_field(name="Region Change Guide", value="https://3ds.hacks.guide/region-changing.html", inline=False)
            await interaction.response.edit_message(embed=embed, view=None)

        elif region_answer == "unsure":
            embed = discord.Embed(
                title="❓ How to Check Region Change",
                description="To check if your console is region-changed:",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Steps",
                value="• Go to System Settings\n"
                      "• Check if your region settings match a different region than original\n"
                      "• If your console was originally from one region (e.g., JPN) but now shows another (e.g., USA), it's region-changed",
                inline=False
            )
            embed.add_field(
                name="Need to region change?",
                value="Visit https://3ds.hacks.guide/region-changing.html",
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=None)

        else:  # yes
            # create SOAP channel automatically
            await self.create_soap_channel(interaction)

    async def create_soap_channel(self, interaction: discord.Interaction):
        # defer the interaction first to prevent timeout
        await interaction.response.defer(ephemeral=True)

        soap_cog = interaction.client.get_cog("SoapCog")

        if not soap_cog:
            embed = discord.Embed(
                title="❌ Error",
                description="SOAP module not loaded. Please contact an administrator.",
                color=discord.Color.red()
            )
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=None)
            return

        # call the soap function
        success, channel, message = await soap_cog.create_soap_channel_for_user(
            interaction.guild,
            self.user,
            interaction.user,
            ctx=interaction
        )

        if success:
            embed = discord.Embed(
                title="✅ Ready to Proceed!",
                description=f"We'll perform your SOAP in {channel.mention}.\n\n",
                footer=discord.Footer(text="Please go there and follow the instructions to get started."),
                color=discord.Color.green()
            )

            # button to go to soap channel
            view = discord.ui.View()
            link_button = discord.ui.Button(
                label="Go to SOAP Channel",
                style=discord.ButtonStyle.link,
                emoji="🧼",
                url=channel.jump_url
            )
            view.add_item(link_button)
        elif channel:  # channel already exists
            embed = discord.Embed(
                title="⚠️ Channel Already Exists",
                description=f"A SOAP channel already exists for {self.user.mention}\n\n"
                           f"Channel: {channel.mention}",
                color=discord.Color.orange()
            )
            view = None
        else:  # error occurred
            embed = discord.Embed(
                title="❌ Error",
                description=message,
                color=discord.Color.red()
            )
            view = None

        await interaction.followup.edit_message(interaction.message.id, embed=embed, view=view)


class SOAPRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request SOAP", style=discord.ButtonStyle.primary, emoji="🧼", custom_id="soap_request_button")
    async def request_soap_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        from constants import SOAP_CHANNEL_SUFFIX

        # check if user already has a SOAP channel
        channel_name = interaction.user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX
        existing_channel = None

        # only check channels in the SOAP categories
        from constants import SOAP_CHANNEL_CATEGORY_ID, MANUAL_SOAP_CATEGORY_ID
        for channel in interaction.guild.text_channels:
            if channel.name == channel_name:
                if channel.category and channel.category.id in [SOAP_CHANNEL_CATEGORY_ID, MANUAL_SOAP_CATEGORY_ID]:
                    existing_channel = channel
                    break

        if existing_channel:
            embed = discord.Embed(
                title="⚠️ Channel Already Exists",
                description=f"You already have a SOAP channel!\n\n"
                           f"Please go to: {existing_channel.mention}",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # no existing channel, proceed with the form
        embed = discord.Embed(
            title="🔍 Pre-SOAP Check",
            description="Let's ensure your console is ready to be SOAPed.",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Question 1 of 2",
            value="Is your console on custom firmware (CFW)?",
            inline=False
        )
        embed.set_footer(text="Questions? Drop us a line in #soap-help")

        view = CFWCheckView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class SOAPRequestCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    def _create_soap_request_embed_and_view(self):
        """Helper method to create the SOAP request embed and view"""
        embed = discord.Embed(
            title="🧼 SOAP Request",
            description="SOAP Transfers allow region-changed consoles to access the eShop, Pokemon Bank, and more. This channel is where you can request one!\n\n"
                       "**Before requesting:**\n"
                       "• Ensure your 3DS is modded and region-changed\n"
                       "• Have your serial number ready\n"
                       "• Be ready to get files off your console",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Click the button below to request a SOAP transfer.")
        view = SOAPRequestView()
        return embed, view

    # This fixes broken embeds if the bot stops.
    @commands.Cog.listener()
    async def on_ready(self):
        """Clear the request SOAP channel and post the embed on startup"""
        self.bot.add_view(SOAPRequestView())

        channel = self.bot.get_channel(REQUEST_SOAP_CHANNEL_ID)
        if channel:
            try:
                await channel.purge(limit=None)
            except discord.Forbidden:
                print(f"No permission to clear messages in request SOAP channel")
            except Exception as e:
                print(f"Error clearing request SOAP channel: {e}")

            embed, view = self._create_soap_request_embed_and_view()
            await channel.send(embed=embed, view=view)

    @command_with_perms(
        name="requestsoap",
        help="Creates an embed with a button for SOAP requests"
    )
    async def requestsoap(self, ctx: commands.Context):
        embed, view = self._create_soap_request_embed_and_view()
        await ctx.send(embed=embed, view=view)


def setup(bot):
    return bot.add_cog(SOAPRequestCog(bot))