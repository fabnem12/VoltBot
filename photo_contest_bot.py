import asyncio
import json
import os
from datetime import datetime, timedelta
from enum import Enum
from random import shuffle
from typing import Dict, List, Optional, Tuple

import nextcord as discord
import requests
from arrow import utcnow
from nextcord.ext import commands, tasks

import constantes

from photo_contest import genVoteInfo
from photo_contest.photo_contest_data import Contest, Period, Schedule, Submission, make_contest


class ContestPeriod(Enum):
    IDLE = "idle"
    SUBMISSION = "submission"
    QUALIF = "qualif"
    SEMIS = "semis"
    FINAL = "final"


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

# Global state for contest period
current_period = None  # Will be set to ContestPeriod enum value

if os.path.exists("photo_contest/contest2026.yaml"):
    contest = Contest.from_file("photo_contest/contest2026.yaml")
    print(contest)
else:
    start_time = datetime(2026, 11, 16, 19, 0)

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
    contest.save("photo_contest/contest2026.yaml")


# Functions for handling the contest ##########################################

async def submit(contest: Contest, message: discord.Message) -> Contest:
    """Handles a submission from a user.
    
    Takes a message with an image attachment or a link, processes it, and records the submission.
    
    The process consists of downloading the image locally, uploading it to a designated channel for submissions, recovering the link to the uploaded image, and storing the submission in the contest data.
    
    Returns the new contest object.
    """
    
    # Check the channel and thread to see if they are valid for submissions
    if hasattr(message.channel, "parent") and message.channel.parent: # pyright: ignore[reportAttributeAccessIssue]
        channel_id = message.channel.parent.id # pyright: ignore[reportAttributeAccessIssue]
        thread_id = message.channel.id
    else:
        channel_id = message.channel.id
        thread_id = None
    
    if (channel_id, thread_id) not in contest.channel_threads_open_for_submissions:
        return contest
    
    # Define allowed static image formats
    allowed_extensions = ['.jpg', '.jpeg', '.png', '.webp']
    
    if len(message.attachments) == 0:
        # check for a link in the message content
        words = message.content.split()
        url = None
        for word in words:
            if word.startswith("http://") or word.startswith("https://"):
                url = word
                break
        if url is None:
            ref = discord.MessageReference(
                message_id=message.id, channel_id=message.channel.id
            )
            await message.channel.send(
                f"No attachment or valid link found in your message. Please attach an image or provide a valid URL.",
                delete_after=3600,
                reference=ref,
            )
            return contest
        else:
            submission_url = url
            # Try to extract extension from URL
            file_extension = os.path.splitext(url.split('?')[0])[1].lower()
            if not file_extension or file_extension not in allowed_extensions:
                ref = discord.MessageReference(
                    message_id=message.id, channel_id=message.channel.id
                )
                await message.channel.send(
                    f"Invalid image format. Only static images in JPG, JPEG, PNG, or WebP formats are accepted. Animated images (GIF) are not allowed.",
                    delete_after=3600,
                    reference=ref,
                )
                return contest
    else:
        attachment = message.attachments[0]
        submission_url = attachment.url
        # Get extension from attachment filename
        file_extension = os.path.splitext(attachment.filename)[1].lower()
        if not file_extension or file_extension not in allowed_extensions:
            ref = discord.MessageReference(
                message_id=message.id, channel_id=message.channel.id
            )
            await message.channel.send(
                f"Invalid image format. Only static images in JPG, JPEG, PNG, or WebP formats are accepted. Animated images (GIF) are not allowed.",
                delete_after=3600,
                reference=ref,
            )
            return contest
    
    # Download the image locally
    response = requests.get(submission_url)
    if response.status_code != 200:
        ref = discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        )
        await message.channel.send(
            f"Failed to download the image from the provided URL. Please check the link and try again.",
            delete_after=3600,
            reference=ref,
        )
        return contest

    local_filename = f"photo_contest/pictures/{message.id}{file_extension}"
    with open(local_filename, "wb") as f:
        f.write(response.content)
    
    # Upload the image to the designated submissions channel
    assert message.guild is not None, "Message guild is None"
    save_channel = message.guild.get_channel(save_channel_id)
    assert save_channel is not None, "Save channel not found"
    assert isinstance(save_channel, discord.TextChannel), "Save channel is not a TextChannel"
    
    with open(local_filename, "rb") as f:
        uploaded_message = await save_channel.send(
            content=f"Submission {message.id}{file_extension}",
            file=discord.File(f, filename=f"{message.id}{file_extension}"),
        )
        assert isinstance(uploaded_message, discord.Message), "Failed to upload submission"
    
    await message.channel.send(
        f"Your submission has been received successfully!",
        delete_after=3600,
        reference=discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        ),
    )
    
    # Save the submission in the contest
    submission = Submission(author_id=message.author.id, submission_time=round(utcnow().timestamp()), local_save_path=local_filename, discord_save_path=uploaded_message.jump_url)
    
    return contest.add_submission(submission, channel_id, thread_id)


async def setup_qualif_period(bot):
    """Set up qualification threads and post submissions."""
    global contest
    
    # Get the number of threads needed per category
    thread_counts = contest.count_qualifs()
    
    # Get all submission competitions
    submission_competitions = contest.submission_competitions
    
    # Create threads for each category
    all_thread_ids = []
    for comp, thread_count in zip(submission_competitions, thread_counts):
        channel = bot.get_channel(comp.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            print(f"Warning: Could not find channel {comp.channel_id}")
            continue
        
        thread_ids = []
        for i in range(thread_count):
            thread = await channel.create_thread(
                name=f"Qualification Thread {i+1}",
                auto_archive_duration=10080  # 7 days
            )
            thread_ids.append(thread.id)
        all_thread_ids.append(thread_ids)
    
    # Update contest with qualifications
    contest = contest.make_qualifs(all_thread_ids)
    contest.save("photo_contest/contest2026.yaml")
    
    # Post submissions in their respective threads
    qualif_competitions = contest.qualif_competitions
    for comp in qualif_competitions:
        thread = bot.get_channel(comp.thread_id)
        if not thread or not isinstance(thread, discord.Thread):
            print(f"Warning: Could not find thread {comp.thread_id}")
            continue
        
        for i, submission in enumerate(comp.competing_entries):
            await thread.send(
                content=f"Submission #{i+1}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )


async def setup_semis_period(bot):
    """Set up semi-final competitions and post qualified submissions."""
    global contest
    
    # Solve qualifications to determine semi-finalists
    contest = contest.solve_qualifs()
    contest.save("photo_contest/contest2026.yaml")
    
    # Post submissions in semi-final channels
    semis_competitions = contest.semis_competitions
    for comp in semis_competitions:
        channel = bot.get_channel(comp.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            print(f"Warning: Could not find channel {comp.channel_id}")
            continue
        
        await channel.send("🎉 **Semi-Finals have begun!** 🎉")
        for i, submission in enumerate(comp.competing_entries):
            await channel.send(
                content=f"Submission #{i+1}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )


async def setup_final_period(bot):
    """Set up final competitions and post finalists."""
    global contest
    
    # Solve semi-finals to determine finalists
    contest = contest.solve_semis()
    contest.save("photo_contest/contest2026.yaml")
    
    # Post submissions in final channels
    final_competitions = contest.final_competitions
    for comp in final_competitions:
        channel = bot.get_channel(comp.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            print(f"Warning: Could not find channel {comp.channel_id}")
            continue
        
        await channel.send("🏆 **GRAND FINAL!** 🏆")
        for i, submission in enumerate(comp.competing_entries):
            await channel.send(
                content=f"Submission #{i+1}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )


def main():
    intents = discord.Intents.all()
    bot = commands.Bot(
        command_prefix=constantes.prefixVolt, help_command=None, intents=intents
    )

    async def planner(now, bot):
        """Determines the current state of the contest based on the schedule and triggers period transitions."""
        global contest, current_period
        current_timestamp = now.timestamp()
        
        # Determine which period we're in
        if contest.schedule.submission_period.start <= current_timestamp < contest.schedule.submission_period.end:
            period = ContestPeriod.SUBMISSION
        elif contest.schedule.qualif_period.start <= current_timestamp < contest.schedule.qualif_period.end:
            period = ContestPeriod.QUALIF
        elif contest.schedule.semis_period.start <= current_timestamp < contest.schedule.semis_period.end:
            period = ContestPeriod.SEMIS
        elif contest.schedule.final_period.start <= current_timestamp < contest.schedule.final_period.end:
            period = ContestPeriod.FINAL
        else:
            period = ContestPeriod.IDLE
        
        # Handle period transitions
        if current_period != period:
            old_period = current_period
            current_period = period
            
            # Trigger period-specific setup on transition
            if period == ContestPeriod.QUALIF and old_period == ContestPeriod.SUBMISSION:
                await setup_qualif_period(bot)
            elif period == ContestPeriod.SEMIS and old_period == ContestPeriod.QUALIF:
                await setup_semis_period(bot)
            elif period == ContestPeriod.FINAL and old_period == ContestPeriod.SEMIS:
                await setup_final_period(bot)
        
        return period

    @tasks.loop(minutes=1.0)
    async def autoplanner():
        now = utcnow().to("Europe/Brussels")
        await planner(now, bot)

    @bot.event
    async def on_ready():
        autoplanner.start()

    @bot.event
    async def on_message(message):
        global contest
        await bot.process_commands(message)
        
        if message.author.bot:
            return
        
        # Handle submissions only during submission period
        if current_period == ContestPeriod.SUBMISSION:
            contest = await submit(contest, message)
            contest.save("photo_contest/contest2026.yaml")

    @bot.event
    async def on_raw_reaction_add(payload):
        # Handle reactions based on current contest period
        if current_period == ContestPeriod.IDLE or current_period is None:
            return  # No active period, ignore reactions
        
        # TODO: Implement traitementRawReact to extract payload information
        # traitement = await traitementRawReact(payload)
        # if not traitement:
        #     return
        # 
        # messageId = traitement["messageId"]
        # user = traitement["user"]
        # if user.bot:
        #     return
        # 
        # guild = traitement["guild"]
        # emojiHash = traitement["emojiHash"]
        # channel = traitement["channel"]
        # 
        # # Handle reactions based on period
        # if current_period == ContestPeriod.SUBMISSION:
        #     await withdraw_submission(messageId, user, guild, emojiHash, channel)
        # elif current_period == ContestPeriod.QUALIF:
        #     await cast_vote_submission_period(messageId, user, guild, emojiHash, channel)
        #     await cast_vote_jury(messageId, user, guild, emojiHash, channel)
        # elif current_period == ContestPeriod.SEMIS:
        #     await cast_vote_semi(messageId, user, guild, emojiHash, channel)
        # elif current_period == ContestPeriod.FINAL:
        #     await cast_vote_gf(messageId, user, guild, emojiHash, channel)
        pass

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