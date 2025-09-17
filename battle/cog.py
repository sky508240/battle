import discord
from discord.ext import commands
from discord import app_commands

# Import the BallInstanceTransform (already exists in your repo)
from ballsdex.core.models import BallInstance
from ballsdex.core.transformers import BallInstanceTransform


SPECIALS = {
    "Shiny": {"extra_health": 2500, "extra_attack": 2500},
    "Robot": {"extra_health": 100, "extra_attack": 100},
    "Mythic": {"extra_health": 7500, "extra_attack": 7500},
    "Global Superpower": {"extra_health": 3750, "extra_attack": 3750},
    "Boss": {"extra_health": 10000, "extra_attack": 10000},
}


class FullBattleSystemCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_battles = {}

    # ---------------------------
    # /battle start
    # ---------------------------
    @app_commands.command(name="start", description="Start a solo or multiplayer battle")
    @app_commands.describe(
        mode="Battle mode: solo or multiplayer",
        opponents="User to challenge (for solo mode)",
        team_size="Number of players per team (multiplayer only, default 3)"
    )
    async def start(
        self,
        interaction: discord.Interaction,
        mode: str,
        opponents: discord.Member = None,
        team_size: int = 3,
    ):
        if mode not in ["solo", "multiplayer"]:
            await interaction.response.send_message("Invalid mode. Choose solo or multiplayer.", ephemeral=True)
            return

        if mode == "solo" and not opponents:
            await interaction.response.send_message("You must specify an opponent in solo mode.", ephemeral=True)
            return

        if mode == "multiplayer":
            if team_size < 2 or team_size > 25:
                await interaction.response.send_message("Team size must be between 2 and 25.", ephemeral=True)
                return

        self.active_battles[interaction.channel.id] = {
            "mode": mode,
            "players": {interaction.user.id: []},
            "team_size": team_size if mode == "multiplayer" else 1,
            "started": False,
        }

        if opponents:
            self.active_battles[interaction.channel.id]["players"][opponents.id] = []

        await interaction.response.send_message(
            f"Battle created in **{mode}** mode. Use `/battle add` to add your balls!"
        )

    # ---------------------------
    # /battle add
    # ---------------------------
    @app_commands.command(name="add", description="Add a ball to the battle")
    async def add(
        self,
        interaction: discord.Interaction,
        ball: BallInstanceTransform,
    ):
        battle = self.active_battles.get(interaction.channel.id)
        if not battle:
            await interaction.response.send_message("No battle is active here.", ephemeral=True)
            return

        if battle["started"]:
            await interaction.response.send_message("Battle already started, you cannot add more balls.", ephemeral=True)
            return

        if interaction.user.id not in battle["players"]:
            battle["players"][interaction.user.id] = []

        if len(battle["players"][interaction.user.id]) >= 50:
            await interaction.response.send_message("You cannot add more than 50 balls.", ephemeral=True)
            return

        stats = self._apply_specials(ball)
        battle["players"][interaction.user.id].append(stats)

        await interaction.response.send_message(
            f"Added **{ball.country}** with {stats['health']} HP and {stats['attack']} ATK to the battle!"
        )

    # ---------------------------
    # /battle remove
    # ---------------------------
    @app_commands.command(name="remove", description="Remove a ball from the battle")
    async def remove(
        self,
        interaction: discord.Interaction,
        ball: BallInstanceTransform,
    ):
        battle = self.active_battles.get(interaction.channel.id)
        if not battle:
            await interaction.response.send_message("No battle is active here.", ephemeral=True)
            return

        if interaction.user.id not in battle["players"]:
            await interaction.response.send_message("You have no balls in this battle.", ephemeral=True)
            return

        for b in battle["players"][interaction.user.id]:
            if b["id"] == ball.id:
                battle["players"][interaction.user.id].remove(b)
                await interaction.response.send_message(
                    f"Removed **{ball.country}** from the battle."
                )
                return

        await interaction.response.send_message("That ball is not in the battle.", ephemeral=True)

    # ---------------------------
    # /battle bulk
    # ---------------------------
    @app_commands.command(name="bulk", description="Add multiple balls at once (solo only)")
    async def bulk(
        self,
        interaction: discord.Interaction,
        balls: app_commands.Transform[list[BallInstance], BallInstanceTransform],
    ):
        battle = self.active_battles.get(interaction.channel.id)
        if not battle:
            await interaction.response.send_message("No battle is active here.", ephemeral=True)
            return

        if battle["mode"] == "multiplayer":
            await interaction.response.send_message("Bulk add is not allowed in multiplayer mode.", ephemeral=True)
            return

        if battle["started"]:
            await interaction.response.send_message("Battle already started, you cannot bulk add.", ephemeral=True)
            return

        if interaction.user.id not in battle["players"]:
            battle["players"][interaction.user.id] = []

        for ball in balls:
            stats = self._apply_specials(ball)
            battle["players"][interaction.user.id].append(stats)

        await interaction.response.send_message(
            f"Added {len(balls)} balls to the battle for {interaction.user.display_name}."
        )

    # ---------------------------
    # Helpers
    # ---------------------------
    def _apply_specials(self, ball: BallInstance) -> dict:
        stats = {
            "id": ball.id,
            "country": ball.country,
            "health": ball.health,
            "attack": ball.attack,
        }
        for special, bonus in SPECIALS.items():
            if getattr(ball, "special", None) == special:
                stats["health"] += bonus["extra_health"]
                stats["attack"] += bonus["extra_attack"]
        return stats
