import discord

# Exceptions
class ChannelOrCategoryNotFound(discord.DiscordException): # basic exception for when something (excluding commands) is not found
    def __init__(self, id: int, kind: str):
        super().__init__(f"{kind} with id {id} not found!")

class ChannelNotFound(ChannelOrCategoryNotFound):
    def __init__(self, channel_id: int, note: str = None):
        prefix = f"{note} " if note else ""
        super().__init__(channel_id, f"{prefix}Channel".capitalize())

class CategoryNotFound(ChannelOrCategoryNotFound):
    def __init__(self, category_id: int, note: str = None):
        prefix = f"{note} " if note else ""
        super().__init__(category_id, f"{prefix}Category".capitalize())
