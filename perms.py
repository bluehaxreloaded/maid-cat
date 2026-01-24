import discord
from discord.ext import commands, bridge
from constants import SOAP_USABLE_IDS


def _get_member(ctx) -> discord.Member | None:
    """Extract member from any context type. Returns None if not found."""
    # BridgeContext/ApplicationContext use .author for the invoking member
    if hasattr(ctx, "author") and isinstance(ctx.author, discord.Member):
        return ctx.author
    # Fallback for raw Interaction
    if isinstance(ctx, discord.Interaction) and isinstance(ctx.user, discord.Member):
        return ctx.user
    return None


def _has_role_or_higher(member: discord.Member, role: discord.Role) -> bool:
    """Check if member has the role or a higher-positioned role."""
    return role in member.roles or member.top_role.position > role.position


def command_with_perms(
    *, min_role: str = "Default", slash: bool = True, **kwargs
):
    """Decorator for bridge/prefix commands with role-based permission checking."""

    def check_perms(ctx) -> bool:
        member = _get_member(ctx)
        if member is None:
            raise commands.CheckFailure("Could not determine member from context")

        # Guild owner bypasses all checks
        if ctx.guild and member.id == ctx.guild.owner_id:
            return True

        # Default = no restriction
        if min_role == "Default":
            return True

        role = discord.utils.get(ctx.guild.roles, name=min_role)
        if role is None or not _has_role_or_higher(member, role):
            raise commands.MissingRole(min_role)

        return True

    def decorator(func):
        # Copy help to description for slash commands
        cmd_kwargs = dict(kwargs)
        if cmd_kwargs.get("help") and "description" not in cmd_kwargs:
            cmd_kwargs["description"] = cmd_kwargs["help"]

        # Create the command
        if slash:
            cmd = bridge.bridge_command(extras={"min_role": min_role}, **cmd_kwargs)(func)
        else:
            cmd = commands.command(extras={"min_role": min_role}, **cmd_kwargs)(func)

        # Add the permission check
        cmd.add_check(check_perms)
        return cmd

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
