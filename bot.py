import asyncio
import nextcord as discord
from nextcord.ext import commands
from unidecode import unidecode

import constantes
import datetime

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

def main():
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix=constantes.prefixVolt, help_command=None, intents = intents)

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
        if message.id % 100 == 13: #purge the log of deleted-edited-message about every 100 messages
            await purge_log(None, bot.get_guild(voltServer))

        await verif_word_train(message)
        await verif_word_train2(message)

        if message.content.startswith(".ban"):
            await ban(message, banAppealOk = False)
    
    @bot.event
    async def on_message_edit(before, after):
        await verif_word_train(after)
        await verif_word_train2(after)

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
                    print(previousMsgLetters, msgLetters)

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

        if await isMod(guild, ctx.author.id):
            channel = await guild.fetch_channel(deletedEditedMessages)

            await ctx.message.add_reaction("👌")

            import datetime
            now = datetime.datetime.now()
            oneDay = datetime.timedelta(hours=24)
            
            async for msg in channel.history(limit = None, before = now - oneDay):
                await msg.delete()

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