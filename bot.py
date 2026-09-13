import os
import re
import random
import asyncio
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands


# =========================================================
# SETUP
# =========================================================


TOKEN = os.getenv("BOT_TOKEN")
PREFIX = "."

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
        "message": "Welcome {user} to {server}!",
        "delay": 0,
        "channels": []
    },
    "welcome": {
        "message": "Welcome {user} to {server}!",
        "channel": None
    },
    "level": {
        "channel": None,
        "message": "{user} reached level {level}!",
        "users": {}
    },
    "roles": {
        "friend": None,
        "mod": None,
        "staff": None,
        "jail": None,
        "vip": None
    }
})

snipes = defaultdict(lambda: deque(maxlen=10))

invite_cache = {}
invite_data = defaultdict(lambda: defaultdict(lambda: {
    "joins": 0,
    "leaves": 0,
    "fake": 0,
    "rejoins": 0
}))

member_inviter = {}

timers = {}
timer_tasks = {}

giveaways = {}
giveaway_tasks = {}
giveaway_blacklist = defaultdict(set)


# =========================================================
# BOT PRESENCE
# =========================================================

@bot.event
async def on_ready():
    activity = discord.CustomActivity(
        name="Best Event at .gg/thundernight"
    )

    await bot.change_presence(
        status=discord.Status.online,
        activity=activity
    )

    print(f"Logged in as {bot.user} ({bot.user.id})")


# =========================================================
# COMPONENTS V2 HELPERS
# =========================================================

def make_card(*items):
    view = discord.ui.LayoutView()

    container = discord.ui.Container()

    for item in items:
        if isinstance(item, str):
            container.add_item(
                discord.ui.TextDisplay(item)
            )
        else:
            container.add_item(item)

    view.add_item(container)
    return view


async def send_card(
    target,
    text,
    *,
    ephemeral=False,
    allowed_mentions=None
):
    view = make_card(text)

    if isinstance(target, discord.Interaction):
        await target.response.send_message(
            view=view,
            ephemeral=ephemeral,
            allowed_mentions=allowed_mentions
        )
    else:
        await target.send(
            view=view,
            allowed_mentions=allowed_mentions
        )


async def edit_card(
    interaction,
    text,
    *,
    view=None,
    allowed_mentions=None
):
    if view is None:
        view = make_card(text)

    await interaction.response.edit_message(
        view=view,
        allowed_mentions=allowed_mentions
    )


# =========================================================
# PERMISSION
# =========================================================

def is_admin():
    async def predicate(ctx):
        return (
            ctx.guild is not None
            and ctx.author.guild_permissions.administrator
        )

    return commands.check(predicate)


# =========================================================
# VARIABLE SYSTEM
# =========================================================

def replace_variables(text, member, guild):
    if not text:
        return ""

    values = {
        "{user}": member.mention,
        "{user.name}": member.name,
        "{user.id}": str(member.id),
        "{user.tag}": str(member),
        "{server}": guild.name,
        "{server.id}": str(guild.id),
        "{server.owner}": guild.owner.mention if guild.owner else "Unknown",
        "{membercount}": str(guild.member_count),
        "{server.members}": str(guild.member_count),
        "{channel}": getattr(member.guild.system_channel, "mention", "#channel")
        if guild.system_channel else "#channel"
    }

    for key, value in values.items():
        text = text.replace(key, value)

    return text


# =========================================================
# HELP
# =========================================================

HELP_CATEGORIES = {
    "Moderation": [
        ".ban @user",
        ".kick @user",
        ".mute @user [1h/1d/1m]",
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
        ".greet setchannel [channel]",
        ".greet reset",
        ".greet removechannel [channel]"
    ],
    "Welcome": [
        ".welcome channel [channel]",
        ".welcome message [message]",
        ".welcome variables",
        ".welcome reset"
    ],
    "Level": [
        ".lvl [user]",
        ".lvl message [message]",
        ".lvl variables",
        ".level channel [channel]",
        ".level lb",
        ".level lbreset"
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
        ".gblacklist [user]"
    ],
    "Roles": [
        ".set friend [role]",
        ".set mod [role]",
        ".set staff [role]",
        ".set jail [role]",
        ".set vip [role]",
        ".friend [user]",
        ".mod [user]",
        ".staff [user]",
        ".jail [user]",
        ".vip [user]"
    ]
}


class HelpView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=180)

        self.container = discord.ui.Container()

        self.container.add_item(
            discord.ui.TextDisplay(
                "## Thundernight\n\n"
                "Hey, I'm Thundernight\n"
                "My prefix for this server is `.`\n"
                "Type `.help [context]` for more\n\n"
                f"Total Commands: "
                f"{sum(len(x) for x in HELP_CATEGORIES.values())}\n\n"
                "**Modules**\n"
                "Moderation\n"
                "Greet\n"
                "Welcome\n"
                "Level\n"
                "Invites\n"
                "General\n"
                "Giveaway\n"
                "Roles\n\n"
                "Select a module to see its commands."
            )
        )

        self.container.add_item(
            discord.ui.Separator()
        )

        select = discord.ui.Select(
            placeholder="Select a module",
            options=[
                discord.SelectOption(
                    label=name,
                    value=name
                )
                for name in HELP_CATEGORIES
            ]
        )

        select.callback = self.category_selected

        row = discord.ui.ActionRow()
        row.add_item(select)

        self.container.add_item(row)
        self.add_item(self.container)

    async def category_selected(self, interaction):
        category = interaction.data["values"][0]

        commands_text = "\n".join(
            f"`{command}`"
            for command in HELP_CATEGORIES[category]
        )

        new_view = CategoryHelpView(
            category,
            commands_text
        )

        await interaction.response.edit_message(
            view=new_view
        )


class CategoryHelpView(discord.ui.LayoutView):
    def __init__(self, category, commands_text):
        super().__init__(timeout=180)

        container = discord.ui.Container()

        container.add_item(
            discord.ui.TextDisplay(
                f"## {category}\n\n"
                f"{commands_text}"
            )
        )

        container.add_item(
            discord.ui.Separator()
        )

        back = discord.ui.Button(
            label="Back",
            style=discord.ButtonStyle.secondary
        )

        async def back_callback(interaction):
            await interaction.response.edit_message(
                view=HelpView()
            )

        back.callback = back_callback

        row = discord.ui.ActionRow()
        row.add_item(back)

        container.add_item(row)

        self.add_item(container)


@bot.command()
async def help(ctx, *, context=None):
    if context:
        matched = None

        for category in HELP_CATEGORIES:
            if category.lower() == context.lower():
                matched = category
                break

        if matched:
            commands_text = "\n".join(
                f"`{command}`"
                for command in HELP_CATEGORIES[matched]
            )

            await ctx.send(
                view=CategoryHelpView(
                    matched,
                    commands_text
                )
            )
            return

    await ctx.send(view=HelpView())


# =========================================================
# MODERATION
# =========================================================

@bot.command()
@is_admin()
async def ban(ctx, member: discord.Member = None, *, reason=None):
    if member is None:
        return await send_card(ctx, "Usage: `.ban @user [reason]`")

    await member.ban(reason=reason)

    await send_card(
        ctx,
        f"## Ban\n\n"
        f"Given User: {member.mention}\n"
        f"Reason: {reason or 'No reason provided'}"
    )


@bot.command()
@is_admin()
async def kick(ctx, member: discord.Member = None, *, reason=None):
    if member is None:
        return await send_card(ctx, "Usage: `.kick @user [reason]`")

    await member.kick(reason=reason)

    await send_card(
        ctx,
        f"## Kick\n\n"
        f"Given User: {member.mention}\n"
        f"Reason: {reason or 'No reason provided'}"
    )


def parse_duration(value):
    match = re.fullmatch(
        r"(\d+)(s|m|h|d)",
        value.lower()
    )

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    if unit == "s":
        return timedelta(seconds=amount)

    if unit == "m":
        return timedelta(minutes=amount)

    if unit == "h":
        return timedelta(hours=amount)

    if unit == "d":
        return timedelta(days=amount)

    return None


@bot.command()
@is_admin()
async def mute(
    ctx,
    member: discord.Member = None,
    duration: str = None
):
    if member is None or duration is None:
        return await send_card(
            ctx,
            "Usage: `.mute @user [1h/1d/1m]`"
        )

    delta = parse_duration(duration)

    if delta is None:
        return await send_card(
            ctx,
            "Invalid duration."
        )

    if delta > timedelta(days=28):
        return await send_card(
            ctx,
            "Maximum timeout duration is 28 days."
        )

    until = discord.utils.utcnow() + delta

    await member.timeout(
        until,
        reason=f"Muted by {ctx.author}"
    )

    await send_card(
        ctx,
        f"## Mute\n\n"
        f"Given User: {member.mention}\n"
        f"Duration: `{duration}`"
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

    await send_card(ctx, "## Lock\n\nThis channel has been locked.")


@bot.command()
@is_admin()
async def unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await send_card(ctx, "## Unlock\n\nThis channel has been unlocked.")


@bot.command()
@is_admin()
async def hide(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = False

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await send_card(ctx, "## Hide\n\nThis channel has been hidden.")


@bot.command()
@is_admin()
async def unhide(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = None

    await ctx.channel.set_permissions(
        ctx.guild.default_role,
        overwrite=overwrite
    )

    await send_card(ctx, "## Unhide\n\nThis channel is visible again.")


@bot.command()
@is_admin()
async def unban(ctx, user_id: int = None):
    if user_id is None:
        return await send_card(
            ctx,
            "Usage: `.unban [userid]`"
        )

    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user)

        await send_card(
            ctx,
            f"## Unban\n\nUnbanned User: {user.mention}"
        )

    except discord.NotFound:
        await send_card(ctx, "User is not banned or could not be found.")


@bot.command()
@is_admin()
async def purge(ctx, amount: int = None):
    if amount is None or amount < 1:
        return await send_card(
            ctx,
            "Usage: `.purge [message count]`"
        )

    deleted = await ctx.channel.purge(
        limit=amount + 1
    )

    message = await ctx.send(
        view=make_card(
            f"## Purge\n\n"
            f"Deleted `{len(deleted) - 1}` messages."
        )
    )

    await asyncio.sleep(3)

    try:
        await message.delete()
    except discord.NotFound:
        pass


@bot.command()
@is_admin()
async def nuke(ctx):
    channel = ctx.channel

    new_channel = await channel.clone(
        reason=f"Nuked by {ctx.author}"
    )

    await channel.delete()

    await send_card(
        new_channel,
        "## Nuke\n\nThis channel has been nuked."
    )


@bot.command()
@is_admin()
async def clone(ctx):
    channel = ctx.channel

    new_channel = await channel.clone(
        reason=f"Cloned by {ctx.author}"
    )

    await channel.delete()

    await send_card(
        new_channel,
        "## Clone\n\nThis channel has been cloned."
    )


@bot.command()
async def snipe(ctx):
    messages = snipes.get(ctx.channel.id)

    if not messages:
        return await send_card(
            ctx,
            "There are no deleted messages to show."
        )

    message = messages[-1]

    await send_card(
        ctx,
        f"## Snipe\n\n"
        f"Author: {message['author']}\n"
        f"Message:\n{message['content'] or '[No text]'}"
    )


# =========================================================
# SNIPE EVENT
# =========================================================

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return

    snipes[message.channel.id].append({
        "author": str(message.author),
        "content": message.content
    })


# =========================================================
# GREET
# =========================================================

@bot.group(name="greet", invoke_without_command=True)
async def greet(ctx):
    await send_card(
        ctx,
        "## Greet\n\n"
        "Use `.greet [command]`.\n\n"
        "Commands:\n"
        "`.greet delafter [seconds]`\n"
        "`.greet message [message]`\n"
        "`.greet variables`\n"
        "`.greet setchannel [channel]`\n"
        "`.greet reset`\n"
        "`.greet removechannel [channel]`"
    )


@greet.command(name="delafter")
@is_admin()
async def greet_delafter(ctx, seconds: int = None):
    if seconds is None or seconds < 0:
        return await send_card(
            ctx,
            "Usage: `.greet delafter [seconds]`"
        )

    guild_settings[ctx.guild.id]["greet"]["delay"] = seconds

    await send_card(
        ctx,
        f"Greet delete delay set to `{seconds}` seconds."
    )


@greet.command(name="message")
@is_admin()
async def greet_message(ctx, *, message=None):
    if not message:
        return await send_card(
            ctx,
            "Usage: `.greet message [message]`"
        )

    guild_settings[ctx.guild.id]["greet"]["message"] = message

    await send_card(
        ctx,
        "Greet message updated."
    )


@greet.command(name="variables")
async def greet_variables(ctx):
    await send_card(
        ctx,
        "## Greet Variables\n\n"
        "`{user}`\n"
        "`{user.name}`\n"
        "`{user.id}`\n"
        "`{user.tag}`\n"
        "`{server}`\n"
        "`{server.id}`\n"
        "`{server.owner}`\n"
        "`{membercount}`\n"
        "`{server.members}`\n"
        "`{channel}`"
    )


@greet.command(name="setchannel")
@is_admin()
async def greet_setchannel(
    ctx,
    channel: discord.TextChannel = None
):
    if channel is None:
        return await send_card(
            ctx,
            "Usage: `.greet setchannel #channel`"
        )

    channels = guild_settings[ctx.guild.id]["greet"]["channels"]

    if channel.id not in channels:
        if len(channels) >= 5:
            return await send_card(
                ctx,
                "You can configure a maximum of 5 greet channels."
            )

        channels.append(channel.id)

    await send_card(
        ctx,
        f"Greet channel added: {channel.mention}"
    )


@greet.command(name="removechannel")
@is_admin()
async def greet_removechannel(
    ctx,
    channel: discord.TextChannel = None
):
    if channel is None:
        return await send_card(
            ctx,
            "Usage: `.greet removechannel #channel`"
        )

    channels = guild_settings[ctx.guild.id]["greet"]["channels"]

    if channel.id in channels:
        channels.remove(channel.id)

    await send_card(
        ctx,
        f"Greet channel removed: {channel.mention}"
    )


@greet.command(name="reset")
@is_admin()
async def greet_reset(ctx):
    guild_settings[ctx.guild.id]["greet"] = {
        "message": "Welcome {user} to {server}!",
        "delay": 0,
        "channels": []
    }

    await send_card(ctx, "Greet settings have been reset.")


# =========================================================
# WELCOME
# =========================================================

@bot.group(name="welcome", invoke_without_command=True)
async def welcome(ctx):
    await send_card(
        ctx,
        "## Welcome\n\n"
        "`.welcome channel [channel]`\n"
        "`.welcome message [message]`\n"
        "`.welcome variables`\n"
        "`.welcome reset`"
    )


@welcome.command(name="channel")
@is_admin()
async def welcome_channel(
    ctx,
    channel: discord.TextChannel = None
):
    if channel is None:
        return await send_card(
            ctx,
            "Usage: `.welcome channel #channel`"
        )

    guild_settings[ctx.guild.id]["welcome"]["channel"] = channel.id

    await send_card(
        ctx,
        f"Welcome channel set to {channel.mention}."
    )


@welcome.command(name="message")
@is_admin()
async def welcome_message(ctx, *, message=None):
    if not message:
        return await send_card(
            ctx,
            "Usage: `.welcome message [message]`"
        )

    guild_settings[ctx.guild.id]["welcome"]["message"] = message

    await send_card(
        ctx,
        "Welcome message updated."
    )


@welcome.command(name="variables")
async def welcome_variables(ctx):
    await send_card(
        ctx,
        "## Welcome Variables\n\n"
        "`{user}`\n"
        "`{user.name}`\n"
        "`{user.id}`\n"
        "`{user.tag}`\n"
        "`{server}`\n"
        "`{server.id}`\n"
        "`{server.owner}`\n"
        "`{membercount}`\n"
        "`{server.members}`\n"
        "`{channel}`"
    )


@welcome.command(name="reset")
@is_admin()
async def welcome_reset(ctx):
    guild_settings[ctx.guild.id]["welcome"] = {
        "message": "Welcome {user} to {server}!",
        "channel": None
    }

    await send_card(
        ctx,
        "Welcome settings have been reset."
    )


# =========================================================
# LEVEL
# =========================================================

@bot.command()
async def lvl(ctx, *args):
    if args and args[0].lower() == "message":
        if not ctx.author.guild_permissions.administrator:
            return await send_card(
                ctx,
                "Administrator permission required."
            )

        message = " ".join(args[1:])

        if not message:
            return await send_card(
                ctx,
                "Usage: `.lvl message [message]`"
            )

        guild_settings[ctx.guild.id]["level"]["message"] = message

        return await send_card(
            ctx,
            "Level-up message updated."
        )

    if args and args[0].lower() == "variables":
        return await send_card(
            ctx,
            "## Level Variables\n\n"
            "`{user}`\n"
            "`{user.name}`\n"
            "`{user.id}`\n"
            "`{user.tag}`\n"
            "`{level}`\n"
            "`{messages}`\n"
            "`{server}`\n"
            "`{membercount}`"
        )

    member = ctx.author

    if args:
        try:
            member = await commands.MemberConverter().convert(
                ctx,
                args[0]
            )
        except commands.BadArgument:
            return await send_card(
                ctx,
                "User not found."
            )

    data = guild_settings[ctx.guild.id]["level"]["users"]
    user_data = data.get(
        member.id,
        {"messages": 0, "level": 0}
    )

    await send_card(
        ctx,
        f"## Level\n\n"
        f"User: {member.mention}\n"
        f"Level: `{user_data['level']}`\n"
        f"Messages: `{user_data['messages']}`"
    )


@bot.group(name="level", invoke_without_command=True)
async def level(ctx):
    await send_card(
        ctx,
        "## Level\n\n"
        "`.level channel #channel`\n"
        "`.level lb`\n"
        "`.level lbreset`"
    )


@level.command(name="channel")
@is_admin()
async def level_channel(
    ctx,
    channel: discord.TextChannel = None
):
    if channel is None:
        return await send_card(
            ctx,
            "Usage: `.level channel #channel`"
        )

    guild_settings[ctx.guild.id]["level"]["channel"] = channel.id

    await send_card(
        ctx,
        f"Level channel set to {channel.mention}."
    )


@level.command(name="lb")
async def level_lb(ctx):
    users = guild_settings[ctx.guild.id]["level"]["users"]

    sorted_users = sorted(
        users.items(),
        key=lambda x: (
            x[1]["level"],
            x[1]["messages"]
        ),
        reverse=True
    )[:3]

    if not sorted_users:
        return await send_card(
            ctx,
            "No level data yet."
        )

    lines = []

    for index, (user_id, data) in enumerate(
        sorted_users,
        start=1
    ):
        member = ctx.guild.get_member(user_id)
        name = member.mention if member else str(user_id)

        lines.append(
            f"**{index}.** {name} — "
            f"Level `{data['level']}` "
            f"Messages `{data['messages']}`"
        )

    await send_card(
        ctx,
        "## Level Leaderboard\n\n" +
        "\n".join(lines)
    )


@level.command(name="lbreset")
@is_admin()
async def level_lbreset(ctx):
    guild_settings[ctx.guild.id]["level"]["users"].clear()

    await send_card(
        ctx,
        "Level leaderboard has been reset."
    )


# =========================================================
# INVITES
# =========================================================

async def cache_invites(guild):
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
    await cache_invites(guild)


@bot.event
async def on_member_join(member):
    guild = member.guild

    before = invite_cache.get(guild.id, {})

    await asyncio.sleep(1)

    try:
        invites = await guild.invites()

        used_invite = None

        for invite in invites:
            old_uses = before.get(invite.code, 0)

            if invite.uses > old_uses:
                used_invite = invite
                break

        invite_cache[guild.id] = {
            invite.code: invite.uses
            for invite in invites
        }

        if used_invite and used_invite.inviter:
            inviter_id = used_invite.inviter.id

            member_inviter[member.id] = inviter_id

            data = invite_data[guild.id][inviter_id]
            data["joins"] += 1

            account_age = (
                discord.utils.utcnow() - member.created_at
            ).days

            if account_age < 15:
                data["fake"] += 1

    except discord.Forbidden:
        pass

    # Greet
    settings = guild_settings[guild.id]["greet"]

    for channel_id in settings["channels"]:
        channel = guild.get_channel(channel_id)

        if not channel:
            continue

        text = replace_variables(
            settings["message"],
            member,
            guild
        )

        try:
            message = await channel.send(
                view=make_card(text)
            )

            if settings["delay"] > 0:
                await asyncio.sleep(settings["delay"])

                try:
                    await message.delete()
                except discord.NotFound:
                    pass

        except discord.HTTPException:
            pass

    # Welcome
    welcome_settings = guild_settings[guild.id]["welcome"]
    welcome_channel_id = welcome_settings["channel"]

    if welcome_channel_id:
        channel = guild.get_channel(
            welcome_channel_id
        )

        if channel:
            text = replace_variables(
                welcome_settings["message"],
                member,
                guild
            )

            try:
                await channel.send(
                    view=make_card(
                        f"## Welcome\n\n{text}"
                    )
                )
            except discord.HTTPException:
                pass


@bot.event
async def on_member_remove(member):
    inviter_id = member_inviter.get(member.id)

    if inviter_id:
        data = invite_data[member.guild.id][inviter_id]
        data["leaves"] += 1


@bot.command(name="i")
async def invite_info(ctx, member: discord.Member = None):
    member = member or ctx.author

    data = invite_data[ctx.guild.id][member.id]

    await send_card(
        ctx,
        f"## Invite log\n\n"
        f"{member.mention}\n\n"
        f"Joins: `{data['joins']}`\n"
        f"Left: `{data['leaves']}`\n"
        f"Fake: `{data['fake']}`\n"
        f"Rejoins: `{data['rejoins']}`\n\n"
        f"Requested by {ctx.author.mention}"
    )


@bot.command(name="lb")
async def leaderboard(ctx, category=None):
    if category is None:
        return await send_card(
            ctx,
            "Usage: `.lb i`"
        )

    if category.lower() != "i":
        return await send_card(
            ctx,
            "Available leaderboard: `.lb i`"
        )

    users = invite_data[ctx.guild.id]

    sorted_users = sorted(
        users.items(),
        key=lambda x: x[1]["joins"],
        reverse=True
    )[:10]

    if not sorted_users:
        return await send_card(
            ctx,
            "No invite data yet."
        )

    lines = []

    for index, (user_id, data) in enumerate(
        sorted_users,
        start=1
    ):
        member = ctx.guild.get_member(user_id)

        name = (
            member.mention
            if member
            else str(user_id)
        )

        lines.append(
            f"**{index}.** {name} — "
            f"`{data['joins']}` invites"
        )

    await send_card(
        ctx,
        "## Invite Leaderboard\n\n" +
        "\n".join(lines)
    )


@bot.command(name="lbreset")
@is_admin()
async def lbreset(ctx):
    invite_data[ctx.guild.id].clear()

    await send_card(
        ctx,
        "Invite leaderboard has been reset."
    )


# =========================================================
# LEVEL MESSAGE TRACKING
# =========================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.guild:
        data = guild_settings[
            message.guild.id
        ]["level"]["users"]

        user_data = data.setdefault(
            message.author.id,
            {
                "messages": 0,
                "level": 0
            }
        )

        user_data["messages"] += 1

        if (
            user_data["messages"] % 100 == 0
        ):
            user_data["level"] += 1

            channel_id = guild_settings[
                message.guild.id
            ]["level"]["channel"]

            if channel_id:
                channel = message.guild.get_channel(
                    channel_id
                )

                if channel:
                    text = guild_settings[
                        message.guild.id
                    ]["level"]["message"]

                    text = text.replace(
                        "{user}",
                        message.author.mention
                    )
                    text = text.replace(
                        "{user.name}",
                        message.author.name
                    )
                    text = text.replace(
                        "{user.id}",
                        str(message.author.id)
                    )
                    text = text.replace(
                        "{level}",
                        str(user_data["level"])
                    )
                    text = text.replace(
                        "{messages}",
                        str(user_data["messages"])
                    )
                    text = text.replace(
                        "{server}",
                        message.guild.name
                    )
                    text = text.replace(
                        "{membercount}",
                        str(message.guild.member_count)
                    )

                    await channel.send(
                        view=make_card(
                            "## Level Up\n\n" + text
                        )
                    )

    await bot.process_commands(message)


# =========================================================
# GENERAL
# =========================================================

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author

    avatar_url = member.display_avatar.url

    await send_card(
        ctx,
        f"## Avatar\n\n"
        f"User: {member.mention}\n"
        f"{avatar_url}"
    )


@bot.command()
async def banner(ctx, member: discord.Member = None):
    member = member or ctx.author

    user = await bot.fetch_user(member.id)

    if not user.banner:
        return await send_card(
            ctx,
            "This user does not have a banner."
        )

    await send_card(
        ctx,
        f"## Banner\n\n"
        f"User: {member.mention}\n"
        f"{user.banner.url}"
    )


@bot.command()
async def srvlogo(ctx):
    if not ctx.guild.icon:
        return await send_card(
            ctx,
            "This server does not have a logo."
        )

    await send_card(
        ctx,
        f"## Server Logo\n\n"
        f"{ctx.guild.icon.url}"
    )


@bot.command()
async def srvbanner(ctx):
    if not ctx.guild.banner:
        return await send_card(
            ctx,
            "This server does not have a banner."
        )

    await send_card(
        ctx,
        f"## Server Banner\n\n"
        f"{ctx.guild.banner.url}"
    )


@bot.command()
async def profile(ctx, member: discord.Member = None):
    member = member or ctx.author

    await send_card(
        ctx,
        f"## Profile\n\n"
        f"User: {member.mention}\n"
        f"Username: `{member}`\n"
        f"ID: `{member.id}`\n"
        f"Created: <t:{int(member.created_at.timestamp())}:F>\n"
        f"Joined: "
        f"<t:{int(member.joined_at.timestamp())}:F>"
    )


@bot.command(name="si")
async def server_info(ctx):
    guild = ctx.guild

    await send_card(
        ctx,
        f"## Server Information\n\n"
        f"Name: `{guild.name}`\n"
        f"ID: `{guild.id}`\n"
        f"Owner: {guild.owner.mention if guild.owner else 'Unknown'}\n"
        f"Members: `{guild.member_count}`\n"
        f"Channels: `{len(guild.channels)}`\n"
        f"Roles: `{len(guild.roles)}`\n"
        f"Created: <t:{int(guild.created_at.timestamp())}:F>"
    )


# =========================================================
# TIMER
# =========================================================

def parse_timer(value):
    return parse_duration(value)


class TimerView(discord.ui.LayoutView):
    def __init__(self, name, ends_at, paused=False):
        super().__init__(timeout=None)

        self.name = name
        self.ends_at = ends_at
        self.paused = paused

        container = discord.ui.Container()

        if paused:
            description = "Timer Paused"
        else:
            timestamp = int(ends_at.timestamp())

            description = (
                f"Timer End in <t:{timestamp}:R>\n"
                f"End at <t:{timestamp}:F>"
            )

        container.add_item(
            discord.ui.TextDisplay(
                f"## {name}\n\n"
                f"{description}"
            )
        )

        self.add_item(container)


@bot.command()
@is_admin()
async def tstart(ctx, duration=None, *, name=None):
    if not duration or not name:
        return await send_card(
            ctx,
            "Usage: `.tstart [1h] [name]`"
        )

    delta = parse_timer(duration)

    if delta is None:
        return await send_card(
            ctx,
            "Invalid timer duration."
        )

    ends_at = discord.utils.utcnow() + delta

    timers[(ctx.guild.id, name.lower())] = {
        "name": name,
        "ends_at": ends_at,
        "channel_id": ctx.channel.id,
        "message_id": None,
        "paused": False,
        "remaining": delta
    }

    view = TimerView(name, ends_at)

    message = await ctx.send(view=view)

    timers[
        (ctx.guild.id, name.lower())
    ]["message_id"] = message.id

    await send_card(
        ctx,
        f"Timer `{name}` started."
    )


@bot.command()
@is_admin()
async def tend(ctx, *, name=None):
    if not name:
        return await send_card(
            ctx,
            "Usage: `.tend [name]`"
        )

    key = (ctx.guild.id, name.lower())

    timer = timers.pop(key, None)

    if not timer:
        return await send_card(
            ctx,
            "Timer not found."
        )

    await send_card(
        ctx,
        f"Timer `{timer['name']}` ended."
    )


@bot.command()
@is_admin()
async def tpause(ctx, *, name=None):
    if not name:
        return await send_card(
            ctx,
            "Usage: `.tpause [name]`"
        )

    key = (ctx.guild.id, name.lower())
    timer = timers.get(key)

    if not timer:
        return await send_card(
            ctx,
            "Timer not found."
        )

    if timer["paused"]:
        return await send_card(
            ctx,
            "Timer is already paused."
        )

    remaining = timer["ends_at"] - discord.utils.utcnow()

    timer["remaining"] = remaining
    timer["paused"] = True

    channel = ctx.guild.get_channel(
        timer["channel_id"]
    )

    if channel:
        try:
            message = await channel.fetch_message(
                timer["message_id"]
            )

            await message.edit(
                view=TimerView(
                    timer["name"],
                    timer["ends_at"],
                    paused=True
                )
            )
        except discord.HTTPException:
            pass

    await send_card(
        ctx,
        f"Timer `{timer['name']}` paused."
    )


# =========================================================
# GIVEAWAYS
# =========================================================

class GiveawayView(discord.ui.LayoutView):
    def __init__(self, giveaway_id):
        super().__init__(timeout=None)

        self.giveaway_id = giveaway_id

        data = giveaways[giveaway_id]

        timestamp = int(data["ends_at"].timestamp())

        container = discord.ui.Container()

        container.add_item(
            discord.ui.TextDisplay(
                f"## {data['reward']}\n\n"
                f"Winners: `{data['winners']}`\n"
                f"Ends: <t:{timestamp}:R> "
                f"(<t:{timestamp}:F>)\n"
                f"Hosted by: {data['host'].mention}\n\n"
                f"Click the button below to participate."
            )
        )

        container.add_item(
            discord.ui.Separator()
        )

        container.add_item(
            discord.ui.TextDisplay(
                f"**Ends at • <t:{timestamp}:F>**"
            )
        )

        button = discord.ui.Button(
            label="Participate",
            style=discord.ButtonStyle.secondary
        )

        button.callback = self.join

        row = discord.ui.ActionRow()
        row.add_item(button)

        container.add_item(row)

        self.add_item(container)

    async def join(self, interaction):
        data = giveaways.get(self.giveaway_id)

        if not data:
            return await interaction.response.send_message(
                view=make_card(
                    "This giveaway no longer exists."
                ),
                ephemeral=True
            )

        if interaction.user.id in giveaway_blacklist[
            interaction.guild.id
        ]:
            return await interaction.response.send_message(
                view=make_card(
                    "You are blacklisted from giveaways."
                ),
                ephemeral=True
            )

        if interaction.user.id in data["entries"]:
            return await interaction.response.send_message(
                view=make_card(
                    "You are already participating."
                ),
                ephemeral=True
            )

        data["entries"].add(interaction.user.id)

        await interaction.response.send_message(
            view=make_card(
                "You have entered the giveaway."
            ),
            ephemeral=True
        )


@bot.command()
@is_admin()
async def gstart(ctx, duration=None, winners: int = None, *, reward=None):
    if not duration or not winners or not reward:
        return await send_card(
            ctx,
            "Usage: `.gstart [1h] [winners] [reward]`"
        )

    if winners < 1:
        return await send_card(
            ctx,
            "Winner amount must be at least 1."
        )

    delta = parse_duration(duration)

    if delta is None:
        return await send_card(
            ctx,
            "Invalid giveaway duration."
        )

    giveaway_id = str(
        random.randint(
            100000000,
            999999999
        )
    )

    ends_at = discord.utils.utcnow() + delta

    giveaways[giveaway_id] = {
        "id": giveaway_id,
        "reward": reward,
        "winners": winners,
        "ends_at": ends_at,
        "host": ctx.author,
        "channel_id": ctx.channel.id,
        "message_id": None,
        "entries": set()
    }

    message = await ctx.send(
        view=GiveawayView(giveaway_id)
    )

    giveaways[giveaway_id]["message_id"] = message.id

    task = asyncio.create_task(
        finish_giveaway(giveaway_id)
    )

    giveaway_tasks[giveaway_id] = task


async def finish_giveaway(giveaway_id):
    data = giveaways.get(giveaway_id)

    if not data:
        return

    seconds = (
        data["ends_at"] -
        discord.utils.utcnow()
    ).total_seconds()

    if seconds > 0:
        await asyncio.sleep(seconds)

    data = giveaways.get(giveaway_id)

    if not data:
        return

    entries = list(data["entries"])

    winners_count = min(
        data["winners"],
        len(entries)
    )

    if winners_count:
        winners = random.sample(
            entries,
            winners_count
        )

        winner_text = "\n".join(
            f"<@{user_id}>"
            for user_id in winners
        )
    else:
        winner_text = "No eligible participants."

    channel = bot.get_channel(
        data["channel_id"]
    )

    if channel:
        try:
            message = await channel.fetch_message(
                data["message_id"]
            )

            await message.edit(
                view=make_card(
                    f"## Giveaway Ended\n\n"
                    f"Reward: **{data['reward']}**\n"
                    f"Winners:\n{winner_text}"
                )
            )

        except discord.HTTPException:
            pass

    giveaways.pop(giveaway_id, None)
    giveaway_tasks.pop(giveaway_id, None)


@bot.command()
@is_admin()
async def gend(ctx, message_id: int = None):
    if message_id is None:
        return await send_card(
            ctx,
            "Usage: `.gend [message id]`"
        )

    found = None

    for giveaway_id, data in giveaways.items():
        if data["message_id"] == message_id:
            found = giveaway_id
            break

    if found is None:
        return await send_card(
            ctx,
            "Giveaway not found."
        )

    task = giveaway_tasks.get(found)

    if task:
        task.cancel()

    await finish_giveaway(found)

    await send_card(
        ctx,
        "Giveaway ended."
    )


@bot.command()
@is_admin()
async def gblacklist(
    ctx,
    member: discord.Member = None
):
    if member is None:
        return await send_card(
            ctx,
            "Usage: `.gblacklist @user`"
        )

    blacklist = giveaway_blacklist[
        ctx.guild.id
    ]

    if member.id in blacklist:
        blacklist.remove(member.id)

        await send_card(
            ctx,
            f"{member.mention} has been removed from the giveaway blacklist."
        )

    else:
        blacklist.add(member.id)

        await send_card(
            ctx,
            f"{member.mention} has been added to the giveaway blacklist."
        )


# =========================================================
# ROLES
# =========================================================

SUPPORTED_ROLES = (
    "friend",
    "mod",
    "staff",
    "jail",
    "vip"
)


@bot.command(name="set")
@is_admin()
async def set_role(
    ctx,
    role_type=None,
    role: discord.Role = None
):
    if role_type is None or role is None:
        return await send_card(
            ctx,
            "Usage:\n"
            "`.set friend @role`\n"
            "`.set mod @role`\n"
            "`.set staff @role`\n"
            "`.set jail @role`\n"
            "`.set vip @role`"
        )

    role_type = role_type.lower()

    if role_type not in SUPPORTED_ROLES:
        return await send_card(
            ctx,
            "Supported roles:\n"
            "`friend`\n"
            "`mod`\n"
            "`staff`\n"
            "`jail`\n"
            "`vip`"
        )

    if role >= ctx.guild.me.top_role:
        return await send_card(
            ctx,
            "I cannot manage this role because it is above or equal to my highest role."
        )

    guild_settings[
        ctx.guild.id
    ]["roles"][role_type] = role.id

    await send_card(
        ctx,
        f"## Role Setup\n\n"
        f"Type: `{role_type}`\n"
        f"Role: {role.mention}",
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=False,
            replied_user=False
        )
    )


async def give_configured_role(
    ctx,
    role_type,
    member=None
):
    if member is None:
        reference = ctx.message.reference

        if reference and reference.message_id:
            try:
                replied_message = await ctx.channel.fetch_message(
                    reference.message_id
                )
                member = replied_message.author

            except discord.HTTPException:
                pass

    if member is None:
        return await send_card(
            ctx,
            f"Usage: `.{role_type} @user`\n"
            f"Or reply to a user's message with `.{role_type}`"
        )

    role_id = guild_settings[
        ctx.guild.id
    ]["roles"].get(role_type)

    if not role_id:
        return await send_card(
            ctx,
            f"The `{role_type}` role has not been configured.\n"
            f"Use `.set {role_type} @role` first."
        )

    role = ctx.guild.get_role(role_id)

    if role is None:
        return await send_card(
            ctx,
            f"The configured `{role_type}` role no longer exists."
        )

    if role >= ctx.guild.me.top_role:
        return await send_card(
            ctx,
            "I cannot manage this role because it is above or equal to my highest role."
        )

    if member == ctx.guild.me:
        return await send_card(
            ctx,
            "I cannot give this role to myself."
        )

    try:
        await member.add_roles(
            role,
            reason=f"{role_type} role given by {ctx.author}"
        )
    except discord.Forbidden:
        return await send_card(
            ctx,
            "I do not have permission to give this role."
        )

    # User mention may work normally.
    # Role mention is visually shown but role notifications are suppressed.
    allowed_mentions = discord.AllowedMentions(
        users=True,
        roles=False,
        everyone=False,
        replied_user=False
    )

    await send_card(
        ctx,
        f"## {role_type.title()}\n\n"
        f"Given User: {member.mention}\n"
        f"Role: {role.mention}",
        allowed_mentions=allowed_mentions
    )


@bot.command()
@is_admin()
async def friend(ctx, member: discord.Member = None):
    await give_configured_role(
        ctx,
        "friend",
        member
    )


@bot.command()
@is_admin()
async def mod(ctx, member: discord.Member = None):
    await give_configured_role(
        ctx,
        "mod",
        member
    )


@bot.command()
@is_admin()
async def staff(ctx, member: discord.Member = None):
    await give_configured_role(
        ctx,
        "staff",
        member
    )


@bot.command()
@is_admin()
async def jail(ctx, member: discord.Member = None):
    await give_configured_role(
        ctx,
        "jail",
        member
    )


@bot.command()
@is_admin()
async def vip(ctx, member: discord.Member = None):
    await give_configured_role(
        ctx,
        "vip",
        member
    )


# =========================================================
# ERROR HANDLER
# =========================================================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        return await send_card(
            ctx,
            "You do not have permission to use this command."
        )

    if isinstance(error, commands.CheckFailure):
        return await send_card(
            ctx,
            "Administrator permission required."
        )

    if isinstance(error, commands.MissingRequiredArgument):
        return await send_card(
            ctx,
            "Missing required argument."
        )

    if isinstance(error, commands.BadArgument):
        return await send_card(
            ctx,
            "Invalid argument."
        )

    print(
        f"Command error in {ctx.command}: {error}"
    )


# =========================================================
# START
# =========================================================

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN was not found in the environment."
    )

bot.run(TOKEN)
