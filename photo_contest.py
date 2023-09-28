import asyncio
import nextcord as discord
from nextcord.ext import commands, tasks

import constantes
import os
import json
from arrow import utcnow
from random import shuffle
from typing import Dict, List, Tuple

#temporary way to update the bot
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
organizerId = 619574125622722560
mapUrl = "https://cdn.discordapp.com/attachments/847488864713048144/1155832232792047667/image.png"
regions = ["Northern Europe", "Western Europe", "Central Europe", "Eastern Europe", "Southern Europe", "Rest of the World"]
Submission = Tuple[str, int, float] #url, author_id, timestamp
Vote = Tuple[int, str] #voter_id, url of the submission

#data of the contest
submissions: Dict[int, Dict[str, Tuple[int, List[Submission]]]] = dict() #{channel_id: {region_name: (thread_id, submissions)}}
votes1: Dict[int, List[Vote]] = dict() #{thread_id: list of votes}
votes2: Dict[int, Dict[int, List[str]]] = dict() #{channel_id: {voter_id}}
contestState: List[int] = [0] #0 for inactive, 1 for submission period, 2-3-4-5 for semi-finals (depending on the order of channels), 6 for the first final, 7 for the grand final
contestData = (submissions, votes1, votes2, contestState)

if "photo_contest_data.json" in os.listdir():
    with open("photo_contest_data.json") as f:
        contestData = json.load(f)

    submissions, votes1, votes2 = contestData

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

def saveData():
    with open("photo_contest_data.json", "w") as f:
        json.dump(contestData, f)

#######################################################################
async def setup(*channels: discord.TextChannel):
    """Setup for the contest.

    Args:
    - channels: a list of channels, one channel per category
    """

    submissions.clear()
    submissions.update({c.id: {r: (0, []) for r in regions} for c in channels})

    for channel in channels:
        await channel.send(mapUrl)

        channelInfo = submissions[channel.id]
        for region in channelInfo.keys():
            txt = f"Photos from {region}"
            msg = await channel.send(txt)
            thread = await msg.create_thread(name = txt, auto_archive_duration = 60 * 24 * 7) #1 week
            
            channelInfo[region] = (thread.id, [])
    
    saveData()

async def planner(now, bot):
    if now.minute == 0 and now.hour == 8 and (now.day, now.month) == (26, 9):
        await start_submissions(bot)
    if now.minute == 0 and now.hour == 0 and (now.day, now.month) == (28, 9):
        await end_submissions(bot)

async def start_submissions(bot):
    """Starts the submission period

    Args:
    - the bot object (to recover the channels)
    """

    txt = f"**Hey! The photo contest is starting now!**\n\nPlease read the submission rules in <#1155785196029890570>.\nOne can submit **up to 2 photos** in this thread. You can upvote **as many proposals as you want**, the 4 photos with most upvotes will reach the semi-final, as well as the photo with most upvotes from contestants.\n\nSubmit photos in this thread that are **related with its geographic area**."

    for channelInfo in submissions.values():
        for threadId, _ in channelInfo.values():
            thread = await bot.fetch_channel(threadId)
            await thread.send(txt)

    contestState[0] = 1 #set the contest state as "submission period"
    saveData()

async def end_submissions(bot):
    """Ends the submission period

    Args:
    - the bot object (to recover the channels)
    """

    contestants = set(authorId for channelInfo in submissions.values() for _, subs in channelInfo.values() for _, authorId, _ in subs)

    for channelId, channelInfo in submissions.items():
        channel = await bot.fetch_channel(channelId)

        count = 0
        for threadName, (threadId, subs) in channelInfo.items():
            #count the votes of authors and global votes
            votesContestants, globalVotes = {s[0]: 0 for s in subs}, {s[0]: 0 for s in subs}
            url2sub = {s[0]: s for s in subs}

            for voterId, subUrl in votes1[threadId]:
                if voterId in contestants:
                    votesContestants[subUrl] += 1
                
                globalVotes[subUrl] += 1
            
            #find out which submissions got selected
            #the best photo according to contestants, and the top 4 of the global vote (except the photo that got already selected)
            selected = [max(votesContestants, key=lambda x: votesContestants[x])]
            selected += sorted(filter(lambda x: x not in selected, globalVotes), key=lambda x: (globalVotes[x], votesContestants[x]), reverse=True)[:4]
            shuffle(selected) #we don't want to show the selected photos in the order of their number of votes

            #post an announcement
            thread = await bot.fetch_channel(threadId)
            await thread.send(f"Here are the photos selected for the semi-finals in the thread {threadName}")

            for subUrl in selected:
                #embed in the thread
                e = discord.Embed(description = "Congrats, this photo has been selected for the semi-finals!")
                e.set_image(url = subUrl)
                await thread.send(embed = e)
                await (await dmChannelUser(url2sub[subUrl][1])).send(embed = e)

                #embed in the semi-final channel
                count += 1
                e2 = discord.Embed(description = f"Photo #{count} for <#{channelId}>")
                e2.set_image(url = subUrl)
                await channel.send(embed = e)

#######################################################################

def main():
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix=constantes.prefixVolt, help_command=None, intents = intents)

    @tasks.loop(minutes = 1.0)
    async def autoplanner():
        now = utcnow().to("Europe/Brussels")
        await planner(now, bot)

    @bot.event
    async def on_ready():
        autoplanner.start()

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
    
    @bot.command(name = "setup")
    async def command_setup(ctx, *channels: discord.TextChannel):
        if ctx.author.id == organizerId:
            await setup(ctx, *channels)

    return bot, constantes.TOKENVOLT

if __name__ == "__main__": #pour lancer le bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()