import logging
import random
import io
import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List

import discord
from discord import app_commands, ui
from discord.ext import commands

from ballsdex.core.models import Ball, BallInstance, Player
from ballsdex.settings import settings
from ballsdex.packages.battle.xe_battle_lib import BattleBall, BattleInstance, gen_battle
from ballsdex.core.utils.transformers import BallInstanceTransform, BallEnabledTransform

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.battle")

# === Global battle storage ===
active_battles: Dict[int, "BattleSession"] = {}  # battle_id -> BattleSession


# === Dataclasses for battle management ===
@dataclass
class BattleSession:
    interaction: discord.Interaction
    battle_id: int
    mode: str = "solo"  # solo or multiplayer
    max_players_per_team: int = 3
    players_balls: Dict[int, List[BattleBall]] = field(default_factory=dict)
    teams: Dict[int, List[int]] = field(default_factory=lambda: {0: [], 1: []})
    players_ready: Dict[int, bool] = field(default_factory=dict)
    started: bool = False
    message: discord.Message = None
    battle_instance: BattleInstance = field(default_factory=BattleInstance)

    # Add a player
    def add_player(self, player_id: int, team: int = 0):
        if player_id not in self.players_balls:
            self.players_balls[player_id] = []
        if player_id not in self.teams[team]:
            self.teams[team].append(player_id)
        self.players_ready[player_id] = False

    # Mark a player ready; auto-start battle if all ready
    def mark_player_ready(self, player_id: int):
        self.players_ready[player_id] = True
        if all(self.players_ready.get(pid, False) for pid in self.players_balls):
            self.started = True
            if self.message:
                asyncio.create_task(self.start_battle())

    # Add balls to a player
    async def add_balls(self, player_id: int, balls: List[BallInstance]):
        user_balls = self.players_balls[player_id]
        for b in balls[:self.max_players_per_team]:
            battle_ball = BattleBall(
                name=b.countryball.country,
                owner=str(player_id),
                health=b.health,
                attack=b.attack,
                emoji=self.interaction.client.get_emoji(b.countryball.emoji_id)
            )
            if battle_ball not in user_balls:
                user_balls.append(battle_ball)
                # Add to BattleInstance for xe_battle_lib
                if player_id in self.teams[0]:
                    self.battle_instance.p1_balls.append(battle_ball)
                else:
                    self.battle_instance.p2_balls.append(battle_ball)
        await self.update_embed()

    # Remove balls from a player
    async def remove_balls(self, player_id: int, balls: List[BallInstance]):
        user_balls = self.players_balls[player_id]
        for b in balls:
            to_remove = None
            for bb in user_balls:
                if bb.name == b.countryball.country:
                    to_remove = bb
                    break
            if to_remove:
                user_balls.remove(to_remove)
                if player_id in self.teams[0] and to_remove in self.battle_instance.p1_balls:
                    self.battle_instance.p1_balls.remove(to_remove)
                elif player_id in self.teams[1] and to_remove in self.battle_instance.p2_balls:
                    self.battle_instance.p2_balls.remove(to_remove)
        await self.update_embed()

    # Update embed showing all decks and readiness
    async def update_embed(self):
        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Battle Plan",
            description="Players select their balls. Press Ready when done.",
            color=discord.Color.blurple()
        )
        for team_id, player_ids in self.teams.items():
            team_str = ""
            for pid in player_ids:
                ready = "✅" if self.players_ready.get(pid, False) else "❌"
                balls = self.players_balls.get(pid, [])
                deck_text = "\n".join([f"{b.emoji} {b.name} (HP:{b.health} | ATK:{b.attack})" for b in balls]) or "Empty"
                team_str += f"**Player {pid} [{ready}]**\n{deck_text}\n"
            embed.add_field(name=f"Team {team_id}", value=team_str, inline=False)

        if self.message:
            view = BattleReadyView(self)
            await self.message.edit(embed=embed, view=view)

    # Start the battle once all ready
    async def start_battle(self):
        battle_log = "\n".join(gen_battle(self.battle_instance))
        winner_text = f"{self.battle_instance.winner} - Turns: {self.battle_instance.turns}"
        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Battle",
            description="Battle Started!",
            color=discord.Color.green()
        )
        for team_id, player_ids in self.teams.items():
            team_str = ""
            for pid in player_ids:
                balls = self.players_balls.get(pid, [])
                deck_text = "\n".join([f"{b.emoji} {b.name} (HP:{b.health} | ATK:{b.attack})" for b in balls]) or "Empty"
                team_str += f"**Player {pid}**\n{deck_text}\n"
            embed.add_field(name=f"Team {team_id}", value=team_str, inline=False)

        embed.add_field(name="Winner", value=winner_text)
        embed.set_footer(text="Battle log attached.")

        if self.message:
            await self.message.edit(
                content=f"Battle {self.battle_id}",
                embed=embed,
                view=create_disabled_buttons(),
                attachments=[discord.File(io.StringIO(battle_log), filename="battle-log.txt")]
            )


# === Views ===
class BattleReadyView(ui.View):
    def __init__(self, battle: BattleSession):
        super().__init__(timeout=None)
        self.battle = battle
        for pid in battle.players_balls:
            button = ui.Button(style=discord.ButtonStyle.success, label=f"Ready: Player {pid}")
            button.callback = self.make_ready_callback(pid)
            self.add_item(button)

    def make_ready_callback(self, pid: int):
        async def callback(interaction: discord.Interaction):
            if not self.battle.players_ready.get(pid, False):
                self.battle.mark_player_ready(pid)
                await interaction.response.send_message(f"Player {pid} is now ready.", ephemeral=True)
                await self.battle.update_embed()
        return callback


def create_disabled_buttons() -> ui.View:
    view = ui.View()
    view.add_item(ui.Button(label="Ready", style=discord.ButtonStyle.success, disabled=True))
    return view


# === Discord Commands Cog ===
class BattleCog(commands.Cog):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    # Main battle command
    @app_commands.command(name="battle")
    async def battle(self, interaction: discord.Interaction, mode: str = "solo", users: int = 2):
        battle_id = random.randint(1000, 9999)
        session = BattleSession(interaction, battle_id, mode=mode)
        active_battles[battle_id] = session

        # Add players placeholders
        if mode.lower() == "solo":
            session.add_player(interaction.user.id, team=0)
            # opponent must be chosen in the UI later
        else:
            for i in range(users):
                session.add_player(i, team=0)
                session.add_player(i+users, team=1)

        msg = await interaction.response.send_message(
            f"Battle {battle_id} created. Players can add balls and press Ready!",
            embed=discord.Embed(title="Preparing Battle..."),
            view=BattleReadyView(session)
        )
        session.message = await msg.original_response()

    # Add a single ball
    @app_commands.command()
    async def add(self, interaction: discord.Interaction, countryball: BallInstanceTransform):
        session = next((b for b in active_battles.values() if interaction.user.id in b.players_balls), None)
        if not session:
            await interaction.response.send_message("You are not in a battle.", ephemeral=True)
            return
        await session.add_balls(interaction.user.id, [countryball])
        await interaction.response.send_message(f"Added {countryball.countryball.country}.", ephemeral=True)

    # Remove a single ball
    @app_commands.command()
    async def remove(self, interaction: discord.Interaction, countryball: BallInstanceTransform):
        session = next((b for b in active_battles.values() if interaction.user.id in b.players_balls), None)
        if not session:
            await interaction.response.send_message("You are not in a battle.", ephemeral=True)
            return
        await session.remove_balls(interaction.user.id, [countryball])
        await interaction.response.send_message(f"Removed {countryball.countryball.country}.", ephemeral=True)

    # Bulk add balls
    @app_commands.command()
    async def bulk_add(self, interaction: discord.Interaction, countryball: BallEnabledTransform):
        session = next((b for b in active_battles.values() if interaction.user.id in b.players_balls), None)
        if not session:
            await interaction.response.send_message("You are not in a battle.", ephemeral=True)
            return
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        balls = await countryball.ballinstances.filter(player=player)
        await session.add_balls(interaction.user.id,
