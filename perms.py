import discord
from discord.ext import commands, bridge
from constants import SOAP_USABLE_IDS, NNID_CHANNEL_CATEGORY_ID, NNID_CHANNEL_SUFFIX


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
    *, min_role: str = "Default", allowed_roles: list[str] | None = None, slash: bool = True, **kwargs
):
    """Decorator for bridge/prefix commands with role-based permission checking.
    Use min_role for a single role (or hierarchy). Use allowed_roles for "any of these roles"."""
    if allowed_roles is not None and min_role != "Default":
        raise ValueError("Use either min_role or allowed_roles, not both")

    def check_perms(ctx) -> bool:
        member = _get_member(ctx)
        if member is None:
            raise commands.CheckFailure("Could not determine member from context")

        if allowed_roles is not None:
            for role_name in allowed_roles:
                role = discord.utils.get(ctx.guild.roles, name=role_name)
                if role and role in member.roles:
                    return True
            raise commands.MissingRole(" or ".join(allowed_roles))

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

        extras_role = " or ".join(allowed_roles) if allowed_roles else min_role

        # Apply the check to the function BEFORE creating the command
        # This ensures it applies to both prefix and slash variants of bridge commands
        checked_func = commands.check(check_perms)(func)

        # Create the command
        if slash:
            cmd = bridge.bridge_command(extras={"min_role": extras_role}, **cmd_kwargs)(checked_func)
        else:
            cmd = commands.command(extras={"min_role": extras_role}, **cmd_kwargs)(checked_func)

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


def nnid_channels_only():
    """Lock command to NNID channels only."""
    def decorator(func):
        async def nnid_chan(ctx):
            category = getattr(ctx.channel, "category", None)
            is_nnid = (
                category
                and category.id == NNID_CHANNEL_CATEGORY_ID
                and getattr(ctx.channel, "name", "").endswith(NNID_CHANNEL_SUFFIX)
            )
            if is_nnid:
                return True
            raise WrongChannel(ctx.command.name, ctx.channel.mention)

        return commands.check(nnid_chan)(func)

    return decorator
