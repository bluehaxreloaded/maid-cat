import discord
from discord.ext import commands, bridge
from constants import SOAP_USABLE_IDS


def command_with_perms(
    *, min_role: str = "Default", slash: bool = True, **kwargs
):  # permission managment using roles
    def decorator(func):
        async def perm_check(ctx):
            # Bridge commands use ctx.user for slash, ctx.author for prefix
            # Try both to support both command types
            user = getattr(ctx, 'user', None) or getattr(ctx, 'author', None)
            if user is None:
                raise commands.CheckFailure("Unable to determine user from context")
            
            # Guild owner always has permission
            if user == ctx.guild.owner:
                return True
            
            role = (
                discord.utils.get(ctx.guild.roles, name=min_role)
                if min_role != "Default"
                else ctx.guild.default_role
            )
            
            if role is None:
                raise commands.MissingRole(min_role)
            
            # Check if user has the role or a higher role
            # For bridge commands, user might be a User object, need to get Member
            if isinstance(user, discord.Member):
                member = user
            else:
                # Try to get member from guild
                member = ctx.guild.get_member(user.id)
                if member is None:
                    # If we can't get member, deny access
                    raise commands.MissingRole(min_role)
            
            has_role = role in member.roles
            has_higher_role = member.top_role.position > role.position
            
            if not (has_role or has_higher_role):
                raise commands.MissingRole(min_role)

            return True

        # Bridge command (prefix + slash) by default; allow opting out for complex signatures
        if slash:
            # Mirror the help text into the description
            cmd_kwargs = dict(kwargs)
            help_text = cmd_kwargs.get("help")
            if help_text and "description" not in cmd_kwargs:
                cmd_kwargs["description"] = help_text
            x = bridge.bridge_command(extras={"min_role": min_role}, **cmd_kwargs)(func)
        else:
            x = commands.command(extras={"min_role": min_role}, **kwargs)(func)
        x = commands.check(perm_check)(x)

        return x

    return decorator


class WrongChannel(commands.CheckFailure):
    def __init__(self, command: str, channel: str):
        super().__init__(f"Cannot use command `{command}` in {channel}!")


def soap_channels_only():  # lock command to SOAP, NNID and dev channels only
    def decorator(func):
        async def soap_chan(ctx):
            """Check that the command is used in an allowed SOAP/NNID/dev channel."""
            category = getattr(ctx.channel, "category", None)
            if category and category.id in SOAP_USABLE_IDS:
                return True
            raise WrongChannel(ctx.command.name, ctx.channel.mention)

        return commands.check(soap_chan)(func)

    return decorator
