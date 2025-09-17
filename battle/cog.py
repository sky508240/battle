import discord
from discord.ext import commands
from discord import app_commands
from ballsdex.core.models import BallInstance, Player
from ballsdex.core.utils.transformers import BallInstanceTransform

SPECIALS = {
    "Shiny": {"extra_health": 2500, "extra_attack": 2500},
    "Robot": {"extra_health": 100, "extra_attack": 100},
    "Mythic": {"extra_health": 7500, "extra_attack": 7500},
    "Global Superpower": {"extra_health": 3750, "extra_attack": 3750},
    "Boss": {"extra_health": 10000, "extra_attack": 10000},
}

MAX_BALLS = 50

class FullBattleSystemCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_battles = {}  # channel_id -> battle info

    # ---------------------------
    # /battle start
    # ---------------------------
    @app_commands.command(name="start", description="Start a solo or multiplayer battle")
    @app_commands.describe(
        mode="Battle mode: solo or multiplayer",
        opponent="User to challenge (solo mode only)",
        team_size="Number of players per team (multiplayer only, default 3)"
    )
    async def start(
        self,
        interaction: discord.Interaction,
        mode: str,
        opponent: discord.Member = None,
        team_size: int = 3,
    ):
        if mode not in ["solo", "multiplayer"]:
            await interaction.response.send_message("Invalid mode.", ephemeral=True)
            return
        if mode == "solo" and not opponent:
            await interaction.response.send_message("You must specify an opponent.", ephemeral=True)
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
        if opponent:
            self.active_battles[interaction.channel.id]["players"][opponent.id] = []

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
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return
        if battle["started"]:
            await interaction.response.send_message("Battle started, cannot add.", ephemeral=True)
            return

        user_balls = battle["players"].setdefault(interaction.user.id, [])
        if len(user_balls) >= MAX_BALLS:
            await interaction.response.send_message("Cannot add more than 50 balls.", ephemeral=True)
            return

        user_balls.append(self._apply_specials(ball))
        await interaction.response.send_message(f"Added **{ball.country}** to the battle!")

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
        if not battle or interaction.user.id not in battle["players"]:
            await interaction.response.send_message("You have no balls in this battle.", ephemeral=True)
            return

        user_balls = battle["players"][interaction.user.id]
        for b in user_balls:
            if b["id"] == ball.id:
                user_balls.remove(b)
                await interaction.response.send_message(f"Removed **{ball.country}**.")
                return
        await interaction.response.send_message("Ball not in your deck.", ephemeral=True)

    # ---------------------------
    # /battle bulk
    # ---------------------------
    @app_commands.command(name="bulk", description="Add all your balls to the battle (solo only)")
    async def bulk(self, interaction: discord.Interaction):
        battle = self.active_battles.get(interaction.channel.id)
        if not battle:
            await interaction.response.send_message("No active battle here.", ephemeral=True)
            return
        if battle["mode"] == "multiplayer":
            await interaction.response.send_message("Bulk not allowed in multiplayer.", ephemeral=True)
            return
        if battle["started"]:
            await interaction.response.send_message("Battle started, cannot bulk add.", ephemeral=True)
            return

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        balls = await BallInstance.filter(player=player)
        user_balls = battle["players"].setdefault(interaction.user.id, [])

        added = 0
        for ball in balls:
            if len(user_balls) >= MAX_BALLS:
                break
            user_balls.append(self._apply_specials(ball))
            added += 1

        await interaction.response.send_message(f"Added {added} balls to your deck.")

    # ---------------------------
    # Helper: Apply Specials
    # ---------------------------
    def _apply_specials(self, ball: BallInstance) -> dict:
        stats = {"id": ball.id, "country": ball.country, "health": ball.health, "attack": ball.attack}
        for special, bonus in SPECIALS.items():
            if getattr(ball, "special", None) == special:
                stats["health"] += bonus["extra_health"]
                stats["attack"] += bonus["extra_attack"]
        return stats

# ---------------------------
# Setup
# ---------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(FullBattleSystemCog(bot))
