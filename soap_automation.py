import discord
from discord.ext import commands

class SOAPAutomationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def create_soap_interface(self, channel, user):
        """Create the welcome embed for new SOAP channels"""
        # Welcome embed
        embed = discord.Embed(
            title="🧼 Welcome to Your SOAP Channel!",
            description="This is where we'll perform your SOAP transfer. Please follow the instructions below.\n\n",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="📋 Step-by-Step Instructions",
            value="1. Ensure your SD card is in your console\n"
                  "2. Hold **START** while powering on → this will boot you into GodMode9\n"
                  "3. Navigate to `SysNAND Virtual`\n"
                  "4. Select `essential.exefs`\n"
                  "5. Select `Copy to 0:/gm9/out` (select Overwrite field(s) if prompted)\n"
                  "6. Power off your console\n"
                  "7. Insert your SD card into your PC or connect to your console via FTPD\n"
                  "8. Navigate to `/gm9/out/`, where essential.exefs should be located\n"
                  "9. Upload the `essential.exefs` file and provide your serial number below\n"
                  "10. Please wait for a Soaper to assist you",
            inline=False
        )
        # Send with mention
        await channel.send(content=user.mention, embed=embed)

def setup(bot):
    return bot.add_cog(SOAPAutomationCog(bot))