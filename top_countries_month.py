import asyncio
import json
import nextcord as discord
import os
import pickle
from datetime import datetime, timedelta
from nextcord.ext import commands
from typing import Dict, List, Tuple, Union, Optional, Set

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constantes import TOKENVOLT as token, prefixVolt as prefix
outputsPath = "outputs"

#token = "" #bot token
#prefix = ","
byLength = True

multinationalMembers = dict()
if not os.path.isfile("multinationals.p"):
    pickle.dump(multinationalMembers, open("multinationals.p", "wb"))
else:
    multinationalMembers = pickle.load(open("multinationals.p", "rb"))

def topByCountryRole(keyDicoByAuthorId, nbMsgPerPerson):
    perCountry = dict()
    for authorId, (_, _, name, roles) in keyDicoByAuthorId.items():
        for role in roles:
            if role != name:
                if role not in perCountry:
                    perCountry[role] = set()

                perCountry[role].add((name, nbMsgPerPerson[authorId]))

    topPerCountry = f"Top users per country role:\n"
    topPerCountry += "\n\n".join(f"{role}:\n" + "\n".join(f"#{i+1} {name} with {nbMsgs} {'letters' if byLength else 'messages'}" for i, (name, nbMsgs) in zip(range(10), sorted(members, key=lambda x: x[1], reverse = True))) for role, members in sorted(perCountry.items()))

    return topPerCountry

async def countMessages(guild, bot):
    now = datetime.now()

    def previousMonthYear(month, year):
        return (month - 1) if month != 1 else 12, year if month != 1 else year - 1
    def nextMonthYear(month, year):
        return (month + 1) if month != 12 else 1, year if month != 12 else year + 1

    #let's find the beginning and the end of the previous month/day according to UTC
    if len(sys.argv) == 1: #previous month
        currentMonth, currentYear = now.month, now.year
        prevMonth, prevMonthYear = previousMonthYear(currentMonth, currentYear)

        timeLimitEarly = datetime(prevMonthYear, prevMonth, 1)
        timeLimitLate = datetime(currentYear, currentMonth, 1)
    else: #custom period, assumes sys.argv[2:5] -> (day, month, year)
        if sys.argv[1] == "day":
            day, month, year = map(int, (sys.argv[2], sys.argv[3], sys.argv[4]))
            timeLimitEarly = datetime(year, month, day)
            timeLimitLate = timeLimitEarly + timedelta(hours = 24)
        elif sys.argv[1] == "year":
            day, month, year = map(int, (sys.argv[2], sys.argv[3], sys.argv[4]))
            timeLimitEarly = datetime(year, month, day)
            timeLimitLate = timeLimitEarly + timedelta(days = 365 + int(not (year % 4 != 0 or (year % 100 == 0 and year % 400 != 0))))
        else: #if sys.argv[1] == "month"
            day, month, year = map(int, (sys.argv[2], sys.argv[3], sys.argv[4]))
            nexMonth, nexMonthYear = nextMonthYear(month, year)

            timeLimitEarly = datetime(year, month, day)
            timeLimitLate = datetime(nexMonthYear, nexMonth, day)

    #the file will be stored here… the bot will send the txt file when the counting is done
    pathSave = os.path.join(outputsPath, "infoTopCountries.txt")

    nbMsgPerCountry = dict()
    nbMsgPerMultinational = dict()
    nbMsgPerPerson = dict()
    topPerChannel = dict()
    keyDicoByAuthorId = dict()

    totalNbMsgs = [0]
    countries = {"Europe", 'Vatican', 'Ukraine', 'United Kingdom', 'Turkey', 'Switzerland', 'Sweden', 'Spain', 'Slovenia', 'Slovakia', 'Serbia', 'San Marino', 'Portugal', 'Russia', 'Romania', 'Poland', 'Norway', 'North Macedonia', 'Netherlands', 'Montenegro', 'Monaco', 'Moldova', 'Malta', 'Luxembourg', 'Lithuania', 'Liechtenstein', 'Latvia', 'Kazakhstan', 'Kosovo', 'Italy', 'Ireland', 'Iceland', 'Hungary', 'Greece', 'Georgia', 'Germany', 'France', 'Finland', 'Estonia', 'Denmark', 'Czechia', 'Cyprus', 'Croatia', 'Bulgaria', 'Bosnia & Herzegovina', 'Belgium', 'Belarus', 'Azerbaijan', 'Austria', 'Andorra', 'Armenia', 'Albania', 'Asia', 'Africa', 'North America', 'Oceania', 'South America'}

    async def readChannel(channel):
        showName = channel.name if channel.category and channel.category.id != 567029949538500640 else "[redacted]"
        #print(showName)
        await bot.change_presence(activity=discord.Game(name=f"Counting messages in #{showName} - {totalNbMsgs[0]}+ messages counted so far"))

        topChannel = dict()
        topPerChannel[channel.id] = (topChannel, channel.name if not hasattr(channel, "parent") else f"{channel.parent.name}-{channel.name}")

        async for msg in channel.history(limit = None, after = timeLimitEarly, before = timeLimitLate): #let's read the messages sent last month in the current channel
            totalNbMsgs[0] += 1

            if totalNbMsgs[0] % 2000 == 0:
                await bot.change_presence(activity=discord.Game(name=f"Counting messages in #{showName} - {totalNbMsgs[0]}+ messages counted so far"))

            author = msg.author
            try:
                if author.id not in keyDicoByAuthorId:
                    author = await guild.fetch_member(author.id)

                    if author.bot: continue
            except: #the author left the server, there is no way to know their country roles…
                try:
                    if author.id in multinationalMembers:
                        author = await bot.fetch_user(author.id)
                    else:
                        continue
                except:
                    continue
                else:
                    if author.bot: continue

            msgLength = len(msg.content) if byLength else 1

            if author.id not in keyDicoByAuthorId:
                if author.id in multinationalMembers:
                    authorsCountries = (multinationalMembers[author.id],)
                else:
                    if isinstance(author, discord.Member):
                        authorsCountries = tuple(role.name for role in author.roles if role.name in countries)
                    else:
                        authorsCountries = ()

                if isinstance(author, discord.Member):
                    name = f"{author.nick} ({author.name})" if author.nick else author.name
                else:
                    name = author.name

                if len(authorsCountries) == 1: #the author has only 1 country role: easy
                    key = authorsCountries[0]
                    dico = nbMsgPerCountry
                else: #the author has several country roles, it's up to Isak!
                    key = name
                    dico = nbMsgPerMultinational

                keyDicoByAuthorId[author.id] = (key, dico, name, authorsCountries)
            else:
                key, dico, authorNick, countryRoles = keyDicoByAuthorId[author.id]

            if key not in dico: #increase the count of messages
                dico[key] = msgLength
            else:
                dico[key] += msgLength

            if author.id not in nbMsgPerPerson:
                nbMsgPerPerson[author.id] = msgLength
            else:
                nbMsgPerPerson[author.id] += msgLength

            if author.id not in topChannel:
                topChannel[author.id] = msgLength
            else:
                topChannel[author.id] += msgLength

    for channel in filter((lambda x: "logs" not in x.name and (hasattr(x, "history") or hasattr(x, "threads"))), guild.channels): #let's read all the channels
        try: #discord raises Forbidden error if the bot is not allowed to read messages in "channel"
            await readChannel(channel)
        except Exception as e:
            print(channel.name, e)

        if hasattr(channel, "threads"):
            for thread in channel.threads:
                try:
                    await readChannel(thread)
                except Exception as e:
                    print(thread.name, e)

    with open(pathSave, "w") as f:
        f.write("Top countries (with mono-nationals only):\n\n")
        f.write("\n".join(f"{country} with {nbMsgs} {'letters' if byLength else 'messages'}" for country, nbMsgs in sorted(nbMsgPerCountry.items(), key=lambda x: x[1], reverse = True)))
        f.write("\n\nTop multi-national users:\n")
        f.write("\n".join(f"{name} with {nbMsgs} {'letters' if byLength else 'messages'}" for name, nbMsgs in sorted(nbMsgPerMultinational.items(), key=lambda x: x[1], reverse = True)))
        f.write(f"\n\nTop 100 users of the {sys.argv[1] if len(sys.argv) >= 2 else 'month'}:\n")
        f.write("\n".join(f"#{i+1} {keyDicoByAuthorId[authId][2]} with {nbMsgs} {'letters' if byLength else 'messages'}" for i, (authId, nbMsgs) in zip(range(100), sorted(nbMsgPerPerson.items(), key=lambda x: x[1], reverse = True))))
        f.write("\n\n")
        f.write(topByCountryRole(keyDicoByAuthorId, nbMsgPerPerson))

    await bot.change_presence()
    quit()

def previousMonthYear(month, year):
    return (month - 1) if month != 1 else 12, year if month != 1 else year - 1

async def countStats(guild, bot):
    infosUser = dict()
    
    now = datetime.now()
    currentMonth, currentYear = now.month, now.year
    prevMonth, prevMonthYear = previousMonthYear(currentMonth, currentYear)

    timeLimitEarly = datetime(prevMonthYear, prevMonth, 1)
    timeLimitLate = datetime(currentYear, currentMonth, 1)
    
    os.makedirs("outputs", exist_ok=True)

    totalNbMsgs = [0]
    async def readChannel(channel):
        def saveStatus():
            with open("status.txt", "w") as f:
                f.write(f"Counting messages in #{channel.name} - {totalNbMsgs[0]}+ messages counted so far")
                print(f"Counting messages in #{channel.name} - {totalNbMsgs[0]}+ messages counted so far")
            with open("outputs/infoUserActivity.json", "w") as f:
                json.dump(infosUser, f)

        saveStatus()

        #let's read the messages sent last month in the current channel
        async for msg in channel.history(limit = None, after = timeLimitEarly, before = timeLimitLate):
            totalNbMsgs[0] += 1

            if totalNbMsgs[0] % 2000 == 0:
                saveStatus()

            authorId = str(msg.author.id)
            date = f"{msg.created_at.year}-{msg.created_at.month}-{msg.created_at.day}"
            channelId = str(channel.id)

            if authorId not in infosUser:
                infosUser[authorId] = dict()

            if channel.id not in infosUser[authorId]:
                infosUser[authorId][channelId] = dict()

            if date not in infosUser[authorId][channelId]:
                infosUser[authorId][channelId][date] = 0

            infosUser[authorId][channelId][date] += 1


    for channel in filter((lambda x: "logs" not in x.name), guild.text_channels): #let's read all the channels
        try: #discord raises Forbidden error if the bot is not allowed to read messages in "channel"
            await readChannel(channel)

            for thread in channel.threads:
                await readChannel(thread)
        except Exception as e:
            print(channel.name, e)

def main() -> None:
    intentsBot = discord.Intents.default()
    intentsBot.members = True
    intentsBot.messages = True
    intentsBot.message_content = True
    bot = commands.Bot(command_prefix=prefix, help_command=None, intents = intentsBot)

    @bot.event
    async def on_ready():
        if "statsUser" in sys.argv:
            await countStats(bot.get_guild(567021913210355745), bot)
        else:
            await countMessages(bot.get_guild(567021913210355745), bot)

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()

main()
