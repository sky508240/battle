import discord
from discord import app_commands, ui
from discord.ext import commands
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import random
import io

from ballsdex.core.models import BallInstance, Player
from ballsdex.settings import settings
from ballsdex.packages.battle.xe_battle_lib import BattleBall, BattleInstance, gen_battle

# -------------------------
# Guild Battle container
# -------------------------
@dataclass
class GuildBattle:
    interaction: discord.Interaction
    author: discord.Member
    opponent: discord.Member
    author_ready: bool = False
    opponent_ready: bool = False
    battle: BattleInstance = field(default_factory=BattleInstance)

battles: List[GuildBattle] = []

MAX_BALLS = 50

def gen_deck(balls: List[BattleBall]) -> str:
    if not balls:
        return "Empty"
    deck = "\n".join(f"- {b.emoji} {b.name} (HP: {b.health} | DMG: {b.attack})" for b in balls)
    return deck[:1024] + ("\n<truncated>" if len(deck) > 1024 else "")

def update_embed(author_balls, opponent_balls, author, opponent, author_ready, opponent_ready):
    embed = discord.Embed(
        title=f"{settings.plural_collectible_name.title()} Battle Plan",
        description=f"Use /battle add/remove to edit your deck. Click Ready to start.",
        color=discord.Color.blurple()
    )
    embed.add_field(name=f"{'✔' if author_ready else ''} {author}'s deck:", value=gen_deck(author_balls), inline=True)
    embed.add_field(name=f"{'✔' if opponent_ready else ''} {opponent}'s deck:", value=gen_deck(opponent_balls), inline=True)
    return embed

def create_disabled_buttons() -> discord.ui.View:
    view = ui.View()
    view.add_item(ui.Button(style=discord.ButtonStyle.success, emoji="✔", label="Ready", disabled=True))
    view.add_item(ui.Button(style=discord.ButtonStyle.danger, emoji="✖", label="Cancel", disabled=True))
    return view

def fetch_battle(user: discord.User) -> Optional[GuildBattle]:
    for b in battles:
        if user in (b.author, b.opponent):
            return b
    return None

# -------------------------
# Full Battle Cog
# -------------------------
class FullBattleSystemCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -------------------------
    # /battle start
    # -------------------------
    @app_commands.command()
    async def battle(self, interaction: discord.Interaction, opponent: discord.Member):
        if opponent.bot or opponent == interaction.user:
            await interaction.response.send_message("Invalid opponent.", ephemeral=True)
            return
        if fetch_battle(interaction.user) or fetch_battle(opponent):
            await interaction.response.send_message("Either you or opponent is already in a battle.", ephemeral=True)
            return

        gb = GuildBattle(interaction, interaction.user, opponent)
        battles.append(gb)
        embed = update_embed([], [], interaction.user.name, opponent.name, False, False)

        start_btn = ui.Button(style=discord.ButtonStyle.success, emoji="✔", label="Ready")
        cancel_btn = ui.Button(style=discord.ButtonStyle.danger, emoji="✖", label="Cancel")
        view = ui.View(timeout=None)
        view.add_item(start_btn)
        view.add_item(cancel_btn)

        start_btn.callback = lambda i: self.start_battle(i)
        cancel_btn.callback = lambda i: self.cancel_battle(i)

        await interaction.response.send_message(f"{opponent.mention}, {interaction.user.name} challenged you!", embed=embed, view=view)

    # -------------------------
    # Add / Remove Balls
    # -------------------------
    async def add_balls(self, interaction: discord.Interaction, balls: List[BallInstance]):
        gb = fetch_battle(interaction.user)
        if not gb:
            return 0

        user_balls = gb.battle.p1_balls if interaction.user == gb.author else gb.battle.p2_balls
        added = 0
        for b in balls:
            bb = BattleBall(
                b.countryball.country,
                interaction.user.name,
                b.health,
                b.attack,
                self.bot.get_emoji(b.countryball.emoji_id)
            )
            if len(user_balls) >= MAX_BALLS:
                break
            if bb not in user_balls:
                user_balls.append(bb)
                added += 1

        await gb.interaction.edit_original_response(embed=update_embed(
            gb.battle.p1_balls, gb.battle.p2_balls, gb.author.name, gb.opponent.name, gb.author_ready, gb.opponent_ready
        ))
        return added

    async def remove_balls(self, interaction: discord.Interaction, balls: List[BallInstance]):
        gb = fetch_battle(interaction.user)
        if not gb:
            return 0

        user_balls = gb.battle.p1_balls if interaction.user == gb.author else gb.battle.p2_balls
        removed = 0
        for b in balls:
            bb = BattleBall(
                b.countryball.country,
                interaction.user.name,
                b.health,
                b.attack,
                self.bot.get_emoji(b.countryball.emoji_id)
            )
            if bb in user_balls:
                user_balls.remove(bb)
                removed += 1

        await gb.interaction.edit_original_response(embed=update_embed(
            gb.battle.p1_balls, gb.battle.p2_balls, gb.author.name, gb.opponent.name, gb.author_ready, gb.opponent_ready
        ))
        return removed

    # -------------------------
    # /battle add single
    # -------------------------
    @app_commands.command()
    async def add(self, interaction: discord.Interaction, ball: BallInstance):
        count = await self.add_balls(interaction, [ball])
        if count == 0:
            await interaction.response.send_message("Ball already in deck or max reached.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Added {ball.countryball.country} to deck.", ephemeral=True)

    # -------------------------
    # /battle remove single
    # -------------------------
    @app_commands.command()
    async def remove(self, interaction: discord.Interaction, ball: BallInstance):
        count = await self.remove_balls(interaction, [ball])
        if count == 0:
            await interaction.response.send_message("Ball not in deck.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Removed {ball.countryball.country} from deck.", ephemeral=True)

    # -------------------------
    # /battle bulk add
    # -------------------------
    @app_commands.command()
    async def bulk_add(self, interaction: discord.Interaction):
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        balls = await BallInstance.filter(player=player)
        added = await self.add_balls(interaction, balls)
        await interaction.response.send_message(f"Added {added} balls to deck.", ephemeral=True)

    # -------------------------
    # /battle bulk remove
    # -------------------------
    @app_commands.command()
    async def bulk_remove(self, interaction: discord.Interaction):
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        balls = await BallInstance.filter(player=player)
        removed = await self.remove_balls(interaction, balls)
        await interaction.response.send_message(f"Removed {removed} balls from deck.", ephemeral=True)

# -------------------------
# Setup
# -------------------------
async def setup(bot):
    await bot.add_cog(FullBattleSystemCog(bot))
