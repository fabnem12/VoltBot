import asyncio
import csv
import flag
import discord
import pickle, os
import random
import time
from arrow import utcnow
from discord.ext import commands

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
    msgVote: list[int | None] = [0, 0, 0]

timeClickVote = dict()
vote_status_dms: dict[int, discord.Message] = {}
voting_closed = False
live_count_msg: discord.Message | None = None

ALL_COUNTRY_CODES = {
    "Albania": "AL", "Armenia": "AM", "Australia": "AU", "Austria": "AT",
    "Azerbaijan": "AZ", "Belgium": "BE", "Bulgaria": "BG", "Croatia": "HR",
    "Cyprus": "CY", "Czechia": "CZ", "Denmark": "DK", "Estonia": "EE",
    "Finland": "FI", "France": "FR", "Georgia": "GE", "Germany": "DE",
    "Greece": "GR", "Iceland": "IS", "Ireland": "IE", "Israel": "IL",
    "Italy": "IT", "Latvia": "LV", "Lithuania": "LT", "Luxembourg": "LU",
    "Malta": "MT", "Moldova": "MD", "Montenegro": "ME", "Netherlands": "NL",
    "Norway": "NO", "Poland": "PL", "Portugal": "PT", "Romania": "RO",
    "San Marino": "SM", "Serbia": "RS", "Slovenia": "SI", "Spain": "ES",
    "Sweden": "SE", "Switzerland": "CH", "Ukraine": "UA", "UK": "GB",
}

songs: list[str] = []
countryCodes: dict[str, str] = {}
flags: dict[str, str] = {}
flagsRev: dict[str, str] = {}

def load_songs_from_file(path: str) -> list[str]:
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        return [row["Name"] for row in reader]

def load_semi(semi: str):
    global songs, countryCodes, flags, flagsRev

    if semi == "1":
        songs = load_songs_from_file("data_contest/acts_sf1.csv")
    elif semi == "2":
        songs = load_songs_from_file("data_contest/acts_sf2.csv")
    elif semi == "F":
        songs = load_songs_from_file("data_contest/acts.csv")
    else:
        return

    countryCodes = {s: ALL_COUNTRY_CODES[s] for s in songs if s in ALL_COUNTRY_CODES}
    flags = {country: flag.flag(code) for country, code in countryCodes.items()}
    flagsRev = {v: k for k, v in flags.items()}

load_semi("1")

numberVotesJury = 10
numberMaxVotesPublic = 20
maxVotesPerSong = 5

#FUNCTIONS #####################################################################
async def dmChannelUser(user):
    if user.dm_channel is None:
        await user.create_dm()
    assert user.dm_channel is not None
    return user.dm_channel

def save():
    pickle.dump((JURY, infoVote, votes, msgVote), open("data_contest/vote_music.p", "wb"))

def countVotes():
    jury = dict()
    tele = {e: 0 for e in songs}

    calPointsJury = lambda i: 12 if i == 0 else 10 if i == 1 else 10-i
    pointsJury = lambda top: tuple((e, calPointsJury(i)) for i, e in enumerate(top[:10]))
    
    votesLoc = {i: x for i, x in enumerate(votes)}

    # Keep only votes for the currently loaded semi/final
    votesLoc = {
        i: v for i, v in votesLoc.items()
        if (not v[1] and v[2] in songs) or (v[1] and all(c in songs for c in v[2]))
    }

    #let's keep only the last numberMaxVotesPublic votes of non-jury votes
    nbVotesNonContestant = dict()
    for i, (username, isJury, _) in reversed(list(enumerate(votes.copy()))):
        if not isJury:
            if username not in nbVotesNonContestant:
                nbVotesNonContestant[username] = 1
            else:
                nbVotesNonContestant[username] += 1
                if nbVotesNonContestant[username] > numberMaxVotesPublic:
                    if i in votesLoc:
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

class JuryVotingView(discord.ui.View):
    def __init__(self, user_id, remaining, songs_slice):
        super().__init__(timeout = 3600)
        self.user_id = user_id
        self.remaining = remaining
        self.songs_slice = songs_slice
        self.other_msg: discord.Message | None = None
        self._build()

    def _build(self):
        self.clear_items()
        for i, country in enumerate(self.songs_slice):
            flag_emoji = flags.get(country)
            picked = country in infoVote.get(self.user_id, [])
            btn = discord.ui.Button(label=country, emoji=flag_emoji, style=discord.ButtonStyle.success if picked else discord.ButtonStyle.secondary, disabled=picked, row=i // 5)
            btn.callback = self._make_callback(country)
            self.add_item(btn)

    def _make_callback(self, country):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return

            ranking = infoVote.setdefault(self.user_id, [])

            if country in ranking:
                self._build()
                await interaction.response.edit_message(view=self)
                return

            ranking.append(country)
            infoVote[self.user_id] = ranking
            save()

            self._build()

            text = "\n".join(f"**#{i+1}** {c}" for i, c in enumerate(ranking))
            if len(ranking) < self.remaining:
                await interaction.response.edit_message(content=f"**Your ranking so far:**\n{text}\n\nSelect **#{len(ranking)+1}**:", view=self)
            else:
                self.clear_items()
                await interaction.response.edit_message(content=f"**Your complete top {self.remaining}:**\n{text}", view=self)
                if self.other_msg:
                    empty_view = discord.ui.View()
                    await self.other_msg.edit(view=empty_view)
                await interaction.followup.send("Submit or cancel your vote?", view=JuryConfirmView(self.user_id, ranking))

        return callback

class JuryConfirmView(discord.ui.View):
    def __init__(self, user_id, ranking):
        super().__init__(timeout = 300)
        self.user_id = user_id
        self.ranking = ranking

    @discord.ui.button(label="Submit vote", style=discord.ButtonStyle.success, emoji="✅")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        votes.append((interaction.user.name, True, tuple(self.ranking)))
        infoVote[self.user_id] = []
        save()
        await interaction.response.edit_message(content="**Thanks!** Your jury vote has been saved.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        infoVote[self.user_id] = []
        save()
        await interaction.response.edit_message(content="Vote cancelled.", view=None)

async def update_live_count(channel: discord.abc.Messageable | None):
    global live_count_msg
    if channel is None:
        return
    count = len([v for v in votes if not v[1] and v[2] in songs])
    content = f"📊 **Live Televote:** {count} vote{'s' if count != 1 else ''} cast"
    if live_count_msg is not None:
        try:
            await live_count_msg.edit(content=content)
        except:
            live_count_msg = await channel.send(content)
    else:
        live_count_msg = await channel.send(content)

class PublicVoteView(discord.ui.View):
    def __init__(self, songs_slice):
        super().__init__(timeout=3600)
        self.songs_slice = songs_slice
        self.other_msg: discord.Message | None = None
        self._build()

    def _build(self):
        self.clear_items()
        for i, country in enumerate(self.songs_slice):
            btn = discord.ui.Button(label=country, emoji=flags.get(country), style=discord.ButtonStyle.secondary, row=i // 5)
            btn.callback = self._make_callback(country)
            self.add_item(btn)

    def _make_callback(self, country):
        async def callback(interaction: discord.Interaction):
            user = interaction.user
            song = country

            if voting_closed:
                await interaction.response.send_message("Voting is closed.", ephemeral=True, delete_after=5)
                return

            user_votes = [s for (u, is_jury, s) in votes if u == user.name and not is_jury and s in songs]
            nb_votes = len(user_votes)
            same_song = sum(1 for s in user_votes if s == song)

            if same_song >= maxVotesPerSong:
                await interaction.response.send_message(
                    f"You reached the limit of {maxVotesPerSong} votes for **{song}**.", ephemeral=True, delete_after=5
                )
                return
            if nb_votes >= numberMaxVotesPublic:
                await interaction.response.send_message(
                    f"You reached the limit of {numberMaxVotesPublic} votes.", ephemeral=True, delete_after=5
                )
                return

            votes.append((user.name, False, song))
            save()
            assert isinstance(interaction.channel, discord.abc.Messageable)
            await update_live_count(interaction.channel)

            from collections import Counter
            all_user_votes = user_votes + [song]
            counts = Counter(all_user_votes)
            summary = "\n".join(
                f"{flags.get(c, '')} **{c}**: {n}/{maxVotesPerSong}"
                for c, n in counts.most_common()
            )
            remaining = numberMaxVotesPublic - nb_votes - 1
            content = (
                f"**Voted for {flags.get(song, '')} {song}!**\n\n"
                f"**Your votes ({nb_votes + 1}/{numberMaxVotesPublic}):**\n{summary}\n\n"
                f"*You can still cast {remaining} vote{'s' if remaining != 1 else ''}*"
            )

            await interaction.response.send_message(content, ephemeral=True, delete_after=5)

            try:
                if user.id in vote_status_dms:
                    await vote_status_dms[user.id].edit(content=content)
                else:
                    vote_status_dms[user.id] = await user.send(content)
            except discord.Forbidden:
                pass

        return callback

async def vote(user):
    channel = await dmChannelUser(user)

    if voting_closed:
        await channel.send("Voting is closed.")
        return

    infoVote[user.id] = []
    song_list = "\n".join(f"{str(i+1).zfill(2)} {flags.get(c, '')} **{c}**" for i, c in enumerate(songs))
    await channel.send(f"**__Jury voting__**\n\nSongs:\n{song_list}")

    if len(songs) > 25:
        view1 = JuryVotingView(user.id, numberVotesJury, songs[:25])
        view2 = JuryVotingView(user.id, numberVotesJury, songs[25:])
        msg1 = await channel.send(f"Click the buttons in order of preference to build your top {numberVotesJury}.\n**Your jury vote will be counted only if you provide a full top 10.** (Page 1/2)", view=view1)
        msg2 = await channel.send("(Page 2/2)", view=view2)
        view1.other_msg = msg2
        view2.other_msg = msg1
    else:
        await channel.send(f"Click the buttons in order of preference to build your top {numberVotesJury}.\n**Your jury vote will be counted only if you provide a full top 10.**", view=JuryVotingView(user.id, numberVotesJury, songs))


async def react_vote(messageId, user, guild, emojiHash, channel):
    if user.bot: return

    if (user.id in timeClickVote and time.time() - timeClickVote[user.id] > 60) or user.id not in timeClickVote:
        infoVote[user.id] = []

    if messageId in msgVote:
        timeClickVote[user.id] = time.time()
        if emojiHash == "🗳️" and infoVote[user.id] == []:
            await vote(user)

async def startVote(channel):
    global voting_closed, live_count_msg
    voting_closed = False
    live_count_msg = None
    vote_status_dms.clear()

    view1 = PublicVoteView(songs[:25])
    if len(songs) > 25:
        view2 = PublicVoteView(songs[25:])
        msg1 = await channel.send(f"**__Televote__**\nClick on the countries you want to vote for!\n-# There is a limit of {numberMaxVotesPublic} votes per user and {maxVotesPerSong} votes per song", view=view1)
        msg2 = await channel.send("(Page 2/2)", view=view2)
        view1.other_msg = msg2
        view2.other_msg = msg1
    else:
        msg1 = await channel.send(f"**__Televote__**\nClick on the countries you want to vote for!\n-# There is a limit of {numberMaxVotesPublic} votes per user and {maxVotesPerSong} votes per song", view=view1)

    jury_msg = await channel.send("(Jury voting will also be available tonight, but only after the end of the last performance)")
    
    await update_live_count(channel)
    
    msgVote[0] = msg1.id
    msgVote[1] = jury_msg.id
    msgVote[2] = channel.id
    save()

async def showResults(channel, semi):
    semi_text = "First Semi-Final" if semi == "1" else "Second Semi-Final" if semi == "2" else "Grand Final"
    
    await channel.send(f"**Time for the results of the Volt Europa Discord's vote for the {semi_text} of the Eurovision Song Contest 2026!**")
    await channel.send("Let's start with Jury votes…")
    await asyncio.sleep(5)

    i = 0
    for filePath, currentVoter, nextVoter in generateSvgs(semi):
        if currentVoter == "results":
            await channel.send(f"**Here are the full results of the Televote!**\nThank you for your votes <:meowhuggies_left:780807943704412241>", file=discord.File(filePath, filename="viewvotes.png"))
        elif currentVoter == "standings":
            assert isinstance(nextVoter, list)
            if semi == "F":
                winner, winner_points = nextVoter[0]
                await channel.send(f"{flags[winner]} **{winner}** wins the Grand Final with **{winner_points}** points!")
            else:
                top10 = [c for c, _ in nextVoter[:10]]
                await channel.send("According to the server's vote, the following countries **qualified for the Grand Final:**\n" + "\n".join(f"{flags[c]} {c}" for c in top10))
        elif currentVoter != "public":
            await channel.send(f"Thank you **{currentVoter}** for your votes <:meowhuggies_left:780807943704412241>", file=discord.File(filePath, filename="viewvotes.png"))
        
            await asyncio.sleep(5)
            if nextVoter is not None:
                await channel.send(f"Our next voter is… {nextVoter}")
                await asyncio.sleep(15)
            elif currentVoter == "jurors":
                await channel.send("**And now it is time to see the results of the Televote** :eyes:")
                await asyncio.sleep(5)
                await channel.send("The points from the televote will be announced in the ascending order of the jury results")
                await asyncio.sleep(5)
                await channel.send("Here we go!")
        else:
            i += 1
            assert isinstance(nextVoter, tuple)
            country, points, _ = nextVoter

            total = len(songs)
            remaining = total - i

            if remaining == 0:
                await asyncio.sleep(30)

            await channel.send(f"**{points}** for **{country}** {flags[country]}", file=discord.File(filePath, filename="viewvotes.png"))

            await asyncio.sleep(4)

#MAIN ##########################################################################
def main():
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="T.", help_command=None, intents = intents)

    async def traitementRawReact(payload):
        assert bot.user is not None
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
    async def voteCommand(ctx, semi: str):
        if ctx.author.id == ADMIN_ID:
            if semi not in ("1", "2", "F"):
                await ctx.send("Invalid semi. Use 1, 2, or F.")
                return
            load_semi(semi)
            await startVote(ctx.channel)
    
    @bot.command(name = "start_jury")
    async def startJuryCommand(ctx, semi: str):
        if ctx.author.id == ADMIN_ID and msgVote[0]:
            if semi not in ("1", "2", "F"):
                await ctx.send("Invalid semi. Use 1, 2, or F.")
                return
            load_semi(semi)
            channelId = msgVote[2]
            messageId = msgVote[1]
            assert channelId is not None and messageId is not None
            channel = await bot.fetch_channel(channelId)
            assert isinstance(channel, (discord.TextChannel, discord.DMChannel, discord.Thread, discord.VoiceChannel))
            msg = await channel.fetch_message(messageId)
            
            await msg.edit(content = "__**Jury vote**__\nReact with 🗳️ to vote with a **full top 10** (if you vote again with 🗳️, only your latest top 10 counts as a jury vote)")
            await msg.add_reaction("🗳️")

    @bot.command(name = "count")
    async def countCommand(ctx, semi: str):
        global voting_closed
        if ctx.author.id == ADMIN_ID:
            if semi not in ("1", "2", "F"):
                await ctx.send("Invalid semi. Use 1, 2, or F.")
                return
            voting_closed = True
            load_semi(semi)
            countVotes()
            await ctx.message.add_reaction("🗳️")

            channel_id = msgVote[2]
            message_id = msgVote[0]
            if channel_id is not None and message_id is not None:
                channel = bot.get_channel(channel_id)
                if channel and isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.VoiceChannel)):
                    try:
                        msg = await channel.fetch_message(message_id)
                        await msg.edit(view=None)
                    except:
                        pass
    
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
    async def showResultsCommand(ctx, semi: str):
        if ctx.author.id == ADMIN_ID:
            if semi not in ("1", "2", "F"):
                await ctx.send("Invalid semi. Use 1, 2, or F.")
                return
            load_semi(semi)
            await showResults(ctx.channel, semi)

    bot.run(token)

main()
