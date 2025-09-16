import discord
from discord import app_commands, ui
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Dict
import random
import asyncio
import io

# -------------------------
# Models / Dataclasses
# -------------------------

SPECIAL_BUFFS = {
    "Shiny": {"health": 2500, "attack": 2500},
    "Robot": {"health": 100, "attack": 100},
    "Mythic": {"health": 7500, "attack": 7500},
    "Global Superpower": {"health": 3750, "attack": 3750},
}

@dataclass
class Ball:
    name: str
    owner_id: int
    health: int
    attack: int
    shiny: bool = False
    special: str = ""
    dead: bool = False

@dataclass
class BattleInstance:
    battle_id: int
    mode: str  # "solo" or "multiplayer"
    teams: Dict[int, List[int]] = field(default_factory=lambda: {0: [], 1: []})
    players_balls: Dict[int, List[Ball]] = field(default_factory=dict)
    ready_players: Dict[int, bool] = field(default_factory=dict)
    turn_order: List[int] = field(default_factory=list)
    current_turn_index: int = 0
    started: bool = False
    winner: str = ""
    logs: List[str] = field(default_factory=list)

# -------------------------
# Battle Logic
# -------------------------

def apply_special_buffs(ball: Ball):
    if ball.shiny:
        buff = SPECIAL_BUFFS["Shiny"]
        ball.health += buff["health"]
        ball.attack += buff["attack"]
    if ball.special in SPECIAL_BUFFS:
        buff = SPECIAL_BUFFS[ball.special]
        ball.health += buff["health"]
        ball.attack += buff["attack"]

def get_damage(ball: Ball):
    return int(ball.attack * random.uniform(0.8, 1.2))

def attack(attacker: Ball, targets: List[Ball]):
    alive_targets = [b for b in targets if not b.dead]
    if not alive_targets:
        return "No valid targets."

    target = random.choice(alive_targets)
    dmg = get_damage(attacker)
    target.health -= dmg
    if target.health <= 0:
        target.dead = True
        target.health = 0
        return f"{attacker.name} killed {target.name}!"
    else:
        return f"{attacker.name} dealt {dmg} damage to {target.name}."

def next_turn(battle: BattleInstance):
    battle.current_turn_index = (battle.current_turn_index + 1) % len(battle.turn_order)
    return battle.turn_order[battle.current_turn_index]

def is_battle_over(battle: BattleInstance):
    alive_teams = []
    for team, players in battle.teams.items():
        if any(ball.dead is False for pid in players for ball in battle.players_balls.get(pid, [])):
            alive_teams.append(team)
    return len(alive_teams) <= 1

# -------------------------
# Discord Bot Cog
# -------------------------

class BattleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.battles: Dict[int, BattleInstance] = {}
        self.battle_counter = 1000

    # -------------------------
    # /battle start
    # -------------------------
    @app_commands.command(name="battle_start")
    async def start(self, interaction: discord.Interaction, mode: str = "solo", users: int = 1):
        """Start a battle: mode=solo/multiplayer, users=# per team"""
        if mode not in ["solo", "multiplayer"]:
            await interaction.response.send_message("Mode must be 'solo' or 'multiplayer'.", ephemeral=True)
            return

        battle_id = self.battle_counter
        self.battle_counter += 1

        battle = BattleInstance(battle_id=battle_id, mode=mode)
        self.battles[battle_id] = battle

        # Register starter player
        battle.teams[0].append(interaction.user.id)
        battle.players_balls[interaction.user.id] = []
        battle.ready_players[interaction.user.id] = False
        battle.turn_order.append(interaction.user.id)

        if mode == "solo":
            await interaction.response.send_message(f"Solo battle created! ID: {battle_id}. Opponent can be challenged.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Multiplayer battle created! ID: {battle_id}. Players can now join.", ephemeral=True)

    # -------------------------
    # /battle add
    # -------------------------
    @app_commands.command(name="battle_add")
    async def add(self, interaction: discord.Interaction, name: str, health: int, attack: int, shiny: bool = False, special: str = ""):
        """Add a ball to your pre-battle deck"""
        battle = next((b for b in self.battles.values() if interaction.user.id in b.players_balls), None)
        if not battle:
            await interaction.response.send_message("You are not in a battle.", ephemeral=True)
            return

        if battle.ready_players.get(interaction.user.id, False):
            await interaction.response.send_message("You cannot add balls after ready.", ephemeral=True)
            return

        ball = Ball(name=name, owner_id=interaction.user.id, health=health, attack=attack, shiny=shiny, special=special)
        apply_special_buffs(ball)
        battle.players_balls[interaction.user.id].append(ball)
        await interaction.response.send_message(f"Added {ball.name} to your deck.", ephemeral=True)

    # -------------------------
    # /battle remove
    # -------------------------
    @app_commands.command(name="battle_remove")
    async def remove(self, interaction: discord.Interaction, name: str):
        """Remove a ball from your pre-battle deck"""
        battle = next((b for b in self.battles.values() if interaction.user.id in b.players_balls), None)
        if not battle:
            await interaction.response.send_message("You are not in a battle.", ephemeral=True)
            return

        if battle.ready_players.get(interaction.user.id, False):
            await interaction.response.send_message("You cannot remove balls after ready.", ephemeral=True)
            return

        balls = battle.players_balls.get(interaction.user.id, [])
        removed = [b for b in balls if b.name == name]
        if not removed:
            await interaction.response.send_message("Ball not found in your deck.", ephemeral=True)
            return
        for b in removed:
            balls.remove(b)
        await interaction.response.send_message(f"Removed {name} from your deck.", ephemeral=True)

    # -------------------------
    # /battle bulk
    # -------------------------
    @app_commands.command(name="battle_bulk")
    async def bulk(self, interaction: discord.Interaction, count: int):
        """Add multiple generic balls to your deck (solo only)"""
        battle = next((b for b in self.battles.values() if interaction.user.id in b.players_balls), None)
        if not battle:
            await interaction.response.send_message("You are not in a battle.", ephemeral=True)
            return

        if battle.mode != "solo":
            await interaction.response.send_message("Bulk adding is only allowed in solo battles.", ephemeral=True)
            return

        if battle.ready_players.get(interaction.user.id, False):
            await interaction.response.send_message("You cannot bulk add after ready.", ephemeral=True)
            return

        for i in range(count):
            ball = Ball(name=f"GenericBall{i+1}", owner_id=interaction.user.id, health=500, attack=100)
            battle.players_balls[interaction.user.id].append(ball)

        await interaction.response.send_message(f"Added {count} generic balls to your deck.", ephemeral=True)

    # -------------------------
    # /battle ready
    # -------------------------
    @app_commands.command(name="battle_ready")
    async def ready(self, interaction: discord.Interaction):
        """Mark yourself ready to start the battle"""
        battle = next((b for b in self.battles.values() if interaction.user.id in b.players_balls), None)
        if not battle:
            await interaction.response.send_message("You are not in a battle.", ephemeral=True)
            return

        battle.ready_players[interaction.user.id] = True
        await interaction.response.send_message("You are now ready!", ephemeral=True)

        # Check if all players ready
        if all(battle.ready_players[pid] for pid in battle.players_balls):
            battle.started = True
            await self.run_battle(battle, interaction.channel)

    # -------------------------
    # Battle Runner
    # -------------------------
    async def run_battle(self, battle: BattleInstance, channel: discord.TextChannel):
        logs = []
        alive = True
        while alive:
            current_pid = battle.turn_order[battle.current_turn_index]
            player_balls = [b for b in battle.players_balls[current_pid] if not b.dead]

            # Get enemy balls
            enemy_pids = [pid for pid in battle.players_balls if pid != current_pid]
            enemy_balls = [b for pid in enemy_pids for b in battle.players_balls[pid] if not b.dead]

            if not player_balls or not enemy_balls:
                alive = False
                break

            attacker = random.choice(player_balls)
            log_entry = attack(attacker, enemy_balls)
            logs.append(f"Player {current_pid}: {log_entry}")

            if is_battle_over(battle):
                alive = False
                break

            next_turn(battle)

        battle.logs = logs
        # Determine winner
        for team, players in battle.teams.items():
            if any(not b.dead for pid in players for b in battle.players_balls[pid]):
                battle.winner = f"Team {team}"
                break

        # Send battle log
        text = "\n".join(logs)
        await channel.send(f"Battle {battle.battle_id} finished! Winner: {battle.winner}\n```{text}```")

# -------------------------
# Setup
# -------------------------
async def setup(bot):
    await bot.add_cog(BattleCog(bot))
