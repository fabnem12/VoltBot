import asyncio
import nextcord as discord
from nextcord.ext import commands
from unidecode import unidecode

import constantes

wordTrainChannel = 1141992409165733988

async def dmChannelUser(user):
    if user.dm_channel is None:
        await user.create_dm()
    return user.dm_channel

def main():
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix=constantes.prefixVolt, help_command=None, intents = intents)

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
        await verif_word_train(message)

    async def verif_word_train(message):
        """
        Word train channel:
        your word has to start with the same letter as the word before ends. Example "The elephant tries snorkeling..."
        """
        
        if message.channel.id != wordTrainChannel:
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
            

    @bot.command(name="màj")
    async def maj(ctx):
        if ctx.author.id == constantes.mainAdminId:
            from subprocess import Popen, DEVNULL

            await ctx.message.add_reaction("👌")
            Popen(["python3", "maj.py"], stdout = DEVNULL)

    return bot, constantes.TOKENVOLT

if __name__ == "__main__": #pour lancer le bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()