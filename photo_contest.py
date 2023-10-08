import asyncio
import nextcord as discord
from nextcord.ext import commands, tasks

import constantes
import os
import json
import requests
from arrow import utcnow
from random import shuffle
from typing import Dict, List, Tuple, Optional, Set

#temporary way to update the bot
def stockePID():
    from os.path import join, dirname, abspath
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
saveChannelId = 1157987716919734293
grandFinalChannel = 1155786015521374208

Submission = Tuple[str, int, float] #url, author_id, timestamp
Vote = Tuple[int, str] #voter_id, url of the submission

#data of the contest
submissions: Dict[int, Dict[int, Dict[int, Submission]]] = dict() #{channel_id: {thread_id: {message_id: submission}}}
entriesInSemis: Dict[int, Dict[int, Submission]] = dict() #{channel_id: {message_id: submission}}
votes1: Dict[int, Set[Vote]] = dict() #{thread_id: list of votes}
votes2: Dict[int, Dict[int, List[str]]] = dict() #{channel_id: {voter_id: submissions}}
contestState: List[int] = [0] #0 for inactive, 1 for submission period, 2-3-4-5 for semi-finals (depending on the order of channels), 6 for the first final, 7 for the grand final
contestData = (submissions, entriesInSemis, votes1, votes2, contestState)

if "photo_contest_data.json" in os.listdir():
    with open("photo_contest_data.json") as f:
        contestData = json.load(f)

    submissions, entriesInSemis, votes1, votes2, contestState = contestData
    submissions = {int(i): {int(j): {int(k): tuple(w) for k, w in v.items()} for j, v in entries.items()} for i, entries in submissions.items()}
    entriesInSemis = {int(i): {int(j): tuple(w) for j, w in channels.items()} for i, channels in entriesInSemis.items()}
    votes1 = {int(k): {tuple(x) for x in v} for k, v in votes1.items()} #for the jsonification, the set needs to be stored as a list. so we have to convert it here

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
    votes1Loc = {k: list(v) for k, v in votes1.items()} #for the jsonificiation, the set needs to be stored as a list. so we have to convert it here
    with open("photo_contest_data.json", "w") as f:
        json.dump((submissions, entriesInSemis, votes1Loc, votes2, contestState), f)

def checkNbSubsPerThread(dictOfSubs: Dict[int, Submission], userId: int) -> bool:
    """
    Check whether the user is allowed to make new submissions in a given thread.

    Args:
    - dictOfSubs, the dict of submissions made in a thread
    - userId, the id of the user to check
    """
    return sum(x[1] == userId for x in dictOfSubs.values()) < 2 #one can submit up to 2 photos per thread

#######################################################################
async def setup(*channels: discord.TextChannel):
    """Setup for the contest.

    Args:
    - channels: a list of channels, one channel per category
    """

    submissions.clear()
    submissions.update({c.id: dict() for c in channels})

    for channel in channels:
        await channel.send(mapUrl)

        channelInfo = submissions[channel.id]
        for region in channelInfo.keys():
            txt = f"Photos from {region}"
            msg = await channel.send(txt)
            thread = await msg.create_thread(name = txt, auto_archive_duration = 60 * 24 * 7) #1 week
            
            channelInfo[thread.id] = []
    
    saveData()

async def planner(now, bot):
    date, hour = (now.day, now.month), (now.hour, now.minute)
    if hour == (8, 0) and date == (2, 10):
        await start_submissions(bot)
    if hour == (0, 0) and date == (9, 10):
        await end_submissions(bot)
    if hour == (0, 5) and date == (9, 10):
        await start_semis(bot)
    if hour == (22, 0) and date == (13, 10):
        await end_semis(bot)
    if hour == (22, 5) and date == (13, 10):
        #best of each semi-final
        pass
    if hour == (22, 0) and date == (16, 10):
        #end of best of each semi-final
        pass
    if hour == (22, 5) and date == (16, 10):
        #grand final
        pass
    if hour == (22, 0) and date == (22, 10):
        #end of grand final
        pass

async def resendFile(url: str, saveChannelId: int) -> str:
    """
    Sends submissions in a safe channel to then keep a constant url.

    Args:
    - the url of the submission

    Returns the new url of the file
    """ #renvoi chez le serveur squadro pour avoir une image quelque part
    filename = "-".join(url.replace(":","").split("/"))
    outputsPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    savePath = os.path.join(outputsPath, filename)
    r = requests.get(url)
    with open(savePath, "wb") as f:
        f.write(r.content)

    channelRefresh = await bot.fetch_channel(saveChannelId)
    msgTmp = await channelRefresh.send(file = discord.File(savePath))
    newUrl = msgTmp.attachments[0].url

    os.remove(savePath)

    return newUrl

async def traitementRawReact(payload):
    if payload.user_id != bot.user.id: #sinon, on est dans le cas d'une réaction en dm
        messageId = payload.message_id
        guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
        try:
            user = (await guild.fetch_member(payload.user_id)) if guild else (await bot.fetch_user(payload.user_id))
        except:
            user = (await bot.fetch_user(payload.user_id))
        channel = await bot.fetch_channel(payload.channel_id)

        partEmoji = payload.emoji
        emojiHash = partEmoji.id if partEmoji.is_custom_emoji() else partEmoji.name

        return locals()
    else:
        return None

async def start_submissions(bot):
    """Starts the submission period

    Args:
    - the bot object (to recover the channels)
    """

    txt = f"**Hey! The photo contest is starting now!**\n\nPlease read the submission rules in <#1155785196029890570>.\nOne can submit **up to 2 photos** in this thread. You can upvote **as many proposals as you want**, the 4 photos with most upvotes will reach the semi-final, as well as the photo with most upvotes from contestants.\n\nSubmit photos in this thread that are **related with its geographic area**."

    for channelInfo in submissions.values():
        for threadId in channelInfo.keys():
            thread = await bot.fetch_channel(threadId)
            await thread.send(txt)

    contestState[0] = 1 #set the contest state as "submission period"
    saveData()

async def end_submissions(bot):
    """Ends the submission period

    Args:
    - the bot object (to recover the channels)
    """

    contestants = set(authorId for channelInfo in submissions.values() for subs in channelInfo.values() for _, authorId, _ in subs.values())

    for channelId, channelInfo in submissions.items():
        channel = await bot.fetch_channel(channelId)
        entriesInSemis[channelId] = dict()

        count = 0
        for threadId, subs in channelInfo.items():

            #count the votes of authors and global votes
            votesContestants, globalVotes = {subs[s][0]: 0 for s in subs}, {subs[s][0]: 0 for s in subs}
            url2sub = {subs[s][0]: subs[s] for s in subs}

            if threadId in votes1:
                for voterId, subUrl in votes1[threadId]:
                    voteWeight = 1 if voterId != url2sub[subUrl][1] else 0.5
                    #when you vote for yourself, your vote is worth 0.5 only

                    if voterId in contestants:
                        votesContestants[subUrl] += voteWeight
                    
                    globalVotes[subUrl] += voteWeight
            
            #find out which submissions got selected
            #the best photo according to contestants, and the top 4 of the global vote (except the photo that got already selected)
            if len(votesContestants):
                selected = [max(votesContestants, key=lambda x: (votesContestants[x], globalVotes[x], -url2sub[x][2]))]
                #the tie breaker for the vote among contestants is the global vote, then photos that got submitted earlier get the priority
            else:
                selected = []
            selected += sorted(filter(lambda x: x not in selected, globalVotes), key=lambda x: (globalVotes[x], votesContestants[x], -url2sub[x][2]), reverse=True)[:5-len(selected)]
            shuffle(selected) #we don't want to show the selected photos in the order of their number of votes

            #post an announcement
            thread = await bot.fetch_channel(threadId)
            await thread.send(f"Here are the photos selected for the semi-finals in this thread")

            for subUrl in selected:
                #embed in the thread
                e = discord.Embed(description = "Congrats, this photo has been selected for the semi-finals!")
                e.set_image(url = subUrl)
                await thread.send(embed = e)
                try:
                    await (await dmChannelUser(await bot.fetch_user(url2sub[subUrl][1]))).send(embed = e)
                except:
                    pass

                #embed in the semi-final channel
                count += 1
                e2 = discord.Embed(description = f"Photo #{count} for <#{channelId}>")
                e2.set_image(url = subUrl)
                msgEntry = await channel.send(embed = e)

                entriesInSemis[channel.id][msgEntry.id] = url2sub[subUrl]
                saveData()

async def start_semis(bot):
    """
    Starts the voting period for the semi-finals

    Args:
    - bot, the object representing the bot
    """

    for channelId, entries in entriesInSemis.items():
        channel = await bot.fetch_channel(channelId)

        for msgId in entries:
            msg = await channel.fetch_message(msgId)
            await msg.add_reaction("👍")

        await channel.send("**You can upvote as many photos as you want among those above this message**\nThen the photos that will reach the grand-final will be the one that ranked the best among contestants' votes and the top 4 among the global vote.")

    contestState[0] = 2
    saveData()

async def end_semis(bot):
    """
    Ends the voting period for the semi-final

    Args:
    - bot, the object representing the bot
    """

    entriesInSemis[grandFinalChannel] = dict()

    for channelId, entries in entriesInSemis.items():
        if channelId == grandFinalChannel: continue

        contestants = set(authorId for channelInfo in submissions.values() for subs in channelInfo.values() for _, authorId, _ in subs.values())
        channel = await bot.fetch_channel(channelId)
        count = 0

        #count the votes of authors and global votes
        votesContestants, globalVotes = {v[0]: 0 for v in entries.values()}, {v[0]: 0 for v in entries.values()}
        url2sub = {v[0]: v for v in entries.values()}

        if channelId in votes1: #should be true but who knows
            for voterId, subUrl in votes1[channelId]:
                voteWeight = 1 if voterId != url2sub[subUrl][1] else 0.5
                #when you vote for yourself, your vote is worth 0.5 only

                if voterId in contestants:
                    votesContestants[subUrl] += voteWeight
                
                globalVotes[subUrl] += voteWeight
        
        #find out which submissions got selected
        #the best photo according to contestants, and the top 4 of the global vote (except the photo that got already selected)
        if len(votesContestants):
            selected = [max(votesContestants, key=lambda x: (votesContestants[x], globalVotes[x], -url2sub[x][2]))]
            #the tie breaker for the vote among contestants is the global vote, then photos that got submitted earlier get the priority
        else:
            selected = []
        selected += sorted(filter(lambda x: x not in selected, globalVotes), key=lambda x: (globalVotes[x], votesContestants[x], -url2sub[x][2]), reverse=True)[:5-len(selected)]
        shuffle(selected) #we don't want to show the selected photos in the order of their number of votes

        #post an announcement
        await channel.send(f"Here are the photos selected for the Grand Final from this channel:")

        for subUrl in selected:
            #embed in the thread
            e = discord.Embed(description = "Congrats, this photo has been selected for the Grand Final!")
            e.set_image(url = subUrl)
            await channel.send(embed = e)
            try:
                await (await dmChannelUser(await bot.fetch_user(url2sub[subUrl][1]))).send(embed = e)
            except:
                pass

            #embed in the Grand Final channel
            count += 1
            e2 = discord.Embed(description = f"Photo #{count} for <#{channelId}>")
            e2.set_image(url = subUrl)
            msgEntry = await (await bot.fetch_channel(grandFinalChannel)).send(embed = e)

            entriesInSemis[grandFinalChannel][msgEntry.id] = url2sub[subUrl]
        
        contestState[0] = 0
        saveData()

class ButtonConfirm(discord.ui.View):
    """
    A class for the confirmation button for submissions.
    """
    def __init__(self, url: str, userId: int, timestamp: int, message: discord.Message):
        super().__init__(timeout = 300)

        self.url = url
        self.userId = userId 
        self.timestamp = timestamp
        self.message = message
    
    @discord.ui.button(label = "Confirm", style = discord.ButtonStyle.blurple)
    async def confirmSub(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id == self.userId:
            thread = interaction.channel
            parent = thread.parent

            e = discord.Embed(description = f"**You can upvote this photo with 👍**")
            e.set_image(url = self.url)
            msgVote = await thread.send(embed = e)
            await msgVote.add_reaction("👍")

            submissions[parent.id][thread.id][msgVote.id] = (self.url, self.userId, self.timestamp)
            saveData()

            await interaction.message.delete()
            await self.message.delete()
    
    @discord.ui.button(label = "❌", style = discord.ButtonStyle.blurple)
    async def deny(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id == self.userId:
            await interaction.message.delete()
            await self.message.delete()

async def submit(ctx, url: Optional[str]):
    userId = ctx.author.id

    if contestState[0] != 1:
        await ctx.send("Sorry, you can't make submissions for the photo contest at the moment…")
        return
    
    #submissions have to be made in threads
    if ctx.channel.parent:
        parent = ctx.channel.parent

        if parent.id in submissions:
            subsThread = submissions[parent.id][ctx.channel.id]

            if url is None:
                if ctx.message.attachments != []:
                    url = ctx.message.attachments[0].url
            
            if url:
                if "#" in url: url = url.split("#")[0]
                if "?" in url: url = url.split("?")[0]

                ref = discord.MessageReference(message_id = ctx.message.id, channel_id = ctx.channel.id)

                if checkNbSubsPerThread(subsThread, userId): #the user is still allowed to make submissions in that thread
                    newUrl = await resendFile(url, saveChannelId)
                    await ctx.send("Are you sure that:\n- you took this photo yourself?\n- the photo somewhat fits the channel and the geographic area of the thread?", view = ButtonConfirm(newUrl, userId, utcnow().timestamp(), ctx.message), reference = ref)
                else:
                    await ctx.send("You are not allowed to submit more than 2 photos per thread. If you really want to submit this photo, withdraw one of your previous submissions by reacting to it with ❌, then submit this photo again.", reference = ref)
                    await ctx.message.delete()
            else:
                await ctx.send("The submission seems to be invalid, I can't find a valid URL for your image.")
        else:
            await ctx.send("Submissions for the photo contest are not allowed in this thread.")
    else:
        await ctx.send("Submissions for the photo contest are not allowed in this channel.")
    
async def withdraw_submission(messageId, user, guild, emojiHash, channel):
    """
    Withdram a submission in the photo contest.
    """

    if emojiHash != "❌":
        return #we can ignore other reactions

    if channel.parent and channel.parent.id in submissions: #good start
        parentId = channel.parent.id
        channelId = channel.id

        if channelId in submissions[parentId] and messageId in submissions[parentId][channelId]: #it is a submission
            if submissions[parentId][channelId][messageId][1] == user.id or await isMod(guild, user.id): #the user can withdraw the submission
                del submissions[parentId][channelId][messageId]
                msg = await channel.fetch_message(messageId)

                await msg.delete()
                saveData()

async def cast_vote_submission_period(messageId, user, guild, emojiHash, channel):
    if emojiHash != "👍":
        return

    if contestState[0] != 1:
        return #today is not a day of the submission period

    if channel.parent and channel.parent.id in submissions:
        parentId = channel.parent.id
        channelId = channel.id

        if channelId in submissions[parentId] and messageId in submissions[parentId][channelId]:
            url, _, _ = submissions[parentId][channelId][messageId]

            if channelId not in votes1:
                votes1[channelId] = set()
            
            #register the vote or withdraw it, then tell the voter
            voteInfo = (user.id, url)

            if voteInfo not in votes1[channelId]:
                votes1[channelId].add(voteInfo)
                e = discord.Embed(description = "Your upvote for this photo has been saved. You can withdraw it by reacting again with 👍 (in the server, not here).")
            else:
                votes1[channelId].remove(voteInfo)
                e = discord.Embed(description = "Your upvote for this photo has been properly withdrawn.")
            
            e.set_image(url = url)
            try:
                await (await dmChannelUser(user)).send(embed = e)
            except:
                pass

            #the following works because channel is a Thread
            if user.id not in {x.id for x in channel.members}: await channel.add_user(user)

            #remove the reaction to make the vote invisible
            await (await channel.fetch_message(messageId)).remove_reaction("👍", user)

            saveData()

async def cast_vote_semi(messageId, user, guild, emojiHash, channel):
    if emojiHash != "👍":
        return

    channelId = channel.id
    if contestState[0] != 2:
        return #the semi-finals are not open

    if channelId in entriesInSemis and messageId in entriesInSemis[channelId]:
        submission = entriesInSemis[channelId][messageId]
        url, _, _ = submission
        
        #remove the reaction to make the vote invisible
        await (await channel.fetch_message(messageId)).remove_reaction("👍", user)

        voteInfo = (user.id, url)

        if channelId not in votes1:
            votes1[channelId] = set()

        if voteInfo not in votes1[channelId]:
            votes1[channelId].add(voteInfo)
            e = discord.Embed(description = "Your upvote for this photo has been saved. You can withdraw it by reacting again with 👍 (in the server, not here).")
        else:
            votes1[channelId].remove(voteInfo)
            e = discord.Embed(description = "Your upvote for this photo has been properly withdrawn.")
        
        e.set_image(url = url)
        try:
            await (await dmChannelUser(user)).send(embed = e)
        except:
            pass

        saveData()

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

        if message.author.bot: return
        if message.channel.parent and message.channel.parent.id in submissions and ";submit" not in message.content:
            ref = discord.MessageReference(message_id = message.id, channel_id = message.channel.id)
            await message.channel.send("This doesn't count as a valid submission, please use the `;submit` command as explained in <#1155785196029890570>", delete_after = 3600, reference = ref)

    @bot.event
    async def on_raw_reaction_add(payload):
        traitement = await traitementRawReact(payload)
        if traitement:
            messageId = traitement["messageId"]
            user = traitement["user"]
            if user.bot: return #no need to go further

            guild = traitement["guild"]
            emojiHash = traitement["emojiHash"]
            channel = traitement["channel"]
        
            await withdraw_submission(messageId, user, guild, emojiHash, channel)
            await cast_vote_submission_period(messageId, user, guild, emojiHash, channel)
            await cast_vote_semi(messageId, user, guild, emojiHash, channel)
    
    @bot.command(name = "setup")
    async def command_setup(ctx, *channels: discord.TextChannel):
        if ctx.author.id == organizerId:
            await setup(ctx, *channels)
        
    @bot.command(name = "start_subs")
    async def command_stat_subs(ctx):
        if ctx.author.id == organizerId:
            await start_submissions(bot)
    
    @bot.command(name = "submit")
    async def command_submit(ctx, url: Optional[str]):
        await submit(ctx, url)

    return bot, constantes.TOKENVOLT

if __name__ == "__main__": #pour lancer le bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()