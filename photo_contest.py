import asyncio
import nextcord as discord
from nextcord.ext import commands, tasks

import constantes
import os
import json
import requests
from arrow import utcnow
from random import shuffle
from typing import Dict, List, Tuple, Optional, Set, Any

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
entriesInGF: Dict[int, List[Submission]] = dict() #{channel_of_origin_id: [submissions]} #channel_of_origin_id is grandFinalChannel for the last 4
votes1: Dict[int, Set[Vote]] = dict() #{thread_id: list of votes}
votes2: Dict[int, Dict[int, List[Submission]]] = dict() #{channel_id: {voter_id: submissions}}
contestState: List[int] = [0] #0 for inactive, 1 for submission period, 2-3-4-5 for semi-finals (depending on the order of channels), 6 for the first final, 7 for the grand final
contestData = (submissions, entriesInSemis, entriesInGF, votes1, votes2, contestState)

if "photo_contest_data.json" in os.listdir():
    with open("photo_contest_data.json") as f:
        contestData = json.load(f)

    submissions, entriesInSemis, entriesInGF, votes1, votes2, contestState = contestData
    submissions = {int(i): {int(j): {int(k): tuple(w) for k, w in v.items()} for j, v in entries.items()} for i, entries in submissions.items()}
    entriesInSemis = {int(i): {int(j): tuple(w) for j, w in channels.items()} for i, channels in entriesInSemis.items()}
    entriesInGF = {int(i): [tuple(x) for x in listSubs] for i, listSubs in entriesInGF.items()}
    votes1 = {int(k): {tuple(x) for x in v} for k, v in votes1.items()} #for the jsonification, the set needs to be stored as a list. so we have to convert it here
    votes2 = {int(i): {int(j): [tuple(x) for x in v] for j, v in val.items()} for i, val in votes2.items()}

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
        json.dump((submissions, entriesInSemis, entriesInGF, votes1Loc, votes2, contestState), f)

def checkNbSubsPerThread(dictOfSubs: Dict[int, Submission], userId: int) -> bool:
    """
    Check whether the user is allowed to make new submissions in a given thread.

    Args:
    - dictOfSubs, the dict of submissions made in a thread
    - userId, the id of the user to check
    """
    return sum(x[1] == userId for x in dictOfSubs.values()) < 2 #one can submit up to 2 photos per thread

def condorcet(rankings: Dict[int, List[Submission]], candidates: List[Submission]) -> Tuple[Submission, Dict[Tuple[Submission, Submission], Tuple[Submission, Tuple[float, float]]]]:
    """
    Compute the results of each duel from ranked voting ballots.
    Returns the Condorcet winner (or None if there is no winner) and detailed duel results.

    Args:
    - rankings, dictionary {voter_id: [submissions_ranked_by_voter]}
    - submissions, dictionary {url: submission}

    Returns:
    - the Condorcet winner (if it exists), Optional[Submission]
    - detailed duel results, Dict[Tuple[Submission, Submission], Tuple[Submission, Tuple[float, float]]]
    """

    def borda_elim():
        candidatesLoc = set(tuple(x) for x in candidates)
        while len(candidatesLoc) > 1:
            nbPoints = {c: 0 for c in candidatesLoc}

            for voterId, ranking in rankings.items():
                for i, sub in enumerate(filter(lambda x: x in nbPoints, ranking)):
                    authorId = sub[1]
                    malus = 0 if authorId != voterId else 0.5

                    nbPoints[sub] += len(candidatesLoc) - i - malus
            
            loser = min(nbPoints.items(), key=lambda x: (x[1], -x[0][2]))[0]
            #we remove the submission that got the lowest amount of points
            #in case of a tie, the submission that got submitted later gets the priority for getting removed
            
            candidatesLoc.remove(loser)
        
        return candidatesLoc.pop()

    if rankings == dict():
        #select the photo that got submitted earlier
        return min(candidates, key=lambda x: x[2]), dict()
    else:
        #{candidate_1: {candidate2: number_votes_candidate1_preferred_over_candidate2}}
        countsDuels: Dict[Submission, Dict[Submission, float]] = {c: {c2: 0 for c2 in candidates if c != c2} for c in candidates}

        for voterId, vote in rankings.items():
            for i, subI in enumerate(vote):
                _, authorId, _ = subI

                weightVote = 1 if voterId != authorId else 0.5
                for j in range(i+1, len(vote)):
                    countsDuels[subI][vote[j]] += weightVote
        
        #{(winner, loser): (score_winner, score_loser)}
        winsDuels: Dict[Tuple[Submission, Submission], Tuple[Submission, Tuple[float, float]]] = dict()
        for i, subI in enumerate(candidates):
            for j in range(i+1, len(candidates)):
                subJ = candidates[j]

                duelIJ = countsDuels[subI][subJ]
                duelJI = countsDuels[subJ][subI]

                if duelIJ > duelJI:
                    winsDuels[subI, subJ] = (duelIJ, duelJI)
                elif duelIJ < duelJI:
                    winsDuels[subJ, subI] = (duelJI, duelIJ)
                else: #there is a tie, we use the timestamp as a tiebreaker
                    timestampI = subI[2]
                    timestampJ = subJ[2]

                    if timestampI <= timestampJ:
                        #the probability of having an equality on the timestamp is neglictible
                        #the submission submitted earlier gets the priority
                        winsDuels[subI, subJ] = (duelIJ, duelJI)
        
        #let's find the condorcet winner
        nbWins = {c: 0 for c in candidates}
        for (a, b) in winsDuels:
            nbWins[a] += 1
        
        biggestWinner, nbWinsBigger = max(nbWins.items(), key=lambda x: x[1])
        if nbWinsBigger == len(candidates) - 1: #the candidate won all its duels, it is an actual Condorcet winner
            return biggestWinner, winsDuels
        else:
            return borda_elim(), winsDuels

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
    if hour == (8, 0) and date == (14, 10):
        await start_gf1(bot)
    if hour == (22, 5) and date == (17, 10):
        #end of best of each semi-final
        await end_gf1(bot)
    if hour == (22, 20) and date == (17, 10):
        #grand final
        await start_gf2(bot)
    if hour == (22, 0) and date == (22, 10):
        #end of grand final
        await end_gf2(bot)

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
                msgEntry = await channel.send(embed = e2)

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

    for channelId, entries in entriesInSemis.items():
        if channelId == grandFinalChannel: continue

        entriesInGF[channelId] = []

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
            e2 = discord.Embed(description = f"Photo #F{count} for <#{channelId}>")
            e2.set_image(url = subUrl)
            await (await bot.fetch_channel(grandFinalChannel)).send(embed = e2)

            entriesInGF[channelId].append(url2sub[subUrl])
        
        contestState[0] = 0
        saveData()

async def start_gf1(bot):
    """
    Sends the message for voting in the first part of the Grand Final

    Args:
    - bot, the object representing the bot
    """

    emoji2channel = {(await bot.fetch_channel(channelId)).name[0]: channelId for channelId in entriesInGF}
    channel = await bot.fetch_channel(grandFinalChannel)

    msg = await channel.send("**Vote for the first part of the Grand-Final!**\nReact to this message and the bot will ask you in DMs to rank the remaining 5 photos of the category.\n" + "\n".join(f"{e} for <#{channelId}>" for e, channelId in emoji2channel.items()))
    for e in emoji2channel:
        await msg.add_reaction(e)

async def end_gf1(bot):
    """
    End of the first part of GF1. Count the votes, find the Condorcet winner (if there is no Condorcet winner, select the winner of Borda with elimination)
    """

    entriesInGF[grandFinalChannel] = []

    channel = await bot.fetch_channel(grandFinalChannel)
    for channelId, submissionsFromChannel in entriesInGF.items():
        if channelId == grandFinalChannel: continue

        winnerGF, _ = condorcet(votes2[channelId], submissionsFromChannel)
        
        e = discord.Embed(description = f"**Congratulations, this photo won the <#{channelId}> category!**")
        e.set_image(url = winnerGF[0])
        await channel.send(embed = e)

        #tell the author
        authorId = winnerGF[1]
        try:
            await (await dmChannelUser(await bot.fetch_user(authorId))).send(embed = e)
        except:
            pass

        entriesInGF[grandFinalChannel].append(winnerGF)

        saveData()

async def start_gf2(bot):
    """
    Beginning of GF2.
    """

    channel = await bot.fetch_channel(grandFinalChannel)

    msg = await channel.send("**Vote for the winner of the 2023 edition of the Volt Photo Contest!**\nReact to this message with ✅ and the bot will ask you in DMs to rank the remaining 4 photos.\nYou have to rank them all for your vote to count.")
    await msg.add_reaction("✅")

async def end_gf2(bot):
    """
    End of the contest!
    """

    channel = await bot.fetch_channel(grandFinalChannel)
    winnerGF, _ = condorcet(votes2[grandFinalChannel], entriesInGF[grandFinalChannel])
    
    url, authorId, _ = winnerGF
    e = discord.Embed(description = f"**Congratulations <@{authorId}>, you won the 2023 edition of the Photo Contest!**")
    e.set_image(url = url)

    await channel.send(embed = e)

    try:
        await (await dmChannelUser(await bot.fetch_user(authorId))).send(embed = e)
    except:
        pass

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

def VoteGF(submissions: List[Submission], channelOfOrigin: int):
    """
    Defines the view for voting in the Grand Final
    """

    class Aux(discord.ui.View):
        def __init__(self):
            super().__init__(timeout = 3600)
            self.selectedItems: List[Tuple[Submission, str]] = []
        
        def showSelected(self):
            selectedItems = self.selectedItems
            return "\n".join(f"**#{i+1}** {affi}" for i, (_, affi) in enumerate(selectedItems))

    Aux.__view_children_items__ = []

    for i in range(len(submissions)):
        def aux(idPhoto):
            #trick to keep idPhoto correct, because otherwise it would be evaluated at the end of the loop
            #with i = 4 for all callbacks

            affi = f"Photo #F{idPhoto+1}"
            @discord.ui.button(label = affi)
            async def callback(self, button: discord.ui.Button, interaction: discord.Interaction):
                self.selectedItems.append((submissions[idPhoto], affi))
                button.disabled = True

                if len(self.selectedItems) < len(submissions):
                    await interaction.message.edit(content = self.showSelected() + "\n" + f"Please click on a button below to select **your #{len(self.selectedItems)+1} preferred photo** (you have to rank all photos for your vote to be taken into account)", view=self)
                else:
                    if channelOfOrigin not in votes2:
                        votes2[channelOfOrigin] = dict()

                    votes2[channelOfOrigin][interaction.user.id] = list(map(lambda x: x[0], self.selectedItems))
                    saveData()

                    await interaction.message.edit(content = "**Your vote has been saved**\nYou can change it by reacting again on the server\n\n" + self.showSelected(), view = self)
            
            return callback
        
        fonc = aux(i)
        
        #add the button to Aux
        setattr(Aux, f"callback{i}", fonc)
        Aux.__view_children_items__.append(fonc)
    
    return Aux()
            
async def submit(ctx, url: Optional[str]):
    """
    Submit a photo
    """

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
    """
    Save an upvote during the submission period.
    """

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
    """
    Save an upvote during the semi-finals.
    """

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
    
async def cast_vote_gf(messageId, user, guild, emojiHash, channel):
    """
    Ask the bot to send a DM to vote in the first part of the Grand Final
    """
    
    emoji2channel: Dict[str, int] = {(await guild.fetch_channel(channelId)).name[0]: channelId for channelId in entriesInGF}
    emoji2channel["✅"] = grandFinalChannel

    if emojiHash not in emoji2channel or channel.id != grandFinalChannel:
        return

    channelId = emoji2channel[emojiHash]
    dmChannel = await dmChannelUser(user)

    await dmChannel.send(f"**You can vote among the remaining 5 photos for <#{channelId}>**" if channelId != grandFinalChannel else "**You can vote among the 4 category winners to select the final winner of the contest!**")
    for i, submission in enumerate(entriesInGF[channelId]):
        url, _, _ = submission
        
        e = discord.Embed(description = f"Photo #F{i+1} for <#{channelId}>")
        e.set_image(url = url)
        await dmChannel.send(embed = e)
    
    await dmChannel.send(f"Please click on a button below to select **your preferred photo** among the {len(entriesInGF[channelId])}", view = VoteGF(entriesInGF[channelId], channelId))

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

        if hasattr(message.channel, "parent") and message.channel.parent and message.channel.parent.id in submissions and ";submit" not in message.content:
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
            await cast_vote_gf(messageId, user, guild, emojiHash, channel)
    
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