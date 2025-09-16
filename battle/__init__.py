from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

# Import the battle cog
from .battle_cog import Battle  # Replace 'battle_cog' with the actual filename of your Cog without .py

async def setup(bot: "BallsDexBot"):
    """Setup function to add the Battle Cog."""
    await bot.add_cog(Battle(bot))
