import discord
import yt_dlp
import os
import asyncio
import time
from discord.ext import commands
from discord import FFmpegOpusAudio
from dotenv import load_dotenv
from discord.app_commands.transformers import Range
from discord import app_commands

load_dotenv()

TOKEN = os.getenv('discord_token')

# Set up the bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Create voice client dictionary and song queue (per server)
voice_clients = {}
song_queues = {}

# Set up yt_dlp options for best audio format
yt_dl_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,  # Keep playlists disabled by default
    'verbose': False,  # Disable verbose output in production - change to True for debugging
    'dump_single_json': False  # Don't enable full JSON dump as it breaks the download
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
      
    # Schedule periodic cleanup
    bot.loop.create_task(periodic_cleanup())

  except Exception as e:
    print(f"⚠️ Error syncing commands: {e}")


# Helper function to ensure the bot is connected to voice
async def ensure_voice_connection(interaction, user_voice_channel=None):
    """Ensure the bot is connected to a voice channel and return the voice client"""
    guild_id = interaction.guild.id
    
    # Check if already connected
    if guild_id in voice_clients and voice_clients[guild_id].is_connected():
        return voice_clients[guild_id]
        
    # Need to connect - check if user is in voice
    if user_voice_channel is None and interaction.user.voice:
        user_voice_channel = interaction.user.voice.channel
        
    # If we have a channel to join
    if user_voice_channel:
        voice_client = await user_voice_channel.connect()
        voice_clients[guild_id] = voice_client
        
        # Initialize queue if needed
        if guild_id not in song_queues:
            song_queues[guild_id] = []
            
        return voice_client
    else:
        return None


# Utility function to extract audio information
async def extract_audio_info(url, loop):
    """Extract audio information from a URL"""
    def _extract():
        try:
            info = ytdl.extract_info(url, download=False)
            
            # Check if this is a search result or playlist
            if 'entries' in info:
                # Get the first entry for searches
                entry = info['entries'][0]
                if os.getenv('DEBUG'):  # Only print in debug mode
                    print(f"Entry keys: {list(entry.keys())}")
                    # Print format information if available
                    if 'formats' in entry:
                        print_format_info(entry)
                return entry
            else:
                # Direct URL
                if os.getenv('DEBUG'):  # Only print in debug mode
                    print_format_info(info)
                return info
                
        except Exception as e:
            print(f"Error extracting info: {e}")
            return None
    
    return await loop.run_in_executor(None, _extract)

# 🎵 Function to play the next song in queue
async def play_next_song(guild_id, voice_client, channel, retry_count=0):
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
      try:
        player = FFmpegOpusAudio(song_url, **ffmpeg_options)
        voice_client.play(
            player,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                handle_next_song(guild_id, voice_client, channel, e), bot.loop))
      except Exception as audio_error:
        print(f"⚠️ Error creating audio player: {audio_error}")
        
        # Retry for transient errors (max 2 retries)
        if retry_count < 2:
          print(f"Retrying... Attempt {retry_count + 1}/2")
          # Put the song back at the front of the queue
          song_queues[guild_id].insert(0, next_song)
          await asyncio.sleep(2)  # Wait a bit before retrying
          await play_next_song(guild_id, voice_client, channel, retry_count + 1)
        else:
          await channel.send(f"⚠️ Failed to play **{title}** after multiple attempts. Skipping.")
          # Try to play the next song
          if guild_id in song_queues and song_queues[guild_id]:
            await play_next_song(guild_id, voice_client, channel)

    except Exception as e:
      print(f"⚠️ Error playing audio: {e}")
      await channel.send(f"⚠️ Error playing **{title}**")
      
      # Try next song if available
      if guild_id in song_queues and song_queues[guild_id]:
        await channel.send("Attempting to play next song...")
        await play_next_song(guild_id, voice_client, channel)

  else:
    await channel.send("🎶 The queue is currently empty.")


async def handle_next_song(guild_id, voice_client, channel, error=None):
  await asyncio.sleep(1)  # ✅ Prevent race conditions
  
  # Log any errors from the previous song
  if error:
    print(f"Error from previous song: {error}")
    
  if not voice_client.is_playing() and guild_id in song_queues and song_queues[guild_id]:
    await play_next_song(guild_id, voice_client, channel)


# Helper function to print format information
def print_format_info(info_dict):
    """Print detailed format information from yt-dlp results"""
    # Only run if debug mode is enabled
    if not os.getenv('DEBUG'):
        return
        
    # Check if info_dict is None
    if info_dict is None:
        print("No info dictionary provided to print_format_info")
        return
        
    # Print available info keys
    print(f"Info keys: {list(info_dict.keys())}")
    
    # Print all available formats
    if 'formats' in info_dict:
        print("\nALL AVAILABLE FORMATS:")
        for fmt in info_dict['formats']:
            print(f"Format ID: {fmt.get('format_id')} - Ext: {fmt.get('ext')} - "
                  f"Audio: {fmt.get('acodec')} - Video: {fmt.get('vcodec')} - "
                  f"ABR: {fmt.get('abr')} - TBR: {fmt.get('tbr')}")
    
    # Debug: Look for best format - what yt-dlp actually selected
    if 'requested_formats' in info_dict:
        print("\nSELECTED FORMATS:")
        for fmt in info_dict['requested_formats']:
            format_id = fmt.get('format_id', 'unknown')
            ext = fmt.get('ext', 'unknown')
            acodec = fmt.get('acodec', 'none')
            abr = fmt.get('abr', fmt.get('tbr', 'unknown'))
            
            print(f"Selected format: {format_id} ({ext})")
            print(f"Audio codec: {acodec}, Bitrate: {abr} kbps")
    elif 'format_id' in info_dict:
        # Single format selected
        print("\nSINGLE SELECTED FORMAT:")
        print(f"Format ID: {info_dict.get('format_id')} - Ext: {info_dict.get('ext')}")
        print(f"Audio codec: {info_dict.get('acodec')}, Bitrate: {info_dict.get('abr', info_dict.get('tbr', 'unknown'))} kbps")
    else:
        print("No format information available in the info")
        # If we're here but have a URL, we probably have a direct stream URL
        if 'url' in info_dict:
            print(f"Direct URL found: {info_dict['url'][:50]}... (truncated)")
            if 'ext' in info_dict:
                print(f"Extension: {info_dict.get('ext')}")


# 🔍 Slash command to search YouTube and play the first result
@bot.tree.command(
    name="search",
    description="Search YouTube for a song and play the first result")
async def search_song(interaction: discord.Interaction, song_title: str):
  await interaction.response.defer()
  search_url = f"ytsearch:{song_title}"
  loop = asyncio.get_event_loop()
  
  try:
    # Use the utility function to extract audio info
    data = await extract_audio_info(search_url, loop)
    
    # Process and queue the song
    if data and 'url' in data:
      await add_song_to_queue(interaction, data['url'], data['title'])
    else:
      await interaction.followup.send(f"No results found for '{song_title}'.")
      
  except Exception as e:
    print(f"Error in search_song: {e}")
    await interaction.followup.send(f"Error searching for '{song_title}': {str(e)}")


# ▶️ Slash command to play a song from a URL
@bot.tree.command(name="play",
                  description="Play a song from a direct YouTube link")
async def play_song(interaction: discord.Interaction, song_url: str):
  await interaction.response.defer()
  loop = asyncio.get_event_loop()
  
  try:
    # Use the utility function to extract audio info
    data = await extract_audio_info(song_url, loop)
    
    # Process and queue the song
    if data and 'url' in data:
      await add_song_to_queue(interaction, data['url'], data['title'])
    else:
      await interaction.followup.send("⚠️ Could not extract audio from the provided URL.")
      
  except Exception as e:
    print(f"Error in play_song: {e}")
    await interaction.followup.send(f"Error playing '{song_url}': {str(e)}")


# ➕ Function to add a song to the queue
async def add_song_to_queue(interaction, song_url, title):
  guild_id = interaction.guild.id
  
  # Get or create a voice client
  voice_client = await ensure_voice_connection(interaction)
  if not voice_client:
    await interaction.followup.send("⚠️ You need to join a voice channel first.")
    return

  # Initialize queue if needed
  if guild_id not in song_queues:
    song_queues[guild_id] = []

  # Add song to queue
  song_queues[guild_id].append({
    'url': song_url, 
    'title': title
  })

  # Start playing if not already playing
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
    # Clean up server resources
    guild_id = interaction.guild.id
    if guild_id in voice_clients:
        del voice_clients[guild_id]
    if guild_id in song_queues:
        del song_queues[guild_id]
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

  # Format the queue with just song titles
  queue_list = "\n".join([
      f"{index + 1}. {song['title']}"
      for index, song in enumerate(song_queues[guild_id])
  ])

  embed = discord.Embed(title="🎵 Current Queue",
                        description=queue_list,
                        color=discord.Color.blue())
  await interaction.response.send_message(embed=embed)


# Helper function to format time durations
def format_time_duration(seconds):
    """Format seconds into human readable time"""
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"


# 🗑️ Slash command to clear the song queue
@bot.tree.command(name="clear", description="Clear the current song queue")
async def clear_queue(interaction: discord.Interaction):
  guild_id = interaction.guild.id
  
  if guild_id not in song_queues or len(song_queues[guild_id]) == 0:
    await interaction.response.send_message("⚠️ The queue is already empty.")
    return
  
  # Store the queue length for the message
  queue_length = len(song_queues[guild_id])
  
  # Clear the queue
  song_queues[guild_id] = []
  
  await interaction.response.send_message(f"🗑️ Cleared {queue_length} songs from the queue.")


# 🗑️ Slash command to remove songs from the queue
@bot.tree.command(name="remove", description="Remove specific songs from the queue")
@app_commands.describe(
    index="Index of the song to remove (starting from 1)",
    length="Number of songs to remove (default: 1)"
)
async def remove_from_queue(
    interaction: discord.Interaction, 
    index: Range[int, 1, 1000],
    length: Range[int, 1, 100] = 1
):
    guild_id = interaction.guild.id
    
    # Check if queue exists and is not empty
    if guild_id not in song_queues or len(song_queues[guild_id]) == 0:
        await interaction.response.send_message("⚠️ The queue is empty.")
        return
    
    # Convert from 1-based (user) to 0-based (internal) indexing
    zero_index = index - 1
    queue_length = len(song_queues[guild_id])
    
    # Check if index is valid
    if zero_index >= queue_length:
        await interaction.response.send_message(f"⚠️ Invalid index: {index}. The queue only has {queue_length} songs.")
        return
    
    # Adjust length if it would exceed the queue bounds
    if zero_index + length > queue_length:
        actual_length = queue_length - zero_index
    else:
        actual_length = length
    
    # Get titles of songs being removed for the message
    removed_songs = []
    for i in range(zero_index, zero_index + actual_length):
        removed_songs.append(song_queues[guild_id][i]['title'])
    
    # Remove songs from the queue
    del song_queues[guild_id][zero_index:zero_index + actual_length]
    
    # Prepare response message
    if actual_length == 1:
        response = f"🗑️ Removed song #{index}: {removed_songs[0]}"
    else:
        response = f"🗑️ Removed {actual_length} songs from positions {index}-{index+actual_length-1}:\n"
        for i, title in enumerate(removed_songs):
            response += f"{index+i}. {title}\n"
            # Limit message length to avoid hitting Discord limits
            if i >= 9 and len(removed_songs) > 10:  # Show at most 10 songs
                remaining = len(removed_songs) - 10
                response += f"...and {remaining} more."
                break
    
    await interaction.response.send_message(response)


# 🎵 Slash command to add a YouTube playlist to the queue
@bot.tree.command(
    name="list",
    description="Add songs from a YouTube playlist to the queue")
@app_commands.describe(
    playlist_url="The URL of the YouTube playlist",
    limit="Maximum number of songs to add (optional, default: all songs)"
)
async def add_playlist(
    interaction: discord.Interaction, 
    playlist_url: str,
    limit: int = None
):
  await interaction.response.defer()
  loop = asyncio.get_event_loop()
  
  try:
    # Extract playlist info
    def extract_playlist_info():
      # Configure ytdl to extract playlist
      playlist_options = yt_dl_options.copy()
      playlist_options['noplaylist'] = False  # Enable playlist processing
      playlist_options['extract_flat'] = 'in_playlist'  # Don't extract individual videos yet
      playlist_ytdl = yt_dlp.YoutubeDL(playlist_options)
      
      # Get playlist info
      playlist_info = playlist_ytdl.extract_info(playlist_url, download=False)
      if os.getenv('DEBUG'):
          print(f"Playlist info keys: {list(playlist_info.keys())}")
      # Don't try to print format info for the playlist itself - it won't have any
      
      return playlist_info
    
    playlist_data = await loop.run_in_executor(None, extract_playlist_info)
    
    # Check if it's a valid playlist
    if not playlist_data or 'entries' not in playlist_data:
      await interaction.followup.send("⚠️ Could not extract playlist information or invalid URL.")
      return
    
    entries = list(playlist_data['entries'])
    total_songs = len(entries)
    
    if total_songs == 0:
      await interaction.followup.send("⚠️ No songs found in the playlist.")
      return
    
    # Apply limit if specified
    if limit and limit > 0 and limit < total_songs:
      entries = entries[:limit]
      await interaction.followup.send(f"🎵 Found {total_songs} songs in playlist. Adding first {limit} songs to queue...")
    else:
      await interaction.followup.send(f"🎵 Found {total_songs} songs in playlist. Adding all songs to queue...")
    
    # Get guild information
    guild_id = interaction.guild.id
    
    # Get or create voice client
    voice_client = await ensure_voice_connection(interaction)
    if not voice_client:
      await interaction.followup.send("⚠️ You need to join a voice channel first.")
      return
    
    # Send initial progress message that we'll update
    progress_message = await interaction.followup.send("⏳ Progress: 0/" + str(len(entries)) + " songs added to the queue...")
    
    # Process songs in batches to avoid rate limiting
    # Adjust batch size based on playlist length
    batch_size = min(10, max(1, len(entries) // 10))  # Larger batches for larger playlists (max 10)
    added_songs = 0
    failed_songs = 0
    last_update = 0
    update_interval = max(5, len(entries) // 10)  # Update progress every ~10% or at least every 5 songs
    
    # Track start time to calculate ETA
    start_time = time.time()
    
    for i in range(0, len(entries), batch_size):
        # Process this batch of songs
        current_batch = entries[i:i+batch_size]
        batch_tasks = []
        
        # Create tasks for each song in the batch
        for entry in current_batch:
            if 'id' in entry:
                video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                task = extract_audio_info(video_url, loop)
                batch_tasks.append((task, entry))
        
        # Process all tasks in the batch concurrently
        for task, entry in batch_tasks:
            try:
                song_data = await task
                
                if song_data and 'url' in song_data:
                    # Add to queue with the actual audio URL
                    title = song_data.get('title', entry.get('title', f"Song #{added_songs+1}"))
                    
                    # Add song to queue with metadata
                    song_queues[guild_id].append({
                        'url': song_data['url'], 
                        'title': title
                    })
                    
                    added_songs += 1
                else:
                    print(f"Could not extract audio URL for {entry.get('title', 'Unknown song')}")
                    failed_songs += 1
            except Exception as e:
                print(f"Error processing playlist item: {e}")
                failed_songs += 1
            
            # Check if we need to update the progress after each song
            if added_songs - last_update >= update_interval:
                try:
                    # Calculate ETA if we've processed enough songs
                    eta_string = ""
                    if added_songs > update_interval:
                        elapsed = time.time() - start_time
                        songs_per_second = added_songs / elapsed
                        remaining_songs = len(entries) - added_songs
                        if songs_per_second > 0:
                            eta_seconds = remaining_songs / songs_per_second
                            eta = format_time_duration(eta_seconds)
                            eta_string = f" (ETA: {eta})"
                    
                    await progress_message.edit(
                        content=f"⏳ Progress: {added_songs}/{len(entries)} songs added to the queue...{eta_string}"
                    )
                    last_update = added_songs
                    if os.getenv('DEBUG'):
                        print(f"Updated progress message: {added_songs}/{len(entries)}")
                except Exception as e:
                    print(f"Error updating progress message: {e}")
        
        # Small delay between batches to avoid rate limiting
        if i + batch_size < len(entries):
            await asyncio.sleep(1)
    
    # Final progress update if not already at 100%
    if added_songs != last_update:
        try:
            await progress_message.edit(content=f"⏳ Progress: {added_songs}/{len(entries)} songs added to the queue...")
        except Exception as e:
            print(f"Error updating final progress message: {e}")
    
    # Calculate total processing time
    total_time = format_time_duration(time.time() - start_time)
    
    # Start playing if not already playing
    if not voice_client.is_playing() and not voice_client.is_paused() and added_songs > 0:
      await play_next_song(guild_id, voice_client, interaction.channel)
    
    # Send final summary
    if failed_songs > 0:
      await interaction.followup.send(f"✅ Added {added_songs} songs to the queue. {failed_songs} songs could not be added. (Took {total_time})")
    else:
      await interaction.followup.send(f"✅ Successfully added all {added_songs} songs to the queue! (Took {total_time})")
      
  except Exception as e:
    print(f"Error processing playlist: {e}")
    await interaction.followup.send(f"⚠️ Error processing playlist: {str(e)}")


# Clean up disconnected voice clients
async def cleanup_voice_clients():
    """Remove disconnected voice clients from the dictionary"""
    disconnected = []
    for guild_id, voice_client in voice_clients.items():
        if not voice_client.is_connected():
            disconnected.append(guild_id)
    
    for guild_id in disconnected:
        del voice_clients[guild_id]
        # Also clean up guild queues when removing voice client
        if guild_id in song_queues:
            del song_queues[guild_id]
        print(f"Cleaned up disconnected voice client for guild {guild_id}")


# Periodic cleanup task
async def periodic_cleanup():
    """Run cleanup tasks periodically"""
    # Time tracking for idle voice clients
    last_activity = {}
    MAX_IDLE_TIME = 3600  # Disconnect after 1 hour of inactivity
    
    while True:
        await asyncio.sleep(3600)  # Check every hour
        
        current_time = time.time()
        
        # Clean up disconnected voice clients and their resources
        await cleanup_voice_clients()
        
        # Clean up idle voice clients
        for guild_id, voice_client in list(voice_clients.items()):
            # Skip if not connected
            if not voice_client.is_connected():
                continue
                
            # Check if the voice client is idle (not playing)
            if not voice_client.is_playing():
                # First time we see it idle, record the time
                if guild_id not in last_activity:
                    last_activity[guild_id] = current_time
                    print(f"Voice client in guild {guild_id} is now idle")
                
                # Check if it's been idle for too long
                elif current_time - last_activity[guild_id] > MAX_IDLE_TIME:
                    print(f"Voice client in guild {guild_id} idle for {format_time_duration(current_time - last_activity[guild_id])} - disconnecting")
                    try:
                        await voice_client.disconnect()
                        del voice_clients[guild_id]
                        # Also clean up guild queues
                        if guild_id in song_queues:
                            del song_queues[guild_id]
                        del last_activity[guild_id]
                    except Exception as e:
                        print(f"Error disconnecting idle client in guild {guild_id}: {e}")
            else:
                # If playing, remove from idle tracking
                if guild_id in last_activity:
                    del last_activity[guild_id]
        
        # Print system status
        print(f"Periodic cleanup completed - Connected to {len(voice_clients)} guilds")


# Run the bot with retry logic
asyncio.run(start_bot())
