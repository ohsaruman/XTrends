import asyncio
import os
import traceback

import discord
from discord import app_commands
from dotenv import load_dotenv

from main import build_report, to_chunks

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
_busy = asyncio.Lock()


@tree.command(name="trends", description="政治・経済・AI・ゲームのXトレンドを解説")
async def trends(interaction: discord.Interaction):
    if _busy.locked():
        await interaction.response.send_message(
            "ただいま別の取得を実行中です。完了してからもう一度実行してください。",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    async with _busy:
        try:
            text = await asyncio.to_thread(build_report)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"取得に失敗しました: {type(e).__name__}: {e}")
            return

        chunks = to_chunks(text)
        if not chunks:
            await interaction.followup.send("トレンドを取得できませんでした")
            return
        for chunk in chunks:
            await interaction.followup.send(chunk)


async def _sync_commands(guild: discord.abc.Snowflake | None = None):
    if guild is not None:
        tree.copy_global_to(guild=guild)
        return await tree.sync(guild=guild)
    return await tree.sync()


@client.event
async def on_ready():
    guilds = list(client.guilds)
    print(f"ready as {client.user} / guilds={len(guilds)}", flush=True)
    for g in guilds:
        print(f"  guild {g.id} {g.name}", flush=True)

    target = None
    if GUILD_ID:
        gid = int(GUILD_ID)
        target = client.get_guild(gid) or next((g for g in guilds if g.id == gid), None)

    try:
        synced = await _sync_commands(target)
        where = "guild" if target is not None else "global"
        print(f"{where} synced {len(synced)} command(s): {[c.name for c in synced]}", flush=True)
    except Exception:
        traceback.print_exc()
        synced = await _sync_commands()
        print(f"global synced {len(synced)} command(s): {[c.name for c in synced]}", flush=True)

    if not guilds:
        print(
            "invite: https://discord.com/oauth2/authorize?client_id="
            f"{client.user.id}&permissions=3072&scope=bot%20applications.commands",
            flush=True,
        )


@client.event
async def on_guild_join(guild: discord.Guild):
    print(f"joined guild {guild.id} {guild.name}", flush=True)
    try:
        synced = await _sync_commands(guild)
        print(f"guild synced {len(synced)} command(s): {[c.name for c in synced]}", flush=True)
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN がありません。.env に Discord Bot のトークンを設定してください。"
        )
    client.run(TOKEN)
