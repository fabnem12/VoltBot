import asyncio
import json
import nextcord as discord
from nextcord.ext import commands
from unidecode import unidecode
import os
import re

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
wordTrainChannel2 = 1143858096339439677
deletedEditedMessages = 982242792422146098

#-roles
voltDiscordTeam = 674583505446895616
voltSubTeam = 858692593104715817
voltAdmin = 567023540193198080

#info in json
if "bot_info.json" in os.listdir(os.path.dirname(__file__)):
    with open("bot_info.json", "r") as f:
        info = json.load(f)
else:
    info = {"smart_tweet": dict()}
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

async def ban(msg, banAppealOk = True):
    if msg.guild.id != 567021913210355745 or not await isMod(msg.guild, msg.author.id): #not on volt server or not a mod of the volt server
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
        await msg.guild.ban(user, reason = f"{banReason} (ban by {msg.author.name})", delete_message_seconds = 0)
    except Exception as e:
        await (await dmChannelUser(msg.author)).send(f"Unable to ban {user.name}\n{e}")
    else:
        await msg.channel.send(f"Banned **{user.name}**")

async def smart_tweet(msg: discord.Message, delete: bool = False):
    """
    Reply to messages with Twitter links whose embed fails with vxtwitter
    """
    
    msgId = msg.id
    infoSmartTweet = info["smart_tweet"]

    if delete and msgId in infoSmartTweet:
        msgRep = await msg.channel.fetch_message(infoSmartTweet[msgId])
        await msgRep.delete()
        del infoSmartTweet[msgId]

    links = re.findall("https:\/\/([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])", msg.content)
    links = [(x.lower(), y.lower()) for x, y in links]
    twitterLinks = ["https://" + x.replace("x.com", "twitter.com").replace("twitter.com", "vxtwitter.com") + y for x, y in links if (x.startswith("x.com") or x.startswith("twitter.com") in x) and "fxtwitter.com" not in x and "vxtwitter.com" not in x]

    if len(twitterLinks):
        ref = discord.MessageReference(channel_id = msg.channel.id, message_id = msgId)
        
        if msg.edited_at and "smart_tweet" in info and msgId in infoSmartTweet:
            msgRep = await msg.channel.fetch_message(infoSmartTweet[msgId])
            await msgRep.edit(content = "\n".join(twitterLinks))
        else:
            rep = await msg.channel.send("\n".join(twitterLinks), reference = ref)
            infoSmartTweet[msgId] = rep.id

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
        await verif_word_train2(message)
        await smart_tweet(message)

        if message.content.startswith(".ban"):
            await ban(message, banAppealOk = False)
        
    @bot.event
    async def on_message_edit(before, after):
        await verif_word_train(after)
        await verif_word_train2(after)
        await smart_tweet(after)
        
    @bot.event
    async def on_message_delete(message):
        await smart_tweet(message, delete=True)
    
    @bot.event
    async def on_member_join(member: discord.Member):
        dmChannel = await dmChannelUser(member)
        await dmChannel.send("Hey! **Welcome to the Volt Europa Discord server**\nTo get the access to the server, please introduce yourself in <#567024817128210433>, citing the country/countries you are from, the languages you speak and whether you are a <:volt:698844154418954311> member.\nOnce you get verified, you can check <#727489317210947655> to get access to topic channels.")

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

        if len(words) > 1:
            lastLetter = words[0][-1]

            for word in words[1:]:
                if word[0] != lastLetter:
                    await message.delete()
                    await message.channel.send(f"<:bonk:843489770918903819> {message.author.mention}")

                    return False
                else:
                    lastLetter = word[-1]

        return True

    async def verif_word_train2(message):
        if message.channel.id != wordTrainChannel2 or message.author.bot:
            return

        previousMsg = None
        async for msg in message.channel.history(limit = None):
            if msg != message and not msg.author.bot:
                previousMsg = msg
                break

        if previousMsg:
            isLetter = lambda x: x in "abcdefghijklmnopqrstuvwxyz "
            previousMsgTxt = unidecode(previousMsg.content.lower())
            msgTxt = unidecode(message.content.lower())

            previousMsgLetters = "".join(filter(isLetter, previousMsgTxt))
            msgLetters = "".join(filter(isLetter, msgTxt))

            if len(previousMsgLetters) and len(msgLetters):
                if previousMsgLetters[-1] != msgLetters[0]:
                    await message.delete()
                    await message.channel.send(f"<:bonk:843489770918903819> {message.author.mention}")

                    return False

        return True

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

    @bot.command(name="màj")
    async def maj(ctx):
        if ctx.author.id == constantes.mainAdminId:
            from subprocess import Popen, DEVNULL

            await ctx.message.add_reaction("👌")
            Popen(["python3", "maj.py"], stdout = DEVNULL)

    @bot.command(name = "ban")
    async def bancommand(ctx):
        await ban(ctx.message)

    return bot, constantes.TOKENVOLT

if __name__ == "__main__": #pour lancer le bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()