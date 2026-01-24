import discord
import exceptions
from discord.ext import commands
from constants import SOAP_LOG_ID, MOD_LOG_ID, ERROR_LOG_ID


class LogChannelNotFound(exceptions.ChannelNotFound):
    def __init__(self, channel_id: int, log_type: str = None):
        prefix = f"{log_type} " if log_type else ""
        super().__init__(channel_id, f"{prefix}Log".capitalize())


class ErrorLogChannelNotFound(LogChannelNotFound):
    def __init__(self, channel_id: int, original_error: Exception):
        super().__init__(channel_id, "Error")
        self.original_error = original_error


async def log_to(
    ctx: commands.Context | discord.Interaction,
    channel_id: int,
    title: str | None = None,
):
    log_channel = discord.utils.get(ctx.guild.channels, id=channel_id)
    if log_channel:
        log_embed = discord.Embed(title=title)

        # Handle Context, Interaction, and bridge contexts safely
        if isinstance(ctx, discord.Interaction):
            author = ctx.user
            action = "Interaction"
        else:
            message = getattr(ctx, "message", None)
            if message is not None:
                author = message.author
                action = message.content
            else:
                # Bridge / application context without a backing message
                author = getattr(ctx, "author", getattr(ctx, "user", None))
                action = getattr(
                    getattr(ctx, "command", None),
                    "qualified_name",
                    "Application command",
                )

        log_embed.add_field(
            name="Action made by:",
            value=f"{author.name} - {author.id}",
            inline=False,
        )
        log_embed.add_field(name="Action: ", value=action, inline=False)
        await log_channel.send(embed=log_embed)
    else:
        raise LogChannelNotFound(channel_id)


# On errors
async def error_log(ctx: commands.Context, error: Exception):
    error_log_channel = discord.utils.get(ctx.guild.channels, id=ERROR_LOG_ID)
    if error_log_channel:
        error_log_embed = discord.Embed(
            title="ERROR", description=str(error), color=discord.Color.red()
        )

        error_log_embed.add_field(
            name="Sent by:",
            value=f"{ctx.message.author.name} - {ctx.message.author.id}",
            inline=False,
        )
        error_log_embed.add_field(
            name="Action:", value=ctx.message.content, inline=False
        )

        await error_log_channel.send(embed=error_log_embed)
        raise error
    else:  # last ditch effort to at least display SOMETHING if no error log is found for some reason
        raise ErrorLogChannelNotFound(ERROR_LOG_ID, error) from error


def log_to_mod_log(ctx: commands.Context, title: str):
    return log_to(ctx, MOD_LOG_ID, title)


def log_to_soaper_log(ctx: commands.Context | discord.Interaction, title: str):
    return log_to(ctx, SOAP_LOG_ID, title)
