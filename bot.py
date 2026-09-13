import os
import re
import random
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Select, Button


# =========================================================
# SETUP
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")
PREFIX = "."

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.presences = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)


# =========================================================
# IN-MEMORY DATA
# =========================================================

guild_settings = defaultdict(lambda: {
    "greet": {
        "channels": [],
        "message": "Welcome {user} to {server}!",
        "delete_after": 0
    },
    "welcome": {
        "channels": [],
        "message": "Welcome {user} to {server}!",
    },
    "level": {
        "channel": None,
        "message": "Congratulations {user}, you reached level {level}!"
    }
})

level_data = defaultdict(lambda: {
    "messages": 0,
    "level": 0
})

invite_data = defaultdict(lambda: {
    "joins": 0,
    "left": 0,
    "fake": 0,
    "rejoins": 0,
    "invites": 0
})

invite_cache = {}
member_inviter = {}

snipes = defaultdict(lambda: deque(maxlen=20))

timers = {}
timer_tasks = {}

giveaways = {}
giveaway_tasks = {}
giveaway_blacklist = defaultdict(set)


# =========================================================
# HELPERS
# =========================================================

def is_admin():
    async def predicate(ctx):
        return (
            ctx.guild is not None
            and ctx.author.guild_permissions.administrator
        )

    return commands.check(predicate)


def get_member_text(member):
    return f"{member.mention}"


def replace_variables(message, member=None, guild=None, level=None):
    if guild is None and member:
        guild = member.guild

    if guild is None:
        return message

    if member:
        values = {
            "{user}": member.mention,
            "{username}": member.name,
            "{userid}": str(member.id),
            "{user_id}": str(member.id),
            "{server}": guild.name,
            "{serverid}": str(guild.id),
            "{server_id}": str(guild.id),
        }
    else:
        values = {
            "{server}": guild.name,
            "{serverid}": str(guild.id),
            "{server_id}": str(guild.id),
        }

    if level is not None:
        values["{level}"] = str(level)

    for key, value in values.items():
        message = message.replace(key, value)

    return message


def format_duration(seconds):
    seconds = max(0, int(seconds))

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def format_end_time(dt):
    return dt.strftime("%-I:%M %p, %-d %B %Y")


def parse_duration(value):
    value = value.lower().strip()

    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(s|m|h|d|w)",
        value
    )

    if not match:
        raise ValueError(
            "Invalid duration. Use formats such as `30m`, `1h`, `1d`."
        )

    amount = float(match.group(1))
    unit = match.group(2)

    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800
    }

    return int(amount * multipliers[unit])


async def send_card(
    destination,
    title,
    description,
    *,
    components=None,
    ephemeral=False
):
    view = LayoutView(timeout=None)

    container = Container()

    container.add_item(
        TextDisplay(
            f"## {title}"
        )
    )

    if description:
        container.add_item(Separator())
        container.add_item(
            TextDisplay(description)
        )

    if components:
        for component in components:
            container.add_item(component)

    view.add_item(container)

    if isinstance(destination, discord.Interaction):
        if destination.response.is_done():
            return await destination.followup.send(
                view=view,
                ephemeral=ephemeral
            )

        return await destination.response.send_message(
            view=view,
            ephemeral=ephemeral
        )

    return await destination.send(view=view)


async def edit_card(
    interaction,
    title,
    description,
    *,
    components=None
):
    view = LayoutView(timeout=None)

    container = Container()

    container.add_item(
        TextDisplay(f"## {title}")
    )

    if description:
        container.add_item(Separator())
        container.add_item(TextDisplay(description))

    if components:
        for component in components:
            container.add_item(component)

    view.add_item(container)

    await interaction.response.edit_message(view=view)


# =========================================================
# COMPONENTS V2 HELP
# =========================================================

HELP_CATEGORIES = {
    "Moderation": [
        ".ban @user",
        ".kick @user",
        ".mute @user [duration]",
        ".lock",
        ".unlock",
        ".hide",
        ".unhide",
        ".unban [userid]",
        ".nuke",
        ".clone",
        ".purge [amount]",
        ".snipe"
    ],
    "Greet": [
        ".greet delafter [seconds]",
        ".greet message [message]",
        ".greet variables",
        ".greet setchannel #channel",
        ".greet removechannel #channel",
        ".greet reset"
    ],
    "Welcome": [
        ".welcome channel #channel",
        ".welcome message [message]",
        ".welcome variables",
        ".welcome reset"
    ],
    "Level": [
        ".lvl [user]",
        ".level channel #channel",
        ".level lb",
        ".level lbreset",
        ".lvl message [message]",
        ".lvl variables"
    ],
    "Invites": [
        ".i [user]",
        ".lb i",
        ".lbreset"
    ],
    "General": [
        ".avatar [user]",
        ".banner [user]",
        ".srvlogo",
        ".srvbanner",
        ".profile",
        ".si",
        ".tstart [time] [name]",
        ".tend [name]",
        ".tpause [name]"
    ],
    "Giveaway": [
        ".gstart [time] [winners] [reward]",
        ".gend [message id]",
        ".gblacklist @user"
    ]
}


def help_text(category=None):
    if category is None:
        total = sum(len(x) for x in HELP_CATEGORIES.values())

        return (
            "Hey, I'm Dream Land Security\n\n"
            "My prefix for this server is `.`\n"
            "Type `.help [context]` for more\n\n"
            f"Total commands: {total}\n\n"
            "Modules\n"
            "Moderation\n"
            "Greet\n"
            "Welcome\n"
            "Level\n"
            "Invites\n"
            "General\n"
            "Giveaway\n\n"
            "Select a module to see its commands."
        )

    commands_list = HELP_CATEGORIES.get(category, [])

    return (
        f"Module: **{category}**\n\n"
        + "\n".join(f"`{command}`" for command in commands_list)
    )


class HelpSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=name,
                value=name
            )
            for name in HELP_CATEGORIES
        ]

        super().__init__(
            placeholder="Select a module",
            options=options,
            custom_id="dreamland_help_select"
        )

    async def callback(self, interaction):
        await edit_card(
            interaction,
            "Dream Land Security",
            help_text(self.values[0]),
            components=[
                ActionRow(HelpSelect()),
                ActionRow(HelpBack())
            ]
        )


class HelpBack(Button):
    def __init__(self):
        super().__init__(
            label="Back",
            style=discord.ButtonStyle.secondary,
            custom_id="dreamland_help_back"
        )

    async def callback(self, interaction):
        await edit_card(
            interaction,
            "Dream Land Security",
            help_text(),
            components=[
                ActionRow(HelpSelect())
            ]
        )


def make_help_view():
    return [
        ActionRow(HelpSelect())
    ]


# =========================================================
# BASIC EVENTS
# =========================================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print("Dream Land Security is ready.")


@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return

    snipes[message.channel.id].appendleft({
        "content": message.content,
        "author": message.author,
        "created_at": message.created_at
    })


# =========================================================
# HELP
# =========================================================

@bot.command(name="help")
async def help_command(ctx, category=None):
    if category:
        category = category.lower()

        found = None

        for name in HELP_CATEGORIES:
            if name.lower() == category:
                found = name
                break

        if found:
            await send_card(
                ctx,
                "Dream Land Security",
                help_text(found),
                components=[
                    ActionRow(HelpSelect()),
                    ActionRow(HelpBack())
                ]
            )
            return

    await send_card(
        ctx,
        "Dream Land Security",
        help_text(),
        components=make_help_view()
    )


# =========================================================
# MODERATION
# =========================================================

@bot.command()
@is_admin()
async def ban(ctx, member: discord.Member, *, reason=None):
    await member.ban(reason=reason)

    await send_card(
        ctx,
        "User Banned",
        f"{member.mention} has been banned."
    )


@bot.command()
@is_admin()
async def kick(ctx, member: discord.Member, *, reason=None):
    await member.kick(reason=reason)

    await send_card(
        ctx,
        "User Kicked",
        f"{member.mention} has been kicked."
    )


@bot.command()
@is_admin()
async def mute(ctx, member: discord.Member, duration=None):
    if not duration:
        await send_card(
            ctx,
            "Mute",
            "Usage: `.mute @user 1h`"
        )
        return

    try:
        seconds = parse_duration(duration)
    except ValueError as e:
        await send_card(
            ctx,
            "Mute",
            str(e)
        )
        return

    if seconds > 28 * 86400:
        await send_card(
            ctx,
            "Mute",
            "Discord timeouts cannot exceed 28 days."
        )
        return

    until = discord.utils.utcnow() + timedelta(seconds=seconds)

    await member.timeout(
        until,
        reason=f"Muted by {ctx.author}"
    )

    await send_card(
        ctx,
        "User Muted",
        f"{member.mention} has been muted for `{format_duration(seconds)}`."
    )


@bot.command()
@is_admin()
async def lock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await send_card(
        ctx,
        "Channel Locked",
        f"{ctx.channel.mention} has been locked."
    )


@bot.command()
@is_admin()
async def unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await send_card(
        ctx,
        "Channel Unlocked",
        f"{ctx.channel.mention} has been unlocked."
    )


@bot.command()
@is_admin()
async def hide(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = False

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await send_card(
        ctx,
        "Channel Hidden",
        f"{ctx.channel.mention} has been hidden."
    )


@bot.command()
@is_admin()
async def unhide(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await send_card(
        ctx,
        "Channel Visible",
        f"{ctx.channel.mention} is visible again."
    )


@bot.command()
@is_admin()
async def unban(ctx, user_id: int):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user)

        await send_card(
            ctx,
            "User Unbanned",
            f"{user.mention} has been unbanned."
        )

    except discord.NotFound:
        await send_card(
            ctx,
            "Unban Failed",
            "That user could not be found in the ban list."
        )


@bot.command()
@is_admin()
async def nuke(ctx):
    old_channel = ctx.channel

    new_channel = await old_channel.clone(
        name=old_channel.name,
        reason=f"Nuked by {ctx.author}"
    )

    await new_channel.edit(
        position=old_channel.position
    )

    await old_channel.delete(
        reason=f"Nuked by {ctx.author}"
    )

    await send_card(
        new_channel,
        "Channel Nuked",
        f"This channel was recreated by {ctx.author.mention}."
    )


@bot.command()
@is_admin()
async def clone(ctx):
    old_channel = ctx.channel

    new_channel = await old_channel.clone(
        name=old_channel.name,
        reason=f"Cloned by {ctx.author}"
    )

    await new_channel.edit(
        position=old_channel.position
    )

    await old_channel.delete(
        reason=f"Cloned by {ctx.author}"
    )

    await send_card(
        new_channel,
        "Channel Cloned",
        f"This channel was recreated by {ctx.author.mention}."
    )


@bot.command()
@is_admin()
async def purge(ctx, amount: int):
    if amount <= 0:
        await send_card(
            ctx,
            "Purge",
            "Amount must be greater than zero."
        )
        return

    deleted = await ctx.channel.purge(limit=amount)

    await send_card(
        ctx,
        "Messages Purged",
        f"Deleted `{len(deleted)}` messages."
    )


@bot.command()
async def snipe(ctx):
    messages = snipes.get(ctx.channel.id)

    if not messages:
        await send_card(
            ctx,
            "Snipe",
            "There are no deleted messages to show."
        )
        return

    data = messages[0]

    await send_card(
        ctx,
        "Deleted Message",
        (
            f"Author: {data['author'].mention}\n\n"
            f"{data['content'] or '[No text content]'}"
        )
    )


# =========================================================
# GREET
# =========================================================

@bot.group(name="greet", invoke_without_command=True)
async def greet(ctx):
    await send_card(
        ctx,
        "Greet",
        (
            "Use one of these commands:\n\n"
            "`.greet delafter [seconds]`\n"
            "`.greet message [message]`\n"
            "`.greet variables`\n"
            "`.greet setchannel #channel`\n"
            "`.greet removechannel #channel`\n"
            "`.greet reset`"
        )
    )


@greet.command(name="delafter")
@is_admin()
async def greet_delafter(ctx, seconds: int):
    guild_settings[ctx.guild.id]["greet"]["delete_after"] = max(0, seconds)

    await send_card(
        ctx,
        "Greet Settings",
        f"Greet messages will be deleted after `{seconds}` seconds."
    )


@greet.command(name="message")
@is_admin()
async def greet_message(ctx, *, message):
    guild_settings[ctx.guild.id]["greet"]["message"] = message

    await send_card(
        ctx,
        "Greet Message",
        "The greet message has been updated."
    )


@greet.command(name="variables")
async def greet_variables(ctx):
    await send_card(
        ctx,
        "Greet Variables",
        (
            "`{user}`\n"
            "`{username}`\n"
            "`{userid}`\n"
            "`{user_id}`\n"
            "`{server}`\n"
            "`{serverid}`\n"
            "`{server_id}`\n"
            "`{channel}`\n"
            "`{channel_id}`\n"
            "`{membercount}`\n"
            "`{created}`\n"
            "`{joined}`"
        )
    )


@greet.command(name="setchannel")
@is_admin()
async def greet_setchannel(ctx, channel: discord.TextChannel):
    channels = guild_settings[ctx.guild.id]["greet"]["channels"]

    if channel.id not in channels:
        if len(channels) >= 5:
            await send_card(
                ctx,
                "Greet",
                "You can configure a maximum of 5 greet channels."
            )
            return

        channels.append(channel.id)

    await send_card(
        ctx,
        "Greet Channel",
        f"{channel.mention} has been added to greet channels."
    )


@greet.command(name="removechannel")
@is_admin()
async def greet_removechannel(ctx, channel: discord.TextChannel):
    channels = guild_settings[ctx.guild.id]["greet"]["channels"]

    if channel.id in channels:
        channels.remove(channel.id)

    await send_card(
        ctx,
        "Greet Channel",
        f"{channel.mention} has been removed."
    )


@greet.command(name="reset")
@is_admin()
async def greet_reset(ctx):
    guild_settings[ctx.guild.id]["greet"] = {
        "channels": [],
        "message": "Welcome {user} to {server}!",
        "delete_after": 0
    }

    await send_card(
        ctx,
        "Greet Reset",
        "Greet settings have been reset."
    )


# =========================================================
# WELCOME
# =========================================================

@bot.group(name="welcome", invoke_without_command=True)
async def welcome(ctx):
    await send_card(
        ctx,
        "Welcome",
        (
            "Use one of these commands:\n\n"
            "`.welcome channel #channel`\n"
            "`.welcome message [message]`\n"
            "`.welcome variables`\n"
            "`.welcome reset`"
        )
    )


@welcome.command(name="channel")
@is_admin()
async def welcome_channel(ctx, channel: discord.TextChannel):
    channels = guild_settings[ctx.guild.id]["welcome"]["channels"]

    if channel.id not in channels:
        if len(channels) >= 5:
            await send_card(
                ctx,
                "Welcome",
                "You can configure a maximum of 5 welcome channels."
            )
            return

        channels.append(channel.id)

    await send_card(
        ctx,
        "Welcome Channel",
        f"{channel.mention} has been added."
    )


@welcome.command(name="message")
@is_admin()
async def welcome_message(ctx, *, message):
    guild_settings[ctx.guild.id]["welcome"]["message"] = message

    await send_card(
        ctx,
        "Welcome Message",
        "The welcome message has been updated."
    )


@welcome.command(name="variables")
async def welcome_variables(ctx):
    await send_card(
        ctx,
        "Welcome Variables",
        (
            "`{user}`\n"
            "`{username}`\n"
            "`{userid}`\n"
            "`{user_id}`\n"
            "`{server}`\n"
            "`{serverid}`\n"
            "`{server_id}`\n"
            "`{channel}`\n"
            "`{channel_id}`\n"
            "`{membercount}`\n"
            "`{created}`\n"
            "`{joined}`"
        )
    )


@welcome.command(name="reset")
@is_admin()
async def welcome_reset(ctx):
    guild_settings[ctx.guild.id]["welcome"] = {
        "channels": [],
        "message": "Welcome {user} to {server}!"
    }

    await send_card(
        ctx,
        "Welcome Reset",
        "Welcome settings have been reset."
    )


@bot.event
async def on_member_join(member):
    guild = member.guild

    data = invite_data[(guild.id, member.id)]

    data["joins"] += 1

    if (guild.id, member.id) in member_inviter:
        data["rejoins"] += 1

    if (
        datetime.now(timezone.utc) - member.created_at
    ).days < 15:
        data["fake"] += 1

    settings = guild_settings[guild.id]["welcome"]

    message = replace_variables(
        settings["message"],
        member,
        guild
    )

    for channel_id in settings["channels"]:
        channel = guild.get_channel(channel_id)

        if channel:
            await send_card(
                channel,
                "Welcome",
                message
            )


# =========================================================
# LEVEL
# =========================================================

@bot.command(name="lvl")
async def lvl(ctx, member: discord.Member = None):
    member = member or ctx.author

    data = level_data[(ctx.guild.id, member.id)]

    await send_card(
        ctx,
        "Level",
        (
            f"User: {member.mention}\n"
            f"Level: `{data['level']}`\n"
            f"Messages: `{data['messages']}`"
        )
    )


@bot.group(name="level", invoke_without_command=True)
async def level(ctx):
    await send_card(
        ctx,
        "Level",
        (
            "Use:\n\n"
            "`.level channel #channel`\n"
            "`.level lb`\n"
            "`.level lbreset`"
        )
    )


@level.command(name="channel")
@is_admin()
async def level_channel(ctx, channel: discord.TextChannel):
    guild_settings[ctx.guild.id]["level"]["channel"] = channel.id

    await send_card(
        ctx,
        "Level Channel",
        f"Level-up messages will be sent in {channel.mention}."
    )


@level.command(name="lb")
async def level_lb(ctx):
    users = []

    for (guild_id, user_id), data in level_data.items():
        if guild_id != ctx.guild.id:
            continue

        users.append(
            (user_id, data["level"], data["messages"])
        )

    users.sort(
        key=lambda x: (x[1], x[2]),
        reverse=True
    )

    lines = []

    for index, (user_id, lvl_value, messages) in enumerate(
        users[:3],
        start=1
    ):
        member = ctx.guild.get_member(user_id)
        name = member.mention if member else str(user_id)

        lines.append(
            f"{index}. {name} — Level `{lvl_value}` — `{messages}` messages"
        )

    if not lines:
        lines.append("No level data yet.")

    await send_card(
        ctx,
        "Level Leaderboard",
        "\n".join(lines)
    )


@level.command(name="lbreset")
@is_admin()
async def level_lbreset(ctx):
    for key in list(level_data.keys()):
        if key[0] == ctx.guild.id:
            del level_data[key]

    await send_card(
        ctx,
        "Level Leaderboard",
        "The level leaderboard has been reset."
    )


@bot.command(name="lvlmessage")
@is_admin()
async def lvl_message(ctx, *, message):
    guild_settings[ctx.guild.id]["level"]["message"] = message

    await send_card(
        ctx,
        "Level Message",
        "The level-up message has been updated."
    )


@bot.command(name="lvlvariables")
async def lvl_variables(ctx):
    await send_card(
        ctx,
        "Level Variables",
        (
            "`{user}`\n"
            "`{username}`\n"
            "`{userid}`\n"
            "`{server}`\n"
            "`{serverid}`\n"
            "`{level}`"
        )
    )


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.guild:
        key = (message.guild.id, message.author.id)

        level_data[key]["messages"] += 1

        messages = level_data[key]["messages"]

        if messages % 100 == 0:
            level_data[key]["level"] += 1

            level = level_data[key]["level"]

            channel_id = guild_settings[
                message.guild.id
            ]["level"]["channel"]

            if channel_id:
                channel = message.guild.get_channel(channel_id)

                if channel:
                    text = replace_variables(
                        guild_settings[
                            message.guild.id
                        ]["level"]["message"],
                        message.author,
                        message.guild,
                        level
                    )

                    await send_card(
                        channel,
                        "Level Up",
                        text
                    )

    await bot.process_commands(message)


# =========================================================
# INVITES
# =========================================================

async def cache_guild_invites(guild):
    try:
        invites = await guild.invites()

        invite_cache[guild.id] = {
            invite.code: invite.uses
            for invite in invites
        }

    except discord.Forbidden:
        invite_cache[guild.id] = {}


@bot.event
async def on_guild_join(guild):
    await cache_guild_invites(guild)


@bot.event
async def on_member_remove(member):
    key = (member.guild.id, member.id)

    if key in member_inviter:
        inviter_id = member_inviter[key]
        invite_data[
            (member.guild.id, inviter_id)
        ]["left"] += 1


@bot.command(name="i")
async def invite_info(ctx, member: discord.Member = None):
    member = member or ctx.author

    data = invite_data[
        (ctx.guild.id, member.id)
    ]

    await send_card(
        ctx,
        "Invite Log",
        (
            f"{member.mention}\n\n"
            f"Invites: `{data['invites']}`\n"
            f"Joins: `{data['joins']}`\n"
            f"Left: `{data['left']}`\n"
            f"Fake: `{data['fake']}`\n"
            f"Rejoins: `{data['rejoins']}`"
        )
    )


@bot.command(name="lb")
async def leaderboard(ctx, category=None):
    if category != "i":
        await send_card(
            ctx,
            "Leaderboard",
            "Use `.lb i` for the invite leaderboard."
        )
        return

    users = []

    for (guild_id, user_id), data in invite_data.items():
        if guild_id != ctx.guild.id:
            continue

        users.append(
            (user_id, data["invites"])
        )

    users.sort(
        key=lambda x: x[1],
        reverse=True
    )

    lines = []

    for index, (user_id, invites) in enumerate(
        users[:10],
        start=1
    ):
        member = ctx.guild.get_member(user_id)

        if member:
            name = member.mention
        else:
            name = str(user_id)

        lines.append(
            f"{index}. {name} — `{invites}` invites"
        )

    if not lines:
        lines.append("No invite data yet.")

    await send_card(
        ctx,
        "Invite Leaderboard",
        "\n".join(lines)
    )


@bot.command(name="lbreset")
@is_admin()
async def lbreset(ctx):
    for key in list(invite_data.keys()):
        if key[0] == ctx.guild.id:
            del invite_data[key]

    await send_card(
        ctx,
        "Invite Leaderboard",
        "The invite leaderboard has been reset."
    )


# =========================================================
# GENERAL
# =========================================================

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author

    await send_card(
        ctx,
        "Avatar",
        f"{member.mention}\n{member.display_avatar.url}"
    )


@bot.command()
async def banner(ctx, member: discord.Member = None):
    member = member or ctx.author

    user = await bot.fetch_user(member.id)

    if not user.banner:
        await send_card(
            ctx,
            "Banner",
            "This user does not have a banner."
        )
        return

    await send_card(
        ctx,
        "Banner",
        f"{member.mention}\n{user.banner.url}"
    )


@bot.command(name="srvlogo")
async def srvlogo(ctx):
    if not ctx.guild.icon:
        await send_card(
            ctx,
            "Server Logo",
            "This server does not have a logo."
        )
        return

    await send_card(
        ctx,
        "Server Logo",
        ctx.guild.icon.url
    )


@bot.command(name="srvbanner")
async def srvbanner(ctx):
    if not ctx.guild.banner:
        await send_card(
            ctx,
            "Server Banner",
            "This server does not have a banner."
        )
        return

    await send_card(
        ctx,
        "Server Banner",
        ctx.guild.banner.url
    )


@bot.command()
async def profile(ctx):
    member = ctx.author

    await send_card(
        ctx,
        "Profile",
        (
            f"Username: {member}\n"
            f"ID: `{member.id}`\n"
            f"Created: <t:{int(member.created_at.timestamp())}:F>\n"
            f"Joined: <t:{int(member.joined_at.timestamp())}:F>"
        )
    )


@bot.command(name="si")
async def server_info(ctx):
    guild = ctx.guild

    await send_card(
        ctx,
        "Server Information",
        (
            f"Name: {guild.name}\n"
            f"ID: `{guild.id}`\n"
            f"Members: `{guild.member_count}`\n"
            f"Channels: `{len(guild.channels)}`\n"
            f"Roles: `{len(guild.roles)}`\n"
            f"Created: <t:{int(guild.created_at.timestamp())}:F>"
        )
    )


# =========================================================
# TIMER COMPONENT
# =========================================================

class TimerView(LayoutView):
    def __init__(self, timer_id):
        super().__init__(timeout=None)
        self.timer_id = timer_id

        self.container = Container()
        self.text = TextDisplay("")
        self.container.add_item(self.text)

        self.add_item(self.container)

        self.refresh()

    def refresh(self):
        timer = timers.get(self.timer_id)

        if not timer:
            self.text.content = (
                "## Timer\n\n"
                "This timer no longer exists."
            )
            return

        remaining = max(
            0,
            int(
                (
                    timer["ends_at"]
                    - discord.utils.utcnow()
                ).total_seconds()
            )
        )

        end_text = format_end_time(
            timer["ends_at"]
        )

        self.text.content = (
            f"## {timer['name']}\n\n"
            f"Timer End in `{format_duration(remaining)}`\n"
            f"End at **{end_text}**"
        )


async def timer_loop(timer_id):
    while timer_id in timers:
        timer = timers[timer_id]

        channel = bot.get_channel(
            timer["channel_id"]
        )

        if not channel:
            break

        remaining = (
            timer["ends_at"]
            - discord.utils.utcnow()
        ).total_seconds()

        if remaining <= 0:
            await send_card(
                channel,
                timer["name"],
                "Timer Ended."
            )

            timers.pop(timer_id, None)
            timer_tasks.pop(timer_id, None)
            break

        try:
            message = await channel.fetch_message(
                timer["message_id"]
            )

            view = TimerView(timer_id)

            await message.edit(view=view)

        except discord.HTTPException:
            pass

        await asyncio.sleep(1)


@bot.command()
@is_admin()
async def tstart(ctx, duration=None, *, name=None):
    if not duration or not name:
        await send_card(
            ctx,
            "Timer",
            "Usage: `.tstart 1h Timer Name`"
        )
        return

    try:
        seconds = parse_duration(duration)
    except ValueError as e:
        await send_card(
            ctx,
            "Timer",
            str(e)
        )
        return

    ends_at = discord.utils.utcnow() + timedelta(
        seconds=seconds
    )

    timer_id = f"{ctx.guild.id}-{ctx.channel.id}-{ctx.message.id}"

    timers[timer_id] = {
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "name": name,
        "ends_at": ends_at,
        "message_id": None,
        "paused": False,
        "remaining": seconds
    }

    view = TimerView(timer_id)

    message = await ctx.send(view=view)

    timers[timer_id]["message_id"] = message.id

    task = asyncio.create_task(
        timer_loop(timer_id)
    )

    timer_tasks[timer_id] = task


@bot.command()
@is_admin()
async def tend(ctx, *, name):
    timer_id = None

    for key, timer in timers.items():
        if (
            timer["guild_id"] == ctx.guild.id
            and timer["name"].lower() == name.lower()
        ):
            timer_id = key
            break

    if not timer_id:
        await send_card(
            ctx,
            "Timer",
            "Timer not found."
        )
        return

    timer = timers.pop(timer_id)

    task = timer_tasks.pop(timer_id, None)

    if task:
        task.cancel()

    await send_card(
        ctx,
        name,
        "Timer ended manually."
    )


@bot.command()
@is_admin()
async def tpause(ctx, *, name):
    timer_id = None

    for key, timer in timers.items():
        if (
            timer["guild_id"] == ctx.guild.id
            and timer["name"].lower() == name.lower()
        ):
            timer_id = key
            break

    if not timer_id:
        await send_card(
            ctx,
            "Timer",
            "Timer not found."
        )
        return

    timer = timers[timer_id]

    timer["remaining"] = max(
        0,
        int(
            (
                timer["ends_at"]
                - discord.utils.utcnow()
            ).total_seconds()
        )
    )

    task = timer_tasks.pop(timer_id, None)

    if task:
        task.cancel()

    await send_card(
        ctx,
        timer["name"],
        f"Timer paused with `{format_duration(timer['remaining'])}` remaining."
    )


# =========================================================
# GIVEAWAYS
# =========================================================

class GiveawayJoinButton(Button):
    def __init__(self, giveaway_id):
        super().__init__(
            label="Join Giveaway",
            style=discord.ButtonStyle.secondary,
            custom_id=f"giveaway_join:{giveaway_id}"
        )

        self.giveaway_id = giveaway_id

    async def callback(self, interaction):
        giveaway = giveaways.get(self.giveaway_id)

        if not giveaway:
            await send_card(
                interaction,
                "Giveaway",
                "This giveaway no longer exists.",
                ephemeral=True
            )
            return

        if discord.utils.utcnow() >= giveaway["ends_at"]:
            await send_card(
                interaction,
                "Giveaway",
                "This giveaway has ended.",
                ephemeral=True
            )
            return

        if interaction.user.id in giveaway_blacklist[
            interaction.guild.id
        ]:
            await send_card(
                interaction,
                "Giveaway",
                "You are blacklisted from this giveaway.",
                ephemeral=True
            )
            return

        if interaction.user.id in giveaway["entries"]:
            giveaway["entries"].remove(
                interaction.user.id
            )

            await send_card(
                interaction,
                "Giveaway",
                "You have left the giveaway.",
                ephemeral=True
            )
            return

        giveaway["entries"].add(
            interaction.user.id
        )

        await send_card(
            interaction,
            "Giveaway",
            "You have entered the giveaway.",
            ephemeral=True
        )


class GiveawayView(LayoutView):
    def __init__(self, giveaway_id):
        super().__init__(timeout=None)

        container = Container()

        self.text = TextDisplay("")

        container.add_item(self.text)
        container.add_item(Separator())

        container.add_item(
            ActionRow(
                GiveawayJoinButton(giveaway_id)
            )
        )

        self.add_item(container)

        self.giveaway_id = giveaway_id
        self.refresh()

    def refresh(self):
        giveaway = giveaways.get(
            self.giveaway_id
        )

        if not giveaway:
            self.text.content = (
                "## Giveaway\n\n"
                "This giveaway has ended."
            )
            return

        remaining = max(
            0,
            int(
                (
                    giveaway["ends_at"]
                    - discord.utils.utcnow()
                ).total_seconds()
            )
        )

        end_text = format_end_time(
            giveaway["ends_at"]
        )

        self.text.content = (
            f"## {giveaway['reward']}\n\n"
            f"Winners: `{giveaway['winners']}`\n"
            f"Ends in `{format_duration(remaining)}`\n"
            f"End at **{end_text}**\n\n"
            f"Hosted by: {giveaway['host'].mention}\n\n"
            "Click the button below to participate."
        )


async def giveaway_loop(giveaway_id):
    while giveaway_id in giveaways:
        giveaway = giveaways[giveaway_id]

        channel = bot.get_channel(
            giveaway["channel_id"]
        )

        if not channel:
            break

        remaining = (
            giveaway["ends_at"]
            - discord.utils.utcnow()
        ).total_seconds()

        if remaining <= 0:
            await finish_giveaway(
                giveaway_id
            )
            break

        try:
            message = await channel.fetch_message(
                giveaway["message_id"]
            )

            await message.edit(
                view=GiveawayView(giveaway_id)
            )

        except discord.HTTPException:
            pass

        await asyncio.sleep(1)


async def finish_giveaway(giveaway_id):
    giveaway = giveaways.get(giveaway_id)

    if not giveaway:
        return

    channel = bot.get_channel(
        giveaway["channel_id"]
    )

    entries = [
        user_id
        for user_id in giveaway["entries"]
        if user_id not in giveaway_blacklist[
            giveaway["guild_id"]
        ]
    ]

    winner_count = min(
        giveaway["winners"],
        len(entries)
    )

    winners = (
        random.sample(entries, winner_count)
        if winner_count
        else []
    )

    if winners:
        winner_text = "\n".join(
            f"<@{user_id}>"
            for user_id in winners
        )
    else:
        winner_text = "No eligible winners."

    if channel:
        await send_card(
            channel,
            giveaway["reward"],
            (
                f"Giveaway ended.\n\n"
                f"Winners:\n{winner_text}\n\n"
                f"Total entries: `{len(entries)}`"
            )
        )

        try:
            message = await channel.fetch_message(
                giveaway["message_id"]
            )

            await message.edit(
                view=LayoutView(timeout=None)
            )

        except discord.HTTPException:
            pass

    giveaways.pop(giveaway_id, None)

    task = giveaway_tasks.pop(
        giveaway_id,
        None
    )

    if task:
        task.cancel()


@bot.command()
@is_admin()
async def gstart(ctx, duration=None, winners: int = None, *, reward=None):
    if not duration or winners is None or not reward:
        await send_card(
            ctx,
            "Giveaway",
            "Usage: `.gstart 1h 1 Nitro`"
        )
        return

    try:
        seconds = parse_duration(duration)
    except ValueError as e:
        await send_card(
            ctx,
            "Giveaway",
            str(e)
        )
        return

    if winners <= 0:
        await send_card(
            ctx,
            "Giveaway",
            "Winner amount must be greater than zero."
        )
        return

    ends_at = discord.utils.utcnow() + timedelta(
        seconds=seconds
    )

    giveaway_id = str(
        ctx.message.id
    )

    giveaways[giveaway_id] = {
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "message_id": None,
        "reward": reward,
        "winners": winners,
        "host": ctx.author,
        "ends_at": ends_at,
        "entries": set()
    }

    view = GiveawayView(
        giveaway_id
    )

    message = await ctx.send(
        view=view
    )

    giveaways[giveaway_id][
        "message_id"
    ] = message.id

    task = asyncio.create_task(
        giveaway_loop(giveaway_id)
    )

    giveaway_tasks[giveaway_id] = task


@bot.command()
@is_admin()
async def gend(ctx, message_id: int):
    giveaway_id = None

    for key, giveaway in giveaways.items():
        if giveaway["message_id"] == message_id:
            giveaway_id = key
            break

    if not giveaway_id:
        await send_card(
            ctx,
            "Giveaway",
            "Giveaway not found."
        )
        return

    await finish_giveaway(
        giveaway_id
    )


@bot.command()
@is_admin()
async def gblacklist(ctx, member: discord.Member):
    blacklist = giveaway_blacklist[
        ctx.guild.id
    ]

    if member.id in blacklist:
        blacklist.remove(member.id)

        await send_card(
            ctx,
            "Giveaway Blacklist",
            f"{member.mention} has been removed from the giveaway blacklist."
        )
        return

    blacklist.add(member.id)

    for giveaway in giveaways.values():
        if giveaway["guild_id"] == ctx.guild.id:
            giveaway["entries"].discard(
                member.id
            )

    await send_card(
        ctx,
        "Giveaway Blacklist",
        f"{member.mention} has been added to the giveaway blacklist."
    )


# =========================================================
# COMMAND ERROR HANDLER
# =========================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.CheckFailure):
        await send_card(
            ctx,
            "Permission Denied",
            "You need administrator permissions to use this command."
        )
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await send_card(
            ctx,
            "Missing Argument",
            f"Missing argument: `{error.param.name}`"
        )
        return

    if isinstance(error, commands.BadArgument):
        await send_card(
            ctx,
            "Invalid Argument",
            "One or more arguments are invalid."
        )
        return

    if isinstance(error, commands.CommandInvokeError):
        original = error.original

        if isinstance(
            original,
            discord.Forbidden
        ):
            await send_card(
                ctx,
                "Permission Error",
                "I do not have permission to perform that action."
            )
            return

    raise error


# =========================================================
# START
# =========================================================

bot.run(TOKEN)
