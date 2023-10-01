import asyncio
import nextcord as discord
from nextcord.ext import commands, tasks

import constantes
import os
import json
import requests
from arrow import utcnow
from random import shuffle
from typing import Dict, List, Tuple, Optional

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
submissions: Dict[int, Dict[int, Dict[int, Submission]]] = dict() #{channel_id: {thread_id: submissions}}
votes1: Dict[int, List[Vote]] = dict() #{thread_id: list of votes}
votes2: Dict[int, Dict[int, List[str]]] = dict() #{channel_id: {voter_id}}
contestState: List[int] = [0] #0 for inactive, 1 for submission period, 2-3-4-5 for semi-finals (depending on the order of channels), 6 for the first final, 7 for the grand final
contestData = (submissions, votes1, votes2, contestState)

if "photo_contest_data.json" in os.listdir():
    with open("photo_contest_data.json") as f:
        contestData = json.load(f)

    submissions, votes1, votes2, contestState = contestData
    submissions = {int(i): {int(j): v for j, v in entries.items()} for i, entries in submissions.items()}

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
    if now.minute == 0 and now.hour == 8 and (now.day, now.month) == (26, 9):
        await start_submissions(bot)
    if now.minute == 0 and now.hour == 0 and (now.day, now.month) == (28, 9):
        await end_submissions(bot)

async def resendFile(url: str, saveChannelId: int):
    """
    Sends submissions in a safe channel to then keep a constant url.

    Args:
    - the url of the submission
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
        for threadId, subs in channelInfo.items():

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
            await thread.send(f"Here are the photos selected for the semi-finals in this thread")

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
        await interaction.message.delete()
        await self.message.delete()

async def submit(ctx, url: Optional[str]):
    userId = ctx.author.id
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
                    msgConfirm = await ctx.send("Are you sure that:\n- you took this photo yourself?\n- the photo somewhat fits the channel and the geographic area of the thread?", view = ButtonConfirm(url, userId, utcnow().timestamp(), ctx.message), reference = ref)
    return

    if ctx.channel.id in submissions:
        if CONTEST_STATE[0]:
            if url:
                human = getHuman(ctx.author)
                languageChannel = getLanguageChannel(ctx.channel)
                if languageChannel.nbProposalsPerson(human) == 3:
                    await ctx.send("Sorry, you already submitted 3 photos in this thread, you can't submit more.\nYou can withdraw one of your previous submissions by adding a reaction ❌ to it.", reference = ref)
                    return


                msgConfirm = await ctx.send("Are you sure that:\n- you took this photo yourself?\n- the photo somewhat fits the channel and the geographic area of the thread?\nIf yes, you can confirm the submission with <:eurolike:759798224764141628>. If not, react with ❌", reference = ref)
                try:
                    msg2submission[msgConfirm.id] = (ctx.message.created_at, ctx.message.id, ctx.author.id, await resendFile(url, 1157987716919734293), 1)
                except Exception as e:
                    await msgConfirm.edit(content = "I'm sorry, it seems that this file is too big, I can't handle it :sweat_smile:")
                    await (await dmChannelUser(await bot.fetch_user(ADMIN_ID))).send(str(e))
                else:
                    await msgConfirm.add_reaction("eurolike:759798224764141628")
                    await msgConfirm.add_reaction("❌")
                    save()
            else:
                await ctx.send("You have to attach a photo to make a submission. You can check <#889538982931755088> to see how to do it")
        else:
            await ctx.send("Sorry, the submission period hasn't started or is over…")
    else:
        await ctx.send("Submissions for the photo contest aren't allowed in this channel…")

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

    @bot.event
    async def on_raw_reaction_add(payload):
        traitement = await traitementRawReact(payload)
        if traitement:
            messageId = traitement["messageId"]
            user = traitement["user"]
            guild = traitement["guild"]
            emojiHash = traitement["emojiHash"]
            channel = traitement["channel"]
    
    @bot.command(name = "setup")
    async def command_setup(ctx, *channels: discord.TextChannel):
        if ctx.author.id == organizerId:
            await setup(ctx, *channels)
    
    @bot.command(name = "submit")
    async def command_submit(ctx, url: Optional[str]):
        await submit(ctx, url)

    return bot, constantes.TOKENVOLT

if __name__ == "__main__": #pour lancer le bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()