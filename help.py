import discord
from discord.ext import commands, bridge
from discord.ext.bridge import BridgeOption


class HelpView(discord.ui.View):
    """Paginated help view using buttons."""

    def __init__(self, pages: list[discord.Embed]):
        # 2 minute timeout; buttons will stop working after that but message stays.
        super().__init__(timeout=120)
        self.pages = pages
        self.index = 0
        # Disable buttons up-front if only one page
        if len(self.pages) <= 1:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

    @property
    def current_page(self) -> discord.Embed:
        return self.pages[self.index]

    async def _update_message(self, interaction: discord.Interaction):
        """Helper to edit the message with the current page and updated buttons."""
        # Update button disabled states based on position
        max_index = len(self.pages) - 1
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "help_prev":
                    item.disabled = self.index == 0
                elif item.custom_id == "help_next":
                    item.disabled = self.index == max_index
        await interaction.response.edit_message(embed=self.current_page, view=self)

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="help_prev")
    async def previous_page(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        if self.index > 0:
            self.index -= 1
        await self._update_message(interaction)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="help_next")
    async def next_page(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        if self.index < len(self.pages) - 1:
            self.index += 1
        await self._update_message(interaction)


class HelpCog(commands.Cog):
    """Cog providing the help command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.help_command = None

    async def _send_all_help(self, ctx):
        """Send paginated help listing all commands."""
        # Flatten commands from all cogs
        cmds = list(self.bot.commands)

        # Deduplicate commands by qualified name
        unique: dict[str, commands.Command] = {}
        for cmd in cmds:
            key = getattr(cmd, "qualified_name", cmd.name)
            if key not in unique:
                unique[key] = cmd
        commands_list = sorted(unique.values(), key=lambda c: c.name)

        page_size = 15
        pages: list[discord.Embed] = []

        for page_index in range(0, len(commands_list), page_size):
            chunk = commands_list[page_index : page_index + page_size]
            embed = discord.Embed(
                title=f"Help (Page {len(pages)+1})",
                color=discord.Color.blue(),
            )

            for cmd in chunk:
                # Prefer the command's help text; fall back to description, then a default.
                description = getattr(cmd, "help", None) or getattr(
                    cmd, "description", None
                ) or "description not provided"
                embed.add_field(
                    name=cmd.name,
                    value=description,
                    inline=False,
                )

            if not embed.fields:
                embed.description = "No commands available."
            embed.set_footer(
                text="Use `.help <command>` for extended info on a specific command."
            )
            pages.append(embed)

        if not pages:
            pages.append(
                discord.Embed(
                    title="Help",
                    description="No commands available.",
                    color=discord.Color.blue(),
                )
            )

        view = HelpView(pages)
        # Bridge contexts support respond() for both prefix and slash
        await ctx.respond(embed=pages[0], view=view)

    async def _send_command_help(self, ctx, command: commands.Command):
        """Send detailed help for a single command."""
        help_text = getattr(command, "help", None) or getattr(
            command, "description", None
        ) or "description not provided"
        embed = discord.Embed(
            title=f"{command.name}:",
            color=discord.Color.blue(),
            description=f"{help_text} \n\n"
            f"{('-# ' + command.extras['min_role'] + '+\n') if hasattr(command, 'extras') and 'min_role' in command.extras and command.extras['min_role'] != 'Default' else ''}"
            f"-# Aliases: {', '.join(sorted(command.aliases or [])) or 'none'}",
        )

        await ctx.respond(embed=embed)

    async def _handle_help(self, ctx, command_name: str | None):
        """Shared implementation for both prefix and slash help commands."""
        if command_name:
            cmd_obj = self.bot.get_command(command_name)
            if cmd_obj is None:
                msg = f"Unknown command `{command_name}`"
                await ctx.respond(msg, ephemeral=True)
                return
            await self._send_command_help(ctx, cmd_obj)
        else:
            await self._send_all_help(ctx)

    @bridge.bridge_command(name="help", description="Show help information for commands")
    async def help_command(
        self,
        ctx,
        command: BridgeOption(str, "Specific command to get detailed help for", required=False) = None,
    ):
        """Help command: works as both prefix (.help) and slash (/help)."""
        await self._handle_help(ctx, command)


def setup(bot: commands.Bot):
    bot.add_cog(HelpCog(bot))
