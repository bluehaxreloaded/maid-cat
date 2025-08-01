import discord
from discord.ext import commands


class CustomHelp(commands.HelpCommand):  # pretty help
    async def command_callback(self, ctx, command=None):
        if command:
            command = command.split(sep="|")[0]

        return await super().command_callback(ctx, command=command)

    async def send_bot_help(self, mapping):
        help_embed = discord.Embed(title="Help:")

        commands = [cmd for cmds in mapping.values() for cmd in cmds]

        for i in commands:
            help_embed.add_field(
                name=f"{i.name}: {i.description}",
                value=i.help or "description not provided",
                inline=False,
            )

        help_embed.set_footer(
            text="use `.help <command>` for extended info on a specific command"
        )

        await self.get_destination().send(embed=help_embed)

    async def send_command_help(self, command: commands.Command):
        help_embed = discord.Embed(
            title=f"{command.name}:",
            description=f"{command.help or 'description not provided'} \n\n"
            f"{('-# ' + command.extras['min_role'] + '+\n') if 'min_role' in command.extras and command.extras['min_role'] != 'Default' else ''}"
            f"-# Aliases: {', '.join(sorted(command.aliases or [])) or 'none'}",
        )
        await self.get_destination().send(embed=help_embed)

    async def send_error_message(self, error: str):
        raise commands.CommandNotFound()
