import discord
from discord.ext import commands
from constants import SOAP_USABLE_IDS


def command_with_perms(
    *, min_role: str = "Default", **kwargs
):  # permission managment using roles
    def decorator(func):
        async def perm_check(ctx: commands.Context):
            role = (
                discord.utils.get(ctx.guild.roles, name=min_role)
                if min_role != "Default"
                else ctx.guild.default_role
            )
            if role is None or ctx.author.top_role.position < role.position:  # how??
                raise commands.MissingRole(min_role)

            return True

        x = commands.command(extras={"min_role": min_role}, **kwargs)(func)
        x = commands.check(perm_check)(x)

        return x

    return decorator


class WrongChannel(commands.CheckFailure):
    def __init__(self, command: str, channel: str):
        super().__init__(f"Cannot use command `{command}` in {channel}!")


def soap_channels_only():  # lock command to SOAP and dev channels only
    def decorator(func):
        async def soap_chan(ctx: commands.Context):
            if ctx.channel.category.id in SOAP_USABLE_IDS:
                return True
            else:
                raise WrongChannel(ctx.command.name, ctx.channel.mention)

        return commands.check(soap_chan)(func)

    return decorator
