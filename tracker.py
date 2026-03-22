import discord
import json
import asyncio
from pathlib import Path
from discord.ext import commands
from discord.ext.bridge import BridgeOption
from perms import command_with_perms
from constants import SOAP_TRACKER_ID, NNID_TRACKER_ID

TRACKER_COUNTS_FILE = Path(__file__).parent / "tracker_counts.json"

class TrackerCog(commands.Cog):
    """Cog to track and update SOAP and NNID channel counts in voice channel names"""

    def __init__(self, bot):
        self.bot = bot
        # Initialize file if it doesn't exist
        try:
            if not TRACKER_COUNTS_FILE.exists():
                self._save_counts_to_file(0, 0)
        except (IOError, PermissionError) as e:
            print(f"Warning: Could not initialize tracker_counts.json: {e}")
            print(f"File path: {TRACKER_COUNTS_FILE.absolute()}")

    def _read_counts(self):
        """Read counts from JSON file"""
        if TRACKER_COUNTS_FILE.exists():
            try:
                with open(TRACKER_COUNTS_FILE, "r") as f:
                    data = json.load(f)
                    return data.get("soap_count", 0), data.get("nnid_count", 0)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error reading tracker counts: {e}")
                return 0, 0
        return 0, 0

    def increment_soap_count(self):
        """Increment SOAP count and save to file (does not update voice channels)"""
        soap_count, nnid_count = self._read_counts()
        soap_count += 1
        self._save_counts_to_file(soap_count, nnid_count)

    def increment_nnid_count(self):
        """Increment NNID count and save to file (does not update voice channels)"""
        soap_count, nnid_count = self._read_counts()
        nnid_count += 1
        self._save_counts_to_file(soap_count, nnid_count)

    def _save_counts_to_file(self, soap_count: int, nnid_count: int):
        """Save counts to file"""
        try:
            TRACKER_COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TRACKER_COUNTS_FILE, "w") as f:
                json.dump({"soap_count": soap_count, "nnid_count": nnid_count}, f)
        except (IOError, PermissionError) as e:
            print(f"Error saving tracker counts: {e}")
            print(f"File path: {TRACKER_COUNTS_FILE.absolute()}")
            print("Please ensure the bot has write permissions to this directory.")

    async def update_trackers(self, guild: discord.Guild):
        """Update both tracker voice channel names with current counts"""
        if not guild:
            return

        # Read counts from JSON file
        soap_count, nnid_count = self._read_counts()

        # Update SOAP tracker
        if SOAP_TRACKER_ID:
            try:
                soap_tracker = guild.get_channel(SOAP_TRACKER_ID)
                if soap_tracker and isinstance(soap_tracker, discord.VoiceChannel):
                    new_name = f"🧼 SOAPs Served: {soap_count}"
                    if soap_tracker.name != new_name:
                        await soap_tracker.edit(name=new_name)
                elif not soap_tracker:
                    print(f"SOAP tracker channel {SOAP_TRACKER_ID} not found in guild {guild.id}")
            except discord.Forbidden:
                print("No permission to edit SOAP tracker channel")
            except Exception as e:
                print(f"Error updating SOAP tracker: {e}")

        # Add a small delay to avoid rate limiting
        await asyncio.sleep(0.5)

        # Update NNID tracker
        if NNID_TRACKER_ID:
            try:
                nnid_tracker = guild.get_channel(NNID_TRACKER_ID)
                if nnid_tracker and isinstance(nnid_tracker, discord.VoiceChannel):
                    new_name = f"🔄 NNIDs Served: {nnid_count}"
                    if nnid_tracker.name != new_name:
                        await nnid_tracker.edit(name=new_name)
                elif not nnid_tracker:
                    print(f"NNID tracker channel {NNID_TRACKER_ID} not found in guild {guild.id}")
            except discord.Forbidden:
                print("No permission to edit NNID tracker channel")
            except Exception as e:
                print(f"Error updating NNID tracker: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Start periodic update task when bot starts"""
        # Update immediately on startup
        for guild in self.bot.guilds:
            await self.update_trackers(guild)
        
        # Start background task to update every 5 minutes
        self.bot.loop.create_task(self._periodic_update())

    async def _periodic_update(self):
        """Background task that updates voice channels every 5 minutes"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    await self.update_trackers(guild)
            except Exception as e:
                print(f"Error in periodic tracker update: {e}")
            
            # Wait 5 minutes (300 seconds)
            await asyncio.sleep(300)

    @command_with_perms(
        allowed_roles=["Developer", "Staff"],
        name="sync",
        aliases=["synctrackers", "forcetrackerupdate"],
        help="Force synchronize tracker voice channels with JSON counts",
    )
    async def sync_trackers(self, ctx):
        """Force synchronize voice channels with JSON counts"""
        soap_count, nnid_count = self._read_counts()

        await ctx.respond(f"🔄 Synchronizing trackers... (SOAP: {soap_count}, NNID: {nnid_count})")

        for guild in self.bot.guilds:
            await self.update_trackers(guild)

        await ctx.respond("✅ Trackers synchronized!")

    @command_with_perms(
        allowed_roles=["Developer", "Staff"],
        name="setcount",
        aliases=["settrackers", "setcounters"],
        help="Manually set SOAP or NNID counter value. Usage: setcount soap 100 or setcount nnid 50",
    )
    async def set_count(
        self,
        ctx,
        counter: BridgeOption(str, "Which counter to set: soap or nnid"),
        value: BridgeOption(int, "The value to set"),
    ):
        """Manually set a tracker count. Usage: setcount soap 100 or setcount nnid 50"""
        counter_lower = counter.strip().lower()
        if counter_lower not in ("soap", "nnid"):
            await ctx.respond("Counter must be `soap` or `nnid`.", ephemeral=True)
            return

        if value < 0:
            await ctx.respond("Value cannot be negative.", ephemeral=True)
            return

        soap_count, nnid_count = self._read_counts()

        if counter_lower == "soap":
            soap_count = value
        else:
            nnid_count = value

        self._save_counts_to_file(soap_count, nnid_count)

        for guild in self.bot.guilds:
            await self.update_trackers(guild)

        await ctx.respond(f"✅ **{counter_lower.upper()}** count set to {value}. (SOAP: {soap_count}, NNID: {nnid_count})")


def setup(bot):
    return bot.add_cog(TrackerCog(bot))

