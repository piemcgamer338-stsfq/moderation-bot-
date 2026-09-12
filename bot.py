import os
import re
import time
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")
PREFIX = "."

BOT_NAME = "Dream Land Security"

FAKE_ACCOUNT_DAYS = 15
MESSAGES_PER_LEVEL = 100
MAX_GREET_CHANNELS = 5


# =========================================================
# INTENTS
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.messages = True


bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)


# =========================================================
# IN-MEMORY DATA
# EVERYTHING RESETS WHEN BOT RESTARTS
# =========================================================

guild_settings = {}

# guild_id -> user_id -> data
level_data = defaultdict(
    lambda: defaultdict(
        lambda: {
            "messages": 0,
            "level": 0,
        }
    )
)

# guild_id -> user_id -> invite data
invite_data = defaultdict(
    lambda: defaultdict(
        lambda: {
            "invites": 0,
            "joins": 0,
            "leaves": 0,
            "fakes": 0,
            "rejoins": 0,
        }
    )
)

# guild_id -> invite_code -> information
invite_cache = defaultdict(dict)

# guild_id -> user_id -> inviter_id
member_inviter = defaultdict(dict)

# guild_id -> channel_id -> latest deleted messages
snipes = defaultdict(lambda: defaultdict(lambda: deque(maxlen=1)))


# =========================================================
# DEFAULT SETTINGS
# =========================================================

def default_settings():
    return {
        "greet": {
            "message": "Welcome {member_mention} to {server_name}!",
            "delafter": 3,
            "channels": [],
        },
        "welcome": {
            "message": (
                "♡ wlcm qt\n\n"
                "• welcome to **{server_name}**\n"
                "• member #{server_members}\n"
                "• hope you enjoy & stay • ♥"
            ),
            "channel": None,
        },
        "level": {
            "channel": None,
            "message": (
                "🎉 Congratulations {user}, you reached **Level {user_level}**!"
            ),
        },
    }


def get_settings(guild_id):
    if guild_id not in guild_settings:
        guild_settings[guild_id] = default_settings()
    return guild_settings[guild_id]


# =========================================================
# PERMISSIONS
# =========================================================

def is_admin():
    async def predicate(ctx):
        if ctx.guild is None:
            return False

        return ctx.author.guild_permissions.administrator

    return commands.check(predicate)


def has_manage_messages():
    async def predicate(ctx):
        if ctx.guild is None:
            return False

        return ctx.author.guild_permissions.manage_messages

    return commands.check(predicate)


async def require_admin(ctx):
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used inside a server.")
        return False

    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You need **Administrator** permission to use this.")
        return False

    return True


# =========================================================
# VARIABLES
# =========================================================

def replace_variables(message, member=None, guild=None, channel=None):
    if guild is None and member is not None:
        guild = member.guild

    if channel is None and guild is not None:
        channel = guild.system_channel

    now = datetime.now(timezone.utc)

    variables = {}

    if member:
        joined = member.joined_at
        created = member.created_at

        variables = {
            "member": member.name,
            "member_name": member.name,
            "member_mention": member.mention,
            "member_id": str(member.id),
            "member_nick": member.display_name,
            "member_discriminator": getattr(member, "discriminator", ""),
            "member_created": discord.utils.format_dt(created, "F"),
            "member_created_relative": discord.utils.format_dt(created, "R"),
            "member_jointime": (
                discord.utils.format_dt(joined, "F")
                if joined
                else "Unknown"
            ),
            "member_jointime_relative": (
                discord.utils.format_dt(joined, "R")
                if joined
                else "Unknown"
            ),
        }

    if guild:
        variables.update({
            "server": guild.name,
            "server_name": guild.name,
            "server_id": str(guild.id),
            "server_members": str(guild.member_count or 0),
            "server_owner": guild.owner.mention if guild.owner else "Unknown",
            "server_owner_name": guild.owner.name if guild.owner else "Unknown",
            "server_created": discord.utils.format_dt(
                guild.created_at, "F"
            ),
            "server_created_relative": discord.utils.format_dt(
                guild.created_at, "R"
            ),
        })

    if channel:
        variables.update({
            "channel": channel.mention,
            "channel_name": channel.name,
            "channel_id": str(channel.id),
        })

    variables.update({
        "date": now.strftime("%d/%m/%Y"),
        "time": now.strftime("%I:%M %p"),
        "timestamp": discord.utils.format_dt(now, "F"),
        "timestamp_relative": discord.utils.format_dt(now, "R"),
    })

    for key, value in variables.items():
        message = message.replace("{" + key + "}", str(value))

    return message


# =========================================================
# COMMAND LIST
# =========================================================

MODULES = {
    "moderation": {
        "name": "Moderation",
        "emoji": "🛡️",
        "commands": [
            (".ban @user", "Ban a member."),
            (".kick @user", "Kick a member."),
            (".mute @user 1h", "Temporarily mute a member."),
            (".lock", "Lock the current channel."),
            (".unlock", "Unlock the current channel."),
            (".hide", "Hide the current channel."),
            (".unhide", "Unhide the current channel."),
            (".unban userid", "Unban a user by ID."),
            (".nuke", "Delete all messages in the channel."),
            (".clone", "Clone the channel and remove the old one."),
            (".purge amount", "Delete a number of messages."),
            (".snipe", "Show the latest deleted message."),
        ],
    },
    "greet": {
        "name": "Greet",
        "emoji": "👋",
        "commands": [
            (".greet delafter seconds", "Set greeting deletion time."),
            (".greet message message", "Set the greeting message."),
            (".greet variables", "Show greeting variables."),
            (".greet setchannel #channel", "Add a greeting channel."),
            (".greet removechannel #channel", "Remove a greeting channel."),
            (".greet reset", "Reset all greeting settings."),
        ],
    },
    "welcome": {
        "name": "Welcome",
        "emoji": "🎉",
        "commands": [
            (".welcome channel #channel", "Set the welcome channel."),
            (".welcome message message", "Set the welcome message."),
            (".welcome variables", "Show welcome variables."),
            (".welcome reset", "Reset welcome settings."),
        ],
    },
    "level": {
        "name": "Level",
        "emoji": "📈",
        "commands": [
            (".lvl", "Show your level."),
            (".lvl @user", "Show another user's level."),
            (".level channel #channel", "Set the level-up channel."),
            (".level lb", "Show the top 3 levels."),
            (".level lbreset", "Reset all levels."),
            (".lvl message message", "Set the level-up message."),
            (".lvl variables", "Show level variables."),
        ],
    },
    "invites": {
        "name": "Invites",
        "emoji": "📩",
        "commands": [
            (".i", "Show your invite statistics."),
            (".i @user", "Show another user's invite statistics."),
            (".lb i", "Show the top 10 inviters."),
            (".lbreset", "Reset invite statistics."),
        ],
    },
}


# =========================================================
# HELP VIEW
# =========================================================

class HelpSelect(discord.ui.Select):

    def __init__(self, author_id):
        self.author_id = author_id

        options = [
            discord.SelectOption(
                label=data["name"],
                value=key,
                emoji=data["emoji"],
                description=f"View {data['name']} commands"
            )
            for key, data in MODULES.items()
        ]

        super().__init__(
            placeholder="Select a module...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ This help menu isn't yours.",
                ephemeral=True
            )
            return

        key = self.values[0]

        embed = create_module_embed(
            interaction.guild,
            key
        )

        await interaction.response.edit_message(
            embed=embed,
            view=HelpView(self.author_id, selected=key)
        )


class HelpBack(discord.ui.Button):

    def __init__(self, author_id):
        self.author_id = author_id

        super().__init__(
            label="Back",
            emoji="↩️",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ This help menu isn't yours.",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(
            embed=create_main_help_embed(interaction.guild),
            view=HelpView(self.author_id)
        )


class HelpView(discord.ui.View):

    def __init__(self, author_id, selected=None):
        super().__init__(timeout=180)

        self.author_id = author_id

        self.add_item(HelpSelect(author_id))

        if selected:
            self.add_item(HelpBack(author_id))


def create_main_help_embed(guild):
    total_commands = sum(
        len(module["commands"])
        for module in MODULES.values()
    )

    embed = discord.Embed(
        title="Hey, I'm Dream Land Security",
        description=(
            f"**My prefix for this server is** `{PREFIX}`\n\n"
            f"**Type** `{PREFIX}help [context]` **for more**\n\n"
            f"**Total commands:** `{total_commands}`\n\n"
            "`»` 🛡️ Moderation\n"
            "`»` 👋 Greet\n"
            "`»` 🎉 Welcome\n"
            "`»` 📈 Level\n"
            "`»` 📩 Invites\n\n"
            "**[Select a module to see]**"
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text=f"{guild.name} • Dream Land Security"
    )

    return embed


def create_module_embed(guild, key):
    module = MODULES[key]

    lines = []

    for command, description in module["commands"]:
        lines.append(
            f"`{command}`\n> {description}"
        )

    embed = discord.Embed(
        title=f"{module['emoji']} {module['name']}",
        description="\n\n".join(lines),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text=f"{guild.name} • Use the menu to switch modules"
    )

    return embed


# =========================================================
# HELP
# =========================================================

@bot.command(name="help")
async def help_command(ctx, context=None):

    if context:
        context = context.lower()

        aliases = {
            "mod": "moderation",
            "moderation": "moderation",
            "greet": "greet",
            "welcome": "welcome",
            "level": "level",
            "lvl": "level",
            "invite": "invites",
            "invites": "invites",
            "i": "invites",
        }

        context = aliases.get(context)

        if context in MODULES:
            await ctx.send(
                embed=create_module_embed(ctx.guild, context),
                view=HelpView(ctx.author.id, selected=context)
            )
            return

    await ctx.send(
        embed=create_main_help_embed(ctx.guild),
        view=HelpView(ctx.author.id)
    )


# =========================================================
# MODERATION
# =========================================================

@bot.command()
@is_admin()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    try:
        await member.ban(reason=reason)

        embed = discord.Embed(
            title="🔨 Member Banned",
            description=(
                f"**User:** {member.mention}\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.red()
        )

        await ctx.send(embed=embed)

    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to ban this member.")


@bot.command()
@is_admin()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    try:
        await member.kick(reason=reason)

        embed = discord.Embed(
            title="👢 Member Kicked",
            description=(
                f"**User:** {member.mention}\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}"
            ),
            color=discord.Color.orange()
        )

        await ctx.send(embed=embed)

    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to kick this member.")


def parse_duration(value):
    match = re.fullmatch(
        r"(\d+)\s*(s|m|h|d|w)",
        value.lower()
    )

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    multipliers = {
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 60 * 60 * 24,
        "w": 60 * 60 * 24 * 7,
    }

    return amount * multipliers[unit]


@bot.command()
@is_admin()
async def mute(ctx, member: discord.Member, duration=None, *, reason="No reason provided"):

    if not duration:
        await ctx.send(
            "❌ Usage: `.mute @user 1h` / `.mute @user 1d` / `.mute @user 1m`"
        )
        return

    seconds = parse_duration(duration)

    if seconds is None:
        await ctx.send(
            "❌ Invalid duration.\n"
            "Examples: `1m`, `10m`, `1h`, `1d`, `1w`"
        )
        return

    if seconds > 28 * 24 * 60 * 60:
        await ctx.send("❌ Discord's timeout limit is 28 days.")
        return

    try:
        until = discord.utils.utcnow() + timedelta(seconds=seconds)

        await member.timeout(
            until,
            reason=reason
        )

        await ctx.send(
            f"🔇 {member.mention} has been muted for **{duration}**."
        )

    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to mute this member.")


@bot.command()
@is_admin()
async def lock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send("🔒 Channel locked.")


@bot.command()
@is_admin()
async def unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send("🔓 Channel unlocked.")


@bot.command()
@is_admin()
async def hide(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = False

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send("👁️ Channel hidden.")


@bot.command()
@is_admin()
async def unhide(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send("👁️ Channel unhidden.")


@bot.command()
@is_admin()
async def unban(ctx, user_id: int):

    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user)

        await ctx.send(
            f"✅ **{user}** has been unbanned."
        )

    except discord.NotFound:
        await ctx.send("❌ That user isn't banned or doesn't exist.")

    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to unban users.")


@bot.command()
@is_admin()
async def nuke(ctx):

    old_channel = ctx.channel

    try:
        new_channel = await old_channel.clone(
            name=old_channel.name,
            reason=f"Nuked by {ctx.author}"
        )

        await new_channel.edit(
            position=old_channel.position,
            category=old_channel.category,
            topic=old_channel.topic
        )

        await old_channel.delete(
            reason=f"Nuked by {ctx.author}"
        )

        await new_channel.send(
            f"💥 Channel nuked by {ctx.author.mention}."
        )

    except discord.Forbidden:
        await ctx.send("❌ I don't have enough permissions to nuke this channel.")


@bot.command()
@is_admin()
async def clone(ctx):

    old_channel = ctx.channel

    try:
        new_channel = await old_channel.clone(
            reason=f"Cloned by {ctx.author}"
        )

        await new_channel.edit(
            position=old_channel.position,
            category=old_channel.category,
            topic=old_channel.topic
        )

        await old_channel.delete(
            reason=f"Cloned by {ctx.author}"
        )

        await new_channel.send(
            f"📋 Channel cloned by {ctx.author.mention}."
        )

    except discord.Forbidden:
        await ctx.send("❌ I don't have enough permissions to clone this channel.")


@bot.command()
@is_admin()
async def purge(ctx, amount: int):

    if amount < 1:
        await ctx.send("❌ Amount must be greater than 0.")
        return

    if amount > 1000:
        await ctx.send("❌ Maximum purge amount is 1000.")
        return

    deleted = await ctx.channel.purge(
        limit=amount + 1
    )

    message = await ctx.send(
        f"🧹 Deleted **{len(deleted) - 1}** messages."
    )

    await asyncio.sleep(3)

    try:
        await message.delete()
    except discord.HTTPException:
        pass


@bot.command()
@has_manage_messages()
async def snipe(ctx):

    messages = snipes[ctx.guild.id][ctx.channel.id]

    if not messages:
        await ctx.send("❌ Nothing to snipe.")
        return

    data = messages[-1]

    embed = discord.Embed(
        title="🕵️ Sniped Message",
        description=data["content"] or "*No text content*",
        color=discord.Color.orange()
    )

    embed.set_author(
        name=str(data["author"]),
        icon_url=data["avatar"]
    )

    embed.set_footer(
        text="Latest deleted message"
    )

    await ctx.send(embed=embed)


# =========================================================
# SNIPE EVENT
# =========================================================

@bot.event
async def on_message_delete(message):

    if message.guild is None:
        return

    if message.author.bot:
        return

    avatar = ""

    if message.author.avatar:
        avatar = message.author.avatar.url

    snipes[message.guild.id][message.channel.id].append({
        "content": message.content,
        "author": message.author,
        "avatar": avatar,
        "time": datetime.now(timezone.utc),
    })


# =========================================================
# GREET VARIABLES
# =========================================================

GREET_VARIABLE_LIST = [
    "{member}",
    "{member_name}",
    "{member_mention}",
    "{member_id}",
    "{member_nick}",
    "{member_created}",
    "{member_created_relative}",
    "{member_jointime}",
    "{member_jointime_relative}",
    "{server}",
    "{server_name}",
    "{server_id}",
    "{server_members}",
    "{server_owner}",
    "{server_owner_name}",
    "{server_created}",
    "{server_created_relative}",
    "{channel}",
    "{channel_name}",
    "{channel_id}",
    "{date}",
    "{time}",
    "{timestamp}",
    "{timestamp_relative}",
]


# =========================================================
# GREET
# =========================================================

@bot.group(name="greet", invoke_without_command=True)
@is_admin()
async def greet(ctx):
    await ctx.send(
        "❌ Use `.help greet` to see all greet commands."
    )


@greet.command(name="delafter")
@is_admin()
async def greet_delafter(ctx, seconds: int):

    if seconds < 0:
        await ctx.send("❌ Seconds cannot be negative.")
        return

    get_settings(ctx.guild.id)["greet"]["delafter"] = seconds

    await ctx.send(
        f"✅ Greet messages will be deleted after **{seconds} seconds**."
    )


@greet.command(name="message")
@is_admin()
async def greet_message(ctx, *, message):

    get_settings(ctx.guild.id)["greet"]["message"] = message

    await ctx.send(
        "✅ Greeting message updated."
    )


@greet.command(name="variables")
@is_admin()
async def greet_variables(ctx):

    embed = discord.Embed(
        title="👋 Greet Variables",
        description="\n".join(
            f"`{variable}`"
            for variable in GREET_VARIABLE_LIST
        ),
        color=discord.Color.blurple()
    )

    await ctx.send(embed=embed)


@greet.command(name="setchannel")
@is_admin()
async def greet_setchannel(ctx, channel: discord.TextChannel):

    settings = get_settings(ctx.guild.id)["greet"]

    if channel.id in settings["channels"]:
        await ctx.send("❌ That channel is already configured.")
        return

    if len(settings["channels"]) >= MAX_GREET_CHANNELS:
        await ctx.send(
            f"❌ You can only have **{MAX_GREET_CHANNELS}** greet channels."
        )
        return

    settings["channels"].append(channel.id)

    await ctx.send(
        f"✅ {channel.mention} added to greet channels."
    )


@greet.command(name="removechannel")
@is_admin()
async def greet_removechannel(ctx, channel: discord.TextChannel):

    settings = get_settings(ctx.guild.id)["greet"]

    if channel.id not in settings["channels"]:
        await ctx.send("❌ That channel isn't configured.")
        return

    settings["channels"].remove(channel.id)

    await ctx.send(
        f"✅ {channel.mention} removed from greet channels."
    )


@greet.command(name="reset")
@is_admin()
async def greet_reset(ctx):

    get_settings(ctx.guild.id)["greet"] = default_settings()["greet"]

    await ctx.send("✅ Greet settings reset.")


# =========================================================
# WELCOME
# =========================================================

@bot.group(name="welcome", invoke_without_command=True)
@is_admin()
async def welcome(ctx):
    await ctx.send(
        "❌ Use `.help welcome` to see all welcome commands."
    )


@welcome.command(name="channel")
@is_admin()
async def welcome_channel(ctx, channel: discord.TextChannel):

    get_settings(ctx.guild.id)["welcome"]["channel"] = channel.id

    await ctx.send(
        f"✅ Welcome channel set to {channel.mention}."
    )


@welcome.command(name="message")
@is_admin()
async def welcome_message(ctx, *, message):

    get_settings(ctx.guild.id)["welcome"]["message"] = message

    await ctx.send(
        "✅ Welcome message updated."
    )


@welcome.command(name="variables")
@is_admin()
async def welcome_variables(ctx):

    embed = discord.Embed(
        title="🎉 Welcome Variables",
        description="\n".join(
            f"`{variable}`"
            for variable in GREET_VARIABLE_LIST
        ),
        color=discord.Color.blurple()
    )

    await ctx.send(embed=embed)


@welcome.command(name="reset")
@is_admin()
async def welcome_reset(ctx):

    get_settings(ctx.guild.id)["welcome"] = default_settings()["welcome"]

    await ctx.send("✅ Welcome settings reset.")


# =========================================================
# MEMBER JOIN
# =========================================================

@bot.event
async def on_member_join(member):

    guild = member.guild
    settings = get_settings(guild.id)

    # -----------------------------------------------------
    # INVITE TRACKING
    # -----------------------------------------------------

    inviter_id = None

    try:
        current_invites = await guild.invites()

        old_invites = invite_cache.get(guild.id, {})

        for invite in current_invites:
            old = old_invites.get(invite.code)

            if old and invite.uses > old["uses"]:
                inviter_id = old["inviter_id"]
                break

        new_cache = {}

        for invite in current_invites:
            new_cache[invite.code] = {
                "uses": invite.uses,
                "inviter_id": (
                    invite.inviter.id
                    if invite.inviter
                    else None
                ),
            }

        invite_cache[guild.id] = new_cache

    except (discord.Forbidden, discord.HTTPException):
        inviter_id = None

    if inviter_id and inviter_id != member.id:

        data = invite_data[guild.id][inviter_id]

        data["invites"] += 1
        data["joins"] += 1

        age = (
            datetime.now(timezone.utc) - member.created_at
        ).days

        if age < FAKE_ACCOUNT_DAYS:
            data["fakes"] += 1

        if member.id in member_inviter[guild.id]:
            data["rejoins"] += 1

        member_inviter[guild.id][member.id] = inviter_id

    # -----------------------------------------------------
    # GREET
    # -----------------------------------------------------

    greet_settings = settings["greet"]

    for channel_id in list(greet_settings["channels"]):

        channel = guild.get_channel(channel_id)

        if channel is None:
            continue

        try:
            message = replace_variables(
                greet_settings["message"],
                member=member,
                guild=guild,
                channel=channel
            )

            sent = await channel.send(message)

            delay = greet_settings["delafter"]

            if delay > 0:
                await asyncio.sleep(delay)

                try:
                    await sent.delete()
                except discord.HTTPException:
                    pass

        except discord.HTTPException:
            pass

    # -----------------------------------------------------
    # WELCOME
    # -----------------------------------------------------

    welcome_settings = settings["welcome"]
    welcome_channel_id = welcome_settings["channel"]

    if welcome_channel_id:

        channel = guild.get_channel(welcome_channel_id)

        if channel:

            message = replace_variables(
                welcome_settings["message"],
                member=member,
                guild=guild,
                channel=channel
            )

            embed = discord.Embed(
                title="♡ Welcome",
                description=message,
                color=discord.Color.from_rgb(
                    245, 170, 190
                )
            )

            embed.set_author(
                name=member.name,
                icon_url=member.display_avatar.url
            )

            embed.set_thumbnail(
                url=member.display_avatar.url
            )

            embed.set_footer(
                text=f"We now have {guild.member_count} members!"
            )

            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass


# =========================================================
# MEMBER LEAVE
# =========================================================

@bot.event
async def on_member_remove(member):

    guild = member.guild

    inviter_id = member_inviter[guild.id].get(member.id)

    if inviter_id:

        data = invite_data[guild.id][inviter_id]

        data["leaves"] += 1

        if data["invites"] > 0:
            data["invites"] -= 1


# =========================================================
# INVITE CACHE
# =========================================================

@bot.event
async def on_ready():

    print(
        f"Logged in as {bot.user} ({bot.user.id})"
    )

    print(
        f"Dream Land Security is online with prefix {PREFIX}"
    )

    for guild in bot.guilds:

        try:
            invites = await guild.invites()

            invite_cache[guild.id] = {
                invite.code: {
                    "uses": invite.uses,
                    "inviter_id": (
                        invite.inviter.id
                        if invite.inviter
                        else None
                    ),
                }
                for invite in invites
            }

        except (discord.Forbidden, discord.HTTPException):
            invite_cache[guild.id] = {}


# =========================================================
# LEVEL SYSTEM
# =========================================================

def get_user_level(guild_id, user_id):
    return level_data[guild_id][user_id]


def level_message_replace(message, member, guild):

    data = get_user_level(
        guild.id,
        member.id
    )

    replacements = {
        "{user}": member.mention,
        "{user_name}": member.name,
        "{user_id}": str(member.id),
        "{user_level}": str(data["level"]),
        "{level}": str(data["level"]),
        "{user_messages}": str(data["messages"]),
        "{messages}": str(data["messages"]),
        "{server_name}": guild.name,
        "{server_members}": str(guild.member_count or 0),
    }

    for key, value in replacements.items():
        message = message.replace(key, value)

    return message


@bot.command(name="lvl")
async def lvl(ctx, member: discord.Member = None):

    member = member or ctx.author

    data = get_user_level(
        ctx.guild.id,
        member.id
    )

    current_messages = data["messages"]
    level = data["level"]

    remaining = (
        MESSAGES_PER_LEVEL -
        (current_messages % MESSAGES_PER_LEVEL)
    )

    if remaining == MESSAGES_PER_LEVEL:
        remaining = 0

    embed = discord.Embed(
        title="📈 Level",
        color=discord.Color.blurple()
    )

    embed.set_author(
        name=str(member),
        icon_url=member.display_avatar.url
    )

    embed.add_field(
        name="Level",
        value=f"**{level}**",
        inline=True
    )

    embed.add_field(
        name="Messages",
        value=f"**{current_messages}**",
        inline=True
    )

    embed.add_field(
        name="Next Level",
        value=f"**{remaining}** messages",
        inline=True
    )

    await ctx.send(embed=embed)


@bot.group(name="level", invoke_without_command=True)
@is_admin()
async def level(ctx):
    await ctx.send(
        "❌ Use `.help level` to see level commands."
    )


@level.command(name="channel")
@is_admin()
async def level_channel(ctx, channel: discord.TextChannel):

    get_settings(ctx.guild.id)["level"]["channel"] = channel.id

    await ctx.send(
        f"✅ Level-up channel set to {channel.mention}."
    )


@level.command(name="lb")
@is_admin()
async def level_lb(ctx):

    users = []

    for user_id, data in level_data[ctx.guild.id].items():

        member = ctx.guild.get_member(user_id)

        if member is None:
            continue

        users.append(
            (
                member,
                data["level"],
                data["messages"]
            )
        )

    users.sort(
        key=lambda x: (x[1], x[2]),
        reverse=True
    )

    users = users[:3]

    embed = discord.Embed(
        title="📈 Level Leaderboard",
        color=discord.Color.blurple()
    )

    if not users:
        embed.description = "No level data yet."

    else:

        medals = ["🥇", "🥈", "🥉"]

        lines = []

        for index, (member, lvl_value, messages) in enumerate(users):
            lines.append(
                f"{medals[index]} **{member.display_name}** "
                f"— Level **{lvl_value}** "
                f"({messages} messages)"
            )

        embed.description = "\n".join(lines)

    await ctx.send(embed=embed)


@level.command(name="lbreset")
@is_admin()
async def level_lbreset(ctx):

    level_data[ctx.guild.id].clear()

    await ctx.send(
        "✅ All level data has been reset."
    )


@level.command(name="message")
@is_admin()
async def level_message(ctx, *, message):

    get_settings(ctx.guild.id)["level"]["message"] = message

    await ctx.send(
        "✅ Level-up message updated."
    )


@level.command(name="variables")
@is_admin()
async def level_variables(ctx):

    variables = [
        "{user}",
        "{user_name}",
        "{user_id}",
        "{user_level}",
        "{level}",
        "{user_messages}",
        "{messages}",
        "{server_name}",
        "{server_members}",
    ]

    embed = discord.Embed(
        title="📈 Level Variables",
        description="\n".join(
            f"`{x}`"
            for x in variables
        ),
        color=discord.Color.blurple()
    )

    await ctx.send(embed=embed)


# =========================================================
# MESSAGE LEVEL TRACKING
# =========================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if message.guild:

        data = get_user_level(
            message.guild.id,
            message.author.id
        )

        data["messages"] += 1

        old_level = data["level"]

        new_level = (
            data["messages"] //
            MESSAGES_PER_LEVEL
        )

        if new_level > old_level:

            data["level"] = new_level

            settings = get_settings(
                message.guild.id
            )["level"]

            channel_id = settings["channel"]

            if channel_id:

                channel = message.guild.get_channel(
                    channel_id
                )

                if channel:

                    content = level_message_replace(
                        settings["message"],
                        message.author,
                        message.guild
                    )

                    try:
                        await channel.send(content)
                    except discord.HTTPException:
                        pass

    await bot.process_commands(message)


# =========================================================
# INVITE DISPLAY
# =========================================================

def invite_stats_embed(
    guild,
    member,
    requested_by
):

    data = invite_data[guild.id][member.id]

    embed = discord.Embed(
        title="Invite log",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )

    embed.description = (
        f"**{member.display_name}** "
        f"(`{member.id}`)\n\n"
        f"**Invites:** {data['invites']}\n"
        f"**Joins:** {data['joins']}\n"
        f"**Leaves:** {data['leaves']}\n"
        f"**Fakes:** {data['fakes']}\n"
        f"**Rejoins:** {data['rejoins']}\n"
    )

    embed.set_author(
        name=str(member),
        icon_url=member.display_avatar.url
    )

    embed.set_footer(
        text=f"Requested by {requested_by.display_name}"
    )

    return embed


@bot.command(name="i")
async def invite_info(ctx, member: discord.Member = None):

    member = member or ctx.author

    await ctx.send(
        embed=invite_stats_embed(
            ctx.guild,
            member,
            ctx.author
        )
    )


# =========================================================
# INVITE LEADERBOARD
# =========================================================

@bot.group(name="lb", invoke_without_command=True)
async def leaderboard(ctx):

    await ctx.send(
        "❌ Use `.lb i` for the invite leaderboard."
    )


@leaderboard.command(name="i")
async def invite_leaderboard(ctx):

    entries = []

    for user_id, data in invite_data[ctx.guild.id].items():

        member = ctx.guild.get_member(user_id)

        if member is None:
            continue

        entries.append(
            (
                member,
                data
            )
        )

    entries.sort(
        key=lambda x: (
            x[1]["invites"],
            x[1]["joins"],
            -x[1]["leaves"]
        ),
        reverse=True
    )

    entries = entries[:10]

    embed = discord.Embed(
        title="Invite Leaderboard",
        color=discord.Color.blurple()
    )

    if not entries:
        embed.description = "No invite data yet."

    else:

        lines = []

        for index, (member, data) in enumerate(entries, 1):

            lines.append(
                f"**#{index}** "
                f"{member.mention} — "
                f"**{data['invites']} Invites** "
                f"({data['joins']} Joins, "
                f"{data['leaves']} Leaves, "
                f"{data['fakes']} Fakes, "
                f"{data['rejoins']} Rejoins)"
            )

        embed.description = "\n".join(lines)

    embed.set_footer(
        text=f"Page 1/1 • Requested by {ctx.author.display_name}"
    )

    await ctx.send(embed=embed)


@bot.command(name="lbreset")
@is_admin()
async def lbreset(ctx):

    invite_data[ctx.guild.id].clear()
    member_inviter[ctx.guild.id].clear()

    await ctx.send(
        "✅ Invite leaderboard has been reset."
    )


# =========================================================
# ERROR HANDLER
# =========================================================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ You don't have permission to use this command."
        )
        return

    if isinstance(error, commands.CheckFailure):
        await ctx.send(
            "❌ You don't have permission to use this command."
        )
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            f"❌ Missing argument: `{error.param.name}`"
        )
        return

    if isinstance(error, commands.MemberNotFound):
        await ctx.send(
            "❌ Member not found. Please mention the member."
        )
        return

    if isinstance(error, commands.ChannelNotFound):
        await ctx.send(
            "❌ Channel not found."
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ Invalid argument."
        )
        return

    print(
        f"Command error in {ctx.command}: {error}"
    )


# =========================================================
# START
# =========================================================

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not set."
    )

bot.run(TOKEN)
