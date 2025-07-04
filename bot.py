import discord
from discord.ext import commands
import asyncio
import random
import os
import json

# --- Load Config ---
with open("config.json", "r") as f:
    config = json.load(f)

TOKEN = config["token"]
COMMAND_PREFIX = config.get("prefix", "!")
GUILD_ID = config["guild_id"]
TICKET_CHANNEL_ID = config["ticket_channel_id"]
STAFF_ROLE_ID = config["staff_role_id"]
ROLE_CHANNEL_ID = config["role_channel_id"]
TARGET_CHANNEL_ID = config["target_channel_id"]
ALLOWED_LINK_CHANNELS = set(config["allowed_link_channels"])
EMOJI_TO_ROLE = config["emoji_to_role"]

# --- Intents and Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# --- Globals ---
open_tickets = {}
role_message_id = None

# --- Ticket System ---

class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket_button")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        guild = interaction.guild

        if user_id in open_tickets:
            await interaction.response.send_message("You already have an open ticket!", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.get_role(STAFF_ROLE_ID): discord.PermissionOverwrite(read_messages=True, send_messages=True),
            bot.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            overwrites=overwrites,
            topic=f"Ticket for {interaction.user} (ID: {interaction.user.id})",
            reason="New support ticket created"
        )

        open_tickets[user_id] = ticket_channel.id

        await interaction.response.send_message(f"Your ticket has been created: {ticket_channel.mention}", ephemeral=True)
        await ticket_channel.send(f"Hello {interaction.user.mention}! A staff member will be with you shortly.\nTo close this ticket, type `!close`.")

@bot.command()
async def close(ctx):
    user_id = ctx.author.id
    channel = ctx.channel

    if channel.id not in open_tickets.values():
        await ctx.send("This command can only be used inside a ticket channel.")
        return

    owner_id = next((uid for uid, cid in open_tickets.items() if cid == channel.id), None)
    if owner_id is None:
        await ctx.send("Error: ticket owner not found.")
        return

    is_staff = any(role.id == STAFF_ROLE_ID for role in ctx.author.roles)
    if ctx.author.id != owner_id and not is_staff:
        await ctx.send("You don't have permission to close this ticket.")
        return

    open_tickets.pop(owner_id)
    await ctx.send("Closing ticket...")
    await channel.delete(reason=f"Ticket closed by {ctx.author}")

# --- On Ready ---

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}!")

    guild = bot.get_guild(GUILD_ID)
    ticket_channel = guild.get_channel(TICKET_CHANNEL_ID)
    if ticket_channel:
        async for msg in ticket_channel.history(limit=100):
            if msg.author == bot.user and msg.content.startswith("Click the button"):
                await msg.edit(view=TicketButton())
                break
        else:
            await ticket_channel.send("Click the button below to create a ticket!", view=TicketButton())
    else:
        print("❌ Ticket channel not found!")

    await setup_self_assign_roles()
    print("✅ Bot is ready.")

# --- Anti-Raid Math Challenge ---

def generate_math_question():
    a = random.randint(1, 20)
    b = random.randint(1, 20)
    op = random.choice(['+', '-'])
    question = f"What is {a} {op} {b}?"
    answer = a + b if op == '+' else a - b
    return question, answer

@bot.event
async def on_member_join(member):
    try:
        question, correct_answer = generate_math_question()
        dm_channel = await member.create_dm()
        await dm_channel.send(
            f"Welcome to {member.guild.name}! Please answer this to verify you're human:\n{question}"
        )

        def check(m):
            return m.author == member and m.channel == dm_channel

        try:
            msg = await bot.wait_for('message', check=check, timeout=120)
        except asyncio.TimeoutError:
            await dm_channel.send("You didn't respond in time. You will be kicked.")
            await member.kick(reason="Verification failed: timeout")
            return

        try:
            user_answer = int(msg.content.strip())
        except ValueError:
            await dm_channel.send("Invalid format. You will be kicked.")
            await member.kick(reason="Verification failed: invalid answer")
            return

        if user_answer == correct_answer:
            await dm_channel.send("✅ Verified successfully!")
        else:
            await dm_channel.send("❌ Incorrect. You will be kicked.")
            await member.kick(reason="Verification failed: wrong answer")

    except Exception as e:
        print(f"Error verifying member {member}: {e}")

# --- Self Assign Roles (Fixed) ---

async def setup_self_assign_roles():
    global role_message_id
    channel = bot.get_channel(ROLE_CHANNEL_ID)
    if channel is None:
        print("❌ Role channel not found!")
        return

    # Load the message ID if it exists
    if os.path.exists("role_message.json"):
        with open("role_message.json", "r") as f:
            try:
                data = json.load(f)
                role_message_id = data.get("message_id")
            except json.JSONDecodeError:
                role_message_id = None

    # Try to fetch the existing message
    if role_message_id:
        try:
            msg = await channel.fetch_message(role_message_id)
            print(f"✅ Role message found: {msg.id}")
            return  # Reuse existing message
        except discord.NotFound:
            print("⚠️ Previous role message not found. Sending a new one.")

    # Create a new message
    description = "React to assign yourself a role:\n"
    for emoji, role_id in EMOJI_TO_ROLE.items():
        role = channel.guild.get_role(role_id)
        if role:
            description += f"{emoji} : {role.name}\n"

    embed = discord.Embed(title="Self-Assign Roles", description=description)
    msg = await channel.send(embed=embed)
    
    for emoji in EMOJI_TO_ROLE.keys():
        await msg.add_reaction(emoji)

    role_message_id = msg.id

    # Save the message ID
    with open("role_message.json", "w") as f:
        json.dump({"message_id": role_message_id}, f)

    print(f"✅ New role message sent: {role_message_id}")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.message_id != role_message_id:
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    if member and not member.bot:
        role_id = EMOJI_TO_ROLE.get(str(payload.emoji))
        if role_id:
            role = guild.get_role(role_id)
            if role:
                await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.message_id != role_message_id:
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    if member and not member.bot:
        role_id = EMOJI_TO_ROLE.get(str(payload.emoji))
        if role_id:
            role = guild.get_role(role_id)
            if role:
                await member.remove_roles(role)

# --- Auto Reactions & Link Filter ---

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    is_target = (
        message.channel.id == TARGET_CHANNEL_ID or
        (hasattr(message.channel, "parent_id") and message.channel.parent_id == TARGET_CHANNEL_ID)
    )

    if is_target:
        try:
            await message.clear_reactions()
            await message.add_reaction("👍")
            await message.add_reaction("👎")
        except Exception as e:
            print(f"Reaction error: {e}")

    # Link Filtering
    if message.channel.id not in ALLOWED_LINK_CHANNELS:
        if "http://" in message.content or "https://" in message.content:
            try:
                await message.delete()
                await message.author.send(
                    f"⚠️ Your message with a link was removed in {message.channel.mention}."
                )
            except Exception:
                pass

    await bot.process_commands(message)

@bot.command(name="reactions")
async def reactions(ctx):
    if ctx.channel.id != TARGET_CHANNEL_ID:
        return

    messages = await ctx.channel.history(limit=50).flatten()

    total_up = 0
    total_down = 0

    for msg in messages:
        for reaction in msg.reactions:
            if reaction.emoji == "👍":
                total_up += reaction.count
            elif reaction.emoji == "👎":
                total_down += reaction.count

    await ctx.send(f"👍: {total_up}, 👎: {total_down}")

# --- Ban Command ---

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban(ctx, user: discord.User, duration: str = None, *, reason: str = "No reason provided"):
    ban_duration = None
    if duration and duration.lower() != "permanent":
        try:
            unit = duration[-1]
            time_amount = int(duration[:-1])
            if unit == "d":
                ban_duration = time_amount * 86400
            elif unit == "h":
                ban_duration = time_amount * 3600
            else:
                await ctx.send("Invalid format. Use '7d', '12h', or 'permanent'.")
                return
        except Exception:
            await ctx.send("Invalid duration.")
            return

    await ctx.guild.ban(user, reason=reason)
    await ctx.send(f"Banned {user.mention} {'permanently' if not ban_duration else f'for {duration}'}.")

    if ban_duration:
        await asyncio.sleep(ban_duration)
        await ctx.guild.unban(user)
        await ctx.send(f"{user.mention} has been unbanned after {duration}.")

# --- Run Bot ---

if not TOKEN or TOKEN == "TOKEN_HERE":
    print("❌ Token not set in config.json.")
else:
    bot.run(TOKEN)
