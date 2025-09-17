from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

from .cog import FullBattleSystemCog

async def setup(bot: "BallsDexBot"):
    """Setup function to add the FullBattleSystemCog."""
    await bot.add_cog(FullBattleSystemCog(bot))
