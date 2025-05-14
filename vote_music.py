import asyncio
import flag
import nextcord as discord
import pickle, os
import time
from arrow import utcnow
from nextcord.ext import commands
nextcord = discord

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constantes import TOKENVOLT as token
from data_contest.genSvg import generateSvgs

ADMIN_ID = 619574125622722560

try:
    if "vote_music.p" in os.listdir("data_contest"):
        JURY, infoVote, votes, msgVote = pickle.load(open("data_contest/vote_music.p", "rb"))
    else:
        raise Exception
except:
    JURY = set()
    #JURY = {ADMIN_ID}
    infoVote = {k: [] for k in JURY}
    votes = []
    msgVote = [0, 0, 0]

#songs = ["Iceland", "Poland", "Slovenia", "Estonia", "Ukraine", "Sweden", "Portugal", "Norway", "Belgium", "Azerbaijan", "San Marino", "Albania", "Netherlands", "Croatia", "Cyprus"]
songs = ["Australia", "Montenegro", "Ireland", "Latvia", "Armenia", "Austria", "Greece", "Lithuania", "Malta", "Georgia", "Denmark", "Czechia", "Luxembourg", "Israel", "Serbia", "Finland"]
#songs = []

countryCodes = {"Cyprus": "CY", "Serbia": "RS", "Lithuania": "LT", "Ireland": "IE", "Ukraine": "UA", "Poland": "PL",
                "Croatia": "HR", "Iceland": "IS", "Slovenia": "SI", "Finland": "FI", "Moldova": "MD", "Azerbaijan": "AZ",
                "Australia": "AU", "Portugal": "PT", "Luxembourg": "LU", "Malta": "MT", "Albania": "AL", "Greece": "GR",
                "Switzerland": "CH", "Czechia": "CZ", "Austria": "AT", "Denmark": "DK", "Armenia": "AM", "Latvia": "LV",
                "San Marino": "SM", "Georgia": "GE", "Belgium": "BE", "Estonia": "EE", "Israel": "IL", "Norway": "NO",
                "Netherlands": "NL", "Sweden": "SE", "UK": "GB", "Germany": "DE", "France": "FR", "Italy": "IT", "Spain": "ES",
                "Montenegro": "ME"}
flags = {country: flag.flag(countryCodes[country]) for country in songs}
flagsRev = {v: k for k, v in flags.items()}
timeClickVote = dict()
#JURY = set()

reactionsVote = ["🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮", "🇯",
"🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷", "🇸", "🇹", "🇺", "🇻", "🇼", "🇽", "🇾", "🇿"]
songsLoc = [(r, x) for r, (i, x) in zip(reactionsVote, enumerate(songs))]

numberVotesJury = 10
numberMaxVotesPublic = 20

#FUNCTIONS #####################################################################
async def dmChannelUser(user):
    if user.dm_channel is None:
        await user.create_dm()
    return user.dm_channel

def save():
    pickle.dump((JURY, infoVote, votes, msgVote), open("data_contest/vote_music.p", "wb"))

def countVotes():
    jury = dict()
    tele = {e: 0 for e in songs}

    calPointsJury = lambda i: 12 if i == 0 else 10 if i == 1 else 10-i
    pointsJury = lambda top: tuple((e, calPointsJury(i)) for i, e in enumerate(top[:10]))
    
    votesLoc = {i: x for i, x in enumerate(votes)}
    
    #let's keep only the last numberMaxVotesPublic votes of non-jury votes
    nbVotesNonContestant = dict()
    for i, (username, isJury, _) in reversed(list(enumerate(votes.copy()))):
        if not isJury:
            if username not in nbVotesNonContestant:
                nbVotesNonContestant[username] = 1
            else:
                nbVotesNonContestant[username] += 1
                if nbVotesNonContestant[username] > numberMaxVotesPublic:
                    del votesLoc[i]

    for (username, isJury, top) in votesLoc.values():
        if isJury:
            jury[username] = pointsJury(top)
        else:
            tele[top] += 1

    nbPointsJury = 58 * len(jury)

    #tele
    def hare(votes, nbPoints):
        totalVotes = sum(votes.values())
        points = {k: ((nbPoints * p) // totalVotes) if totalVotes > 0 else 0 for k, p in votes.items()}

        if totalVotes > 0:
            for k in sorted(votes, key=lambda x: (nbPoints * votes[x]) % totalVotes, reverse=True)[:nbPoints-sum(points.values())]:
                points[k] += 1

        return points

    idSong = lambda x: songs.index(x) + 1

    #register votes
    with open("data_contest/votes_new.csv", "w") as f:
        printF = lambda *args: f.write(" ".join(str(x) for x in args) + "\n")

        printF("Id;Username;Points")

        #jury
        for juror, recap in jury.items():
            for (song, points) in recap:
                printF(f"{idSong(song)};{juror};{points}")

        #tele
        nbPointsTeleBrut = sum(tele.values())
        for (song, points) in hare(tele, min(nbPointsJury, 4*nbPointsTeleBrut) if nbPointsJury else nbPointsTeleBrut).items():
            printF(f"{idSong(song)};public;{points}")

class ButtonConfirm(nextcord.ui.View):
    def __init__(self, song, remaining, selectPrec, listSongs):
        super().__init__(timeout = 300)
        self.value = None
        self.song = song
        self.remaining = remaining
        self.selectPrec = selectPrec
        self.listSongs = listSongs

    @nextcord.ui.button(label = "Confirm", style = nextcord.ButtonStyle.blurple)
    async def test(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        top = infoVote[interaction.user.id]
        top[-1] = self.song

        self.selectPrec.stop()
        self.stop()

        button.disabled=True
        await interaction.response.edit_message(view=self)

        if self.remaining > 0:
            await interaction.channel.send(f"Select the #{len(top)+1} song you prefer", view=ViewSelect([(r, e) for r, e in self.listSongs if e not in top], self.remaining, self.selectPrec.userId))
            save()
        else:
            await interaction.channel.send(f"**Thanks!**\n\n**Your vote:**\n" + "\n".join(f"**#{i+1}** __{e}__" for i, e in enumerate(top)))
            votes.append((interaction.user.name, True, tuple(top)))

            infoVote[interaction.user.id] = []
            save()

def ViewSelect(listSongs, remaining, userId):
    if len(songs) <= 25 or songs[0] in infoVote[userId]:
        class Aux(nextcord.ui.View):
            def __init__(self, listSongs, remaining, userId):
                super().__init__(timeout = 3600)
                self.value = None
                self.select = Select(listSongs[-25:], remaining, self)
                self.remaining = remaining
                self.userId = userId

                self.add_item(self.select)
    
    else:
        class Aux(nextcord.ui.View):
            def __init__(self, listSongs, remaining, userId):
                super().__init__(timeout = 3600)
                self.value = None
                self.select = Select(listSongs[-25:], remaining, self)
                self.remaining = remaining
                self.userId = userId

                self.add_item(self.select)
        
            @discord.ui.button(label=songs[0], style=discord.ButtonStyle.primary, emoji=reactionsVote[0], disabled=songs[0] in infoVote[userId])
            async def button_callback(self, button, interaction):
                num = len(infoVote[userId]) + 1

                async for msg in interaction.channel.history(limit = None):
                    if "Confirm" in msg.content and msg.author.bot: #there is one such message
                        await msg.delete()
                    
                    break

                infoUser = infoVote[self.userId]
                if infoUser == [] or infoUser[-1] is not None:
                    infoUser.append(None)
                    await interaction.response.send_message(content=f"Confirm {songs[0]} as #{num}" + " (you can still select another song thanks to the previous message)", view=ButtonConfirm(songs[0], self.remaining-1, self, songsLoc))

    return Aux(listSongs, remaining, userId)

class Select(nextcord.ui.Select):
    def __init__(self, listSongs, remaining, view):
        options = [discord.SelectOption(label=e, emoji=r) for r, e in listSongs]
        super().__init__(placeholder="Select an option", max_values=1, min_values=1, options=options)
        self.remaining = remaining
        self.parentView = view
        self.listSongs = listSongs

    async def callback(self, interaction: nextcord.Interaction):
        votesUser = infoVote[interaction.user.id]
        num = len(votesUser) + (votesUser == [] or votesUser[-1] is not None)

        async for msg in interaction.channel.history(limit = None):
            if "Confirm" in msg.content and msg.author.bot: #there is one such message
                await msg.delete()

            break

        infoUser = infoVote[self.parentView.userId]
        
        if infoUser == [] or infoUser[-1] is not None: infoUser.append(None)
        await interaction.response.send_message(content=f"Confirm {self.values[0]} as #{num}" + " (you can still select another song thanks to the previous message)", view=ButtonConfirm(self.values[0], self.remaining-1, self.parentView, self.listSongs))

async def vote(user):
    channel = await dmChannelUser(user)

    infoVote[user.id] = []
    await channel.send("__**_Jury voting_**__\n\nList of songs:\n" + "\n".join(f"- {r} **{e}**" for r, e in songsLoc))
    await channel.send("Select the #1 song you prefer.\n**Your jury vote will be counted only if you provide a full top 10.**\nYou can also cast non-jury votes by just reacting with country flags on the server.", view=ViewSelect(songsLoc, numberVotesJury, user.id))

async def saveVotePublic(user, song):
    votesOfUser = [song for (username, isJury, song) in reversed(votes) if user.name == username and not isJury]
    nbVotesOfUser = len(votesOfUser)
    otherVotesSong = len(list(filter(lambda x: x == song, votesOfUser)))
    maxVotesPerSong = 3

    channel = await dmChannelUser(user)

    if otherVotesSong < maxVotesPerSong and nbVotesOfUser < numberMaxVotesPublic:
        votes.append((user.name, False, song))
        await channel.send(f"**Your televote for __{song.upper()}__ has been properly saved.**\nYou have {numberMaxVotesPublic-nbVotesOfUser-1} televotes left.")
        save()
    elif otherVotesSong >= maxVotesPerSong:
        await channel.send(f"**You tried to vote for __{song.upper()}__, but you reached the limit of {maxVotesPerSong} televotes for the same country.**\nThis vote has not been taken into account.")
    else:
        await channel.send(f"**You already reached the limit of {numberMaxVotesPublic} votes, you can no longer vote with a country flag.**")

async def react_vote(messageId, user, guild, emojiHash, channel):
    if user.bot: return

    if (user.id in timeClickVote and time.time() - timeClickVote[user.id] > 60) or user.id not in timeClickVote:
        infoVote[user.id] = []

    if messageId in msgVote:
        timeClickVote[user.id] = time.time()
        if emojiHash == "🗳️" and infoVote[user.id] == []:
            await vote(user)
        elif emojiHash in flagsRev:
            await saveVotePublic(user, flagsRev[emojiHash])
            msg = await channel.fetch_message(messageId)
            await msg.remove_reaction(emojiHash, user)

async def startVote(channel):
    msg = await channel.send(f"**__Televote__**\nYou can also make **unranked votes by simply reacting with country flags** (only your first {numberMaxVotesPublic} votes will be counted)")
    msg2 = await channel.send("(Jury voting will also be available tonight, but only after the end of the last performance)")
    msgVote[0] = msg.id
    msgVote[1] = msg2.id
    msgVote[2] = channel.id
    save()

    for country in songs[:len(songs)//2]:
        await msg.add_reaction(flags[country])
    for country in songs[len(songs)//2:]:
        await msg2.add_reaction(flags[country])

async def showResults(channel):
    await channel.send("**Time for the results of the Volt Europa Discord's vote for the Second Semi-Final of the Eurovision Song Contest 2025!**")
    await channel.send("Let's start with Jury votes…")
    await asyncio.sleep(5)

    i = 0
    for filePath, currentVoter, nextVoter in generateSvgs():
        if currentVoter != "public":
            await channel.send(f"Thank you **{currentVoter}** for your votes <:meowhuggies_left:780807943704412241>", file=discord.File(filePath, filename="viewvotes.png"))
        
            await asyncio.sleep(5)
            if nextVoter is not None:
                await channel.send(f"Our next voter is… {nextVoter}")
                await asyncio.sleep(15)
            elif currentVoter == "jurors":
                await channel.send("**And now it is time to see the results of the Televote** :eyes:")
                await asyncio.sleep(5)
                await channel.send("So…")
                await asyncio.sleep(5)
                await channel.send("Waiting for the results right?")
                await asyncio.sleep(5)
                await channel.send("Okay, gimme a second")
                await asyncio.sleep(10)
                await channel.send("The points from the televote will be announced in the ascending order of the jury results")
                await asyncio.sleep(5)
                await channel.send("Here we go!")
        elif nextVoter:
            i += 1
            country, points = nextVoter
            await channel.send(f"**{points}** for **{country}** {flags[country]}", file=discord.File(filePath, filename="viewvotes.png"))
            if i < 15:
                await asyncio.sleep(10)
            elif i < 20:
                await asyncio.sleep(15)
            else:
                await asyncio.sleep(30)
        else:
            await channel.send(f"**Here are the full results of the Televote!**\nThank you for your votes <:meowhuggies_left:780807943704412241>", file=discord.File(filePath, filename="viewvotes.png"))

#MAIN ##########################################################################
def main():
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="T.", help_command=None, intents = intents)

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

            await react_vote(messageId, user, guild, emojiHash, channel)
    
    @bot.command(name = "vote")
    async def voteCommand(ctx):
        if ctx.author.id == ADMIN_ID:
            await startVote(ctx.channel)
    
    @bot.command(name = "start_jury")
    async def startJuryCommand(ctx):
        if ctx.author.id == ADMIN_ID and msgVote[0]:
            channel = await bot.fetch_channel(msgVote[2])
            msg = await channel.fetch_message(msgVote[1])
            
            await msg.edit(content = "__**Jury vote**__\nReact with 🗳️ to vote with a **full top 10** (if you vote again with 🗳️, only your latest top 10 counts as a jury vote)")
            await msg.add_reaction("🗳️")

    @bot.command(name = "count")
    async def countCommand(ctx):
        if ctx.author.id == ADMIN_ID:
            countVotes()
            await ctx.message.add_reaction("🗳️")
    
    @bot.command(name = "stop")
    async def stopCommand(ctx):
        if ctx.author.id == ADMIN_ID:
            msgVote[0] = None
            msgVote[1] = None
            msgVote[2] = None
    
    @bot.command(name = "get_votes")
    async def getVotesCommand(ctx):
        if ctx.author.id == ADMIN_ID:
            await ctx.send(file=discord.File("data_contest/votes_new.csv", filename="votes.csv"))

    @bot.command(name = "show_results")
    async def showResultsCommand(ctx):
        if ctx.author.id == ADMIN_ID:
            await showResults(ctx.channel)

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()

main()
