import discord, random, asyncio
from discord import app_commands, ui
from discord.ext import commands
from tortoise.models import Model
from tortoise import fields
from dataclasses import dataclass
from typing import List, Dict

# ----------------------
# Database Models
# ----------------------
class Player(Model):
    id = fields.IntField(pk=True)
    discord_id = fields.BigIntField(unique=True)
    balls: fields.ReverseRelation["Ball"]

class Ball(Model):
    id = fields.IntField(pk=True)
    owner: fields.ForeignKeyRelation[Player] = fields.ForeignKeyField("models.Player", related_name="balls")
    name = fields.CharField(max_length=255)
    shiny = fields.BooleanField(default=False)
    special_card = fields.CharField(max_length=255, null=True)
    health = fields.IntField(default=100)
    attack = fields.IntField(default=50)

class BattleLog(Model):
    id = fields.IntField(pk=True)
    battle_id = fields.IntField()
    entry = fields.TextField()

# ----------------------
# Buffs
# ----------------------
SPECIAL_BUFFS = {
    "Shiny": {"health": 2500, "attack": 2500},
    "Mythic": {"health": 7500, "attack": 7500},
    "Robot": {"health": 150, "attack": 150},
    "Global Superpower": {"health": 3750, "attack": 3750},
}

def apply_special_buffs(ball: Ball):
    if ball.shiny:
        buff = SPECIAL_BUFFS["Shiny"]
        ball.health += buff["health"]
        ball.attack += buff["attack"]
    if ball.special_card in SPECIAL_BUFFS:
        buff = SPECIAL_BUFFS[ball.special_card]
        ball.health += buff["health"]
        ball.attack += buff["attack"]

# ----------------------
# Battle Structures
# ----------------------
@dataclass
class BattleBall:
    owner_id: int
    ball_instance: Ball
    alive: bool = True
    team: int = 0

class MultiTeamBattle:
    def __init__(self, battle_id: int, multiplayer: bool = False, max_balls_per_player: int = 3, turn_timer: int = 30):
        self.battle_id = battle_id
        self.multiplayer = multiplayer
        self.max_balls_per_player = max_balls_per_player
        self.turn_timer = turn_timer
        self.players_balls: Dict[int, List[BattleBall]] = {}
        self.player_order: List[int] = []
        self.current_turn_index = 0
        self.started = False
        self.message: discord.Message = None
        self.turn_futures: Dict[int, asyncio.Future] = {}
        self.spectators: set[int] = set()
        self.teams: Dict[int, List[int]] = {0: [], 1: []}

    def add_player_balls(self, player_id: int, balls, team: int = 0, bulk: bool = False):
        if self.started:
            return False
        battle_balls = self.players_balls.get(player_id, [])
        existing_ids = {b.ball_instance.id for b in battle_balls}
        limit = None if bulk else self.max_balls_per_player
        for b in balls[:limit] if limit else balls:
            if b.id in existing_ids:
                continue
            apply_special_buffs(b)
            battle_balls.append(BattleBall(player_id, b, alive=True, team=team))
            existing_ids.add(b.id)
        self.players_balls[player_id] = battle_balls
        if player_id not in self.player_order:
            self.player_order.append(player_id)
        if player_id not in self.teams[team]:
            self.teams[team].append(player_id)
        return True

    def remove_player_ball(self, player_id: int, ball_id: int):
        if self.started:
            return False
        if player_id not in self.players_balls:
            return False
        before = len(self.players_balls[player_id])
        self.players_balls[player_id] = [b for b in self.players_balls[player_id] if b.ball_instance.id != ball_id]
        return len(self.players_balls[player_id]) < before

    def current_player(self):
        return self.player_order[self.current_turn_index]

    def advance_turn(self):
        self.current_turn_index = (self.current_turn_index + 1) % len(self.player_order)

    def is_player_alive(self, player_id: int):
        return any(b.alive for b in self.players_balls.get(player_id, []))

    def is_battle_over(self):
        alive_teams = [team for team, pids in self.teams.items() if any(self.is_player_alive(pid) for pid in pids)]
        return len(alive_teams) <= 1

    def generate_embed(self):
        embed = discord.Embed(title=f"Battle {self.battle_id}", color=discord.Color.blurple())
        for team, players in self.teams.items():
            team_str = ""
            for pid in players:
                balls = self.players_balls.get(pid, [])
                status = "\n".join([f"{b.ball_instance.name}: {b.ball_instance.health} HP {'✅' if b.alive else '❌'}" for b in balls])
                team_str += f"**Player {pid}**\n{status}\n"
            embed.add_field(name=f"Team {team}", value=team_str or "No balls", inline=False)
        if self.started:
            embed.set_footer(text=f"Turn: Player {self.current_player()}")
        return embed

    async def update_message(self):
        if self.message:
            await self.message.edit(embed=self.generate_embed(), view=BattleSelectView(self))

    async def wait_for_player_turn(self, player_id: int):
        future = asyncio.get_event_loop().create_future()
        self.turn_futures[player_id] = future
        return await future

    async def resolve_turn(self, player_id: int, attacker: BattleBall, target_pid: int, target_ball: BattleBall):
        dmg = attacker.ball_instance.attack
        target_ball.ball_instance.health -= dmg
        if target_ball.ball_instance.health <= 0:
            target_ball.alive = False
        await BattleLog.create(battle_id=self.battle_id, entry=f"{attacker.ball_instance.name} attacked {target_ball.ball_instance.name} for {dmg} dmg.")
        if player_id in self.turn_futures:
            self.turn_futures[player_id].set_result(True)

    async def auto_battle(self):
        self.started = True
        while not self.is_battle_over():
            current = self.current_player()
            try:
                await asyncio.wait_for(self.wait_for_player_turn(current), timeout=self.turn_timer)
            except asyncio.TimeoutError:
                await self.auto_attack_player(current)
            self.advance_turn()
            await self.update_message()
            await asyncio.sleep(1)

    async def auto_attack_player(self, player_id: int):
        balls = [b for b in self.players_balls[player_id] if b.alive]
        if not balls: return
        attacker = random.choice(balls)
        enemies = [pid for pid in self.player_order if pid != player_id and self.is_player_alive(pid)]
        if not enemies: return
        target_pid = random.choice(enemies)
        target_ball = random.choice([b for b in self.players_balls[target_pid] if b.alive])
        await self.resolve_turn(player_id, attacker, target_pid, target_ball)

# ----------------------
# Views
# ----------------------
class BattleSelectView(ui.View):
    def __init__(self, battle: MultiTeamBattle):
        super().__init__(timeout=None)
        self.battle = battle
        self.add_item(AttackSelect(battle))

class AttackSelect(ui.Select):
    def __init__(self, battle: MultiTeamBattle):
        options = []
        current_pid = battle.current_player()
        balls = battle.players_balls.get(current_pid, [])
        for i, b in enumerate(balls):
            if b.alive:
                options.append(ui.SelectOption(label=f"{b.ball_instance.name}", value=str(i)))
        super().__init__(placeholder="Choose attacker", options=options)
        self.battle = battle
        self.current_pid = current_pid

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        attacker = self.battle.players_balls[self.current_pid][index]
        targets = [(pid, b) for pid, balls in self.battle.players_balls.items()
                   for b in balls if b.alive and pid != self.current_pid]
        if not targets:
            await interaction.response.send_message("❌ No valid targets.", ephemeral=True)
            return
        await interaction.response.send_message("Choose a target:", view=TargetSelect(self.battle, attacker, targets), ephemeral=True)

class TargetSelect(ui.Select):
    def __init__(self, battle: MultiTeamBattle, attacker: BattleBall, targets):
        options = [ui.SelectOption(label=f"{b.ball_instance.name} (Owner {pid})", value=str(i))
                   for i, (pid, b) in enumerate(targets)]
        super().__init__(placeholder="Select target", options=options)
        self.targets = targets
        self.attacker = attacker
        self.battle = battle

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        target_pid, target_ball = self.targets[index]
        await self.battle.resolve_turn(self.attacker.owner_id, self.attacker, target_pid, target_ball)
        await self.battle.update_message()
        await interaction.response.send_message(f"✅ {self.attacker.ball_instance.name} attacked {target_ball.ball_instance.name}!", ephemeral=True)

# ----------------------
# Cog
# ----------------------
class FullBattleSystemCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_battles = {}

    battle = app_commands.Group(name="battle", description="Battle commands")

    # ---- START ----
    @battle.command(name="start", description="Start a new battle")
    async def start_battle(self, interaction: discord.Interaction, mode: str, max_balls: int = 3, turn_timer: int = 30):
        battle_id = random.randint(1000, 9999)
        multiplayer = mode.lower() == "multiplayer"
        battle = MultiTeamBattle(battle_id, multiplayer=multiplayer, max_balls_per_player=max_balls, turn_timer=turn_timer)
        self.active_battles[battle_id] = battle
        player_obj = await Player.get(discord_id=interaction.user.id)
        balls = await player_obj.balls.all()
        battle.add_player_balls(interaction.user.id, balls, team=0)
        embed = battle.generate_embed()
        battle.message = await interaction.channel.send(embed=embed, view=BattleSelectView(battle))
        await interaction.response.send_message(f"✅ Battle {battle_id} created in **{mode}** mode!", ephemeral=True)
        asyncio.create_task(battle.auto_battle())

    # ---- ADD ----
    @battle.command(name="add", description="Add a ball to your active deck")
    async def add_ball(self, interaction: discord.Interaction, battle_id: int, ball_id: int):
        battle = self.active_battles.get(battle_id)
        if not battle or battle.started:
            await interaction.response.send_message("❌ Cannot add balls right now.", ephemeral=True)
            return
        player_obj = await Player.get(discord_id=interaction.user.id)
        ball = await Ball.get(id=ball_id, owner=player_obj)
        if not ball:
            await interaction.response.send_message("❌ Ball not found.", ephemeral=True)
            return
        success = battle.add_player_balls(interaction.user.id, [ball])
        if success:
            await interaction.response.send_message(f"✅ Added {ball.name} to Battle {battle_id}.", ephemeral=True)
            await battle.update_message()
        else:
            await interaction.response.send_message("❌ Could not add ball.", ephemeral=True)

    # ---- REMOVE ----
    @battle.command(name="remove", description="Remove a ball from your deck")
    async def remove_ball(self, interaction: discord.Interaction, battle_id: int, ball_id: int):
        battle = self.active_battles.get(battle_id)
        if not battle or battle.started:
            await interaction.response.send_message("❌ Cannot remove balls right now.", ephemeral=True)
            return
        success = battle.remove_player_ball(interaction.user.id, ball_id)
        if success:
            await interaction.response.send_message(f"✅ Removed ball {ball_id} from Battle {battle_id}.", ephemeral=True)
            await battle.update_message()
        else:
            await interaction.response.send_message("❌ Ball not found in deck.", ephemeral=True)

    # ---- BULK ----
    @battle.command(name="bulk", description="Bulk add or remove balls")
    async def bulk_manage(self, interaction: discord.Interaction, battle_id: int, ball_ids: str, action: str):
        battle = self.active_battles.get(battle_id)
        if not battle or battle.started:
            await interaction.response.send_message("❌ Cannot bulk edit right now.", ephemeral=True)
            return
        if battle.multiplayer:
            await interaction.response.send_message("❌ Bulk not allowed in multiplayer battles.", ephemeral=True)
            return

        ids = [int(x) for x in ball_ids.split(",")]
        player_obj = await Player.get(discord_id=interaction.user.id)

        if action.lower() == "add":
            balls = await Ball.filter(id__in=ids, owner=player_obj)
            success = battle.add_player_balls(interaction.user.id, balls, bulk=True)
            if success:
                await interaction.response.send_message(f"✅ Bulk added {len(balls)} balls to Battle {battle_id}.", ephemeral=True)
                await battle.update_message()
            else:
                await interaction.response.send_message("❌ Failed to bulk add balls.", ephemeral=True)

        elif action.lower() == "remove":
            removed = 0
            for bid in ids:
                if battle.remove_player_ball(interaction.user.id, bid):
                    removed += 1
            await interaction.response.send_message(f"✅ Bulk removed {removed} balls from Battle {battle_id}.", ephemeral=True)
            await battle.update_message()

        else:
            await interaction.response.send_message("❌ Invalid action. Use 'add' or 'remove'.", ephemeral=True)

# ----------------------
# Setup
# ----------------------
async def setup(bot):
    await bot.add_cog(FullBattleSystemCog(bot))
