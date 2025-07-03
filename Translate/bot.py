import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import random
import os
import json
import datetime

# --- Load Config ---
with open("config.json", "r") as f:
    config = json.load(f)

TOKEN = config.get("token")
COMMAND_PREFIX = config.get("prefix", "!")  # Not used anymore, but kept for compatibility
GUILD_ID = config.get("guild_id")
TICKET_CHANNEL_ID = config.get("ticket_channel_id")
STAFF_ROLE_ID = config.get("staff_role_id")
ROLE_CHANNEL_ID = config.get("role_channel_id")
TARGET_CHANNEL_ID = config.get("target_channel_id")
ALLOWED_LINK_CHANNELS = set(config.get("allowed_link_channels", []))
EMOJI_TO_ROLE = config.get("emoji_to_role", {})

# --- Intents and Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# --- Globals ---
open_tickets = {}  # user_id : channel_id
ticket_message_id = None
role_message_id = None

# Stats tracking variables
stats = {
    "passed_verification": 0,
    "failed_verification": 0,
    "joined": 0,
    "left": 0,
    "banned": 0,
    "inactive": 0,
}

last_message_times = {}
INACTIVITY_DAYS = 30

# --- Helper Functions to Save/Load IDs ---
def save_message_id(filename, message_id):
    with open(filename, "w") as f:
        json.dump({"message_id": message_id}, f)

def load_message_id(filename):
    if not os.path.exists(filename):
        return None
    try:
        with open(filename, "r") as f:
            data = json.load(f)
            return data.get("message_id")
    except json.JSONDecodeError:
        return None

def generate_math_question():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    question = f"{a} + {b}"
    return question, a + b

# --- Slash Command: /stats ---
@bot.tree.command(name="stats", description="Show server statistics (staff only)")
async def stats_command(interaction: discord.Interaction):
    guild = interaction.guild
    staff_role = guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles:
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    inactive_count = 0
    for member in guild.members:
        if member.bot:
            continue
        last_msg_time = last_message_times.get(member.id)
        if not last_msg_time:
            inactive_count += 1
        else:
            delta = now - last_msg_time
            if delta.days >= INACTIVITY_DAYS:
                inactive_count += 1
    stats["inactive"] = inactive_count

    embed = discord.Embed(title="Server Statistics", color=0x00FF00, timestamp=now)
    embed.add_field(name="Users Passed Verification", value=str(stats["passed_verification"]), inline=False)
    embed.add_field(name="Users Failed Verification", value=str(stats["failed_verification"]), inline=False)
    embed.add_field(name="Users Joined", value=str(stats["joined"]), inline=False)
    embed.add_field(name="Users Left", value=str(stats["left"]), inline=False)
    embed.add_field(name="Users Banned", value=str(stats["banned"]), inline=False)
    embed.add_field(name=f"Inactive Users (> {INACTIVITY_DAYS} days)", value=str(stats["inactive"]), inline=False)
    embed.set_footer(text=f"Requested by {interaction.user}", icon_url=interaction.user.display_avatar.url)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- Slash Command: /clear ---
@bot.tree.command(name="clear", description="Delete messages from a user in the current channel within a time range (staff only)")
@app_commands.describe(
    user="The user whose messages to delete",
    from_date="Start date (YYYY-MM-DD) - optional",
    to_date="End date (YYYY-MM-DD) - optional"
)
async def clear_command(interaction: discord.Interaction, user: discord.User, from_date: str = None, to_date: str = None):
    guild = interaction.guild
    staff_role = guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles:
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    channel = interaction.channel

    # Parse dates
    try:
        from_dt = datetime.datetime.strptime(from_date, "%Y-%m-%d") if from_date else None
    except ValueError:
        await interaction.response.send_message("❌ Invalid 'from' date format. Use YYYY-MM-DD.", ephemeral=True)
        return
    try:
        to_dt = datetime.datetime.strptime(to_date, "%Y-%m-%d") if to_date else None
        if to_dt is not None:
            to_dt = to_dt + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
    except ValueError:
        await interaction.response.send_message("❌ Invalid 'to' date format. Use YYYY-MM-DD.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    def in_range(m):
        if m.author.id != user.id:
            return False
        created = m.created_at.replace(tzinfo=None)
        if from_dt and created < from_dt:
            return False
        if to_dt and created > to_dt:
            return False
        return True

    deleted_count = 0
    try:
        async for msg in channel.history(limit=None, oldest_first=True):
            if in_range(msg):
                try:
                    await msg.delete()
                    deleted_count += 1
                    await asyncio.sleep(0.2)
                except Exception:
                    pass
    except Exception as e:
        await interaction.followup.send(f"❌ Error while deleting messages: {e}", ephemeral=True)
        return

    await interaction.followup.send(f"✅ Deleted {deleted_count} messages from {user.mention} in this channel.", ephemeral=True)

# --- Slash Command: /close ---
@bot.tree.command(name="close", description="Close the current ticket (ticket channels only)")
async def close_command(interaction: discord.Interaction):
    channel = interaction.channel
    if channel.id not in open_tickets.values():
        await interaction.response.send_message("❌ This command can only be used in ticket channels.", ephemeral=True)
        return

    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user}")
        # Remove ticket from open_tickets
        user_id_to_remove = None
        for user_id, ch_id in open_tickets.items():
            if ch_id == channel.id:
                user_id_to_remove = user_id
                break
        if user_id_to_remove:
            del open_tickets[user_id_to_remove]

        await interaction.response.send_message("✅ Ticket closed and channel deleted.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to close ticket: {e}", ephemeral=True)

# --- Slash Command: /ban ---
@bot.tree.command(name="ban", description="Ban a user from the server (staff only)")
@app_commands.describe(user="User to ban", reason="Reason for ban (optional)")
async def ban_command(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ You don't have permission to ban members.", ephemeral=True)
        return

    if user == interaction.user:
        await interaction.response.send_message("❌ You cannot ban yourself.", ephemeral=True)
        return

    if user.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message("❌ You cannot ban a member with an equal or higher role.", ephemeral=True)
        return

    try:
        await user.ban(reason=reason)
        stats["banned"] += 1
        await interaction.response.send_message(f"✅ {user} has been banned. Reason: {reason or 'No reason provided'}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to ban user: {e}", ephemeral=True)

# --- Slash Command: /reactions ---
@bot.tree.command(name="reactions", description="Add reactions to a message (staff only)")
@app_commands.describe(
    channel="Channel where the message is",
    message_id="ID of the message to react to"
)
async def reactions_command(interaction: discord.Interaction, channel: discord.TextChannel, message_id: int):
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    if staff_role not in interaction.user.roles:
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        await interaction.response.send_message("❌ Could not find the message.", ephemeral=True)
        return

    try:
        await message.clear_reactions()
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await interaction.response.send_message("✅ Reactions added.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to add reactions: {e}", ephemeral=True)

# --- Event listeners to update stats ---
@bot.event
async def on_member_join(member):
    stats["joined"] += 1
    try:
        question, correct_answer = generate_math_question()
        dm_channel = await member.create_dm()
        await dm_channel.send(
            f"Witaj na {member.guild.name}! Proszę rozwiąż zadanie matematyczne, żebyśmy wiedzieli, że jesteś człowiekiem.\n"
            f"Napisz sam wynik:\n{question}"
        )

        def check(m):
            return m.author == member and m.channel == dm_channel

        try:
            msg = await bot.wait_for('message', check=check, timeout=120)
        except asyncio.TimeoutError:
            await dm_channel.send("Nie odpowiedziałeś na czas. Spróbuj dołączyć ponownie i rozwiązać zadanie.")
            await member.kick(reason="Weryfikacja nieudana: timeout")
            stats["failed_verification"] += 1
            return

        try:
            user_answer = int(msg.content.strip())
        except ValueError:
            await dm_channel.send("Niepoprawna odpowiedź. Spróbuj dołączyć ponownie.")
            await member.kick(reason="Weryfikacja nieudana: zła odpowiedź")
            stats["failed_verification"] += 1
            return

        if user_answer == correct_answer:
            await dm_channel.send("Weryfikacja zakończona sukcesem. Witamy na serwerze!")
            stats["passed_verification"] += 1
        else:
            await dm_channel.send("Niepoprawna odpowiedź. Spróbuj dołączyć ponownie.")
            await member.kick(reason="Weryfikacja nieudana: zła odpowiedź")
            stats["failed_verification"] += 1

    except Exception as e:
        print(f"Error verifying member {member}: {e}")

@bot.event
async def on_member_remove(member):
    stats["left"] += 1

@bot.event
async def on_member_ban(guild, user):
    stats["banned"] += 1

@bot.event
async def on_member_unban(guild, user):
    stats["banned"] = max(0, stats["banned"] - 1)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    last_message_times[message.author.id] = datetime.datetime.utcnow()

    is_target = False
    if message.channel.id == TARGET_CHANNEL_ID:
        is_target = True
    elif hasattr(message.channel, "category") and message.channel.category:
        if message.channel.category.id == TARGET_CHANNEL_ID:
            is_target = True

    if is_target:
        try:
            await message.clear_reactions()
            await message.add_reaction("👍")
            await message.add_reaction("👎")
        except Exception as e:
            print(f"Reaction error: {e}")

    if message.channel.id not in ALLOWED_LINK_CHANNELS:
        if "http://" in message.content or "https://" in message.content:
            try:
                await message.delete()
                await message.author.send(
                    f"⚠️ Twoja wiadomość z linkiem została usunięta z {message.channel.mention}."
                )
            except Exception:
                pass

    await bot.process_commands(message)

# --- On Ready: sync slash commands ---
@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user}!")

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("❌ Nie znaleziono serwera!")
        return

    try:
        await bot.tree.sync(guild=guild)
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

    await setup_ticket_message()
    await setup_role_message()

    print("✅ Bot jest gotowy.")

# --- Placeholder setup functions (implement as needed) ---
async def setup_ticket_message():
    pass

async def setup_role_message():
    pass

# --- Run the bot ---
bot.run(TOKEN)
