import discord
import yt_dlp
import os
import asyncio
from discord.ext import commands
from discord import FFmpegOpusAudio
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
if TOKEN is None:
  raise RuntimeError("DISCORD_TOKEN environment variable not set. Create a .env file with DISCORD_TOKEN=<your token>.")

# Set up the bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Create voice client dictionary and song queue (per server)
voice_clients = {}
song_queues = {}

# Set up yt_dlp options for best audio format
yt_dl_options = {
    'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
    'noplaylist': True
}
ytdl = yt_dlp.YoutubeDL(yt_dl_options)

# Set up ffmpeg options for audio filtering
ffmpeg_options = {
    'before_options':
    '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -filter:a "loudnorm=I=-16:TP=-1.5:LRA=12" -b:a 320k'
}


# Function to handle bot login with retry logic
async def start_bot():
  retries = 5
  delay = 10

  for attempt in range(retries):
    try:
      await bot.start(TOKEN)
      break
    except discord.errors.HTTPException as e:
      if e.status == 429:  # HTTP 429 is the rate limit error
        print(f"Rate limit hit. Retrying in {delay} seconds...")
        await asyncio.sleep(delay)
        delay *= 2
      else:
        raise


# Handle bot startup and log login info


@bot.event
async def on_ready():
  print(f'Logged in as {bot.user}')

  try:
    # 🛠 Debug: Print registered commands before syncing
    registered_commands = [cmd.name for cmd in bot.tree.get_commands()]
    print(f"Registered commands before sync: {registered_commands}")

    # 🔄 Step 1: Clear and re-sync global commands
    await bot.tree.sync()
    print("✅ All commands forced to sync globally.")

    # 🔄 Step 2: Manually sync commands per server (forces immediate update)
    for guild in bot.guilds:
      await bot.tree.sync(guild=guild)
      print(f"✅ Synced commands for {guild.name} ({guild.id})")

  except Exception as e:
    print(f"⚠️ Error syncing commands: {e}")


# 🎵 Function to play the next song in queue
async def play_next_song(guild_id, voice_client, channel):
  if guild_id in song_queues and song_queues[
      guild_id]:  # Check if queue is not empty
    next_song = song_queues[guild_id].pop(0)
    song_url = next_song['url']
    title = next_song['title']

    try:
      # ✅ Send a clean Discord message
      embed = discord.Embed(title=f"🎵 Now Playing: {title}",
                            description=f"[Listen on YouTube]({song_url})",
                            color=discord.Color.blue())
      await channel.send(embed=embed)

      # ✅ Play the song and set an async `after` callback
      player = FFmpegOpusAudio(song_url, **ffmpeg_options)
      voice_client.play(
          player,
          after=lambda e: asyncio.run_coroutine_threadsafe(
              handle_next_song(guild_id, voice_client, channel), bot.loop))

    except Exception as e:
      print(f"⚠️ Error playing audio: {e}")
      await channel.send(f"⚠️ Error playing **{title}**")

  else:
    await channel.send("🎶 The queue is currently empty.")


async def handle_next_song(guild_id, voice_client, channel):
  await asyncio.sleep(1)  # ✅ Prevent race conditions
  if not voice_client.is_playing(
  ) and guild_id in song_queues and song_queues[guild_id]:
    await play_next_song(guild_id, voice_client, channel)


# 🔍 Slash command to search YouTube and play the first result
@bot.tree.command(
    name="search",
    description="Search YouTube for a song and play the first result")
async def search_song(interaction: discord.Interaction, song_title: str):
  await interaction.response.defer()
  search_url = f"ytsearch:{song_title}"
  loop = asyncio.get_event_loop()
  data = await loop.run_in_executor(
      None, lambda: ytdl.extract_info(search_url, download=False))

  if 'entries' in data and len(data['entries']) > 0:
    song = data['entries'][0]
    await add_song_to_queue(interaction, song['url'], song['title'])
  else:
    await interaction.followup.send(f"No results found for '{song_title}'.")


# ▶️ Slash command to play a song from a URL
@bot.tree.command(name="play",
                  description="Play a song from a direct YouTube link")
async def play_song(interaction: discord.Interaction, song_url: str):
  await interaction.response.defer()
  loop = asyncio.get_event_loop()
  data = await loop.run_in_executor(
      None, lambda: ytdl.extract_info(song_url, download=False))

  if 'formats' in data:
    audio_url = next(
        (fmt['url'] for fmt in data['formats']
         if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none'), None)
    if audio_url:
      await add_song_to_queue(interaction, audio_url, data['title'])
    else:
      await interaction.followup.send(
          "⚠️ No valid audio format found for this video.")
  else:
    await interaction.followup.send(
        "⚠️ Could not extract audio from the provided URL.")


# ➕ Function to add a song to the queue
async def add_song_to_queue(interaction, song_url, title):
  guild_id = interaction.guild.id
  if guild_id not in voice_clients or not voice_clients[guild_id].is_connected(
  ):
    if interaction.user.voice:
      voice_channel = interaction.user.voice.channel
      voice_client = await voice_channel.connect()
      voice_clients[guild_id] = voice_client
    else:
      await interaction.followup.send(
          "⚠️ You need to join a voice channel first.")
      return
  else:
    voice_client = voice_clients[guild_id]

  if guild_id not in song_queues:
    song_queues[guild_id] = []

  song_queues[guild_id].append({'url': song_url, 'title': title})

  if not voice_client.is_playing() and not voice_client.is_paused():
    await play_next_song(guild_id, voice_client, interaction.channel)

  embed = discord.Embed(title=f"🎵 Added to Queue: {title}",
                        description="Playing from YouTube",
                        color=discord.Color.blue())
  await interaction.followup.send(embed=embed)


# ⏯️ Slash command to pause the current song
@bot.tree.command(name="pause", description="Pause the current song")
async def pause_song(interaction: discord.Interaction):
  voice_client = voice_clients.get(interaction.guild.id)
  if voice_client and voice_client.is_playing():
    voice_client.pause()
    await interaction.response.send_message("⏸️ Song paused.")
  else:
    await interaction.response.send_message("⚠️ No song is currently playing.")


# ▶️ Slash command to resume a paused song
@bot.tree.command(name="resume", description="Resume the paused song")
async def resume_song(interaction: discord.Interaction):
  voice_client = voice_clients.get(interaction.guild.id)
  if voice_client and voice_client.is_paused():
    voice_client.resume()
    await interaction.response.send_message("▶️ Song resumed.")
  else:
    await interaction.response.send_message("⚠️ No song is currently paused.")


@bot.tree.command(name="skip", description="Skip the current song")
async def skip_song(interaction: discord.Interaction):
  voice_client = voice_clients.get(interaction.guild.id)
  if voice_client and voice_client.is_playing():
    voice_client.stop(
    )  # ✅ Stop the current song (next song will be handled by `after`)
    await interaction.response.send_message("⏩ Skipped the current song.")
  else:
    await interaction.response.send_message("⚠️ No song is currently playing.")


# 🔌 Slash command to disconnect the bot from voice
@bot.tree.command(name="leave",
                  description="Disconnect the bot from the voice channel")
async def leave_voice(interaction: discord.Interaction):
  voice_client = voice_clients.get(interaction.guild.id)
  if voice_client:
    await voice_client.disconnect()
    del voice_clients[interaction.guild.id]
    await interaction.response.send_message(
        "🔌 Disconnected from voice channel.")
  else:
    await interaction.response.send_message("⚠️ I'm not in a voice channel.")


# 📜 Slash command to show the current song queue
@bot.tree.command(name="queue", description="Show the current song queue")
async def show_queue(interaction: discord.Interaction):
  guild_id = interaction.guild.id
  voice_client = voice_clients.get(guild_id)

  if not voice_client or guild_id not in song_queues:
    await interaction.response.send_message(
        "⚠️ The bot is not connected to a voice channel or the queue is empty."
    )
    return

  if len(song_queues[guild_id]) == 0:
    await interaction.response.send_message("🎶 The queue is currently empty.")
    return

  queue_list = "\n".join([
      f"{index + 1}. {song['title']}"
      for index, song in enumerate(song_queues[guild_id])
  ])

  embed = discord.Embed(title="🎵 Current Queue",
                        description=queue_list,
                        color=discord.Color.blue())
  await interaction.response.send_message(embed=embed)


# Run the bot with retry logic
if __name__ == "__main__":
  asyncio.run(start_bot())
