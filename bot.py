import os
import re
import random
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
intents.presences = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)


# =========================================================
# IN-MEMORY STORAGE
# EVERYTHING RESETS WHEN BOT RESTARTS
# =========================================================

guild_settings = {}

level_data = defaultdict(
    lambda: defaultdict(
        lambda: {
            "messages": 0,
            "level": 0
        }
    )
)

invite_data = defaultdict(
    lambda: defaultdict(
        lambda: {
            "invites": 0,
            "joins": 0,
            "leaves": 0,
            "fakes": 0,
            "rejoins": 0
        }
    )
)

invite_cache = defaultdict(dict)
member_inviter = defaultdict(dict)

snipes = defaultdict(
    lambda: defaultdict(
        lambda: deque(maxlen=1)
    )
)

timers = defaultdict(dict)

giveaways = {}
giveaway_tasks = {}

giveaway_blacklist = defaultdict(set)


# =========================================================
# DEFAULT SETTINGS
# =========================================================

def default_settings():
    return {
        "greet": {
            "message": "Welcome {member_mention} to {server_name}!",
            "delafter": 3,
            "channels": []
        },

        "welcome": {
            "message": (
                "Welcome {member_mention} to {server_name}.\n\n"
                "Member number: {server_members}"
            ),
            "channel": None
        },

        "level": {
            "channel": None,
            "message": (
                "Congratulations {user}, "
                "you reached Level {user_level}."
            )
        }
    }


def get_settings(guild_id):
    if guild_id not in guild_settings:
        guild_settings[guild_id] = default_settings()

    return guild_settings[guild_id]


# =========================================================
# GENERAL HELPERS
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def format_duration(seconds):
    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"

    minutes = seconds // 60

    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    hours = minutes // 60
    minutes %= 60

    if hours < 24:
        if minutes:
            return f"{hours}h {minutes}m"
        return f"{hours} hour{'s' if hours != 1 else ''}"

    days = hours // 24
    hours %= 24

    if hours:
        return f"{days}d {hours}h"

    return f"{days} day{'s' if days != 1 else ''}"


def parse_duration(value):
    if not value:
        return None

    value = value.lower().strip()

    match = re.fullmatch(
        r"(\d+)\s*(s|m|h|d|w)",
        value
    )

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800
    }

    return amount * multipliers[unit]


def parse_giveaway_duration(value):
    return parse_duration(value)


def format_discord_time(dt):
    return discord.utils.format_dt(
        dt,
        "F"
    )


def format_discord_relative(dt):
    return discord.utils.format_dt(
        dt,
        "R"
    )


# =========================================================
# VARIABLES
# =========================================================

ALL_VARIABLES = [
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
    "{timestamp_relative}"
]


def replace_variables(
    text,
    member=None,
    guild=None,
    channel=None
):
    if guild is None and member:
        guild = member.guild

    current_time = now_utc()

    values = {
        "date": current_time.strftime("%d/%m/%Y"),
        "time": current_time.strftime("%I:%M %p"),
        "timestamp": format_discord_time(current_time),
        "timestamp_relative": format_discord_relative(current_time)
    }

    if member:
        values.update({
            "member": member.name,
            "member_name": member.name,
            "member_mention": member.mention,
            "member_id": str(member.id),
            "member_nick": member.display_name,

            "member_created": format_discord_time(
                member.created_at
            ),

            "member_created_relative": format_discord_relative(
                member.created_at
            ),

            "member_jointime": (
                format_discord_time(member.joined_at)
                if member.joined_at
                else "Unknown"
            ),

            "member_jointime_relative": (
                format_discord_relative(member.joined_at)
                if member.joined_at
                else "Unknown"
            )
        })

    if guild:
        values.update({
            "server": guild.name,
            "server_name": guild.name,
            "server_id": str(guild.id),
            "server_members": str(guild.member_count or 0),

            "server_owner": (
                guild.owner.mention
                if guild.owner
                else "Unknown"
            ),

            "server_owner_name": (
                guild.owner.name
                if guild.owner
                else "Unknown"
            ),

            "server_created": format_discord_time(
                guild.created_at
            ),

            "server_created_relative": format_discord_relative(
                guild.created_at
            )
        })

    if channel:
        values.update({
            "channel": channel.mention,
            "channel_name": channel.name,
            "channel_id": str(channel.id)
        })

    for key, value in values.items():
        text = text.replace(
            "{" + key + "}",
            str(value)
        )

    return text


# =========================================================
# PERMISSION CHECKS
# =========================================================

def is_admin():
    async def predicate(ctx):
        return (
            ctx.guild is not None
            and ctx.author.guild_permissions.administrator
        )

    return commands.check(predicate)


def has_manage_messages():
    async def predicate(ctx):
        return (
            ctx.guild is not None
            and ctx.author.guild_permissions.manage_messages
        )

    return commands.check(predicate)


# =========================================================
# HELP MODULES
# =========================================================

MODULES = {
    "moderation": {
        "name": "Moderation",
        "commands": [
            (".ban @user", "Ban a member."),
            (".kick @user", "Kick a member."),
            (".mute @user 1h", "Temporarily mute a member."),
            (".lock", "Lock the current channel."),
            (".unlock", "Unlock the current channel."),
            (".hide", "Hide the current channel."),
            (".unhide", "Unhide the current channel."),
            (".unban userid", "Unban a user by ID."),
            (".nuke", "Delete and recreate the current channel."),
            (".clone", "Clone the current channel."),
            (".purge amount", "Delete a number of messages."),
            (".snipe", "Show the latest deleted message.")
        ]
    },

    "greet": {
        "name": "Greet",
        "commands": [
            (".greet delafter seconds", "Set greeting deletion time."),
            (".greet message message", "Set the greeting message."),
            (".greet variables", "Show greeting variables."),
            (".greet setchannel #channel", "Add a greeting channel."),
            (".greet removechannel #channel", "Remove a greeting channel."),
            (".greet reset", "Reset greeting settings.")
        ]
    },

    "welcome": {
        "name": "Welcome",
        "commands": [
            (".welcome channel #channel", "Set the welcome channel."),
            (".welcome message message", "Set the welcome message."),
            (".welcome variables", "Show welcome variables."),
            (".welcome reset", "Reset welcome settings.")
        ]
    },

    "level": {
        "name": "Level",
        "commands": [
            (".lvl", "Show your level."),
            (".lvl @user", "Show another user's level."),
            (".level channel #channel", "Set the level-up channel."),
            (".level lb", "Show the top 3 users."),
            (".level lbreset", "Reset all level data."),
            (".lvl message message", "Set the level-up message."),
            (".lvl variables", "Show level variables.")
        ]
    },

    "invites": {
        "name": "Invites",
        "commands": [
            (".i", "Show invite statistics."),
            (".i @user", "Show another user's invite statistics."),
            (".lb i", "Show the top 10 inviters."),
            (".lbreset", "Reset invite statistics.")
        ]
    },

    "general": {
        "name": "General",
        "commands": [
            (".avatar [user]", "Show a user's avatar."),
            (".banner [user]", "Show a user's banner."),
            (".srvlogo", "Show the server icon."),
            (".srvbanner", "Show the server banner."),
            (".profile", "Show your profile."),
            (".si", "Show server information."),
            (".tstart 1h name", "Start a named timer."),
            (".tend name", "End a named timer."),
            (".tpause name", "Pause a named timer.")
        ]
    },

    "giveaway": {
        "name": "Giveaway",
        "commands": [
            (".gstart 1h 1 reward", "Start a giveaway."),
            (".gend message_id", "End a giveaway."),
            (".gblacklist @user", "Blacklist a user from giveaways.")
        ]
    }
}


# =========================================================
# COMPONENTS V2 HELP
# =========================================================

class HelpSelect(discord.ui.Select):

    def __init__(self, author_id, selected=None):
        self.author_id = author_id

        options = []

        for key, module in MODULES.items():

            options.append(
                discord.SelectOption(
                    label=module["name"],
                    value=key,
                    description=f"View {module['name']} commands",
                    default=key == selected
                )
            )

        super().__init__(
            placeholder="Select a module",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction):

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "This help menu belongs to another user.",
                ephemeral=True
            )

            return

        selected = self.values[0]

        await interaction.response.edit_message(
            view=HelpLayout(
                self.author_id,
                selected
            )
        )


class HelpBack(discord.ui.Button):

    def __init__(self, author_id):
        self.author_id = author_id

        super().__init__(
            label="Back",
            style=discord.ButtonStyle.secondary
        )

    async def callback(self, interaction):

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "This help menu belongs to another user.",
                ephemeral=True
            )

            return

        await interaction.response.edit_message(
            view=HelpLayout(
                self.author_id
            )
        )


class HelpLayout(discord.ui.LayoutView):

    def __init__(
        self,
        author_id,
        selected=None
    ):
        super().__init__(
            timeout=180
        )

        self.author_id = author_id
        self.selected = selected

        container = discord.ui.Container()

        if selected is None:

            total_commands = sum(
                len(module["commands"])
                for module in MODULES.values()
            )

            content = (
                "## Hey, I'm Dream Land Security\n\n"
                f"**My prefix for this server is** `{PREFIX}`\n\n"
                f"**Type** `{PREFIX}help [context]` **for more**\n\n"
                f"**Total commands:** `{total_commands}`\n\n"
                "`»` Moderation\n"
                "`»` Greet\n"
                "`»` Welcome\n"
                "`»` Level\n"
                "`»` Invites\n"
                "`»` General\n"
                "`»` Giveaway\n\n"
                "**Select a module to see**"
            )

        else:

            module = MODULES[selected]

            command_lines = []

            for command, description in module["commands"]:

                command_lines.append(
                    f"**`{command}`**\n{description}"
                )

            content = (
                f"## {module['name']}\n\n"
                + "\n\n".join(command_lines)
            )

        container.add_item(
            discord.ui.TextDisplay(
                content=content
            )
        )

        container.add_item(
            discord.ui.Separator(
                visible=True
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                HelpSelect(
                    author_id,
                    selected
                )
            )
        )

        if selected is not None:

            container.add_item(
                discord.ui.ActionRow(
                    HelpBack(
                        author_id
                    )
                )
            )

        self.add_item(container)


@bot.command(name="help")
async def help_command(
    ctx,
    context=None
):

    if context:

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

            "general": "general",
            "gen": "general",

            "giveaway": "giveaway",
            "giveaways": "giveaway",
            "give": "giveaway"
        }

        selected = aliases.get(
            context.lower()
        )

        if selected:

            await ctx.send(
                view=HelpLayout(
                    ctx.author.id,
                    selected
                )
            )

            return

    await ctx.send(
        view=HelpLayout(
            ctx.author.id
        )
    )


# =========================================================
# MODERATION
# =========================================================

@bot.command()
@is_admin()
async def ban(
    ctx,
    member: discord.Member,
    *,
    reason="No reason provided"
):

    try:

        await member.ban(
            reason=reason
        )

        await ctx.send(
            f"Member **{member}** has been banned."
        )

    except discord.Forbidden:

        await ctx.send(
            "I don't have permission to ban that member."
        )


@bot.command()
@is_admin()
async def kick(
    ctx,
    member: discord.Member,
    *,
    reason="No reason provided"
):

    try:

        await member.kick(
            reason=reason
        )

        await ctx.send(
            f"Member **{member}** has been kicked."
        )

    except discord.Forbidden:

        await ctx.send(
            "I don't have permission to kick that member."
        )


@bot.command()
@is_admin()
async def mute(
    ctx,
    member: discord.Member,
    duration=None,
    *,
    reason="No reason provided"
):

    if not duration:

        await ctx.send(
            "Usage: `.mute @user 1h`"
        )

        return

    seconds = parse_duration(
        duration
    )

    if seconds is None:

        await ctx.send(
            "Invalid duration. Use `1m`, `1h`, `1d` or `1w`."
        )

        return

    if seconds > 28 * 86400:

        await ctx.send(
            "The maximum timeout is 28 days."
        )

        return

    try:

        await member.timeout(
            timedelta(seconds=seconds),
            reason=reason
        )

        await ctx.send(
            f"Member **{member}** has been muted for "
            f"**{duration}**."
        )

    except discord.Forbidden:

        await ctx.send(
            "I don't have permission to mute that member."
        )


@bot.command()
@is_admin()
async def lock(ctx):

    overwrite = ctx.channel.overwrites_for(
        ctx.guild.default_role
    )

    overwrite.send_messages = False

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send(
        "Channel locked."
    )


@bot.command()
@is_admin()
async def unlock(ctx):

    overwrite = ctx.channel.overwrites_for(
        ctx.guild.default_role
    )

    overwrite.send_messages = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send(
        "Channel unlocked."
    )


@bot.command()
@is_admin()
async def hide(ctx):

    overwrite = ctx.channel.overwrites_for(
        ctx.guild.default_role
    )

    overwrite.view_channel = False

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send(
        "Channel hidden."
    )


@bot.command()
@is_admin()
async def unhide(ctx):

    overwrite = ctx.channel.overwrites_for(
        ctx.guild.default_role
    )

    overwrite.view_channel = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await ctx.send(
        "Channel visible."
    )


@bot.command()
@is_admin()
async def unban(
    ctx,
    user_id: int
):

    try:

        user = await bot.fetch_user(
            user_id
        )

        await ctx.guild.unban(
            user
        )

        await ctx.send(
            f"User **{user}** has been unbanned."
        )

    except discord.NotFound:

        await ctx.send(
            "User is not banned or does not exist."
        )

    except discord.Forbidden:

        await ctx.send(
            "I don't have permission to unban users."
        )


@bot.command()
@is_admin()
async def nuke(ctx):

    old_channel = ctx.channel

    try:

        new_channel = await old_channel.clone(
            reason=f"Nuked by {ctx.author}"
        )

        await new_channel.edit(
            position=old_channel.position
        )

        await old_channel.delete(
            reason=f"Nuked by {ctx.author}"
        )

        await new_channel.send(
            f"Channel nuked by {ctx.author.mention}."
        )

    except discord.Forbidden:

        await ctx.send(
            "I don't have permission to nuke this channel."
        )


@bot.command()
@is_admin()
async def clone(ctx):

    old_channel = ctx.channel

    try:

        new_channel = await old_channel.clone(
            reason=f"Cloned by {ctx.author}"
        )

        await new_channel.edit(
            position=old_channel.position
        )

        await old_channel.delete(
            reason=f"Cloned by {ctx.author}"
        )

        await new_channel.send(
            f"Channel cloned by {ctx.author.mention}."
        )

    except discord.Forbidden:

        await ctx.send(
            "I don't have permission to clone this channel."
        )


@bot.command()
@is_admin()
async def purge(
    ctx,
    amount: int
):

    if amount < 1:

        await ctx.send(
            "Amount must be greater than zero."
        )

        return

    if amount > 1000:

        await ctx.send(
            "Maximum purge amount is 1000."
        )

        return

    deleted = await ctx.channel.purge(
        limit=amount + 1
    )

    count = max(
        len(deleted) - 1,
        0
    )

    message = await ctx.send(
        f"Deleted **{count}** messages."
    )

    await asyncio.sleep(3)

    try:
        await message.delete()
    except discord.HTTPException:
        pass


@bot.command()
@has_manage_messages()
async def snipe(ctx):

    messages = snipes[
        ctx.guild.id
    ][
        ctx.channel.id
    ]

    if not messages:

        await ctx.send(
            "There is no deleted message to show."
        )

        return

    data = messages[-1]

    await ctx.send(
        f"**{data['author']}** deleted:\n"
        f"{data['content'] or '[No text content]'}"
    )


# =========================================================
# SNIPE EVENT
# =========================================================

@bot.event
async def on_message_delete(message):

    if not message.guild:
        return

    if message.author.bot:
        return

    snipes[
        message.guild.id
    ][
        message.channel.id
    ].append({
        "author": message.author,
        "content": message.content
    })


# =========================================================
# GREET
# =========================================================

@bot.group(
    name="greet",
    invoke_without_command=True
)
@is_admin()
async def greet(ctx):

    await ctx.send(
        "Use `.help greet` to see greet commands."
    )


@greet.command(name="delafter")
@is_admin()
async def greet_delafter(
    ctx,
    seconds: int
):

    if seconds < 0:

        await ctx.send(
            "Seconds cannot be negative."
        )

        return

    get_settings(
        ctx.guild.id
    )["greet"]["delafter"] = seconds

    await ctx.send(
        f"Greet messages will delete after "
        f"**{seconds} seconds**."
    )


@greet.command(name="message")
@is_admin()
async def greet_message(
    ctx,
    *,
    message
):

    get_settings(
        ctx.guild.id
    )["greet"]["message"] = message

    await ctx.send(
        "Greet message updated."
    )


@greet.command(name="variables")
@is_admin()
async def greet_variables(ctx):

    await ctx.send(
        "\n".join(
            f"`{x}`"
            for x in ALL_VARIABLES
        )
    )


@greet.command(name="setchannel")
@is_admin()
async def greet_setchannel(
    ctx,
    channel: discord.TextChannel
):

    settings = get_settings(
        ctx.guild.id
    )["greet"]

    if channel.id in settings["channels"]:

        await ctx.send(
            "That channel is already configured."
        )

        return

    if len(settings["channels"]) >= MAX_GREET_CHANNELS:

        await ctx.send(
            f"You can only have {MAX_GREET_CHANNELS} "
            f"greet channels."
        )

        return

    settings["channels"].append(
        channel.id
    )

    await ctx.send(
        f"{channel.mention} added to greet channels."
    )


@greet.command(name="removechannel")
@is_admin()
async def greet_removechannel(
    ctx,
    channel: discord.TextChannel
):

    settings = get_settings(
        ctx.guild.id
    )["greet"]

    if channel.id not in settings["channels"]:

        await ctx.send(
            "That channel is not configured."
        )

        return

    settings["channels"].remove(
        channel.id
    )

    await ctx.send(
        f"{channel.mention} removed."
    )


@greet.command(name="reset")
@is_admin()
async def greet_reset(ctx):

    get_settings(
        ctx.guild.id
    )["greet"] = default_settings()["greet"]

    await ctx.send(
        "Greet settings reset."
    )


# =========================================================
# WELCOME
# =========================================================

@bot.group(
    name="welcome",
    invoke_without_command=True
)
@is_admin()
async def welcome(ctx):

    await ctx.send(
        "Use `.help welcome` to see welcome commands."
    )


@welcome.command(name="channel")
@is_admin()
async def welcome_channel(
    ctx,
    channel: discord.TextChannel
):

    get_settings(
        ctx.guild.id
    )["welcome"]["channel"] = channel.id

    await ctx.send(
        f"Welcome channel set to {channel.mention}."
    )


@welcome.command(name="message")
@is_admin()
async def welcome_message(
    ctx,
    *,
    message
):

    get_settings(
        ctx.guild.id
    )["welcome"]["message"] = message

    await ctx.send(
        "Welcome message updated."
    )


@welcome.command(name="variables")
@is_admin()
async def welcome_variables(ctx):

    await ctx.send(
        "\n".join(
            f"`{x}`"
            for x in ALL_VARIABLES
        )
    )


@welcome.command(name="reset")
@is_admin()
async def welcome_reset(ctx):

    get_settings(
        ctx.guild.id
    )["welcome"] = default_settings()["welcome"]

    await ctx.send(
        "Welcome settings reset."
    )


# =========================================================
# LEVEL
# =========================================================

@bot.command(name="lvl")
async def lvl(
    ctx,
    member: discord.Member = None
):

    member = member or ctx.author

    data = level_data[
        ctx.guild.id
    ][
        member.id
    ]

    current = data["messages"]
    level_value = data["level"]

    progress = current % MESSAGES_PER_LEVEL

    remaining = (
        MESSAGES_PER_LEVEL - progress
        if progress
        else MESSAGES_PER_LEVEL
    )

    await ctx.send(
        f"**{member.display_name}**\n"
        f"Level: **{level_value}**\n"
        f"Messages: **{current}**\n"
        f"Next level: **{remaining}** messages"
    )


@bot.group(
    name="level",
    invoke_without_command=True
)
@is_admin()
async def level(ctx):

    await ctx.send(
        "Use `.help level` to see level commands."
    )


@level.command(name="channel")
@is_admin()
async def level_channel(
    ctx,
    channel: discord.TextChannel
):

    get_settings(
        ctx.guild.id
    )["level"]["channel"] = channel.id

    await ctx.send(
        f"Level channel set to {channel.mention}."
    )


@level.command(name="lb")
@is_admin()
async def level_lb(ctx):

    entries = []

    for user_id, data in level_data[
        ctx.guild.id
    ].items():

        member = ctx.guild.get_member(
            user_id
        )

        if member:

            entries.append(
                (
                    member,
                    data["level"],
                    data["messages"]
                )
            )

    entries.sort(
        key=lambda x: (
            x[1],
            x[2]
        ),
        reverse=True
    )

    entries = entries[:3]

    if not entries:

        await ctx.send(
            "No level data yet."
        )

        return

    lines = [
        "**Level Leaderboard**",
        ""
    ]

    for position, (
        member,
        level_value,
        messages
    ) in enumerate(entries, 1):

        lines.append(
            f"**#{position}** "
            f"{member.mention} — "
            f"Level **{level_value}** "
            f"({messages} messages)"
        )

    await ctx.send(
        "\n".join(lines)
    )


@level.command(name="lbreset")
@is_admin()
async def level_lbreset(ctx):

    level_data[
        ctx.guild.id
    ].clear()

    await ctx.send(
        "All level data has been reset."
    )


@level.command(name="message")
@is_admin()
async def level_message(
    ctx,
    *,
    message
):

    get_settings(
        ctx.guild.id
    )["level"]["message"] = message

    await ctx.send(
        "Level-up message updated."
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
        "{server_members}"
    ]

    await ctx.send(
        "\n".join(
            f"`{x}`"
            for x in variables
        )
    )


# =========================================================
# INVITES
# =========================================================

def get_invite_data(
    guild_id,
    user_id
):
    return invite_data[
        guild_id
    ][
        user_id
    ]


@bot.command(name="i")
async def invite_info(
    ctx,
    member: discord.Member = None
):

    member = member or ctx.author

    data = get_invite_data(
        ctx.guild.id,
        member.id
    )

    await ctx.send(
        f"**Invite log**\n\n"
        f"**{member.display_name}**\n"
        f"Invites: **{data['invites']}**\n"
        f"Joins: **{data['joins']}**\n"
        f"Left: **{data['leaves']}**\n"
        f"Fake: **{data['fakes']}**\n"
        f"Rejoins: **{data['rejoins']}**\n\n"
        f"Requested by {ctx.author.mention} "
        f"• {discord.utils.format_dt(now_utc(), 'R')}"
    )


@bot.group(
    name="lb",
    invoke_without_command=True
)
async def lb(ctx):

    await ctx.send(
        "Use `.lb i` for the invite leaderboard."
    )


@lb.command(name="i")
async def invite_lb(ctx):

    entries = []

    for user_id, data in invite_data[
        ctx.guild.id
    ].items():

        member = ctx.guild.get_member(
            user_id
        )

        if member:

            entries.append(
                (
                    member,
                    data
                )
            )

    entries.sort(
        key=lambda x: (
            x[1]["invites"],
            x[1]["joins"]
        ),
        reverse=True
    )

    entries = entries[:10]

    if not entries:

        await ctx.send(
            "No invite data yet."
        )

        return

    lines = [
        "**Invite Leaderboard**",
        ""
    ]

    for position, (
        member,
        data
    ) in enumerate(entries, 1):

        lines.append(
            f"**#{position}** "
            f"{member.mention} — "
            f"**{data['invites']} invites** "
            f"| Joins: {data['joins']} "
            f"| Left: {data['leaves']} "
            f"| Fake: {data['fakes']} "
            f"| Rejoins: {data['rejoins']}"
        )

    await ctx.send(
        "\n".join(lines)
    )


@bot.command(name="lbreset")
@is_admin()
async def lbreset(ctx):

    invite_data[
        ctx.guild.id
    ].clear()

    member_inviter[
        ctx.guild.id
    ].clear()

    await ctx.send(
        "Invite data has been reset."
    )


# =========================================================
# GENERAL
# =========================================================

@bot.command(name="avatar")
async def avatar(
    ctx,
    member: discord.Member = None
):

    member = member or ctx.author

    embed = discord.Embed(
        title=f"{member.display_name}'s Avatar"
    )

    embed.set_image(
        url=member.display_avatar.url
    )

    await ctx.send(
        embed=embed
    )


@bot.command(name="banner")
async def banner(
    ctx,
    member: discord.Member = None
):

    member = member or ctx.author

    try:

        user = await bot.fetch_user(
            member.id
        )

        if not user.banner:

            await ctx.send(
                "This user does not have a banner."
            )

            return

        embed = discord.Embed(
            title=f"{member.display_name}'s Banner"
        )

        embed.set_image(
            url=user.banner.url
        )

        await ctx.send(
            embed=embed
        )

    except discord.HTTPException:

        await ctx.send(
            "Unable to fetch this user's banner."
        )


@bot.command(name="srvlogo")
async def srvlogo(ctx):

    if not ctx.guild.icon:

        await ctx.send(
            "This server does not have a server icon."
        )

        return

    embed = discord.Embed(
        title=f"{ctx.guild.name} Logo"
    )

    embed.set_image(
        url=ctx.guild.icon.url
    )

    await ctx.send(
        embed=embed
    )


@bot.command(name="srvbanner")
async def srvbanner(ctx):

    if not ctx.guild.banner:

        await ctx.send(
            "This server does not have a server banner."
        )

        return

    embed = discord.Embed(
        title=f"{ctx.guild.name} Banner"
    )

    embed.set_image(
        url=ctx.guild.banner.url
    )

    await ctx.send(
        embed=embed
    )


@bot.command(name="profile")
async def profile(ctx):

    member = ctx.author

    roles = [
        role.mention
        for role in member.roles[1:]
    ]

    role_text = (
        ", ".join(roles)
        if roles
        else "None"
    )

    embed = discord.Embed(
        title=f"{member.display_name}'s Profile"
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="Username",
        value=str(member),
        inline=False
    )

    embed.add_field(
        name="User ID",
        value=str(member.id),
        inline=False
    )

    embed.add_field(
        name="Account Created",
        value=format_discord_time(
            member.created_at
        ),
        inline=False
    )

    embed.add_field(
        name="Joined Server",
        value=(
            format_discord_time(member.joined_at)
            if member.joined_at
            else "Unknown"
        ),
        inline=False
    )

    embed.add_field(
        name="Roles",
        value=role_text,
        inline=False
    )

    await ctx.send(
        embed=embed
    )


@bot.command(name="si")
async def server_info(ctx):

    guild = ctx.guild

    embed = discord.Embed(
        title=f"{guild.name} Server Information"
    )

    if guild.icon:
        embed.set_thumbnail(
            url=guild.icon.url
        )

    embed.add_field(
        name="Server ID",
        value=str(guild.id),
        inline=False
    )

    embed.add_field(
        name="Owner",
        value=(
            guild.owner.mention
            if guild.owner
            else "Unknown"
        ),
        inline=False
    )

    embed.add_field(
        name="Members",
        value=str(
            guild.member_count or 0
        ),
        inline=True
    )

    embed.add_field(
        name="Channels",
        value=str(
            len(guild.channels)
        ),
        inline=True
    )

    embed.add_field(
        name="Roles",
        value=str(
            len(guild.roles)
        ),
        inline=True
    )

    embed.add_field(
        name="Created",
        value=format_discord_time(
            guild.created_at
        ),
        inline=False
    )

    embed.add_field(
        name="Verification Level",
        value=str(
            guild.verification_level
        ),
        inline=False
    )

    await ctx.send(
        embed=embed
    )


# =========================================================
# TIMERS
# =========================================================

async def timer_runner(
    guild_id,
    timer_name
):

    timer = timers[
        guild_id
    ].get(
        timer_name
    )

    if not timer:
        return

    while True:

        if timer["paused"]:
            await asyncio.sleep(1)
            continue

        remaining = (
            timer["end_at"]
            - now_utc()
        ).total_seconds()

        if remaining <= 0:
            break

        await asyncio.sleep(
            min(
                remaining,
                1
            )
        )

        if timer_name not in timers[guild_id]:
            return

        timer = timers[
            guild_id
        ].get(
            timer_name
        )

        if not timer:
            return

    if timer_name not in timers[guild_id]:
        return

    timer = timers[
        guild_id
    ].pop(
        timer_name,
        None
    )

    if timer:

        channel = bot.get_channel(
            timer["channel_id"]
        )

        if channel:

            try:

                await channel.send(
                    f"Timer **{timer_name}** has ended."
                )

            except discord.HTTPException:
                pass

    giveaway_tasks.pop(
        f"timer:{guild_id}:{timer_name}",
        None
    )


@bot.command(name="tstart")
@is_admin()
async def timer_start(
    ctx,
    duration,
    *,
    name
):

    seconds = parse_duration(
        duration
    )

    if seconds is None:

        await ctx.send(
            "Invalid duration. Example: `.tstart 1h name`"
        )

        return

    if seconds <= 0:

        await ctx.send(
            "Duration must be greater than zero."
        )

        return

    if name in timers[
        ctx.guild.id
    ]:

        await ctx.send(
            "A timer with that name already exists."
        )

        return

    timer = {
        "name": name,
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "created_by": ctx.author.id,
        "started_at": now_utc(),
        "end_at": now_utc() + timedelta(
            seconds=seconds
        ),
        "remaining": seconds,
        "paused": False
    }

    timers[
        ctx.guild.id
    ][
        name
    ] = timer

    task = asyncio.create_task(
        timer_runner(
            ctx.guild.id,
            name
        )
    )

    giveaway_tasks[
        f"timer:{ctx.guild.id}:{name}"
    ] = task

    await ctx.send(
        f"Timer **{name}** started for "
        f"**{format_duration(seconds)}**."
    )


@bot.command(name="tend")
@is_admin()
async def timer_end(
    ctx,
    *,
    name
):

    timer = timers[
        ctx.guild.id
    ].pop(
        name,
        None
    )

    if not timer:

        await ctx.send(
            "That timer does not exist."
        )

        return

    task_key = (
        f"timer:{ctx.guild.id}:{name}"
    )

    task = giveaway_tasks.pop(
        task_key,
        None
    )

    if task:
        task.cancel()

    await ctx.send(
        f"Timer **{name}** ended."
    )


@bot.command(name="tpause")
@is_admin()
async def timer_pause(
    ctx,
    *,
    name
):

    timer = timers[
        ctx.guild.id
    ].get(
        name
    )

    if not timer:

        await ctx.send(
            "That timer does not exist."
        )

        return

    if timer["paused"]:

        await ctx.send(
            f"Timer **{name}** is already paused."
        )

        return

    timer["remaining"] = max(
        0,
        (
            timer["end_at"]
            - now_utc()
        ).total_seconds()
    )

    timer["paused"] = True

    await ctx.send(
        f"Timer **{name}** paused."
    )


# =========================================================
# GIVEAWAYS
# =========================================================

class GiveawayView(discord.ui.View):

    def __init__(
        self,
        giveaway_id
    ):
        super().__init__(
            timeout=None
        )

        self.giveaway_id = giveaway_id

        self.join_button = discord.ui.Button(
            label="Join Giveaway",
            style=discord.ButtonStyle.secondary,
            custom_id=f"giveaway_join:{giveaway_id}"
        )

        self.join_button.callback = self.join_callback

        self.add_item(
            self.join_button
        )

    async def join_callback(
        self,
        interaction
    ):

        giveaway = giveaways.get(
            self.giveaway_id
        )

        if not giveaway:

            await interaction.response.send_message(
                "This giveaway no longer exists.",
                ephemeral=True
            )

            return

        if giveaway["ended"]:

            await interaction.response.send_message(
                "This giveaway has already ended.",
                ephemeral=True
            )

            return

        if interaction.user.id in giveaway_blacklist[
            interaction.guild.id
        ]:

            await interaction.response.send_message(
                "You are blacklisted from giveaways.",
                ephemeral=True
            )

            return

        if interaction.user.id in giveaway["entries"]:

            giveaway["entries"].remove(
                interaction.user.id
            )

            await interaction.response.send_message(
                "You have left the giveaway.",
                ephemeral=True
            )

        else:

            giveaway["entries"].add(
                interaction.user.id
            )

            await interaction.response.send_message(
                "You have joined the giveaway.",
                ephemeral=True
            )


def giveaway_embed(
    giveaway,
    guild
):

    end_at = giveaway["end_at"]

    embed = discord.Embed(
        title=giveaway["reward"],
        description=(
            f"Winners: {giveaway['winners']}\n"
            f"Ends: {format_discord_relative(end_at)} "
            f"({format_discord_time(end_at)})\n"
            f"Hosted by: {giveaway['host'].mention}\n\n"
            f"Click On Button Below To Participate"
        )
    )

    embed.set_footer(
        text=f"Ends at • {end_at.strftime('%A, %B %d, %Y at %I:%M %p')}"
    )

    return embed


async def update_giveaway_message(
    giveaway
):

    channel = bot.get_channel(
        giveaway["channel_id"]
    )

    if not channel:
        return

    try:

        message = await channel.fetch_message(
            giveaway["message_id"]
        )

        await message.edit(
            embed=giveaway_embed(
                giveaway,
                channel.guild
            ),
            view=GiveawayView(
                giveaway["id"]
            )
        )

    except (
        discord.NotFound,
        discord.HTTPException
    ):
        pass


async def finish_giveaway(
    giveaway_id
):

    giveaway = giveaways.get(
        giveaway_id
    )

    if not giveaway:
        return

    if giveaway["ended"]:
        return

    remaining = (
        giveaway["end_at"]
        - now_utc()
    ).total_seconds()

    if remaining > 0:

        await asyncio.sleep(
            remaining
        )

    giveaway = giveaways.get(
        giveaway_id
    )

    if not giveaway:
        return

    if giveaway["ended"]:
        return

    giveaway["ended"] = True

    entries = list(
        giveaway["entries"]
    )

    winners_amount = min(
        giveaway["winners"],
        len(entries)
    )

    selected_winners = []

    if winners_amount > 0:

        selected_winners = random.sample(
            entries,
            winners_amount
        )

    channel = bot.get_channel(
        giveaway["channel_id"]
    )

    if not channel:
        return

    winner_mentions = []

    for user_id in selected_winners:

        member = channel.guild.get_member(
            user_id
        )

        if member:
            winner_mentions.append(
                member.mention
            )

    if winner_mentions:

        winner_text = ", ".join(
            winner_mentions
        )

        result = (
            f"Giveaway ended.\n\n"
            f"Reward: **{giveaway['reward']}**\n"
            f"Winner{'s' if len(winner_mentions) != 1 else ''}: "
            f"{winner_text}"
        )

    else:

        result = (
            f"Giveaway ended.\n\n"
            f"Reward: **{giveaway['reward']}**\n"
            f"There were no eligible participants."
        )

    try:

        message = await channel.fetch_message(
            giveaway["message_id"]
        )

        await message.edit(
            content=result,
            embed=None,
            view=None
        )

    except (
        discord.NotFound,
        discord.HTTPException
    ):

        try:
            await channel.send(
                result
            )
        except discord.HTTPException:
            pass

    giveaway_tasks.pop(
        f"giveaway:{giveaway_id}",
        None
    )


@bot.command(name="gstart")
@is_admin()
async def giveaway_start(
    ctx,
    duration,
    winners: int,
    *,
    reward
):

    seconds = parse_giveaway_duration(
        duration
    )

    if seconds is None:

        await ctx.send(
            "Invalid duration. Example: `.gstart 1h 1 Nitro`"
        )

        return

    if seconds <= 0:

        await ctx.send(
            "Giveaway duration must be greater than zero."
        )

        return

    if winners < 1:

        await ctx.send(
            "Winner amount must be at least 1."
        )

        return

    if not reward.strip():

        await ctx.send(
            "Giveaway reward cannot be empty."
        )

        return

    end_at = now_utc() + timedelta(
        seconds=seconds
    )

    giveaway_id = (
        f"{ctx.guild.id}-"
        f"{ctx.channel.id}-"
        f"{int(now_utc().timestamp() * 1000)}"
    )

    giveaway = {
        "id": giveaway_id,
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "message_id": None,
        "host": ctx.author,
        "host_id": ctx.author.id,
        "winners": winners,
        "reward": reward,
        "end_at": end_at,
        "entries": set(),
        "ended": False
    }

    giveaways[
        giveaway_id
    ] = giveaway

    view = GiveawayView(
        giveaway_id
    )

    message = await ctx.send(
        embed=giveaway_embed(
            giveaway,
            ctx.guild
        ),
        view=view
    )

    giveaway["message_id"] = message.id

    task = asyncio.create_task(
        finish_giveaway(
            giveaway_id
        )
    )

    giveaway_tasks[
        f"giveaway:{giveaway_id}"
    ] = task


@bot.command(name="gend")
@is_admin()
async def giveaway_end(
    ctx,
    message_id: int
):

    giveaway = None
    giveaway_id = None

    for gid, data in giveaways.items():

        if data["message_id"] == message_id:
            giveaway = data
            giveaway_id = gid
            break

    if not giveaway:

        await ctx.send(
            "No active giveaway was found with that message ID."
        )

        return

    if giveaway["guild_id"] != ctx.guild.id:

        await ctx.send(
            "That giveaway belongs to another server."
        )

        return

    if giveaway["ended"]:

        await ctx.send(
            "That giveaway has already ended."
        )

        return

    task = giveaway_tasks.get(
        f"giveaway:{giveaway_id}"
    )

    if task:
        task.cancel()

    giveaway["end_at"] = now_utc()

    await finish_giveaway(
        giveaway_id
    )


@bot.command(name="gblacklist")
@is_admin()
async def giveaway_blacklist_command(
    ctx,
    member: discord.Member
):

    blacklist = giveaway_blacklist[
        ctx.guild.id
    ]

    if member.id in blacklist:

        blacklist.remove(
            member.id
        )

        await ctx.send(
            f"{member.mention} has been removed "
            f"from the giveaway blacklist."
        )

    else:

        blacklist.add(
            member.id
        )

        await ctx.send(
            f"{member.mention} has been added "
            f"to the giveaway blacklist."
        )


# =========================================================
# INVITE CACHE
# =========================================================

async def refresh_invites(
    guild
):

    try:

        invites = await guild.invites()

        invite_cache[
            guild.id
        ] = {
            invite.code: {
                "uses": invite.uses,
                "inviter_id": (
                    invite.inviter.id
                    if invite.inviter
                    else None
                )
            }
            for invite in invites
        }

    except (
        discord.Forbidden,
        discord.HTTPException
    ):

        invite_cache[
            guild.id
        ] = {}


# =========================================================
# MEMBER JOIN
# =========================================================

@bot.event
async def on_member_join(
    member
):

    guild = member.guild

    old_invites = invite_cache[
        guild.id
    ].copy()

    inviter_id = None

    try:

        invites = await guild.invites()

        for invite in invites:

            old = old_invites.get(
                invite.code
            )

            if old and invite.uses > old["uses"]:

                inviter_id = old[
                    "inviter_id"
                ]

                break

        invite_cache[
            guild.id
        ] = {
            invite.code: {
                "uses": invite.uses,
                "inviter_id": (
                    invite.inviter.id
                    if invite.inviter
                    else None
                )
            }
            for invite in invites
        }

    except (
        discord.Forbidden,
        discord.HTTPException
    ):

        inviter_id = None

    # -----------------------------------------------------
    # INVITES
    # -----------------------------------------------------

    if inviter_id and inviter_id != member.id:

        data = get_invite_data(
            guild.id,
            inviter_id
        )

        data["invites"] += 1
        data["joins"] += 1

        account_age = (
            now_utc()
            - member.created_at
        ).days

        if account_age < FAKE_ACCOUNT_DAYS:
            data["fakes"] += 1

        if member.id in member_inviter[
            guild.id
        ]:
            data["rejoins"] += 1

        member_inviter[
            guild.id
        ][
            member.id
        ] = inviter_id

    # -----------------------------------------------------
    # GREET
    # -----------------------------------------------------

    settings = get_settings(
        guild.id
    )

    greet_settings = settings[
        "greet"
    ]

    for channel_id in list(
        greet_settings["channels"]
    ):

        channel = guild.get_channel(
            channel_id
        )

        if not channel:
            continue

        try:

            content = replace_variables(
                greet_settings["message"],
                member=member,
                guild=guild,
                channel=channel
            )

            sent = await channel.send(
                content
            )

            delay = greet_settings[
                "delafter"
            ]

            if delay > 0:

                await asyncio.sleep(
                    delay
                )

                try:
                    await sent.delete()
                except discord.HTTPException:
                    pass

        except discord.HTTPException:
            pass

    # -----------------------------------------------------
    # WELCOME
    # -----------------------------------------------------

    welcome_settings = settings[
        "welcome"
    ]

    channel_id = welcome_settings[
        "channel"
    ]

    if channel_id:

        channel = guild.get_channel(
            channel_id
        )

        if channel:

            content = replace_variables(
                welcome_settings["message"],
                member=member,
                guild=guild,
                channel=channel
            )

            embed = discord.Embed(
                title="Welcome",
                description=content
            )

            embed.set_author(
                name=member.display_name,
                icon_url=member.display_avatar.url
            )

            embed.set_thumbnail(
                url=member.display_avatar.url
            )

            embed.set_footer(
                text=f"Member #{guild.member_count}"
            )

            try:

                await channel.send(
                    embed=embed
                )

            except discord.HTTPException:
                pass


# =========================================================
# MEMBER LEAVE
# =========================================================

@bot.event
async def on_member_remove(
    member
):

    guild = member.guild

    inviter_id = member_inviter[
        guild.id
    ].get(
        member.id
    )

    if inviter_id:

        data = get_invite_data(
            guild.id,
            inviter_id
        )

        data["leaves"] += 1

        if data["invites"] > 0:
            data["invites"] -= 1


# =========================================================
# LEVEL MESSAGE TRACKING
# =========================================================

@bot.event
async def on_message(
    message
):

    if message.author.bot:
        return

    if message.guild:

        data = level_data[
            message.guild.id
        ][
            message.author.id
        ]

        data["messages"] += 1

        old_level = data["level"]

        new_level = (
            data["messages"]
            // MESSAGES_PER_LEVEL
        )

        if new_level > old_level:

            data["level"] = new_level

            settings = get_settings(
                message.guild.id
            )["level"]

            channel_id = settings[
                "channel"
            ]

            if channel_id:

                channel = message.guild.get_channel(
                    channel_id
                )

                if channel:

                    content = settings[
                        "message"
                    ]

                    content = content.replace(
                        "{user}",
                        message.author.mention
                    )

                    content = content.replace(
                        "{user_name}",
                        message.author.name
                    )

                    content = content.replace(
                        "{user_id}",
                        str(message.author.id)
                    )

                    content = content.replace(
                        "{user_level}",
                        str(new_level)
                    )

                    content = content.replace(
                        "{level}",
                        str(new_level)
                    )

                    content = content.replace(
                        "{user_messages}",
                        str(data["messages"])
                    )

                    content = content.replace(
                        "{messages}",
                        str(data["messages"])
                    )

                    content = replace_variables(
                        content,
                        member=message.author,
                        guild=message.guild,
                        channel=channel
                    )

                    try:

                        await channel.send(
                            content
                        )

                    except discord.HTTPException:
                        pass

    await bot.process_commands(
        message
    )


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():

    print(
        f"Logged in as {bot.user} ({bot.user.id})"
    )

    print(
        "Dream Land Security is online."
    )

    for guild in bot.guilds:

        await refresh_invites(
            guild
        )


# =========================================================
# ERROR HANDLER
# =========================================================

@bot.event
async def on_command_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.CommandNotFound
    ):
        return

    if isinstance(
        error,
        commands.CheckFailure
    ):

        await ctx.send(
            "You don't have permission to use this command."
        )

        return

    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):

        await ctx.send(
            f"Missing argument: `{error.param.name}`"
        )

        return

    if isinstance(
        error,
        commands.MemberNotFound
    ):

        await ctx.send(
            "Member not found."
        )

        return

    if isinstance(
        error,
        commands.ChannelNotFound
    ):

        await ctx.send(
            "Channel not found."
        )

        return

    if isinstance(
        error,
        commands.BadArgument
    ):

        await ctx.send(
            "Invalid argument."
        )

        return

    print(
        f"Command error: {error}"
    )


# =========================================================
# START
# =========================================================

if not TOKEN:

    raise RuntimeError(
        "BOT_TOKEN is not set."
    )

bot.run(
    TOKEN
)
