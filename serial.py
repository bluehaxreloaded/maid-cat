import discord
import re
import asyncio
from perms import command_with_perms
from discord.ext import commands

class SerialCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def compute_check_digit(self, numbers):
        if len(numbers) != 8:
            return None

        even_sum = 0
        odd_sum = 0

        for i in range(len(numbers)):
            ch = numbers[i]

            if ch < '0' or ch > '9':
                return None  # invalid character

            digit = int(ch)
            pos = i + 1  # 1-based index

            if pos % 2 == 0:
                # even position
                even_sum += digit
            else:
                # odd position
                odd_sum += digit

        check = (10 - (((3 * even_sum) + odd_sum) % 10)) % 10
        return check
    
    def validate_serial(self, serial: str):
        valid_models = ["C", "S", "A", "Q", "Y", "N"]
        valid_serial_regions = ["JF", "JH", "JM", "JE", "W", "B", "S", 
        "EE", "EF", "EH", "EM", "EG", "AH", "AG", "AM", "UH", "KF", "KH",
        "KM", "CF", "CH", "CM", "TF", "TH", "TM"]

        region = None
        valid_region = True
        valid_checksum = False
        strange_serial = False
        valid_length = True
        valid_model = True

        serial_length = len(serial)
        # 2-3 letters, 8-9 numbers = 10-12 characters
        if serial_length < 10 or serial_length > 12:
            valid_length = False
        
        letters = re.sub(r'[^A-Za-z]', '', serial)

        model = letters[0]
        if not model in valid_models:
            valid_model = False

        serial_region = letters[1:]
        if not serial_region in valid_serial_regions:
            valid_region = False
        
        numbers = re.sub(r'\D', '', serial)
        if len(numbers) == 9: # (we have a check digit!)
            valid_checksum = self.compute_check_digit(numbers[:8]) == int(numbers[8])

        if serial_region in ["UH", "CF", "CH", "CM"]:
            strange_serial = True

        region = {
            "W": "USA",
            "J": "JPN",
            "E": "EUR",
            "K": "KOR",
            "T": "TWN",
            "C": "CHN",
            "A": "AUS",
            "S": "ASI"
        }.get(serial_region[0], "Unknown")

        model_name = {
            "C": "Old 3DS",
            "S": "Old 3DS XL",
            "A": "Old 2DS",
            "Y": "New 3DS",
            "Q": "New 3DS XL",
            "N": "New 2DS XL"
        }.get(model, "Unknown")

        return region, valid_region, valid_checksum, strange_serial, valid_length, valid_model, model_name

    @command_with_perms(
        min_role="Soaper",
        name="validateserial", 
        aliases=["serialvalidate", "serial"], 
        help="Validate a 3DS serial number"
    )
    async def validate_serial_command(self, ctx, *, serial: str):
        region, valid_region, valid_checksum, strange_serial, valid_length, valid_model, model_name = self.validate_serial(serial)

        embed = discord.Embed(
            title="3DS Serial Validation",
            description=f"Serial: `{serial}`",
            color=discord.Color.blue()
        )

        embed.add_field(name="Region", value=region, inline=True)
        embed.add_field(name="Model", value=model_name, inline=True)
        embed.add_field(name="Valid Region", value=str(valid_region), inline=True)
        embed.add_field(name="Valid Model", value=str(valid_model), inline=True)

        embed.add_field(name="Valid Length", value=str(valid_length), inline=True)
        embed.add_field(name="Valid Checksum", value=str(valid_checksum), inline=True)
        embed.add_field(name="Strange Serial", value=str(strange_serial), inline=True)

        await ctx.respond(embed=embed)

def setup(bot):
    return bot.add_cog(SerialCog(bot))