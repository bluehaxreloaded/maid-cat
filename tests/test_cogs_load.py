"""Every cog main.py loads still imports and sets up, so a typo or bad import fails here instead of on the server."""

import asyncio
import re

import discord
from discord.ext import bridge

from conftest import ROOT


def _extensions() -> list[str]:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    return re.findall(r'^bot\.load_extension\("(\w+)"\)', main, flags=re.M)


def test_main_loads_cogs():
    assert len(_extensions()) >= 14


def test_every_cog_loads():
    async def load():
        bot = bridge.Bot(command_prefix=".", intents=discord.Intents.default())
        for name in _extensions():
            bot.load_extension(name)
        return bot

    bot = asyncio.run(load())
    assert set(bot.extensions) == set(_extensions())


def test_tests_never_use_the_real_database():
    import constants
    import db

    assert constants.DATABASE is None
    assert not db.enabled()
