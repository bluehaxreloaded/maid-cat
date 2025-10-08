import discord 
import time
from perms import command_with_perms
from exceptions import CategoryNotFound
from log import log_to_soaper_log
from discord.ext import commands
from constants import SOAP_CHANNEL_SUFFIX, BOOM_NAME, SOAP_CHANNEL_CATEGORY_ID

class SoapCog(commands.Cog): # SOAP commands
    def __init__(self, bot):
        self.bot = bot
    
    @command_with_perms(min_role="Soaper", name="createsoap", aliases=["soup", "setupsoap", "soap", "setupsoup", "createsoup"], help="Sets up SOAP channel")
    async def createsoap(self, ctx: commands.Context, user: discord.Member | int | str): # Creates soup channel
        if not isinstance(user, discord.Member):
            await ctx.send("User not in server or does not exist!")
            return
        
        channel_name = user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX # channels can't have periods
        channel = discord.utils.get(ctx.guild.channels, name = channel_name)
        
        if channel:
            await ctx.send(f"Soap channel already made for `{user.name}` at {channel.jump_url}")
        else:
            category = discord.utils.get(ctx.guild.categories, id=SOAP_CHANNEL_CATEGORY_ID)
            if category:
                    new = await ctx.guild.create_text_channel(
                        name=channel_name,
                        category=category,
                        topic=f"This is the SOAP channel for <@{user.id}>, please follow all provided instructions."
                    )
            else:
                raise CategoryNotFound(SOAP_CHANNEL_CATEGORY_ID)
            
            await new.set_permissions(user, read_messages=True)
            await new.send(
                f"{user.mention}\n"
                
                "# Welcome!\n\n\n"

                "Make sure your console is modded and region changed first.\n\n"
                
                "1. Ensure your SD card is in your console\n"
                "2. Hold START while powering on your console. This will boot you into GM9\n"
                "3. Navigate to `SysNAND Virtual`\n"
                "4. Select `essential.exefs`\n"
                "5. Select `copy to 0:/gm9/out` (select `Overwrite file(s)` if prompted)\n"
                "6. Power off your console\n"
                "7. Insert your SD card into your PC\n"
                "8. Navigate to `/gm9/out` on your SD, where `essential.exefs` should be located\n"
                "9. Send the `essential.exefs` file to this chat as well as your serial number from your console. The serial number should be a three-letter prefix followed by nine numbers.\n"
                "10. Please wait for further instructions\n"
            )
            await ctx.send(new.jump_url)
            await log_to_soaper_log(ctx, "Created SOAP Channel")
    
    @command_with_perms(min_role="Soaper", name="deletesoap", aliases=["water", "desoap", "unsoup", "spoon", "unsoap", "boom", "desoup"],  help="Deletes SOAP channel")
    async def deletesoap(self, ctx: commands.Context, user: discord.Member | discord.TextChannel | discord.VoiceChannel | int | str = None): # you're never gonna guess what this one does
        match user:
            case discord.Member():
                channel_name = user.name.lower().replace(".", "-") + SOAP_CHANNEL_SUFFIX 
                channel = discord.utils.get(ctx.guild.channels, name = channel_name)
            case discord.TextChannel():
                channel = user
            case discord.VoiceChannel():
                channel = user
            case None:
                channel = ctx.channel
            case _:
                await ctx.send("User not in server or does not exist!")
                return

        if not channel:
            return await ctx.send(f"SOAP channel not found for `{user.name}`")
        elif not (channel.category.id == SOAP_CHANNEL_CATEGORY_ID and channel.name.endswith(SOAP_CHANNEL_SUFFIX)):
            return await ctx.send(f"{channel.mention} is not a SOAP channel!")

        boom = discord.utils.get(channel.guild.emojis, name=BOOM_NAME)
        await channel.send("Self-destruct sequence initiated!")
        await channel.send(f"<a:{boom.name}:{boom.id}>")
        time.sleep(2.75)
        await channel.delete()
        await log_to_soaper_log(ctx, "Removed SOAP Channel")


def setup(bot):
    return bot.add_cog(SoapCog(bot))