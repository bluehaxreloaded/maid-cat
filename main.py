import discord
import traceback
from log import ErrorLogChannelNotFound, error_log
from discord.ext import commands, bridge
from constants import KEY, SOAP_LOG_ID

intent = discord.Intents().default()
intent.message_content = True
intent.members = True
bot = bridge.Bot(command_prefix=".", intents=intent)
bot.load_extension("help")
bot.load_extension("soap")
bot.load_extension("soap_request")
bot.load_extension("soap_automation")
# bot.load_extension("dynamic_cmds")
bot.load_extension("text_commands")
bot.load_extension("nnid")
bot.load_extension("nnid_request")
bot.load_extension("tracker")


@bot.event  # variadic command count. unnecessary? maybe. cool? absolutely
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    message.content = message.content.rsplit("|", 1)[
        0
    ].strip()  # trash everything not command related

    if (
        message.content.startswith(".") and not message.content[1] == "."
    ):  # literally just so "..." doesn't flair up the bot lol
        if message.content[1].isdigit():  # variadic commands (.#)
            count = int(message.content[1])
            if count > 5:
                return await message.channel.send(
                    "A maximum of 5 commands are allowed at a time"
                )

            cmds = message.content[2:].split(sep="|")
            cmds = [cmd.strip() for cmd in cmds]

            if not cmds:
                return

            while cmds:
                cmd = bot.get_command(cmds[0].split()[0])

                if cmd is None:
                    await on_command_error(
                        await bot.get_context(message), commands.CommandNotFound()
                    )
                else:
                    message.content = f".{cmds[0].strip()}"
                    await bot.process_commands(message)
                cmds = cmds[1:]
        else:
            await bot.process_commands(message)


@bot.event  # actually show things on error
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    try:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"Error! Missing argument `{error.param.name}` in command `{ctx.command.name}`"
            )
            await ctx.send_help(ctx.command)
        elif isinstance(error, commands.CommandNotFound):
            cmd_mes = ctx.message.content[1:].split()
            if cmd_mes[0] == "help":
                await ctx.send(f"Unknown command `{cmd_mes[1]}`")
        elif isinstance(error, commands.MissingRole):
            role_name = error.missing_role
            await ctx.send(f"You must be {role_name} or higher to use this command!")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send(error)
        else:
            await ctx.send(
                "Error! Please don't do whatever you did again and notify someone who knows what they're doing."
            )
            await error_log(ctx, error)
    except (
        ErrorLogChannelNotFound
    ) as log_error:  # specific case where error_log() lacks a log to error to
        await ctx.send("Error Log channel not found!")
        original = log_error.original_error
        print(f"{log_error}\n\nTraceback:\n")
        traceback.print_exception(type(original), original, original.__traceback__)
    except Exception as unknown:  # anything else that fails in this event. not sure why this would ever run but here we are
        print("hmm")
        await error_log(ctx, unknown)


@bot.event
async def on_application_command_error(ctx, error: commands.CommandError):
    """Handle errors for slash/bridge commands (application commands)."""
    try:
        if isinstance(error, commands.CheckFailure):
            # Includes WrongChannel and MissingRole coming from checks/decorators
            await ctx.respond(str(error), ephemeral=True)
        else:
            await ctx.respond(
                "Error! Please don't do whatever you did again and notify someone who knows what they're doing.",
                ephemeral=True,
            )
            # Log to console for debugging; prefix error_log handles prefix commands.
            print(f"Application command error: {error}")
    except Exception as unknown:
        print(f"Error while handling application command error: {unknown}")

@bot.event
async def on_ready():
    print(f"Logged-in as {bot.user}")
    log_channel = bot.get_channel(SOAP_LOG_ID)
    if log_channel:
        embed = discord.Embed(
            title="Ready!",
            description=f"{bot.user.name} has just restarted.",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Button interactions created before the restart might be broken now.")
        await log_channel.send(embed=embed)
    else:
        raise ErrorLogChannelNotFound(SOAP_LOG_ID)
        
    # dynamic_setup = bot.get_cog("DynamicCommandsCog").setup_commands()
    # print(f"Loaded dynamic commands: {dynamic_setup}")

bot.run(KEY)
