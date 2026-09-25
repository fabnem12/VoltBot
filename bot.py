import asyncio
import json
import discord
from langdetect import detect
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, Union
from unidecode import unidecode
import datetime
import io
import os
import random
import requests
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import emojis
import regex
import arrow

import constantes

def stockePID():
    from os.path import join, dirname, abspath
    import os
    import pickle

    fichierPID = join(dirname(abspath(__file__)), "fichierPID.p")
    if not os.path.exists(fichierPID):
        pickle.dump(set(), open(fichierPID, "wb"))

    pids = pickle.load(open(fichierPID, "rb"))
    pids.add(os.getpid())

    pickle.dump(pids, open(fichierPID, "wb"))
stockePID()

#constants
voltServer = 567021913210355745

#-channels
wordTrainChannel = 1141992409165733988
deletedEditedMessages = 1467557771598758234
modLogId = 929466478678405211
reportChannelId = 806219815760166972
activeReportChannelId = 1551498084196941834
carlBotCommandChannelId = 567482036919730196
modMessageLog = 1037071502656405584
courtChannel = 912092404570554388
introChannel = 567024817128210433
european_memes = 731895134639095909
memes = 656609693912793100
channelKewkId = 1419775038806163666
casualChannel = 800171310940291103
pollingStation = 806213028034510859
longTimeMembers = 1488637307224326245

#-roles
voltDiscordTeam = 674583505446895616
voltSubTeam = 858692593104715817
voltAdmin = 567023540193198080
inCourt = 709532690692571177
muted = 806589642287480842
welcomeTeam = 801958112173096990

#users
botAdmin = 619574125622722560

#info in json
if "bot_info.json" in os.listdir(os.path.dirname(__file__)):
    with open("bot_info.json", "r") as f:
        info = json.load(f)
else:
    info = {}
    with open("bot_info.json", "w") as f:
        json.dump(info, f)

def save():
    with open("bot_info.json", "w") as f:
        json.dump(info, f, default=list)


def get_birthdays_storage():
    if "birthdays" not in info or not isinstance(info["birthdays"], dict):
        info["birthdays"] = {}
    return info["birthdays"]


def get_reports_storage():
    if "reports" not in info or not isinstance(info["reports"], dict):
        info["reports"] = {}
    reports = info["reports"]
    reports.setdefault("cases", {})
    reports.setdefault("user_threads", {})
    reports.setdefault("migrated_archive_messages", [])
    reports.setdefault("forwarded_migrated_messages", [])
    return reports


def report_thread_name(user: Union[discord.User, discord.Member]) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)
    name = regex.sub(r"\s+", " ", name).strip()
    return f"reports - {name}"[:100]


def case_thread_name(user: Union[discord.User, discord.Member], message_id: int) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)
    name = regex.sub(r"\s+", " ", name).strip()
    return f"report - {name} - {message_id}"[:100]


async def safe_fetch_channel(guild: discord.Guild, channel_id: int):
    channel = guild.get_channel(channel_id)
    if channel is None:
        channel = await guild.fetch_channel(channel_id)
    return channel


def thread_link(guild_id: int, thread_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{thread_id}"


async def get_or_create_user_report_thread(guild: discord.Guild, report_channel: discord.TextChannel, user: Union[discord.User, discord.Member]):
    reports = get_reports_storage()
    user_threads = reports["user_threads"]
    user_id = str(user.id)

    if user_id in user_threads:
        try:
            thread = await guild.fetch_channel(int(user_threads[user_id]))
            if isinstance(thread, discord.Thread):
                try:
                    await thread.edit(archived=False, locked=False)
                except Exception:
                    pass
                return thread
        except Exception:
            pass

    base_msg = await report_channel.send(f"Report history for {user.mention} (`{user.id}`).")
    thread = await base_msg.create_thread(name=report_thread_name(user), auto_archive_duration=10080)
    user_threads[user_id] = str(thread.id)
    save()
    return thread


async def send_report_attachment(channel: discord.TextChannel, filename: str, content: bytes, reference: discord.MessageReference):
    await channel.send(file=discord.File(io.BytesIO(content), filename=filename), reference=reference)


async def duplicate_report_attachments(msg: discord.Message, destinations):
    for att in msg.attachments:
        r = requests.get(att.url)
        for channel, reference in destinations:
            await send_report_attachment(channel, att.filename, r.content, reference)


async def close_report_case(guild: discord.Guild, active_message_id: int, status: str, closed_by: Union[discord.User, discord.Member], summary: str = "", delete_active: bool = True):
    reports = get_reports_storage()
    case = reports["cases"].get(str(active_message_id))
    if not case:
        return False
    if case.get("status") != "open":
        return True

    case["status"] = status
    case["closed_by"] = str(closed_by.id)
    case["closed_at"] = int(time.time())
    if summary:
        case["close_summary"] = summary
    save()

    colors = {
        "dismissed": discord.Color.red(),
        "warning": discord.Color.gold(),
        "punishment": discord.Color.green(),
        "manual_delete": discord.Color.light_grey(),
        "punishment_marked_applied": discord.Color.green(),
    }

    try:
        archive_channel = await safe_fetch_channel(guild, int(case["archive_channel_id"]))
        archive_msg = await archive_channel.fetch_message(int(case["archive_message_id"]))
        if archive_msg.embeds:
            embed = archive_msg.embeds[0]
            embed.color = colors.get(status, embed.color)
            embed.add_field(name="Status", value=f"{status} by {closed_by.mention}" + (f"\n{summary}" if summary else ""), inline=False)
            await archive_msg.edit(embed=embed)
    except Exception:
        pass

    close_note = f"Case closed as **{status}** by {closed_by.mention}."
    if summary:
        close_note += f"\nSummary: {summary}"

    for thread_key in ("active_thread_id",):
        thread_id = case.get(thread_key)
        if not thread_id:
            continue
        try:
            thread = await guild.fetch_channel(int(thread_id))
            await thread.send(close_note)
        except Exception:
            pass

    user_thread_id = case.get("user_thread_id")
    user_thread_message_id = case.get("user_thread_message_id")
    if user_thread_id and user_thread_message_id:
        try:
            user_thread = await guild.fetch_channel(int(user_thread_id))
            user_thread_msg = await user_thread.fetch_message(int(user_thread_message_id))
            await user_thread_msg.edit(content=f"{user_thread_msg.content}\n\n{close_note}")
        except Exception:
            pass
    elif user_thread_id:
        try:
            user_thread = await guild.fetch_channel(int(user_thread_id))
            await user_thread.send(close_note)
        except Exception:
            pass

    if delete_active:
        try:
            active_channel = await safe_fetch_channel(guild, int(case["active_channel_id"]))
            active_msg = await active_channel.fetch_message(int(active_message_id))
            await active_msg.delete()
        except Exception:
            pass

    return True


async def close_deleted_active_report(msg: discord.Message, closed_by: Optional[Union[discord.User, discord.Member]] = None):
    if msg.channel.id != activeReportChannelId:
        return False
    reports = get_reports_storage()
    case = reports["cases"].get(str(msg.id))
    if not case:
        return False
    if case.get("status") != "open":
        return True
    return await close_report_case(msg.guild, msg.id, "manual_delete", closed_by or msg.author, "Active report message was manually deleted.", delete_active=False)


def extract_user_id_from_mention(value: str) -> Optional[int]:
    match = regex.search(r"<@!?(\d+)>", value or "")
    return int(match.group(1)) if match else None


async def forward_report_with_attachments(report_msg: discord.Message, destination: discord.abc.Messageable):
    content = f"Historical report from {report_msg.jump_url}"
    if report_msg.embeds:
        copied_msg = await destination.send(content, embed=report_msg.embeds[0])
    else:
        copied_msg = await destination.send(content)
    async for msg in report_msg.channel.history(after=report_msg.created_at, limit=20, oldest_first=True):
        if msg.id == report_msg.id:
            continue
        if msg.embeds:
            break
        reference = msg.reference
        if reference and reference.message_id == report_msg.id:
            files = []
            for att in msg.attachments:
                r = requests.get(att.url)
                files.append(discord.File(io.BytesIO(r.content), filename=att.filename))
            if files:
                await destination.send(f"Attachments for {report_msg.jump_url}", files=files)
            elif msg.content:
                await destination.send(msg.content)
    return copied_msg


class ReportCloseSummaryModal(discord.ui.Modal):
    def __init__(self, active_message_id: int, status: str):
        super().__init__(title=f"Close report as {status}")
        self.active_message_id = active_message_id
        self.status = status
        self.summary = discord.ui.TextInput(
            label="Summary",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            placeholder="Optional: what was done / why the case is closed",
        )
        self.add_item(self.summary)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return
        if not await isMod(interaction.guild, interaction.user.id):
            await interaction.response.send_message("Only mods can close reports.", ephemeral=True)
            return
        await close_report_case(interaction.guild, self.active_message_id, self.status, interaction.user, str(self.summary.value))
        await interaction.response.send_message("Report closed.", ephemeral=True)


class ReportCloseConfirmView(discord.ui.View):
    def __init__(self, active_message_id: int, status: str):
        super().__init__(timeout=900)
        self.active_message_id = active_message_id
        self.status = status

    @discord.ui.button(label="Confirm close", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return
        if not await isMod(interaction.guild, interaction.user.id):
            await interaction.response.send_message("Only mods can close reports.", ephemeral=True)
            return
        await interaction.response.send_modal(ReportCloseSummaryModal(self.active_message_id, self.status))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return
        if not await isMod(interaction.guild, interaction.user.id):
            await interaction.response.send_message("Only mods can cancel report closure.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Close cancelled.", view=None)


def get_paris_now() -> arrow.Arrow:
    return arrow.now("Europe/Paris")

async def dmChannelUser(user):
    if user.dm_channel is None:
        await user.create_dm()
    return user.dm_channel

async def isMod(guild, memberId):
    member = await guild.fetch_member(memberId)
    return any(role.id in (voltDiscordTeam, voltSubTeam, voltAdmin) for role in member.roles)

async def isWelcome(guild, memberId):
    member = await guild.fetch_member(memberId)
    return any(x.id == welcomeTeam for x in member.roles)

async def ban(msg, banAppealOk = True):
    if msg.guild.id != voltServer or not await isMod(msg.guild, msg.author.id): #not on volt server or not a mod of the volt server
        return

    userIdRaw = msg.content.split(" ")[1]
    if userIdRaw.isdigit():
        userId = int(userIdRaw)
    else:
        userId = int(userIdRaw[2:-1])

    try:
        user = await msg.guild.fetch_member(userId)
    except:
        user = await bot.fetch_user(userId)

    channel = await dmChannelUser(user)
    banReason = ' '.join(msg.content.split(' ')[2:])
    if banReason == "": banReason = "no reason given"

    try:
        if banAppealOk:
            await channel.send(f"Ban reason: {banReason}\nBan appeal form: https://docs.google.com/forms/d/189lUm5ONdJHcI4C8QB4ml__2aAnygmxbCETrBMVhos0. Your discord id (asked in the form) is `{userId}`.")
        else:
            await channel.send(f"Ban reason: {banReason}")
    except:
        pass
    else:
        await msg.add_reaction("👌")

    banBy = f" (ban by {msg.author.mention})" if "(ban by" not in banReason else ""
    try:
        await msg.guild.ban(user, reason = banReason + banBy, delete_message_seconds = 0)
    except Exception as e:
        await (await dmChannelUser(msg.author)).send(f"Unable to ban {user.name}\n{e}")
    else:
        await msg.channel.send(f"Banned **{user.name}**")

async def exclusion(before, after):
    if before.guild.id == voltServer and before.communication_disabled_until is None and after.communication_disabled_until:
        #let's find the reason
        logFound = False
        async for entry in before.guild.audit_logs(action=discord.AuditLogAction.member_update):
            reason = entry.reason
            mod = entry.user
            time = entry.created_at
            
            modlog = await before.guild.fetch_channel(modLogId)
            e = discord.Embed(title = "time out", timestamp = time, color = 0x502379)
            e.add_field(name = "User:", value = f"{after}", inline=False)
            e.add_field(name = "Reason:", value = reason, inline=False)
            e.add_field(name = "Responsible moderator:", value = f"{mod}", inline=False)
            e.set_footer(text = f"ID: {after.id}")

            await modlog.send(embed = e)
            break

async def report(messageId, guild, channel, user, param = ""):
    channelId = reportChannelId

    msgInit = await channel.fetch_message(messageId)
    msg = msgInit
    is_user_report = isinstance(param, str)
    if not isinstance(param, str):
        channelId = modMessageLog
        reporter = param
        param = None
    else:
        reference = msgInit.reference
        reporter = user.id
        if reference and msgInit.content.startswith(constantes.prefixVolt+"report"):
            msg = await channel.fetch_message(reference.message_id)
            await msgInit.delete()
        else: #report by reaction
            ruleEmoji = 742137941211611208
            emoji = await guild.fetch_emoji(ruleEmoji)

            await msgInit.remove_reaction(emoji, user)
    
    reportChannel = await guild.fetch_channel(channelId)

    content = msg.content
    author = msg.author
    channelName = channel.mention

    e = discord.Embed(title = f"Message {'reported' if param is not None else 'deleted by a mod'}", description = content, timestamp = msg.created_at)
    if author.avatar:
        e.set_author(name = author.name, icon_url = author.avatar.url)
    e.add_field(name = "Author", value=author.mention, inline=False)
    e.add_field(name = "Channel", value=channelName, inline=False)
    e.add_field(name = "Reporter", value=f"<@{reporter}>", inline=False)
    if param is not None:
        e.add_field(name = "Link to message", value=msg.jump_url)
        if param != "":
            e.add_field(name = "Details", value=param)
    activeMsgReport = None
    activeThread = None
    userThread = None
    userThreadMsg = None

    if is_user_report:
        assert isinstance(reportChannel, discord.TextChannel)
        activeReportChannel = await guild.fetch_channel(activeReportChannelId)
        assert isinstance(activeReportChannel, discord.TextChannel)
        activeMsgReport = await activeReportChannel.send(embed=e)
        activeThread = await activeMsgReport.create_thread(name=case_thread_name(author, msg.id), auto_archive_duration=10080)

        archiveEmbed = discord.Embed.from_dict(e.to_dict())
        archiveEmbed.add_field(name="Discussion thread", value=thread_link(guild.id, activeThread.id), inline=False)
        msgReport = await reportChannel.send(embed=archiveEmbed)

        userThread = await get_or_create_user_report_thread(guild, reportChannel, author)
        userThreadMsg = await userThread.send(
            f"Report about {author.mention} (`{author.id}`) at <t:{int(msg.created_at.timestamp())}:f>.\n"
            f"Archive: {msgReport.jump_url}\n"
            f"Active discussion: {thread_link(guild.id, activeThread.id)}\n"
            f"Original message: {msg.jump_url}\n"
            f"Reporter: <@{reporter}>" + (f"\nDetails: {param}" if param else ""),
            embed=e,
        )

        reports = get_reports_storage()
        reports["cases"][str(activeMsgReport.id)] = {
            "type": "user_report",
            "archive_message_id": str(msgReport.id),
            "active_message_id": str(activeMsgReport.id),
            "archive_channel_id": str(reportChannel.id),
            "active_channel_id": str(activeReportChannel.id),
            "active_thread_id": str(activeThread.id),
            "user_thread_id": str(userThread.id),
            "user_thread_message_id": str(userThreadMsg.id),
            "reported_user_id": str(author.id),
            "reporter_id": str(reporter),
            "status": "open",
            "created_at": int(time.time()),
        }
        save()
    else:
        msgReport = await reportChannel.send(embed=e)
    
    # reactions to see what has been done with the report
    # either do nothing, informal warn, formal punishment
    
    await msgReport.add_reaction("❌")
    await msgReport.add_reaction("⚠️")
    await msgReport.add_reaction("🔨")
    if activeMsgReport:
        await activeMsgReport.add_reaction("❌")
        await activeMsgReport.add_reaction("⚠️")
        await activeMsgReport.add_reaction("🔨")

    ref = discord.MessageReference(channel_id = msgReport.channel.id, message_id = msgReport.id)
    destinations = [(reportChannel, ref)]
    if activeMsgReport:
        activeRef = discord.MessageReference(channel_id=activeMsgReport.channel.id, message_id=activeMsgReport.id)
        destinations.append((activeMsgReport.channel, activeRef))
    if userThread and userThreadMsg:
        userThreadRef = discord.MessageReference(channel_id=userThread.id, message_id=userThreadMsg.id)
        destinations.append((userThread, userThreadRef))
    await duplicate_report_attachments(msg, destinations)

async def assign_base_roles(newMember, guild):
    roles = [guild.get_role(x) for x in (708313061764890694, 708315631774335008, 754029717211971705, 708313617686069269, 856620435164495902, 596511307209900053, 717132666721402949, 1101606908437221436)]
    await newMember.add_roles(*roles)

async def introreact(messageId, guild, emojiHash, channel, user):
    peaceFingersEmoji = 712416440099143708
    if emojiHash != peaceFingersEmoji:
        return

    message = await channel.fetch_message(messageId)
    await assign_base_roles(message.author, guild)
    await message.add_reaction("👌")


async def reportreact(messageId, guild, emojiHash, channel, user):
    ruleEmoji = 742137941211611208
    if emojiHash != ruleEmoji:
        return

    await report(messageId, guild, channel, user)

regex_x = regex.compile(r"https://.*?x.com")
HIDDEN_MESSAGE = "_ _"

def is_domain(domain: str, allowed_domains) -> bool:
    domain = domain.lower()
    return any(domain == d or domain.endswith(f".{d}") for d in allowed_domains)

def is_non_english(text: str) -> bool:
    try:
        return detect(text) != "en"
    except Exception:
        return False

def fixupx_link(path: str) -> str:
    path = path.split("?")[0].rstrip("/")
    if not path.endswith("/en"):
        path += "/en"
    return "https://fixupx.com" + path

async def fetch_saved_message(channel, message_id):
    try:
        return await channel.fetch_message(int(message_id))
    except (discord.NotFound, TypeError, ValueError):
        return None

async def verif_news_source(message):
    """
    Check that there is no untrusted news source in the message
    """

    untrusted = {
        "x.com/visegrad24": "Visegrád 24", 
        "trtworld.com/": "TRT", "x.com/trtworld": "TRT",
        "x.com/afpost": "AF Post",
        "www.scmp.com": "South China Morning Post",
        "x.com/cerfia": "Cerfia",
        "x.com/mediavenir": "Mediavenir",
        "x.com/dailymail": "Daily Mail",
        "dailymail.co.uk": "Daily Mail",
        "x.com/rt_com": "RT",
        "rt.com": "RT",
        "x.com/sputnikint": "Sputnik",
        "sputniknews.com": "Sputnik",
        "x.com/thesun": "The Sun",
        "thesun.co.uk": "The Sun",
        "x.com/infowars": "Infowars",
        "infowars.com": "Infowars",
        "x.com/breitbartnews": "Breitbart",
        "breitbart.com": "Breitbart",
        "x.com/sundaymirror": "Sunday Mirror",
        "sundaymirror.co.uk": "Sunday Mirror",
        "x.com/nypost": "New York Post",
        "x.com/dailystar": "Daily Star",
        "dailystar.co.uk": "Daily Star",
        "x.com/express": "Daily Express",
        "express.co.uk": "Daily Express",
        "x.com/zerohedge": "Zero Hedge",
        "zerohedge.com": "Zero Hedge",
        "x.com/m_star_online": "Morning Star",
        "morningstaronline.co.uk": "Morning Star",
        "x.com/occupydemocrats": "Occupy Democrats",
        "occupydemocrats.com": "Occupy Democrats",
        "x.com/yournewswire": "Your News Wire",
        "yournewswire.com": "Your News Wire",
        "x.com/ntdnews": "NTD News",
        "ntd.com": "NTD News",
        "ntd.tv": "NTD News",
        "x.com/nzz": "NZZ",
        "x.com/telegraph": "Daily Telegraph",
        "telegraph.co.uk": "Daily Telegraph",
        "x.com/foxnews": "Fox News",
        "foxnews.com": "Fox News",
        "x.com/brevesdepresse": "Brèves de presse",
        }

    ref = discord.MessageReference(channel_id = message.channel.id, message_id = message.id)
    msg_low = message.content.lower()
    #if (len(regex_x.findall(msg_low)) or "twitter.com/" in msg_low) and not message.author.bot:
        #await message.channel.send(f":warning: {message.author.mention} This server recommends no longer sharing content from x.com (formerly known as Twitter). For news, please send the direct link for them rather than a tweet referring to them.", reference = ref)

    for link, source in untrusted.items():        
        pattern = regex.compile(r"(?:^|\s|https?://(?:www\.)?)" + regex.escape(link) + r"(?:/|\s|$)")
        
        if pattern.search(msg_low):
            await message.channel.send(f":warning: This message contains a link to an untrusted news source ({source})", reference = ref)
            return

async def verif_word_train(message):
    """
    Word train channel:
    your word has to start with the same letter as the word before ends. Example "The elephant tries snorkeling..."
    """
    
    if message.channel.id != wordTrainChannel or message.author.bot:
        return

    isLetter = lambda x: x in "abcdefghijklmnopqrstuvwxyz "

    msgTxt = unidecode(message.content.lower())
    msgLetters = "".join(filter(isLetter, msgTxt))
    words = msgLetters.split()

    #previous logic
    """
    if len(words) > 1:
        lastLetter = words[0][-1]

        for word in words[1:]:
            if word[0] != lastLetter:
                await message.delete()
                await message.channel.send(f"<:bonk:843489770918903819> {message.author.mention}")

                return False
            else:
                lastLetter = word[-1]
    """
    #new logic
    previousMsg = None
    async for msg in message.channel.history(oldest_first=False, limit=None):
        if msg != message and not msg.author.bot:
            previousMsg = msg
            previousMsgLetters = "".join(filter(isLetter, unidecode(previousMsg.content.lower())))

            if len(words) > 1 or previousMsgLetters.strip().lower()[-1] != msgTxt[0] or previousMsg.author.id == message.author.id:
                await message.delete()
                await message.channel.send(f"<:bonk:843489770918903819> {message.author.mention}")
            
            break

    return True

async def report_automatic_warn(message):
    """
    Forwarding automatic warns to the report channel
    """

    if message.channel.id == modLogId:
        e = message.embeds[0]
        if "Automatic action" in e.description:
            reportChannel = await message.channel.guild.fetch_channel(reportChannelId)
            await message.forward(reportChannel)

async def smart_tweet(msg: discord.Message, delete: bool = False):
    """
    Reply to messages with Twitter links whose video embed fails with fixupx
    """

    if msg.author.bot: return
    
    msgId = str(msg.id)
    infoSmartTweet = info.get("smart_tweet")
    if infoSmartTweet is None:
        infoSmartTweet = info["smart_tweet"] = dict()

    if delete and msgId in infoSmartTweet:
        msgRep = await fetch_saved_message(msg.channel, infoSmartTweet[msgId])
        if msgRep:
            await msgRep.delete()
        del infoSmartTweet[msgId]
        save()
        return
    elif delete:
        return

    links = regex.findall(r"https:\/\/([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])", msg.content)
    twitterDomains = ("x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com", "girlcockx.com")
    twitterLinks = []
    for domain, path in links:
        if is_domain(domain, twitterDomains):
            link = fixupx_link(path)
            if link not in twitterLinks:
                twitterLinks.append(link)
    anyVideoTweet = msg.embeds and any(e.image.proxy_url and "amplify_video_thumb" in e.image.proxy_url for e in msg.embeds)
    nonEnglish = msg.embeds and any(e.description and len(e.description.split()) > 4 and is_non_english(e.description) and "/en" not in msg.content for e in msg.embeds)

    if len(twitterLinks) and (anyVideoTweet or nonEnglish):
        ref = discord.MessageReference(channel_id = msg.channel.id, message_id = msg.id)
        
        if msgId in infoSmartTweet:
            msgRep = await fetch_saved_message(msg.channel, infoSmartTweet[msgId])
            if msgRep:
                await msgRep.edit(content = "\n".join(twitterLinks))
            else:
                rep = await msg.channel.send("\n".join(twitterLinks), reference = ref)
                infoSmartTweet[msgId] = rep.id
                save()
        else:
            rep = await msg.channel.send("\n".join(twitterLinks), reference = ref)
            infoSmartTweet[msgId] = rep.id
            save()
    elif msg.edited_at and msgId in infoSmartTweet:
        msgRep = await fetch_saved_message(msg.channel, infoSmartTweet[msgId])
        if msgRep:
            await msgRep.edit(content = HIDDEN_MESSAGE)
        else:
            del infoSmartTweet[msgId]
            save()

def clean_social_link(domain: str, path: str) -> Optional[str]:
    domain = domain.lower()
    parsed = urlsplit(f"https://{domain}{path}")

    instagram_domains = ("instagram.com", "instagr.am", "ddinstagram.com", "kkinstagram.com")
    if is_domain(domain, instagram_domains):
        cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
        return cleaned if cleaned != f"https://{domain}{path}" else None

    if is_domain(domain, ("youtube.com", "youtu.be")):
        trackers = {"si", "feature"}
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in trackers and not key.lower().startswith("utm_")
        ]
        cleaned_query = urlencode(filtered_query, doseq=True)
        cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, cleaned_query, parsed.fragment))
        return cleaned if cleaned != f"https://{domain}{path}" else None

    return None

async def sanitize_social_links(msg: discord.Message, delete: bool = False):
    if msg.author.bot: return

    msgId = str(msg.id)
    infoSanitizedLinks = info.get("sanitized_links")
    if infoSanitizedLinks is None:
        infoSanitizedLinks = info["sanitized_links"] = dict()

    if delete and msgId in infoSanitizedLinks:
        msgRep = await fetch_saved_message(msg.channel, infoSanitizedLinks[msgId])
        if msgRep:
            await msgRep.delete()
        del infoSanitizedLinks[msgId]
        save()
        return
    elif delete:
        return

    links = regex.findall(r"https:\/\/([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])", msg.content)
    cleanLinks = []
    for domain, path in links:
        cleanLink = clean_social_link(domain, path)
        if cleanLink and cleanLink not in cleanLinks:
            cleanLinks.append(cleanLink)

    if len(cleanLinks):
        ref = discord.MessageReference(channel_id = msg.channel.id, message_id = msg.id)
        content = "\n".join(cleanLinks)
        if msgId in infoSanitizedLinks:
            msgRep = await fetch_saved_message(msg.channel, infoSanitizedLinks[msgId])
            if msgRep:
                await msgRep.edit(content = content)
            else:
                rep = await msg.channel.send(content, reference = ref)
                infoSanitizedLinks[msgId] = rep.id
        else:
            rep = await msg.channel.send(content, reference = ref)
            infoSanitizedLinks[msgId] = rep.id
        save()
    elif msg.edited_at and msgId in infoSanitizedLinks:
        msgRep = await fetch_saved_message(msg.channel, infoSanitizedLinks[msgId])
        if msgRep:
            await msgRep.edit(content = HIDDEN_MESSAGE)
        else:
            del infoSanitizedLinks[msgId]
            save()

def has_meme_content(message: discord.Message) -> bool:
    thread_created_type = getattr(discord.MessageType, "thread_created", None)
    return (
        bool(message.embeds)
        or bool(message.attachments)
        or bool(getattr(message, "thread", None))
        or bool(getattr(message, "has_thread", False))
        or (thread_created_type is not None and message.type == thread_created_type)
    )


async def reminder_meme(message: discord.Message):
    # Check the message got sent in #european-memes and is not a bot message.
    if message.channel.id != european_memes or message.author.bot:
        return

    if has_meme_content(message):
        return

    try:
        await asyncio.sleep(5)
        message = await message.channel.fetch_message(message.id)
    except (discord.NotFound, discord.Forbidden):
        return

    if has_meme_content(message):
        return

    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden):
        return

    try:
        dm_channel = await dmChannelUser(message.author)
        await dm_channel.send(f"Your message in {message.channel.mention} was deleted because that channel is only for memes. Please post an image, video, GIF, or a link that embeds in Discord.")
    except Exception:
        pass


async def ensure_poll_thread(message: discord.Message):
    """Create a public discussion thread under pollingStation posts that lack one."""
    if message.channel.id != pollingStation or message.author.bot:
        return
    assert isinstance(message.channel, discord.TextChannel)

    has_thread = bool(getattr(message, "thread", None)) or bool(getattr(message, "has_thread", False))
    if has_thread:
        return

    poll = getattr(message, "poll", None)
    if poll:
        assert isinstance(poll, discord.Poll)
        question = poll.question
        title_src = (question or message.content.strip() or "Poll discussion")
        title = (title_src[:97] + "...") if len(title_src) > 100 else title_src

        try:
            await message.channel.create_thread(name=title, message=message, auto_archive_duration=10080)
        except Exception:
            pass


async def handle_report_reaction_color(channel: Optional[discord.abc.Messageable], message_id: int, emoji_hash: Union[int, str], user: Union[discord.User, discord.Member]):
    """Update report embed color and ask for confirmation before closing active reports."""
    if user.bot or channel is None:
        return

    if not isinstance(channel, discord.TextChannel) or channel.id not in (reportChannelId, activeReportChannelId):
        return

    colors = {
        "❌": discord.Color.red(),
        "⚠️": discord.Color.gold(),
        "🔨": discord.Color.green(),
    }

    if emoji_hash not in colors:
        return

    if not await isMod(channel.guild, user.id):
        return

    try:
        msg = await channel.fetch_message(message_id)
    except Exception:
        return

    if not msg.embeds:
        return

    embed = msg.embeds[0]
    embed.color = colors.get(emoji_hash, embed.color)

    try:
        await msg.edit(embed=embed)
    except Exception:
        pass

    if channel.id != activeReportChannelId:
        return

    reports = get_reports_storage()
    case = reports["cases"].get(str(message_id))
    if not case or case.get("status") != "open":
        return

    statuses = {
        "❌": "dismissed",
        "⚠️": "warning",
        "🔨": "punishment",
    }
    status = statuses[emoji_hash]
    if case.get("type") == "banned_word" and emoji_hash == "🔨":
        status = "punishment_marked_applied"
    confirm_text = f"{user.mention} wants to close this report as **{status}**. Confirm?"
    try:
        thread = await channel.guild.fetch_channel(int(case["active_thread_id"]))
        await thread.send(confirm_text, view=ReportCloseConfirmView(message_id, status))
    except Exception:
        await channel.send(confirm_text, reference=discord.MessageReference(channel_id=channel.id, message_id=message_id), view=ReportCloseConfirmView(message_id, status))


async def count_banned_words(guild: discord.Guild, author: discord.Member, msg_txt: str, channel: Optional[discord.TextChannel] = None):
    banned_words = constantes.banned_words
    
    msg_lower = msg_txt.lower()
    banned_word_used = None
    for x in banned_words:
        if x in msg_lower:
            banned_word_used = x
            break
    
    if channel is None:
        banned_word_used = msg_txt
    
    if banned_word_used:
        authorId = str(author.id)
        #report the user, the message got deleted for having a banned word in it
        if "banned_words" not in info:
            info["banned_words"] = dict()
        
        if authorId not in info["banned_words"]:
            info["banned_words"][authorId] = []
        
        banned_words_user = info["banned_words"][authorId]
        banned_words_user.append((banned_word_used, time.time()))
        save()
        
        punishment = {1: "nothing", 2: "nothing", 3: "3h of mute", 4: "6h of mute", 5: "24h of mute", 6: "48h of mute"}.get(len(banned_words_user), "1 week")
        
        reportChannel = await guild.fetch_channel(reportChannelId)
        assert isinstance(reportChannel, discord.TextChannel)
        
        summary = f"**User <@{authorId}> used the banned word {banned_word_used}**\nIt's the #{len(banned_words_user)} use of a banned word by the user since the 14th of July 2025.\n\nPrevious uses:\n" + "\n".join(f'{i+1}. {word} <t:{int(timestamp)}>' for i, (word, timestamp) in enumerate(banned_words_user)) + f"\n\n**Recommended punishment based on the number of offenses: __{punishment}__**"
        archiveSummaryMsg = await reportChannel.send(summary)
        
        e = discord.Embed(description = msg_txt)
        if author.avatar:
            e.set_author(name = author.name, icon_url = author.avatar.url)
        e.add_field(name = "Author", value=author.mention, inline=False)
        if channel:
            e.add_field(name = "Channel", value=channel.name, inline=False)
        
        await reportChannel.send(embed = e)

        activeReportChannel = await guild.fetch_channel(activeReportChannelId)
        assert isinstance(activeReportChannel, discord.TextChannel)
        command_hint = "No mute recommended yet."
        if punishment != "nothing":
            duration = punishment.replace(" of mute", "")
            command_hint = f"Run Carl-bot `/mute` in <#{carlBotCommandChannelId}>: user <@{authorId}>, duration `{duration}`, reason `Banned word usage - offense #{len(banned_words_user)}`."
        activeSummaryMsg = await activeReportChannel.send(summary + f"\n\n{command_hint}\n\nReact ❌ to dismiss or 🔨 after the Carl-bot mute has been manually applied.")
        await activeSummaryMsg.add_reaction("❌")
        await activeSummaryMsg.add_reaction("🔨")

        activeThread = await activeSummaryMsg.create_thread(name=case_thread_name(author, activeSummaryMsg.id), auto_archive_duration=10080)
        await activeThread.send(command_hint)

        userThread = await get_or_create_user_report_thread(guild, reportChannel, author)
        userThreadMsg = await userThread.send(
            f"Banned-word report for {author.mention} (`{author.id}`) at <t:{int(time.time())}:f>.\n"
            f"Archive: {archiveSummaryMsg.jump_url}\n"
            f"Active discussion: {thread_link(guild.id, activeThread.id)}\n"
            f"Recommended punishment: {punishment}",
            embed=e,
        )

        reports = get_reports_storage()
        reports["cases"][str(activeSummaryMsg.id)] = {
            "type": "banned_word",
            "archive_message_id": str(archiveSummaryMsg.id),
            "active_message_id": str(activeSummaryMsg.id),
            "archive_channel_id": str(reportChannel.id),
            "active_channel_id": str(activeReportChannel.id),
            "active_thread_id": str(activeThread.id),
            "user_thread_id": str(userThread.id),
            "user_thread_message_id": str(userThreadMsg.id),
            "reported_user_id": str(author.id),
            "suggested_punishment": punishment,
            "manual_action_required": True,
            "action_channel_id": str(carlBotCommandChannelId),
            "status": "open",
            "created_at": int(time.time()),
        }
        save()

async def kekw_board(message: discord.Message, bot: commands.Bot):
    if message.channel.id == channelKewkId: return
    
    if hasattr(message.channel, "category_id") and message.channel.category_id == 820703128503451708:
        return
        #ignore internal channels

    if any(reaction.count == 10 for reaction in message.reactions if reaction.is_custom_emoji() and int(reaction.emoji.id) == 732674441577889994): #kekw emoji
        guild = bot.get_guild(voltServer)
        channelKewk = guild.get_channel(channelKewkId) #kekw-board channel
        assert isinstance(channelKewk, discord.TextChannel)
        
        if "kekw_board" not in info:
            info["kekw_board"] = set()
        elif not isinstance(info["kekw_board"], set):
            info["kekw_board"] = set(info["kekw_board"])
        
        if message.id in info["kekw_board"]:
            # already forwarded
            return
        
        await message.forward(channelKewk)
        info["kekw_board"].add(message.id)
        save()

async def remove_recycle(message: discord.Message):
    if message.channel.id not in (memes, european_memes):
        return
    
    for reaction in message.reactions:
        if "♻️" == reaction.emoji and reaction.count >= 10:
            await message.delete()

def main():
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix=constantes.prefixVolt, help_command=None, intents = intents)

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
        if int(message.created_at.timestamp()) % 100 == 1: #purge the log of deleted-edited-message about every 100 messages
            await purge_log(None, bot.get_guild(voltServer))
            save()

        await verif_word_train(message)
        await verif_news_source(message)
        await report_automatic_warn(message)
        await smart_tweet(message)
        await reminder_meme(message)
        await sanitize_social_links(message)
        await ensure_poll_thread(message)

        if message.content.startswith(".ban"):
            await ban(message, banAppealOk = False)
        
    @bot.event
    async def on_message_edit(before, after):
        await verif_word_train(after)
        await smart_tweet(after)
        await sanitize_social_links(after)
        await reminder_meme(after)
        
    @bot.event
    async def on_message_delete(msg):
        deleted_by = None
        if msg.guild:
            try:
                async for entry in msg.guild.audit_logs(action=discord.AuditLogAction.message_delete):
                    if msg.author.id == entry.target.id and abs(entry.created_at.timestamp() - time.time()) < 3:
                        deleted_by = entry.user
                        break
            except Exception:
                pass

        if await close_deleted_active_report(msg, deleted_by):
            return

        #resend the attachments of deleted messages in #deleted-edited-messages
        deletedMsgChannel = await msg.guild.fetch_channel(deletedEditedMessages)
        for att in msg.attachments:
            r = requests.get(att.url)
            with open(att.filename, "wb") as outfile:
                outfile.write(r.content)

            await deletedMsgChannel.send(f"Attachment to message with ID {msg.id}", file = discord.File(att.filename))
            os.remove(att.filename)

        async for entry in msg.guild.audit_logs(action=discord.AuditLogAction.message_delete):
            if msg.author.id == entry.target.id and abs(entry.created_at.timestamp() - time.time()) < 1 and (await isMod(msg.guild, entry.user.id) or any(x.id == 1038899815821619270 for x in entry.user.roles)):
                await report(msg.id, msg.guild, msg.channel, entry.user, entry.user.id)
                #await report(msg, entry.user.id)
            
            break
    
        await smart_tweet(msg, delete=True)
        await sanitize_social_links(msg, delete=True)
        await count_banned_words(msg.guild, msg.author, msg.content, msg.channel)
    
    @bot.event
    async def on_member_join(member: discord.Member):
        dmChannel = await dmChannelUser(member)
        await dmChannel.send("Hey! **Welcome to the Volt Europa Discord server!**\nTo get the access to the server, please introduce yourself in <#567024817128210433>, citing the country/countries you are from, the languages you speak and whether you are a <:volt:698844154418954311> member.\nYou can check the server rules in <#1349478582354841610>.\nOnce you get verified, you can check <#727489317210947655> to get access to topic channels.")

        voltServer = bot.get_guild(567021913210355745)
        introChannel = await voltServer.fetch_channel(567024817128210433)
        e = discord.Embed(description = f"Welcome {member.mention} <:volt_comfy:842875809526186026>\nPlease **introduce yourself** in this channel, mentioning:\n-your **country**\n-what **languages** you speak\n-whether you are a <:volt:698844154418954311> **member** <:volt_cool_glasses:819137584722345984>.\nA mod will check and give you the appropriate roles. You can check the <#1349478582354841610>.\n\n**Please do not ping moderators, you will get verified in due time**")
        await introChannel.send(embed = e)

    @bot.event
    async def on_member_update(before, after):
        await exclusion(before, after)

    async def traitementRawReact(payload):
        if payload.user_id != bot.user.id: #sinon, on est dans le cas d'une réaction en dm
            messageId = payload.message_id
            guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
            try:
                user = (await guild.fetch_member(payload.user_id)) if guild else (await bot.fetch_user(payload.user_id))
            except:
                user = (await bot.fetch_user(payload.user_id))
            channel = bot.get_channel(payload.channel_id)

            partEmoji = payload.emoji
            emojiHash = partEmoji.id if partEmoji.is_custom_emoji() else partEmoji.name

            return locals()
        else:
            return None
        
    @bot.event
    async def on_raw_reaction_add(payload):
        traitement = await traitementRawReact(payload)
        if traitement:
            messageId = traitement["messageId"]
            user = traitement["user"]
            guild = traitement["guild"]
            emojiHash = traitement["emojiHash"]
            channel = traitement["channel"]

            await reportreact(messageId, guild, emojiHash, channel, user)
            if emojiHash == 732674441577889994: #kekw emoji
                await kekw_board(await channel.fetch_message(messageId), bot)
            if emojiHash == "♻️": #recycle emoji
                await remove_recycle(await channel.fetch_message(messageId))
            
            if await isWelcome(guild, user.id) or await isMod(guild, user.id):
                await introreact(messageId, guild, emojiHash, channel, user)
            await handle_report_reaction_color(channel, messageId, emojiHash, user)

    @bot.command(name = "ayo")
    async def ayo(ctx):
        await ctx.send("ayo")

    @bot.command(name = "verify")
    async def verify(ctx, member: Optional[discord.Member]):
        if not await isWelcome(ctx.guild, ctx.message.author.id) and not await isMod(ctx.guild, ctx.message.author.id):
            return
        reference = ctx.message.reference
        if reference is None and member is None:
            return

        db = [
            ('🇪🇺', "Europe", []),
            ('🇦🇱', "Albania", ["Albanian"]),
            ('🇦🇲', "Armenia", ["Armenian"]),
            ('🇦🇩', "Andorra", ["Catalan", "Spanish", "French"]),
            ('🇦🇹', "Austria", ["German"]),
            ('🇦🇿', "Azerbeijan", ["Azerbaijani"]),
            ('🇧🇾', "Belarus", ["Belarusian", "Russian"]),
            ('🇧🇪', "Belgium", ["Dutch", "French", "German"]),
            ('🇧🇦', "Bosnia & Herzegovina", ["Bosnian"]),
            ('🇧🇬', "Bulgaria", ["Bulgarian"]),
            ('🇭🇷', "Croatia", ["Serbo-Croatian"]),
            ('🇨🇾', "Cyprus", ["Greek", "Turkish"]),
            ('🇨🇿', "Czechia", ["Czech"]),
            ('🇩🇰', "Denmark", ["Danish"]),
            ('🇪🇪', "Estonia", ["Estonian"]),
            ('🇫🇮', "Finland", ["Finnish", "Swedish"]),
            ('🇫🇷', "France", ["French"]),
            ('🇩🇪', "Germany", ["German"]),
            ('🇬🇪', "Georgia", ["Georgian"]),
            ('🇬🇷', "Greece", ["Greek"]),
            ('🇭🇺', "Hungary", ["Hungarian"]),
            ('🇮🇸', "Iceland", ["Icelandic"]),
            ('🇮🇪', "Ireland", ["Irish"]),
            ('🇮🇹', "Italy", ["Italian"]),
            ('🇽🇰', "Kosovo", ["Albanian"]),
            ('🇰🇿', "Kazakhstan", ["Kazakh"]),
            ('🇱🇻', "Latvia", ["Latvian"]),
            ('🇱🇮', "Liechteinstein", ["German"]),
            ('🇱🇹', "Lithuania", ["Lithuanian"]),
            ('🇱🇺', "Luxembourg", ["Luxembourgish", "French", "German"]),
            ('🇲🇹', "Malta", ["Maltese"]),
            ('🇲🇩', "Moldova", ["Romanian"]),
            ('🇲🇨', "Monaco", ["French"]),
            ('🇲🇪', "Montenegro", []),
            ('🇳🇱', "Netherlands", ["Dutch"]),
            ('🇲🇰', "North Macedonia", ["Macedonian"]),
            ('🇳🇴', "Norway", ["Norwegian"]),
            ('🇵🇱', "Poland", ["Polish"]),
            ('🇵🇹', "Portugal", ["Portuguese"]),
            ('🇷🇴', "Romania", ["Romanian"]),
            ('🇷🇺', "Russia", ["Russian"]),
            ('🇸🇲', "San Marino", ["Italian"]),
            ('🇷🇸', "Serbia", ["Serbo-Croatian"]),
            ('🇸🇰', "Slovakia", ["Slovak"]),
            ('🇸🇮', "Slovenia", ["Slovene"]),
            ('🇪🇸', "Spain", ["Spanish"]),
            ('🇸🇪', "Sweden", ["Swedish"]),
            ('🇨🇭', "Switzerland", ["German", "French", "Italian"]),
            ('🇹🇷', "Turkey", ["Turkish"]),
            ('🇬🇧', "United Kingdom", []),
            ('🇺🇦', "Ukraine", ["Ukrainian"]),
            ('🇻🇦', "Vatican", []),
            (':region_asia:', "Asia", []),
            (':region_africa:', "Africa", []),
            (':region_northamerica:', "North America", []),
            (':region_oceania:', "Oceania", []),
            (':region_southamerica:', "South America", [])
        ]

        countries = list(emojis.get(ctx.message.content))
        reg = regex.compile(r"<(:\w+:)\d+>")
        countries += reg.findall(ctx.message.content)

        roles_countries = []
        roles_langs_add = []
        for (emoji, country_name, languages) in db:
            if emoji in countries:
                roles_countries.append(country_name)
                roles_langs_add.extend(languages)

        reg_lang_add = regex.compile(r"\+(\w+)")
        roles_langs_add.extend(reg_lang_add.findall(ctx.message.content))
        roles_langs_add.append("English")
        reg_lang_remove = regex.compile(r"-(\w+)")
        roles_langs_remove = reg_lang_remove.findall(ctx.message.content)

        roles_langs = set(roles_langs_add) - set(roles_langs_remove)

        roles_to_add = []
        success_countries = []
        success_languages = []
        for role in ctx.guild.roles:
            if role.name in roles_countries:
                success_countries.append(role.name)
                roles_to_add.append(role)
            if role.name in roles_langs:
                success_languages.append(role.name)
                roles_to_add.append(role)

        await ctx.message.delete()

        if member is None:
            og = await ctx.channel.fetch_message(reference.message_id)
            member = og.author

        member_msg = ""
        if "member" in ctx.message.content:
            member_msg = f"\n\nTo get verified as Volt Member and get a <:volt:698844154418954311> purple role, DM (private message) the link to your Haiilo profile to <@{ctx.message.author.id}> or any other mod online.\n"

            volt_membership_claimed = [role for role in ctx.guild.roles if role.id == 715763050413686814]
            assert len(volt_membership_claimed) == 1, "Volt Membership Claimed role not found"
            await member.add_roles(volt_membership_claimed[0])

        await member.add_roles(*roles_to_add)

        channel: discord.TextChannel = ctx.channel
        async with channel.typing():
            await assign_base_roles(member, ctx.guild)

        e = discord.Embed(description = f"Welcome <@{member.id}>, you have full access to the Volt Europa server now. I assigned you the following countries/regions: {', '.join(success_countries)}, and the following languages: {', '.join(success_languages)}.{member_msg}\n-# Feel free to ask mods for help. [Volt Europa](<https://volteuropa.org/>)\nYou can check our rules (<#580529390933245972>) and our opt-in roles (<#727489317210947655>) :fire:")
        await channel.send(embed=e, reference = reference)
        await (await dmChannelUser(member)).send(embed=e)

    @bot.command(name = "court")
    async def courtcommand(ctx, user: discord.Member, *, reason: Optional[str]):
        if ctx.guild.id != voltServer or not await isMod(ctx.guild, ctx.author.id): #not on volt server or not a mod of the volt server
            return

        #create the court thread
        guildVolt = bot.get_guild(voltServer)
        channelCourt = guildVolt.get_channel(courtChannel)
        courtThread = await channelCourt.create_thread(name = f"{user.nick or user.name} court")

        #give the roles "in court" and "muted"
        roles = [guildVolt.get_role(x) for x in (inCourt, muted)]
        await user.add_roles(*roles)

        #ping the mod and the courted user
        await courtThread.send(f"{user.mention} {ctx.author.mention}")

        #register in modlog
        modlog = await guildVolt.fetch_channel(modLogId)
        e = discord.Embed(title = "Courting", timestamp = datetime.datetime.fromtimestamp(time.time()), color = 0x502379)
        e.add_field(name = "User:", value = user.mention, inline=False)
        e.add_field(name = "Reason:", value = reason, inline=False)
        e.add_field(name = "Responsible moderator:", value = ctx.author.mention, inline=False)
        e.set_footer(text = f"ID: {user.id}")

        await modlog.send(embed = e)
        await courtThread.send(embed = e)
    
    @bot.command(name = "uncourt")
    async def courtcommand(ctx, user: discord.User, *, reason: Optional[str]):
        if ctx.guild.id != voltServer or not await isMod(ctx.guild, ctx.author.id): #not on volt server or not a mod of the volt server
            return

        #get the court thread
        guildVolt = bot.get_guild(voltServer)
        courtThread = ctx.channel

        try:
            member = await guildVolt.fetch_member(user.id)
        except discord.errors.NotFound:
            member = None

        if member is not None: #the member did not get banned / didn't leave during the courting
            #remove the roles "in court" and "muted"
            roles = [guildVolt.get_role(x) for x in (inCourt, muted)]
            await member.remove_roles(*roles)

            await courtThread.remove_user(member)
        
        #make the bot and the mod leave the thread. the api doesn't let the bot archive the thread manually, it will be done automatically
        await courtThread.remove_user(ctx.author)
        await courtThread.leave()

        #register in modlog
        modlog = await guildVolt.fetch_channel(modLogId)
        e = discord.Embed(title = "Court case closed", timestamp = datetime.datetime.fromtimestamp(time.time()), color = 0x502379)
        e.add_field(name = "User:", value = user.mention, inline=False)
        e.add_field(name = "Reason:", value = reason, inline=False)
        e.add_field(name = "Responsible moderator:", value = ctx.author.mention, inline=False)
        e.set_footer(text = f"ID: {user.id}")

        await modlog.send(embed = e)

    @bot.command(name = "purge_log")
    async def purge_log(ctx, guild = None):
        """
        Purge #deleted-edited-messages. Records can be kept only up to 72 hours, so we have to delete them
        once that delay is passed.
        """

        if guild is None: 
            guild = ctx.guild

        if ctx is None or (await isMod(guild, ctx.author.id)):
            channel = await guild.fetch_channel(deletedEditedMessages)

            if ctx: await ctx.message.add_reaction("👌")

            import datetime
            now = datetime.datetime.now()
            seventyTwoHours = datetime.timedelta(hours=72)
            
            async for msg in channel.history(limit = None, before = now - seventyTwoHours):
                try:
                    await msg.delete()
                except discord.errors.NotFound:
                    pass
                except discord.errors.HTTPException:
                    await asyncio.sleep(1)
                    await msg.delete()
                else:
                    await asyncio.sleep(1)
    
    @bot.command(name = "purge_user")
    async def purge_user(ctx, user: discord.User, channel: discord.TextChannel):
        check = lambda msg: msg.author.id == user.id

        await ctx.send("Starting the purge…")
        deleted_msgs = await channel.purge(limit = None, check=check)
        await ctx.send(f"Purge completed, {len(deleted_msgs)} deleted messages")

    @bot.command(name = "mod")
    async def command_mod_smart_ping(ctx):
        """
        Smart ping of mod
        """

        if ctx.guild.id == voltServer and await isMod(ctx.guild, ctx.author.id):
            lastMessagesMods = dict()

            for member in ctx.guild.get_role(voltDiscordTeam).members:
                lastMessage = None
                async for msg in member.history():
                    if msg == ctx.message: continue
                    lastMessage = msg
                    break
                
                if hasattr(lastMessage, "created_at"):
                    lastMessagesMods[member.id] = lastMessage.created_at
            
            mostRecentMod = max(lastMessagesMods, key=lambda x: lastMessagesMods[x])
            await ctx.send(f"{mostRecentMod} is the most recent mod")

    @bot.command(name="màj")
    async def maj(ctx):
        if ctx.author.id == constantes.mainAdminId:
            import os

            os.system('git pull')
            await ctx.message.add_reaction("👌")
            os.system('systemctl restart volt')
        
    @bot.command(name = "ban")
    async def bancommand(ctx):
        await ban(ctx.message)
    
    @bot.command(name = "report")
    async def reportcommand(ctx, *, param = ""):
        await report(ctx.message.id, ctx.guild, ctx.channel, ctx.author, param)
        #await report(ctx, param)

    @tasks.loop(minutes=1)
    async def birthday_announcer():
        now_paris = get_paris_now()
        if not (now_paris.hour == 9 and now_paris.minute == 0):
            return
        
        def build_age_text(record: dict, today: datetime.date) -> str:
            """Return (age_text, age_int) for a record with year/month/day; empty text if year missing/invalid."""
            if "year" in record:
                year = int(record["year"])
                month = int(record["month"])
                day = int(record["day"])
                birthday_this_year = datetime.date(today.year, month, day)
                next_birthday_year = today.year if today <= birthday_this_year else today.year + 1
                age = next_birthday_year - year
                return f" They turn **{age}** today!"
            else:
                return ""
        
        guild = bot.get_guild(voltServer)
        assert guild is not None
        
        channel = bot.get_channel(casualChannel)
        if channel is None:
            try:
                channel = await bot.fetch_channel(casualChannel)
                assert isinstance(channel, discord.TextChannel)
            except Exception:
                return

        today = now_paris.date()

        birthdays = get_birthdays_storage().copy()

        for user_id, record in birthdays.items():
            try:
                month = int(record.get("month"))
                day = int(record.get("day"))
            except (TypeError, ValueError):
                continue

            if month != today.month or day != today.day:
                continue

            try:
                user = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
            except Exception:
                continue

            age_text = build_age_text(record, today)

            await channel.send(f"🎉 Happy birthday {user.mention}!{age_text}")

    @tasks.loop(minutes=1)
    async def celebrations():
        now_paris = get_paris_now()
        if not (now_paris.hour == 8 and now_paris.minute == 0):
            return
        
        # recover the channel object
        guild = bot.get_guild(voltServer)
        assert guild is not None
        
        channel = bot.get_channel(longTimeMembers)
        if channel is None:
            try:
                channel = await bot.fetch_channel(longTimeMembers)
                assert isinstance(channel, discord.TextChannel)
            except Exception:
                return

        # recover the list of celebrated users
        with open("celebration.txt", "r") as f:
            celebrated_users = [int(x.strip()) for x in f.readlines()[0].split()]

        # check that we are in April
        date = now_paris.date()
        day, month = date.day, date.month
        if month != 4:
            return

        celebrated_user_id = celebrated_users[day-1]
        
        # send a message in the channel to celebrate the user of the day, with a mention and a public thread on it to let people congratulate them
        member = await guild.fetch_member(celebrated_user_id)
        celebration_phrasings = [
            f"🥳 Big shoutout to {member.mention} today!",
            f"🎊 Let's celebrate {member.mention} today!",
            f"✨ Today we celebrate {member.mention}!",
            f"🎈 It's {member.mention} appreciation day!",
            f"🙌 Sending good vibes to {member.mention} today!",
        ]
        msg = await channel.send(random.choice(celebration_phrasings))
        await msg.create_thread(name=member.display_name, auto_archive_duration=1440)
        

    @bot.event
    async def on_ready():
        if not birthday_announcer.is_running():
            birthday_announcer.start()
        
        if not celebrations.is_running():
            celebrations.start()
        
        if not getattr(bot, "_guild_synced", False):
            await bot.tree.sync(guild=discord.Object(id=voltServer))
            bot._guild_synced = True
    
    @bot.tree.command(name="roll", description="Roll one or more dice")
    @app_commands.guilds(discord.Object(id=voltServer))
    async def roll_dice(interaction: discord.Interaction, option: Optional[str] = "d6"):
        accepted = {"d4": 4, "d6": 6, "d8": 8, "d10": 10, "d12": 12, "d20": 20, "d100": 100}

        if "+" in option or "-" in option: # there is an addition or a subtraction
            if option.count("+") + option.count("-") > 1:
                await interaction.response.send_message("Invalid option", ephemeral=True)
                return

            if "+" in option:
                base, add = option.split("+")
                sign = 1
            else:
                base, add = option.split("-")
                sign = -1

            if not add.isdigit():
                await interaction.response.send_message("Invalid option", ephemeral=True)
                return

            add = int(add) * sign
        else:
            base = option
            add = 0

        nb_dice = 1 if not base[0].isdigit() else int(base[0])
        if base[0].isdigit():
            base = base[1:]

        if base not in accepted:
            await interaction.response.send_message("Invalid option", ephemeral=True)
            return

        vals = [random.randint(1, accepted[base])+add for _ in range(nb_dice)]
        await interaction.response.send_message(f"🎲 You rolled {', '.join(map(str, vals))} ({option})")
    
    @bot.tree.command(name="skibidi", description="Resend a message as the bot")
    @app_commands.guilds(discord.Object(id=voltServer))
    async def resend_slash(interaction: discord.Interaction, taratata: Optional[str] = None, glbtskf: str = "", apzoeiruty: Optional[discord.Attachment] = None):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("No", ephemeral=True)
            return

        role = discord.utils.get(interaction.guild.roles, name="Volt Discord Team")

        if role in interaction.user.roles:
            if taratata is None or taratata.isdigit():
                if taratata: taratata = int(taratata)
                await resend([apzoeiruty] if apzoeiruty else [], interaction.channel, taratata, txt=glbtskf)
                await interaction.response.send_message("Ok", ephemeral=True, delete_after=10)
            else:
                await interaction.response.send_message("Invalid message id", ephemeral=True)
        else:
            await interaction.response.send_message("No", ephemeral=True)
        
    @bot.tree.command(name="birthday", description="Register your birthday; add a year if you want your age announced")
    @app_commands.guilds(discord.Object(id=voltServer))
    @app_commands.describe(
        day="Day of month (1-31)",
        month="Month number (1-12)",
        year="Year (optional, announced as age)",
    )
    async def register_birthday(
        interaction: discord.Interaction,
        day: int,
        month: int,
        year: Optional[int] = None,
    ):
        today = get_paris_now().date()

        if year is not None and year > today.year - 13:
            await interaction.response.send_message("Year cannot be less than 13 years ago", ephemeral=True)
        
        try:
            datetime.date(year if year else 2000, month, day)
        except ValueError:
            await interaction.response.send_message("That date is not valid. Please check the day and month.", ephemeral=True)
            return

        birthdays = get_birthdays_storage()
        record = {"month": month, "day": day}
        if year is not None:
            record["year"] = year

        assert interaction.user is not None
        birthdays[str(interaction.user.id)] = record
        save()

        confirmation = (
            f"Saved! Your birthday is set as {day:02d}/{month:02d}"
            + (f"/{year}" if year else "")
        )

        await interaction.response.send_message(confirmation, ephemeral=True)
        
    @bot.command(name = "resend")
    async def resend(ctx, channel: Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.GroupChannel, discord.Thread], message_rep: Optional[int] = None, *, txt: str = ""):
        msg = ctx.message if hasattr(ctx, "message") else None
        if msg:
            #check the role
            author = msg.author
            role = discord.utils.get(msg.guild.roles, name="Volt Discord Team")
            if role not in author.roles:
                return

        ref = msg.reference if msg else None
        if message_rep:
            try:
                msg_ref = await channel.fetch_message(message_rep)
                ref = discord.MessageReference(message_id = message_rep, channel_id = channel.id)
            except:
                pass

        files = []
        if (msg.attachments if msg else ctx): #ctx is then the list of attachments from the slash command
            for i, att in enumerate(msg.attachments if msg else ctx):
                r = requests.get(att.url)
                with open(str(i), "wb") as outfile:
                    outfile.write(r.content)

                files.append(discord.File(str(i), att.filename))

        await channel.send(txt, reference = ref, embeds = msg.embeds if msg else None, files = files)

        for i in range(len(files)):
            os.remove(str(i))

    @bot.command(name = "top_of_month")
    async def topOfMonth(ctx):
        if ctx.author.id == botAdmin:
            from subprocess import Popen
            import os

            Popen(["python3", os.path.join(os.path.dirname(__file__), "top_countries_month.py")])
            await ctx.message.add_reaction("👌")

    @bot.command(name = "read_top_of_month")
    async def topOfMonth(ctx):
        if ctx.author.id == botAdmin:
            import os

            ref = discord.MessageReference(channel_id = ctx.channel.id, message_id = ctx.message.id)

            pathTop = os.path.join("outputs", "infoTopCountries.txt")
            if os.path.exists(pathTop):
                await ctx.send(file = discord.File(pathTop), reference = ref)
            else:
                await ctx.send("Someone has to start the count!", reference = ref)

    @bot.command(name="react")
    async def react(ctx, *emojis: Union[discord.Emoji, str]):
        reference = ctx.message.reference

        if reference:
            msg = await ctx.channel.fetch_message(reference.message_id)
            for emoji in emojis:
                await msg.add_reaction(emoji)

        await ctx.message.delete()

    async def banFromMsg(msg):
        if msg.author.id == 282859044593598464: #the message is from ProBot -> introduction message
            idNew = int(msg.content.split("<")[1].split(">")[0].split("!")[-1])
            try:
                memberNew = await msg.guild.fetch_member(idNew)
            except discord.errors.NotFound:
                return

            if memberNew and len(memberNew.roles) == 1:
                await memberNew.ban(reason = "raid - mass ban by Volt Europa Bot")

    @bot.command(name="remove_banned_word")
    async def remove_banned_word(ctx, user: discord.Member):
        if await isMod(ctx.guild, ctx.author.id):
            userId = str(user.id)
            if "banned_words" in info and userId in info["banned_words"]:
                banned_words_user = info["banned_words"][userId]
                if banned_words_user:
                    del banned_words_user[-1]
                    save()
                    
                    await ctx.message.add_reaction("👌")

    banFrom = [0]
    @bot.command(name="ban_from")
    async def ban_from(ctx):
        ref = ctx.message.reference
        if ref and (await isMod(ctx.guild, ctx.author.id)):
            msg = await ctx.channel.fetch_message(ref.message_id)
            banFrom[0] = (msg.created_at, msg)

            await ctx.message.add_reaction("👌")

    @bot.command(name="ban_to")
    async def banTo(ctx):
        ref = ctx.message.reference
        if ref and banFrom[0] and (await isMod(ctx.guild, ctx.author.id)):
            timestampInit, msgFrom = banFrom[0]
            msg = await ctx.channel.fetch_message(ref.message_id)

            await banFromMsg(msgFrom)
            await banFromMsg(msg)
            async for msg in ctx.channel.history(limit = None, before = msg.created_at, after = timestampInit):
                await banFromMsg(msg)

            banFrom[0] = 0
            await ctx.message.add_reaction("👌")

    @bot.command(name="show_top")
    async def show_top(ctx):
        if ctx.author.id != botAdmin:
            return

        if not os.path.exists("outputs/infoUserActivity.json"):
            await ctx.send("No information available")
        else:
            now = datetime.datetime.now()
            startswith = f"{now.year}-{now.month-1}"

            data: dict[str, dict[str, dict[str, int]]] = json.load(open("outputs/infoUserActivity.json"))

            data_month: dict[str, dict[str, dict[str, int]]] = dict()
            for user, data_user in data.items():
                data_user_month = {channel: {date: nb for date, nb in channel_user.items() if date.startswith(startswith)} for channel, channel_user in data_user.items()}
                if all(data_user_month.values()):
                    data_month[user] = data_user_month

            # top 20 most active of the month
            msg_per_user = {user: sum(msg_date for channel_user in v.values() for msg_date in channel_user.values()) for user, v in data_month.items()}
            top_users = sorted(msg_per_user.items(), key=lambda x: x[1], reverse=True)[:20]

            # find the usernames
            id_to_name = dict()
            for userId, _ in top_users:
                try:
                    user = await bot.fetch_user(int(userId))
                    id_to_name[userId] = user.name
                except discord.errors.NotFound:
                    id_to_name[userId] = "_former member_"

            # top 10 most active channels
            msg_per_channel = dict()
            for user, per_channel in data_month.items():
                for channelId, channel_user in per_channel.items():
                    if channelId not in msg_per_channel:
                        msg_per_channel[channelId] = 0

                    msg_per_channel[channelId] += sum(channel_user.values())
            top_channel = sorted(msg_per_channel.items(), key=lambda x: x[1], reverse=True)[:10]

            # find the channel names
            id_to_name_channel = dict()
            for channelId, _ in top_channel:
                try:
                    channel = await bot.fetch_channel(int(channelId))
                    id_to_name_channel[channelId] = channel.name
                except discord.errors.NotFound:
                    id_to_name_channel[channelId] = "_unknown channel_"

            await ctx.send("**Top 20 most active users**\n" + "\n".join(f"{i+1} **{id_to_name[user]}**: {nb} messages" for i, (user, nb) in enumerate(top_users)))
            await ctx.send("**Top 10 most active channels/threads**\n" + "\n".join(f"{i+1} **#{id_to_name_channel[channel]}**: {nb} messages" for i, (channel, nb) in enumerate(top_channel)))

    @bot.command(name="del_msg_usr")
    async def del_neur(ctx, user_id: int, channel_id: int, before: str, after: str):
        if ctx.author.id != botAdmin:
            return

        await ctx.send("Starting the deletion…")

        channel = await ctx.guild.fetch_channel(channel_id)
        before = datetime.datetime.fromisoformat(before)
        after = datetime.datetime.fromisoformat(after)
        i = 0
        async for msg in channel.history(limit=None, after=after, before=before, oldest_first=False):
            if msg.author.id == user_id:
                i += 1
                await msg.delete()
                
                if i % 100 == 0:
                    await ctx.send(f"{i} messages deleted")
        
        await ctx.send(f"Deletion completed, {i} messages deleted")
    
    @bot.command(name="report_slur")
    async def report_slur(ctx, author: discord.Member, *, slur: str):
        if (await isMod(ctx.guild, ctx.author.id)):
            await count_banned_words(ctx.guild, author, slur)

    @bot.command(name="migrate_report_user_threads")
    async def migrate_report_user_threads(ctx, mode: str = "dry"):
        if ctx.guild.id != voltServer or not await isMod(ctx.guild, ctx.author.id):
            return

        run = mode.lower() in ("run", "apply", "yes")
        report_channel = await bot.fetch_channel(reportChannelId)
        assert isinstance(report_channel, discord.TextChannel)
        reports = get_reports_storage()
        migrated = set(str(x) for x in reports["migrated_archive_messages"])
        current_case_archive_ids = {str(case.get("archive_message_id")) for case in reports["cases"].values()}
        after = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=183)
        counts = {}
        posts = []

        async for msg in report_channel.history(after=after, limit=None, oldest_first=True):
            if str(msg.id) in migrated or str(msg.id) in current_case_archive_ids or not msg.embeds:
                continue
            embed = msg.embeds[0]
            author_fields = [field.value for field in embed.fields if field.name == "Author"]
            if not author_fields:
                continue
            user_id = extract_user_id_from_mention(author_fields[0])
            if user_id is None:
                continue
            counts[user_id] = counts.get(user_id, 0) + 1
            posts.append((msg, user_id))

        if not run:
            if not counts:
                await ctx.send("Dry run: no historical reports to migrate from the last 6 months.")
                return
            preview = "\n".join(f"<@{user_id}>: {count}" for user_id, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:30])
            await ctx.send(f"Dry run: {len(posts)} reports would be migrated into {len(counts)} user threads.\nRun `{constantes.prefixVolt}migrate_report_user_threads run` to apply.\n{preview}")
            return

        await ctx.send(f"Migrating {len(posts)} historical reports into user threads...")
        done = 0
        for msg, user_id in posts:
            try:
                user = await ctx.guild.fetch_member(user_id)
            except Exception:
                user = await bot.fetch_user(user_id)
            user_thread = await get_or_create_user_report_thread(ctx.guild, report_channel, user)
            await forward_report_with_attachments(msg, user_thread)
            reports["migrated_archive_messages"].append(str(msg.id))
            done += 1
            if done % 25 == 0:
                save()
                await ctx.send(f"Migrated {done}/{len(posts)} reports...")

        save()
        await ctx.send(f"Migration complete: {done} reports migrated.")
    
    @bot.command(name="redirect_reports")
    async def redirect_reports(ctx):
        report_channel = await bot.fetch_channel(reportChannelId)
        
        users = {
            "<@1363525904692805693>": 1426967321083248750,
            "<@237564181656764417>": 1426967356743090189,
            "<@251760037444321280>": 1426967450658017301,
            "<@600709583333490739>": 1426967483264532630
        }
        threads = {k: await ctx.guild.fetch_channel(thread_id) for k, thread_id in users.items()}
        
        i = 0
        msgs = []
        async for msg in report_channel.history(after=datetime.datetime(2025, 6, 1), before=datetime.datetime(2025, 8, 1), limit=None):
            i += 1
            if i % 20 == 0: print(i)
            
            if msg.embeds:
                if msg.embeds[0].fields:
                    author = [x.value for x in msg.embeds[0].fields if x.name == "Author"][0]
                    print(author, msg.created_at)
                    if author in users:
                        if msg.embeds[0].description not in [x.embeds[0].description for x, _ in msgs]:
                            msgs.append((msg, threads[author]))
        
        msgs.sort(key=lambda x: x[0].id)
        print(len(msgs))
        for msg, thread in msgs:
            print(msg)
            await msg.forward(thread)

    return bot, constantes.TOKENVOLT

if __name__ == "__main__": #pour lancer le bot
    bot, token = main()
    try:
        asyncio.run(bot.start(token))
    except KeyboardInterrupt:
        pass
