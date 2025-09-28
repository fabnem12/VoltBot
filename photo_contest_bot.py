import asyncio
import json
import os
from datetime import datetime, timedelta
from random import shuffle
from typing import Dict, List, Optional, Tuple

import nextcord as discord
import requests
from arrow import utcnow
from nextcord.ext import commands, tasks

import constantes

from photo_contest import genVoteInfo
from photo_contest.photo_contest_data import Contest, Period, Schedule, make_contest


# temporary way to update the bot
def stockePID():
    import pickle
    from os.path import abspath, dirname, join

    fichierPID = join(dirname(abspath(__file__)), "fichierPID.p")
    if not os.path.exists(fichierPID):
        pickle.dump(set(), open(fichierPID, "wb"))

    pids = pickle.load(open(fichierPID, "rb"))
    pids.add(os.getpid())

    pickle.dump(pids, open(fichierPID, "wb"))


stockePID()

# constants
voltServer = 567021913210355745
organizer_id = 619574125622722560
save_channel_id = 1421893549573537842

if os.path.exists("photo_contest/contest2025.yaml"):
    contest = Contest.from_file("photo_contest/contest2025.yaml")
    print(contest)
else:
    start_time = datetime(2025, 10, 15, 8, 0)

    submission_period = Period(
        start=start_time.timestamp(),
        end=(start_time + timedelta(days=7, hours=12)).timestamp(),
    )
    qualif_period = Period(
        start=(start_time + timedelta(days=8)).timestamp(),
        end=(start_time + timedelta(days=11, hours=12)).timestamp(),
    )
    semis_period = Period(
        start=(start_time + timedelta(days=12)).timestamp(),
        end=(start_time + timedelta(days=15, hours=12)).timestamp(),
    )
    final_period = Period(
        start=(start_time + timedelta(days=16)).timestamp(),
        end=(start_time + timedelta(days=20, hours=12)).timestamp(),
    )

    schedule = Schedule(
        submission_period=submission_period,
        qualif_period=qualif_period,
        semis_period=semis_period,
        final_period=final_period,
    )

    contest = make_contest(
        [
            1290060315878363156,
            1290060376461017160,
            1290060435944378388,
            1290061801974661251,
        ],
        schedule,
    )
    contest.save("photo_contest/contest2025.yaml")


def main():
    intents = discord.Intents.all()
    bot = commands.Bot(
        command_prefix=constantes.prefixVolt, help_command=None, intents=intents
    )

    @tasks.loop(minutes=1.0)
    async def autoplanner():
        now = utcnow().to("Europe/Brussels")
        # await planner(now, bot)

    @bot.event
    async def on_ready():
        autoplanner.start()

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
        return

        if message.author.bot:
            return

        if (
            hasattr(message.channel, "parent")
            and message.channel.parent
            and message.channel.parent.id in submissions
        ):
            if len(message.attachments) == 0:
                ref = discord.MessageReference(
                    message_id=message.id, channel_id=message.channel.id
                )
                await message.channel.send(
                    f"This doesn't count as a valid submission, please use the `{constantes.prefixVolt}submit` command as explained in <#1288568050810880001>. So I'm deleting your message.",
                    delete_after=3600,
                    reference=ref,
                )
                await message.delete()
            elif "submit" not in message.content:
                pass
                # await submit(message, None)

    @bot.event
    async def on_raw_reaction_add(payload):
        return
        traitement = await traitementRawReact(payload)
        if traitement:
            messageId = traitement["messageId"]
            user = traitement["user"]
            if user.bot:
                return  # no need to go further

            guild = traitement["guild"]
            emojiHash = traitement["emojiHash"]
            channel = traitement["channel"]

            await withdraw_submission(messageId, user, guild, emojiHash, channel)
            await cast_vote_submission_period(
                messageId, user, guild, emojiHash, channel
            )
            await cast_vote_jury(messageId, user, guild, emojiHash, channel)
            await cast_vote_semi(messageId, user, guild, emojiHash, channel)
            await cast_vote_gf(messageId, user, guild, emojiHash, channel)

    @bot.command(name="setup")
    async def command_setup(ctx, *channels: discord.TextChannel):
        if ctx.author.id == organizer_id:
            pass  # await setup(*channels)

    return bot, constantes.TOKENVOLT


if __name__ == "__main__":  # to run the bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()
