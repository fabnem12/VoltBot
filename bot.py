import asyncio
import json
import nextcord as discord
from nextcord.ext import commands
from typing import Optional, Union
from unidecode import unidecode
import datetime
import os
import requests
import time
import emojis
import regex

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
deletedEditedMessages = 982242792422146098
modLogId = 929466478678405211
reportChannelId = 806219815760166972
modMessageLog = 1037071502656405584
courtChannel = 912092404570554388
introChannel = 567024817128210433

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
        json.dump(info, f)

async def dmChannelUser(user):
    if user.dm_channel is None:
        await user.create_dm()
    return user.dm_channel

async def isMod(guild, memberId):
    member = await guild.fetch_member(memberId)
    return any(role.id in (voltDiscordTeam, voltSubTeam, voltAdmin) for role in member.roles)

async def isWelcome(user):
    return any(x.id == welcomeTeam for x in user.roles)

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

    try:
        await msg.guild.ban(user, reason = f"{banReason} (ban by {msg.author.mention})", delete_message_seconds = 0)
    except Exception as e:
        await (await dmChannelUser(msg.author)).send(f"Unable to ban {user.name}\n{e}")
    else:
        await msg.channel.send(f"Banned **{user.name}**")

async def exclusion(before, after):
    if before.guild.id == voltServer and before.communication_disabled_until is None and after.communication_disabled_until:
        #let's find the reason
        async for entry in before.guild.audit_logs(action=discord.AuditLogAction.member_update):
            reason = entry.reason
            mod = entry.user
            time = entry.created_at
            break
        
        modlog = await before.guild.fetch_channel(modLogId)
        e = discord.Embed(title = "time out", timestamp = time, color = 0x502379)
        e.add_field(name = "User:", value = f"{after}", inline=False)
        e.add_field(name = "Reason:", value = reason, inline=False)
        e.add_field(name = "Reponsible moderator:", value = f"{mod}", inline=False)
        e.set_footer(text = f"ID: {after.id}")

        await modlog.send(embed = e)

async def report(messageId, guild, channel, user, param = ""):
    channelId = reportChannelId

    msgInit = await channel.fetch_message(messageId)
    msg = msgInit
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
            await (await dmChannelUser(user)).send(f"The message got reported: {msg.jump_url}. Thanks!")
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
    msgReport = await reportChannel.send(embed = e)

    ref = discord.MessageReference(channel_id = msgReport.channel.id, message_id = msgReport.id)

    for att in msg.attachments:
        r = requests.get(att.url)
        with open(att.filename, "wb") as outfile:
            outfile.write(r.content)

        await reportChannel.send(file = discord.File(att.filename), reference = ref)
        os.remove(att.filename)

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

        if message.content.startswith(".ban"):
            await ban(message, banAppealOk = False)
        
    @bot.event
    async def on_message_edit(before, after):
        await verif_word_train(after)
        
    @bot.event
    async def on_message_delete(msg):
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
    
    @bot.event
    async def on_member_join(member: discord.Member):
        dmChannel = await dmChannelUser(member)
        await dmChannel.send("Hey! **Welcome to the Volt Europa Discord server**\nTo get the access to the server, please introduce yourself in <#567024817128210433>, citing the country/countries you are from, the languages you speak and whether you are a <:volt:698844154418954311> member.\nOnce you get verified, you can check <#727489317210947655> to get access to topic channels.")

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

            if await isWelcome(user) or await isMod(guild, user.id):
                await introreact(messageId, guild, emojiHash, channel, user)

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
                break

        if len(words) > 1 or previousMsgLetters.strip().lower()[-1] != msgTxt[0] or previousMsg.author.id == message.author.id:
            await message.delete()
            await message.channel.send(f"<:bonk:843489770918903819> {message.author.mention}")

        return True

    @bot.command(name = "ayo")
    async def ayo(ctx):
        await ctx.send("ayo")

    @bot.command(name = "verify")
    async def verify(ctx):
        if not await isWelcome(ctx.message.author) and not await isMod(ctx.guild, ctx.message.author.id):
            return
        reference = ctx.message.reference
        if reference is None:
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
            ('🇭🇷', "Croatia", ["Croatian"]),
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
            ('🇽🇰', "Kosovo", ["Kosovar"]),
            ('🇰🇿', "Kazakhstan", ["Kazakh"]),
            ('🇱🇻', "Latvia", ["Latvian"]),
            ('🇱🇮', "Liechteinstein", ["German"]),
            ('🇱🇹', "Lithuania", ["Lithuanian"]),
            ('🇱🇺', "Luxembourg", ["Luxembourgish", "French", "German"]),
            ('🇲🇹', "Malta", ["Maltese"]),
            ('🇲🇩', "Moldova", ["Romanian"]),
            ('🇲🇨', "Monaco", ["French"]),
            ('🇲🇪', "Montenegro", ["Montenegrin"]),
            ('🇳🇱', "Netherlands", ["Dutch"]),
            ('🇲🇰', "North Macedonia", ["Macedonian", "Albanian"]),
            ('🇳🇴', "Norway", ["Norwegian"]),
            ('🇵🇱', "Poland", ["Polish"]),
            ('🇵🇹', "Portugal", ["Portuguese"]),
            ('🇷🇴', "Romania", ["Romanian"]),
            ('🇷🇺', "Russia", ["Russian"]),
            ('🇸🇲', "San Marino", ["Italian"]),
            ('🇷🇸', "Serbia", ["Serbian"]),
            ('🇸🇰', "Slovakia", ["Slovak"]),
            ('🇸🇮', "Slovenia", ["Slovenian"]),
            ('🇪🇸', "Spain", ["Spanish"]),
            ('🇸🇪', "Sweden", ["Swedish"]),
            ('🇨🇭', "Switzerland", ["German", "French", "Italian"]),
            ('🇹🇷', "Turkey", ["Turkish"]),
            ('🇬🇧', "United Kingdom", []),
            ('🇺🇦', "Ukraine", ["Ukrainian", "Russian"]),
            ('🇻🇦', "Vatican", ["Italian"]),
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

        og = await ctx.channel.fetch_message(reference.message_id)

        member_msg = ""
        if "member" in ctx.message.content:
            member_msg = f"\n\nTo get verified as Volt Member and get a <:volt:698844154418954311> purple role, DM (private message) your **full name** and **birth date** to <@{ctx.message.author.id}> or any other mod online.\n"

            volt_membership_claimed = [role for role in ctx.guild.roles if role.id == 715763050413686814]
            assert len(volt_membership_claimed) == 1, "Volt Membership Claimed role not found"
            await og.author.add_roles(volt_membership_claimed[0])

        await og.author.add_roles(*roles_to_add)
        await assign_base_roles(og.author, ctx.guild)
        await ctx.send(f"Welcome <@{og.author.id}>, you have full access now. I assigned you the following countries/regions: {', '.join(success_countries)}, and the following languages: {', '.join(success_languages)}.{member_msg}\n-# Feel free to ask mods for help. [Volt Europa](<https://volteuropa.org/>)")


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
        e.add_field(name = "Reponsible moderator:", value = ctx.author.mention, inline=False)
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
        e.add_field(name = "Reponsible moderator:", value = ctx.author.mention, inline=False)
        e.set_footer(text = f"ID: {user.id}")

        await modlog.send(embed = e)

    @bot.command(name = "purge_log")
    async def purge_log(ctx, guild = None):
        """
        Purge #deleted-edited-messages. Records can be kept only up to 24 hours, so we have to delete them
        once that delay is passed.
        """

        if guild is None: 
            guild = ctx.guild

        if ctx is None or (await isMod(guild, ctx.author.id)):
            channel = await guild.fetch_channel(deletedEditedMessages)

            if ctx: await ctx.message.add_reaction("👌")

            import datetime
            now = datetime.datetime.now()
            oneDay = datetime.timedelta(hours=24)
            
            async for msg in channel.history(limit = None, before = now - oneDay):
                try:
                    await msg.delete()
                except discord.errors.NotFound:
                    pass
                except discord.errors.HTTPException:
                    await asyncio.sleep(1)
                    await msg.delete()
                else:
                    await asyncio.sleep(1)
    
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
            from subprocess import Popen, DEVNULL

            await ctx.message.add_reaction("👌")
            Popen(["python3", "maj.py"], stdout = DEVNULL)

    @bot.command(name = "ban")
    async def bancommand(ctx):
        await ban(ctx.message)
    
    @bot.command(name = "report")
    async def reportcommand(ctx, *, param = ""):
        await report(ctx.message.id, ctx.guild, ctx.channel, ctx.author, param)
        #await report(ctx, param)

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

    return bot, constantes.TOKENVOLT

if __name__ == "__main__": #pour lancer le bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()