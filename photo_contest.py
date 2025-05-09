import asyncio
import nextcord as discord
from nextcord.ext import commands, tasks

import constantes
import genVoteInfo

import os
import json
import requests
from arrow import utcnow
from random import shuffle
from typing import Dict, List, Tuple, Optional

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
mapUrl = "https://cdn.discordapp.com/attachments/1288567770098696324/1290068767061053490/MapChart_Map_4.png?ex=66fb1daa&is=66f9cc2a&hm=766453dba0d1d6ed794cd3e6c14c9ec0a57aab6a42efdca73c97c074885d72b1&"
regions = ["Northern Europe", "Western Europe", "Eastern Europe", "Southern Europe", "Rest of the World"]
saveChannelId = 1157987716919734293
grandFinalChannel = 1290061898779332681
roleJury = 1290062262303854689
#TODO changer ici

Submission = Tuple[str, int, float] #url, author_id, timestamp
Vote = Tuple[int, str] #voter_id, url of the submission

#data of the contest
submissions: Dict[int, Dict[int, Dict[int, Submission]]] = dict() #{channel_id: {thread_id: {message_id: submission}}}
entriesInSemis: Dict[int, Dict[int, Submission]] = dict() #{channel_id: {message_id: submission}}
entriesInGF: Dict[int, List[Submission]] = dict() #{channel_of_origin_id: [submissions]} #channel_of_origin_id is grandFinalChannel for the last 4
votes1: Dict[int, List[Vote]] = dict() #{thread_id: list of votes}
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
    votes1 = {int(k): [tuple(x) for x in v] for k, v in votes1.items()} #for the jsonification, the set needs to be stored as a list. so we have to convert it here
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

def condorcet(rankings: Dict[int, List[Submission]], candidates: List[Submission]) -> Tuple[Submission, Dict[Tuple[Submission, Submission], Tuple[float, float]]]:
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
        winsDuels: Dict[Tuple[Submission, Submission], Tuple[float, float]] = dict()
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

def europoints(rankings: Dict[int, List[Submission]], candidates: List[Submission], howManyWinners: int, semiMode: bool = True, tieBreak = None) -> Tuple[List[Submission], Dict[Submission, int]]:
    points = [12, 10, 8, 7, 6, 5, 4, 3, 2, 1] if semiMode else [7, 5, 3, 2, 1]

    totalPoints = {candidate: 0 for candidate in candidates}
    for ranking in rankings.values():
        for nbPoints, candidate in zip(points, ranking):
            totalPoints[candidate] += nbPoints
        
    if tieBreak is None:
        tieBreakLoc = lambda x: totalPoints[x]
    else:
        tieBreakLoc = lambda x: (totalPoints[x], tieBreak(x))
    return sorted(totalPoints.keys(), key=tieBreakLoc, reverse=True)[:howManyWinners], totalPoints

#######################################################################
async def setup(*channels: discord.TextChannel):
    """Setup for the contest.

    Args:
    - channels: a list of channels, one channel per category
    """

    submissions.clear()
    submissions.update({c.id: dict() for c in channels})

    for channel in channels:
        channelInfo = submissions[channel.id]
        for region in regions:
            txt = f"Photos from {region}"
            msg = await channel.send(txt)
            thread = await msg.create_thread(name = txt, auto_archive_duration = 60 * 24 * 7) #1 week
            
            channelInfo[thread.id] = dict()
        
        await channel.send(mapUrl)
    
    saveData()

async def planner(now, bot):
    date, hour = (now.day, now.month), (now.hour, now.minute)
    if hour == (9, 0) and date == (30, 9):
        await start_submissions(bot)
    if hour == (0, 0) and date == (7, 10):
        await end_submissions(bot)
    if hour == (8, 0) and date == (7, 10):
        await start_vote_threads(bot)
    if hour == (22, 0) and date == (12, 10):
        await end_vote_threads(bot)
    if hour == (8, 0) and date == (13, 10):
        await start_semis(bot)
    if hour == (22, 0) and date == (19, 10):
        await end_semis(bot)
    if hour == (20, 0) and date == (20, 10):
        await start_gf1(bot)
    if hour == (22, 0) and date == (27, 10):
        #end of best of each semi-final
        await end_gf1(bot)
    if hour == (8, 0) and date == (28, 10):
        #grand final
        await start_gf2(bot)
    if hour == (22, 0) and date == (2, 11):
        #end of grand final
        await end_gf2(bot)
    if hour == (12, 0) and date == (3, 11):
        await resultats(bot)

async def resendFile(url: str, saveChannelId: int) -> str:
    """
    Sends submissions in a safe channel to then keep a constant url.

    Args:
    - the url of the submission

    Returns the new url of the file
    """ #renvoi chez le serveur squadro pour avoir une image quelque part
    filename = "-".join(url.replace("https://","").replace(":","").split("/")).split("?")[0]
    outputsPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    savePath = os.path.join(outputsPath, filename)
    os.system(f'wget -O {savePath} "{url}"')

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

    txt = f"**Hey! The photo contest is starting now!**\n\nPlease read the submission rules in <#1288568050810880001>.\nOne can submit **up to 2 photos** in this thread.\n\nSubmit photos in this thread that are **related with its geographic area**."
    #TODO corriger le texte ici
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

    contestState[0] = 0 #neutral mode
    saveData()

    channel = await bot.fetch_channel(1288567833546199171) #photo-contest-chat
    await channel.send("Submissions are closed, thanks for participating!\nVoting in threads will start in a few hours!")

    #change the submission numbers since it got messed by withdrawn submissions
    for channelInfo in submissions.values():
        for threadId, subs in channelInfo.items():
            thread = await bot.fetch_channel(threadId)

            for i, (messageId, (subUrl, _, _)) in enumerate(sorted(subs.items(), key=lambda x: x[0])):
                msg = await thread.fetch_message(messageId)
                e = discord.Embed(description = f"**Submission #{i+1}**")
                e.set_image(subUrl) 
                await msg.edit(embed = e)

async def start_vote_threads(bot):
    """Starts the vote in threads

    Args:
    - the bot object (to recover the channels)
    """

    for channelInfo in submissions.values():
        for threadId, subs in channelInfo.items():
            thread = await bot.fetch_channel(threadId)

            if len(subs) <= 5:
                await thread.send(f"There are {len(subs)} submissions in this thread, they are few enough to qualify directly for the semi-final!")
            else:
                for messageId in subs.keys():
                    msg = await thread.fetch_message(messageId)
                    await msg.add_reaction("👍")

                if len(subs) >= 12: #special voting for jurors
                    votes2[threadId] = dict()
                    msgVoteJury = await thread.send(f"**Special voting for <@&{roleJury}>!**\nReact to this message with ✅, the bot will send you a message to vote with your top 10.\n(in addition to your top 10, you can still upvote photos above like regular users)")
                    await msgVoteJury.add_reaction("✅")
    
    guild = bot.get_guild(voltServer)
    channel = await guild.fetch_channel(1288567833546199171) #photo-contest-chat
    await channel.send("<@&1290196320602030090> It's time to vote in submission threads! You can upvote as many photos as you want.")
    
    contestState[0] = 42 #mode "voting in threads"
    saveData()

async def end_vote_threads(bot):
    """Ends the vote in threads

    Args:
    - the bot object (to recover the channels)
    """
    
    roleJuryObj = bot.get_guild(voltServer).get_role(roleJury)
    jury = set(x.id for x in roleJuryObj.members)

    contestState[0] = 0 #neutral mode, voting is no longer allowed
    saveData()

    for channelId, channelInfo in submissions.items():
        channel = await bot.fetch_channel(channelId)
        entriesInSemis[channelId] = dict()

        count = 0
        for threadId, subs in channelInfo.items():
            #count the votes of the jury and global votes
            votesJury, globalVotes = {subs[s][0]: 0 for s in subs}, {subs[s][0]: 0 for s in subs}
            url2sub = {subs[s][0]: subs[s] for s in subs}

            if threadId in votes1:
                for voterId, subUrl in votes1[threadId]:
                    voteWeight = 1 if voterId != url2sub[subUrl][1] else 0
                    #self-votes are no longer allowed. but some may still be present in the data

                    if voterId in jury:
                        votesJury[subUrl] += voteWeight
                    
                    globalVotes[subUrl] += voteWeight
            
            #find out which submissions got selected
            #the best 2 photos according to the jury (wildcard), and the top 3 of the global vote (except the photos that got already selected)
            if len(subs) < 12: #too few submissions for a detailed europoints jury vote
                if len(votesJury):
                    selected = sorted(votesJury, key=lambda x: (votesJury[x], globalVotes[x], -url2sub[x][2]), reverse=True)[:2]
                    #the tie breaker for the vote among jurors is the global vote, then photos that got submitted earlier get the priority
                else:
                    selected = []

            else:
                #vote according to eurovision points for jurors, the global vote is still used as the tie breaker, then precedence
                top, scores = europoints(votes2[threadId], list(subs.values()), 2, True, lambda x: (globalVotes[x[0]], -x[2]))
                selected = [x[0] for x in top]
                votesJury = {url: nbPoints for (url, _, _), nbPoints in scores.items()}

            selected += sorted(filter(lambda x: x not in selected, globalVotes), key=lambda x: (globalVotes[x], votesJury[x], -url2sub[x][2]), reverse=True)[:5-len(selected)]
            shuffle(selected) #we don't want to show the selected photos in the order of their number of votes

            #post an announcement
            thread = await bot.fetch_channel(threadId)
            await thread.send(f"Here are the photos selected for the semi-finals in this thread:")

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

    emojiNb = {"0️⃣": 0, "1️⃣": 1, "2️⃣": 2, "3️⃣": 3}

    for channelId, entries in entriesInSemis.items():
        channel = await bot.fetch_channel(channelId)

        for msgId in entries:
            msg = await channel.fetch_message(msgId)
            for emoji in emojiNb:
                await msg.add_reaction(emoji)

        await channel.send("**You can give from 0 to 3 points to as many photos as you want among those above this message**")

        msgJuryVote = await channel.send(f"<@&{roleJury}> You can vote by reacting to this message with ✅")
        await msgJuryVote.add_reaction("✅")

    contestState[0] = 2
    saveData()

async def end_semis(bot):
    """
    Ends the voting period for the semi-final

    Args:
    - bot, the object representing the bot
    """

    contestState[0] = 0 #neutral mode, voting is no longer allowed
    saveData()

    roleJuryObj = bot.get_guild(voltServer).get_role(roleJury)
    jury = set(x.id for x in roleJuryObj.members)

    for channelId, entries in entriesInSemis.items():
        if channelId == grandFinalChannel: continue

        entriesInGF[channelId] = []

        channel = await bot.fetch_channel(channelId)
        count = 0

        #count the upvotes
        #the votes of contestants and jurors are used as a tie breaker
        votesJury, globalVotes = {v[0]: 0 for v in entries.values()}, {v[0]: 0 for v in entries.values()}
        url2sub = {v[0]: v for v in entries.values()}

        if channelId in votes1: #should be true but who knows
            for voterId, subUrl in votes1[channelId]:
                voteWeight = 1 if voterId != url2sub[subUrl][1] else 0
                #self-votes are no longer allowed, but they might still be in the data

                if voterId in jury:
                    votesJury[subUrl] += voteWeight
                
                globalVotes[subUrl] += voteWeight
        
        #find out which submissions got selected
        #the best photo 3 according to the jury, and the top 3 of the global vote (except the photos that got already selected)
        #find out which submissions got selected
        #the best 2 photos according to the jury (wildcard), and the top 3 of the global vote (except the photos that got already selected)
        if len(entries) < 20: #too few submissions for a detailed europoints jury vote
            if len(votesJury):
                selected = sorted(votesJury, key=lambda x: (votesJury[x], globalVotes[x], -url2sub[x][2]), reverse=True)[:3]
                #the tie breaker for the vote among jurors is the global vote, then photos that got submitted earlier get the priority
            else:
                selected = []

        else:
            #vote according to eurovision points for jurors, the global vote is still used as the tie breaker, then precedence
            top, scores = europoints(votes2[channelId], list(entries.values()), 3, True, lambda x: (globalVotes[x[0]], -x[2]))
            selected = [x[0] for x in top]
            votesJury = {url: nbPoints for (url, _, _), nbPoints in scores.items()}
        
        selected += sorted(filter(lambda x: x not in selected, globalVotes), key=lambda x: (globalVotes[x], votesJury[x], -url2sub[x][2]), reverse=True)[:6-len(selected)]
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
            await (await bot.fetch_channel(grandFinalChannel)).send(embed = e2)

            entriesInGF[channelId].append(url2sub[subUrl])
        
        if channelId in votes2:
            votes2[channelId*10] = votes2[channelId]
            del votes2[channelId]
            saveData()


async def start_gf1(bot):
    """
    Sends the message for voting in the first part of the Grand Final

    Args:
    - bot, the object representing the bot
    """

    emoji2channel = {(await bot.fetch_channel(channelId)).name[0]: channelId for channelId in entriesInGF}
    channel = await bot.fetch_channel(grandFinalChannel)

    contestState[0] = 3
    saveData()

    msg = await channel.send("**Vote for the first part of the Grand-Final!**\nReact to this message and the bot will ask you in DMs to rank the remaining 6 photos of the category.\n" + "\n".join(f"{e} for <#{channelId}>" for e, channelId in emoji2channel.items()))
    for e in emoji2channel:
        await msg.add_reaction(e)

async def end_gf1(bot):
    """
    End of the first part of GF1. Count the votes, find the Condorcet winner (if there is no Condorcet winner, select the winner of Borda with elimination)
    """

    entriesInGF[grandFinalChannel] = []

    channelGF = await bot.fetch_channel(grandFinalChannel)
    await channelGF.send("Europe, stop voting!")

    contestState[0] = 0
    saveData()

    jury_role = channelGF.guild.get_role(1290062262303854689)
    jurors = set(x.id for x in jury_role.members)

    id2name = dict()
    for channelId, submissionsFromChannel in entriesInGF.items():
        if channelId == grandFinalChannel: continue
        channel = await bot.fetch_channel(channelId)

        votes = votes2[channelId]

        _, scores_jury = europoints({voter: ranking for voter, ranking in votes.items() if voter in jurors}, submissionsFromChannel, 1, False)
        winnerGF, _ = europoints(votes, submissionsFromChannel, 1, False, lambda x: (scores_jury[x], -x[2]))

        #showing all the votes
        #-sub2id
        sub2photoid = {x: i+1 for i, x in enumerate(entriesInGF[channelId])}

        #-id2name of voters
        for voterId in votes.keys():
            voterId = int(voterId)
            if voterId not in id2name:
                id2name[voterId] = (await bot.fetch_user(voterId)).name
        
        await channel.send(f"It's time to check the results of the vote for the category.\n**{len(votes)} votes have been cast. Let's reveal them!**")
        for img_path, voter_id in genVoteInfo.genVoteAnimFinal(channel.name[2:].title(), sub2photoid, id2name, votes, jurors):
            if voter_id not in (None, -1):
                await channel.send(f"Thank you <@{voter_id}> {'(juror) ' if voter_id in jurors else ''}for your votes!", file=discord.File(img_path))
                await asyncio.sleep(15)
            else:
                await channel.send(file=discord.File(img_path))

        #announcement of the winner
        e = discord.Embed(description = f"**Congratulations, this photo won the <#{channelId}> category!**")
        e.set_image(url = winnerGF[0][0])
        await channel.send(embed = e)
        await channelGF.send(embed = e)

        #tell the author
        authorId = winnerGF[0][1]
        try:
            await (await dmChannelUser(await bot.fetch_user(authorId))).send(embed = e)
        except:
            pass

        entriesInGF[grandFinalChannel].append(winnerGF[0])
        saveData()

async def start_gf2(bot):
    """
    Beginning of GF2.
    """

    channel = await bot.fetch_channel(grandFinalChannel)

    msg = await channel.send("**Vote for the winner of the 2024 edition of the Volt Photo Contest!**\nReact to this message with ✅ and the bot will ask you in DMs to rank the remaining 4 photos.\nYou have to rank them all for your vote to count.")
    await msg.add_reaction("✅")

    contestState[0] = 4
    saveData()

async def end_gf2(bot):
    """
    End of the contest!
    """

    channel = await bot.fetch_channel(grandFinalChannel)

    await channel.send("Europe, stop voting!")
    contestState[0] = 0
    saveData()

    jury_role = channel.guild.get_role(1290062262303854689)
    jurors = set(x.id for x in jury_role.members)

    votes = votes2[grandFinalChannel]
    entries = entriesInGF[grandFinalChannel]

    _, scores_jury = europoints({voter: ranking for voter, ranking in votes.items() if voter in jurors}, entries, 1, False)
    winnerGF, _ = europoints(votes, entries, 1, False, lambda x: (scores_jury[x], -x[2]))
    
    #showing all the votes
    #-sub2id
    sub2photoid = {x: i+1 for i, x in enumerate(entries)}

    #-id2name of voters
    id2name = dict()
    for voterId in votes.keys():
        voterId = int(voterId)
        if voterId not in id2name:
            id2name[voterId] = (await bot.fetch_user(voterId)).name
    
    await channel.send(f"It's time to check the results of the final vote!\n**{len(votes)} votes have been cast. Let's reveal them!**")
    for img_path, voter_id in genVoteInfo.genVoteAnimFinal(channel.name[2:].title(), sub2photoid, id2name, votes, jurors, False):
        if voter_id not in (None, -1):
            await channel.send(f"Thank you <@{voter_id}> {'(juror) ' if voter_id in jurors else ''}for your votes!", file=discord.File(img_path))
            await asyncio.sleep(15)
        else:
            await channel.send(file=discord.File(img_path))

    #announcement of the winner
    url, authorId, _ = winnerGF[0]
    e = discord.Embed(description = f"**Congratulations <@{authorId}>, you won the 2024 edition of the Photo Contest!**")
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

            #e = discord.Embed(description = f"**You can upvote this photo with 👍**")
            nbSumissions = len(submissions[parent.id][thread.id]) + 1
            e = discord.Embed(description = f"**Submission #{nbSumissions}**")
            e.set_image(url = self.url)
            msgVote = await thread.send(embed = e)
            #await msgVote.add_reaction("👍")

            submissions[parent.id][thread.id][msgVote.id] = (self.url, self.userId, self.timestamp)
            saveData()

            await interaction.message.delete()
            await self.message.delete()
    
    @discord.ui.button(label = "❌", style = discord.ButtonStyle.blurple)
    async def deny(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id == self.userId:
            await interaction.message.delete()
            await self.message.delete()

def VoteGF(submissions: List[Submission], channelOfOrigin: int, labels: Optional[List[str]] = None, nbToRank: Optional[int] = None):
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

            affi = f"Photo #{idPhoto+1}" if labels is None else labels[i]
            @discord.ui.button(label = affi)
            async def callback(self, button: discord.ui.Button, interaction: discord.Interaction):
                if len(self.selectedItems) == (nbToRank or len(submissions)):
                    button.disabled = True
                    return

                self.selectedItems.append((submissions[idPhoto], affi))
                button.disabled = True

                if len(self.selectedItems) < (nbToRank or len(submissions)):
                    await interaction.message.edit(content = self.showSelected() + "\n" + f"Please click on a button below to select **your #{len(self.selectedItems)+1} preferred photo** (you have to rank {nbToRank or 'all'} photos for your vote to be taken into account)", view=self)
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
    message = ctx.message if hasattr(ctx, "message") else ctx

    if contestState[0] != 1:
        await ctx.channel.send("Sorry, you can't make submissions for the photo contest at the moment…")
        return
    
    #submissions have to be made in threads
    if ctx.channel.parent:
        parent = ctx.channel.parent

        if parent.id in submissions:
            subsThread = submissions[parent.id][ctx.channel.id]

            if url is None:
                if message.attachments != []:
                    url = message.attachments[0].url
            
            if url:
                if "#" in url: url = url.split("#")[0]

                ref = discord.MessageReference(message_id = message.id, channel_id = ctx.channel.id)

                if checkNbSubsPerThread(subsThread, userId): #the user is still allowed to make submissions in that thread
                    newUrl = await resendFile(url, saveChannelId)
                    await ctx.channel.send("Are you sure that:\n- you took this photo **__yourself__**?\n- the photo somewhat **fits the channel and the geographic area of the thread**?\n- the photo **did NOT compete in previous editions of the contest**?", view = ButtonConfirm(newUrl, userId, utcnow().timestamp(), message), reference = ref)
                else:
                    await ctx.channel.send("You are not allowed to submit more than 2 photos per thread. If you really want to submit this photo, withdraw one of your previous submissions by reacting to it with ❌, then submit this photo again.", reference = ref)
                    await message.delete()
            else:
                await ctx.channel.send("The submission seems to be invalid, I can't find a valid URL for your image.")
        else:
            await ctx.channel.send("Submissions for the photo contest are not allowed in this thread.")
    else:
        await ctx.channel.send("Submissions for the photo contest are not allowed in this channel.")
    
async def withdraw_submission(messageId, user, guild, emojiHash, channel):
    """
    Withdram a submission in the photo contest.
    """

    if emojiHash != "❌":
        return #we can ignore other reactions

    if hasattr(channel, "parent") and channel.parent.id in submissions: #good start
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

    emojiNb = {"0️⃣": 0, "1️⃣": 1, "2️⃣": 2, "3️⃣": 3}

    if emojiHash not in emojiNb or contestState[0] != 42:
        return #!=42 -> voting in threads is disabled

    if hasattr(channel, "parent") and channel.parent.id in submissions:
        parentId = channel.parent.id
        channelId = channel.id

        if channelId in submissions[parentId] and messageId in submissions[parentId][channelId]:
            #remove the reaction to make the vote invisible
            await (await channel.fetch_message(messageId)).remove_reaction(emojiHash, user)

            url, authorId, _ = submissions[parentId][channelId][messageId]
            if authorId == user.id: #votes for one's own entry are forbidden
                return

            if channelId not in votes1:
                votes1[channelId] = []
            
            #withdraw past votes of the user, then add the appropriate number, then tell the voter
            voteInfo = (user.id, url)

            while voteInfo in votes1[channelId]:
                votes1[channelId].remove(voteInfo)

            nbVotes = emojiNb[emojiHash]
            votes1[channelId] += [voteInfo] * nbVotes

            e = discord.Embed(description = f"I saved {nbVotes} point{'s' if nbVotes != 1 else ''} from you for this photo")
            e.set_image(url = url)
            try:
                await (await dmChannelUser(user)).send(embed = e)
            except:
                pass

            saveData()

async def cast_vote_jury(messageId, user, guild, emojiHash, channel):
    """
    Let a juror vote.
    """

    if emojiHash != "✅" or channel.id not in votes2 or contestState[0] != 42:
        return
    
    dmChannel = await dmChannelUser(user)

    roleJuryObj = guild.get_role(roleJury)
    if user.id not in (x.id for x in roleJuryObj.members):
        await dmChannel.send("You are not part of the jury, sorry.")
        return

    parentId = channel.parent.id
    threadId = channel.id

    subs = submissions[parentId][threadId]

    await dmChannel.send(f"**You can vote for your top 10 from {channel.mention} among submissions made by others**\nBeware that you have to provide a full top 10 for your jury vote to count.")
    validSubs = []
    labels = []
    for i, sub in enumerate(subs.values()):
        url, authorId, _ = sub
        if authorId != user.id:
            label = f"Photo #{i+1}"
            e = discord.Embed(description = label)
            e.set_image(url = url)
            await dmChannel.send(embed = e)

            validSubs.append(sub)
            labels.append(label)
    
    await dmChannel.send(view = VoteGF(validSubs, threadId, labels, 10))

async def cast_vote_semi(messageId, user, guild, emojiHash, channel):
    """
    Save an upvote during the semi-finals.
    """

    if contestState[0] != 2:
        return #the semi-finals are not open

    emojiNb = {"0️⃣": 0, "1️⃣": 1, "2️⃣": 2, "3️⃣": 3}

    if emojiHash not in emojiNb and emojiHash != "✅":
        return
    
    channelId = channel.id

    if channelId not in votes1 and channelId not in votes2:
        return

    if emojiHash in emojiNb:
        if channelId in entriesInSemis and messageId in entriesInSemis[channelId]:
            #remove the reaction to make the vote invisible
            await (await channel.fetch_message(messageId)).remove_reaction(emojiHash, user)
            
            submission = entriesInSemis[channelId][messageId]
            url, authorId, _ = submission
            if authorId == user.id: return #votes for one's own entry are forbidden

            voteInfo = (user.id, url)

            if channelId not in votes1:
                votes1[channelId] = []

            #remove the current votes and add the right number of votes
            nbVotes = emojiNb[emojiHash]
            while voteInfo in votes1[channelId]:
                votes1[channelId].remove(voteInfo)
            
            votes1[channelId] += [voteInfo] * nbVotes

            e = discord.Embed(description = f"I saved {nbVotes} points from you for this photo")
            e.set_image(url = url)
            try:
                await (await dmChannelUser(user)).send(embed = e)
            except:
                pass

            saveData()
    else: #jury vote
        #check that the user is a juror
        roleJuryObj = guild.get_role(roleJury)
        if user.id not in (x.id for x in roleJuryObj.members):
            await (await dmChannelUser(user)).send("You are not part of the jury, sorry.")
            return
        else:
            #send the voting information
            dmChannel = await dmChannelUser(user)

            if channelId in entriesInSemis:
                subs = list(entriesInSemis[channelId].values())

                await dmChannel.send(f"**You can vote for your top 10 from {channel.mention} among submissions made by others**\nBeware that you have to provide a full top 10 for your jury vote to count.")
                validSubs: List[Submission] = []
                labels: List[str] = []
                for i, sub in enumerate(subs):
                    url, authorId, _ = sub
                    if authorId != user.id:
                        label = f"Photo #{i+1}"
                        e = discord.Embed(description = label)
                        e.set_image(url = url)
                        await dmChannel.send(embed = e)

                        validSubs.append(sub)
                        labels.append(label)
                
                await dmChannel.send(view = VoteGF(validSubs, channelId, labels, 10))
    
async def cast_vote_gf(messageId, user, guild, emojiHash, channel):
    """
    Ask the bot to send a DM to vote in the first part of the Grand Final
    """

    if contestState[0] not in (3, 4): return #grand final mode
    
    emoji2channel: Dict[str, int] = {(await guild.fetch_channel(channelId)).name[0]: channelId for channelId in entriesInGF}
    emoji2channel["✅"] = grandFinalChannel

    if emojiHash not in emoji2channel or channel.id != grandFinalChannel:
        return
    
    if (emojiHash == "✅" and contestState[0] != 4) or (emojiHash != "✅" and contestState[0] != 3):
        return

    channelId = emoji2channel[emojiHash]
    dmChannel = await dmChannelUser(user)
    entries = entriesInGF[channelId]

    #send the voting information
    await dmChannel.send(f"**You can vote among the remaining 6 photos for <#{channelId}>**" if channelId != grandFinalChannel else "**You can vote among the 4 category winners to select the final winner of the contest!**")
    photoIds = dict()
    for i, submission in enumerate(entries):
        url, _, _ = submission
        photoIds[url] = i+1
        
        e = discord.Embed(description = f"Photo #{i+1} for <#{channelId}>")
        e.set_image(url = url)
        await dmChannel.send(embed = e)
    
    #remind the user how they voted in the semi-final
    if contestState[0] == 3:
        #-find jury vote
        if user.id in votes2[channelId * 10]:
            #it is a juror

            voteSemi = votes2[channelId * 10][user.id]
            recap: List[Tuple[Submission, Optional[int]]] = []
            for i, sub in enumerate(voteSemi):
                if sub in entries:
                    recap.append((sub, i+1))
            for sub in entries:
                if sub not in voteSemi:
                    recap.append((sub, None))
            
            await dmChannel.send("\n".join(f"Photo #{photoIds[url]} was **" + (f"your #{order}" if order else "not in your top 10") + "** in the semi-final" for (url, _, _), order in recap))
        
        #-find reactions votes
        votes_reactions = votes1[channelId]
        counts: Dict[int, int] = dict()
        for voter, url in votes_reactions:
            if voter == user.id and url in photoIds:
                photoId = photoIds[url]
                counts[photoId] = counts.get(photoId, 0) + 1
        if len(counts):
            await dmChannel.send("\n".join(f"You gave photo #{photoId} **{nbPoints} point{'s' if nbPoints != 1 else ''}** in the semi-final" for photoId, nbPoints in sorted(counts.items(), key=lambda x: x[1], reverse=True)))

    await dmChannel.send(f"Please click on a button below to select **your preferred photo** among the {len(entriesInGF[channelId])}", view = VoteGF(entriesInGF[channelId], channelId))

async def resultats(bot):
    """
    Show detailed results
    """

    guild = bot.get_guild(voltServer)
    jury_role = guild.get_role(1290062262303854689)
    jurors = set(x.id for x in jury_role.members)

    #results of the submission period

    id2name: Dict[int, str] = dict() #{user_id: user_name}

    for channelId, channelInfo in submissions.items():
        #submissions
        for threadId, subs in channelInfo.items():
            thread = await bot.fetch_channel(threadId)
            if threadId not in votes1:
                continue

            await thread.edit(archived = False)
                
            for i, (messageId, (subUrl, authorId, _)) in enumerate(subs.items()):
                #find the image file
                msg = await thread.fetch_message(messageId)
                urlImg = msg.embeds[0].image.url

                filename = os.path.basename(urlImg.split("?")[0])
                img_path = "data_contest/"+filename
                with open(img_path, "wb") as f:
                    f.write(requests.get(urlImg).content)

                #retrieve the info for the infobox
                photo_id = (i+1, subUrl, img_path, authorId)

                #check the submission
                upvotes = votes1[threadId]
                if threadId in votes2:
                    jury_votes = votes2[threadId]
                else:
                    jury_votes = dict()
                    for voter, vote_url in upvotes:
                        if voter in jurors and voter != authorId and vote_url == subUrl:
                            jury_votes[voter] = jury_votes.get(voter, 0) + 1

                #find the names of the users
                if isinstance(jury_votes, dict):
                    for voterId in jury_votes.keys():
                        voterId = int(voterId)
                        if voterId not in id2name:
                            id2name[voterId] = (await bot.fetch_user(voterId)).name
                for voterId, _ in upvotes:
                    if voterId not in id2name:
                        id2name[voterId] = (await bot.fetch_user(voterId)).name
                if authorId not in id2name:
                    id2name[authorId] = (await bot.fetch_user(authorId)).name
                
                #send the infobox
                pathInfobox = genVoteInfo.genSemiThread(thread.parent.name[2:].title(), photo_id, id2name, jury_votes, upvotes, thread.name[12:])
                channelRefresh = await bot.fetch_channel(saveChannelId)
                msgTmp = await channelRefresh.send(file = discord.File(pathInfobox))
                urlInfobox = msgTmp.attachments[0].url

                e = discord.Embed()
                e.set_image(urlInfobox)
                
                await msg.edit(embeds = [msg.embeds[0], e])
                
        channel = await guild.fetch_channel(channelId)
        #semi
        for i, (messageId, (subUrl, authorId, _)) in enumerate(entriesInSemis[channelId].items()):
            #find the image file
            msg = await channel.fetch_message(messageId)
            urlImg = msg.embeds[0].image.url

            filename = os.path.basename(urlImg.split("?")[0])
            img_path = "data_contest/"+filename
            with open(img_path, "wb") as f:
                f.write(requests.get(urlImg).content)

            #retrieve the info for the infobox
            photo_id = (i+1, subUrl, img_path, authorId)

            #check the submission
            upvotes = votes1[channelId]
            if channelId in votes2:
                jury_votes = votes2[channelId * 10]
            else:
                jury_votes = dict()
                for voter, vote_url in upvotes:
                    if voter in jurors and voter != authorId and vote_url == subUrl:
                        jury_votes[voter] = jury_votes.get(voter, 0) + 1
            
            #find the names of the users
            if isinstance(jury_votes, dict):
                for voterId in jury_votes.keys():
                    voterId = int(voterId)
                    if voterId not in id2name:
                        id2name[voterId] = (await bot.fetch_user(voterId)).name
            for voterId, _ in upvotes:
                if voterId not in id2name:
                    id2name[voterId] = (await bot.fetch_user(voterId)).name
            if authorId not in id2name:
                id2name[authorId] = (await bot.fetch_user(authorId)).name
            
            #send the infobox
            pathInfobox = genVoteInfo.genSemiThread(channel.name[2:].title(), photo_id, id2name, jury_votes, upvotes)
            
            channelRefresh = await bot.fetch_channel(saveChannelId)
            msgTmp = await channelRefresh.send(file = discord.File(pathInfobox))
            urlInfobox = msgTmp.attachments[0].url

            e = discord.Embed()
            e.set_image(urlInfobox)
            
            await msg.edit(embeds = [msg.embeds[0], e])

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

        if hasattr(message.channel, "parent") and message.channel.parent and message.channel.parent.id in submissions:
            if len(message.attachments) == 0:
                ref = discord.MessageReference(message_id = message.id, channel_id = message.channel.id)
                await message.channel.send(f"This doesn't count as a valid submission, please use the `{constantes.prefixVolt}submit` command as explained in <#1288568050810880001>. So I'm deleting your message.", delete_after = 3600, reference = ref)
                await message.delete()
            elif "submit" not in message.content:
                await submit(message, None)

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
            await cast_vote_jury(messageId, user, guild, emojiHash, channel)
            await cast_vote_semi(messageId, user, guild, emojiHash, channel)
            await cast_vote_gf(messageId, user, guild, emojiHash, channel)
    
    @bot.command(name = "setup")
    async def command_setup(ctx, *channels: discord.TextChannel):
        if ctx.author.id == organizerId:
            await setup(*channels)
        
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