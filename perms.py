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
            
            # Get member object - might be User or Member
            if isinstance(user, discord.Member):
                member = user
            else:
                # Try to get member from cache first
                member = ctx.guild.get_member(user.id)
                if member is None:
                    # If not in cache, fetch it
                    try:
                        member = await ctx.guild.fetch_member(user.id)
                    except discord.NotFound:
                        # User not in guild, deny access
                        raise commands.MissingRole(min_role)
                    except Exception:
                        # Other error, deny access to be safe
                        raise commands.MissingRole(min_role)
            
            # Guild owner always has permission
            if member == ctx.guild.owner:
                return True
            
            if min_role == "Default":
                # Default role means everyone can use it
                return True
            
            role = discord.utils.get(ctx.guild.roles, name=min_role)
            
            if role is None:
                # Role doesn't exist, deny access
                raise commands.MissingRole(min_role)
            
            # Check if user has the role or any role with higher position
            has_exact_role = role in member.roles
            has_higher_role = member.top_role.position > role.position
            
            if not (has_exact_role or has_higher_role):
                # User doesn't have the role and doesn't have a higher role
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
        
        # Apply the check after creating the command
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
