import asyncio
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, Optional

import nextcord as discord
import requests
from arrow import utcnow
from nextcord.ext import commands, tasks

import constantes

from photo_contest import genVoteInfo
from photo_contest.photo_contest_data import Contest, Period, PublicVote, Schedule, Submission, make_contest


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
discord_team_role_id = 674583505446895616
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

def get_channel_and_thread(message: discord.Message) -> tuple[int, Optional[int]]:
    """Extract channel_id and thread_id from a message.
    
    Returns:
        tuple[int, Optional[int]]: (channel_id, thread_id)
    """
    if hasattr(message.channel, "parent") and message.channel.parent:  # pyright: ignore[reportAttributeAccessIssue]
        channel_id = message.channel.parent.id  # pyright: ignore[reportAttributeAccessIssue]
        thread_id = message.channel.id
    else:
        channel_id = message.channel.id
        thread_id = None
    return channel_id, thread_id


async def submit(contest: Contest, message: discord.Message) -> Contest:
    """Handles a submission from a user.
    
    Takes a message with an image attachment or a link, processes it, and records the submission.
    
    The process consists of downloading the image locally, uploading it to a designated channel for submissions, recovering the link to the uploaded image, and storing the submission in the contest data.
    
    Returns the new contest object.
    """
    
    # Check the channel and thread to see if they are valid for submissions
    channel_id, thread_id = get_channel_and_thread(message)
    
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
        delete_after=300,
        reference=discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        ),
    )
    
    # Get the current submission count to determine the index for this new submission
    current_count = contest.get_submission_count(channel_id, thread_id)
    
    # Resend the message in the submission channel/thread
    if thread_id is not None:
        submission_channel = message.guild.get_channel(thread_id)
    else:
        submission_channel = message.guild.get_channel(channel_id)
    assert submission_channel is not None, "Submission channel not found"
    assert isinstance(submission_channel, (discord.TextChannel, discord.Thread)), "Submission channel is not a TextChannel or Thread"

    message_resend = await submission_channel.send(
        content=f"Submission #{current_count + 1}",
        embed=discord.Embed().set_image(url=uploaded_message.jump_url)
    )
    
    # Save the submission in the contest
    submission = Submission(author_id=message.author.id, submission_time=round(utcnow().timestamp()), local_save_path=local_filename, discord_save_path=uploaded_message.jump_url)
    
    return contest.add_submission(submission, channel_id, message_resend.id, thread_id)


async def withdraw(contest: Contest, message: discord.Message, user: discord.Member | discord.User) -> Contest:
    """Handles withdrawal of a submission.
    
    Withdraws a submission when the message_resend gets a ❌ reaction from either
    the author or a user with discord_team_role.
    
    Returns the updated contest object.
    """
    
    # Check the channel and thread
    channel_id, thread_id = get_channel_and_thread(message)
    
    # Check if this channel/thread is valid for submissions
    if (channel_id, thread_id) not in contest.channel_threads_open_for_submissions:
        return contest
    
    # Find the competition
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        return contest
    
    _, competition = res
    
    # Check if this message is a submission message
    if message.id not in competition.msg_to_sub:
        return contest
    
    # Get the submission index
    submission_index = competition.msg_to_sub[message.id]
    submission = competition.competing_entries[submission_index]
    
    # Check if user is authorized to withdraw (author or discord team member)
    is_author = user.id == submission.author_id
    has_team_role = False
    if isinstance(user, discord.Member):
        has_team_role = any(role.id == discord_team_role_id for role in user.roles)
    
    if not (is_author or has_team_role):
        # User is not authorized to withdraw, remove their reaction
        try:
            await message.remove_reaction("❌", user)
        except (discord.NotFound, discord.Forbidden):
            pass
        return contest
    
    # Withdraw the submission
    contest = contest.withdraw_submission(channel_id, message.id, thread_id)
    
    # Update the message numbers for all subsequent submissions
    # Get the updated competition
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if res:
        _, updated_competition = res
        
        # For each submission with a higher index, update their message
        for msg_id, idx in updated_competition.msg_to_sub.items():
            if idx >= submission_index:  # All submissions that were after the withdrawn one
                try:
                    msg = await message.channel.fetch_message(msg_id)
                    await msg.edit(content=f"Submission #{idx + 1}")
                except discord.NotFound:
                    # Message was deleted, skip
                    pass
                except discord.Forbidden:
                    # No permission to edit, skip
                    pass
    
    # Delete the original submission message
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden):
        pass
    
    # Save the updated contest
    contest.save("photo_contest/contest2026.yaml")
    
    return contest


async def handle_public_vote(contest: Contest, message: discord.Message, user: discord.Member | discord.User, emoji: str) -> Contest:
    """Handle a public vote (0-3 points) during qualif or semis periods.
    
    Args:
        contest: The current contest
        message: The submission message that was reacted to
        user: The user who reacted
        emoji: The emoji name (should be 0️⃣, 1️⃣, 2️⃣, or 3️⃣)
    
    Returns:
        Updated contest object
    """
    # Map emoji to points
    emoji_to_points: dict[str, Literal[0] | Literal[1] | Literal[2] | Literal[3]] = {
        "0️⃣": 0,
        "1️⃣": 1,
        "2️⃣": 2,
        "3️⃣": 3,
    }
    
    if emoji not in emoji_to_points:
        return contest
    
    points = emoji_to_points[emoji]
    
    # Get channel and thread
    channel_id, thread_id = get_channel_and_thread(message)
    
    # Find the competition
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        return contest
    
    _, competition = res
    
    # Check if this message is a submission
    if message.id not in competition.msg_to_sub:
        return contest
    
    # Get the submission
    submission_idx = competition.msg_to_sub[message.id]
    submission = competition.competing_entries[submission_idx]
    
    # Create the public vote
    vote = PublicVote(
        voter_id=user.id,
        nb_points=points,
        submission=submission
    )
    
    try:
        # Save the vote
        contest = contest.save_public_vote(channel_id, thread_id, vote)
        contest.save("photo_contest/contest2026.yaml")
        
        # Remove all number reactions from this user on this message to keep votes invisible
        for emoji_str in ["0️⃣", "1️⃣", "2️⃣", "3️⃣"]:
            try:
                await message.remove_reaction(emoji_str, user)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
    except ValueError as e:
        # User tried to vote for themselves, remove their reaction
        try:
            await message.remove_reaction(emoji, user)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    
    return contest


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
    
    # Post submissions in their respective threads with voting reactions
    qualif_competitions = contest.qualif_competitions
    for comp in qualif_competitions:
        thread = bot.get_channel(comp.thread_id)
        if not thread or not isinstance(thread, discord.Thread):
            print(f"Warning: Could not find thread {comp.thread_id}")
            continue
        
        for i, submission in enumerate(comp.competing_entries):
            msg = await thread.send(
                content=f"Submission #{i+1}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )
            # Add voting reactions: 0, 1, 2, 3 points
            await msg.add_reaction("0️⃣")
            await msg.add_reaction("1️⃣")
            await msg.add_reaction("2️⃣")
            await msg.add_reaction("3️⃣")
            
            # Update the contest with the message_id mapping
            contest = contest.set_message_id(comp.channel_id, comp.thread_id, i, msg.id)
    
    # Save the updated contest with message mappings
    contest.save("photo_contest/contest2026.yaml")


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
            msg = await channel.send(
                content=f"Submission #{i+1}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )
            # Add voting reactions: 0, 1, 2, 3 points
            await msg.add_reaction("0️⃣")
            await msg.add_reaction("1️⃣")
            await msg.add_reaction("2️⃣")
            await msg.add_reaction("3️⃣")
            
            # Update the contest with the message_id mapping
            contest = contest.set_message_id(comp.channel_id, comp.thread_id, i, msg.id)
    
    # Save the updated contest with message mappings
    contest.save("photo_contest/contest2026.yaml")


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
        global contest
        
        # Handle reactions based on current contest period
        if current_period == ContestPeriod.IDLE or current_period is None:
            return  # No active period, ignore reactions
        
        # Ignore bot reactions
        assert bot.user is not None, "Bot user is None"
        if payload.user_id == bot.user.id:
            return
        
        # Get the user who reacted
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        
        user = guild.get_member(payload.user_id)
        if not user:
            return
        
        # Get the channel and message
        channel = bot.get_channel(payload.channel_id)
        if not channel:
            return
        
        try:
            assert isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread), "Channel is not a TextChannel or Thread"
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        
        # Handle different reactions based on current period
        if current_period == ContestPeriod.SUBMISSION:
            # Check if this is a ❌ reaction for withdrawal
            if payload.emoji.name == "❌":
                contest = await withdraw(contest, message, user)
        
        elif current_period == ContestPeriod.QUALIF or current_period == ContestPeriod.SEMIS:
            # Handle public votes (0-3 points)
            if payload.emoji.name in ["0️⃣", "1️⃣", "2️⃣", "3️⃣"]:
                contest = await handle_public_vote(contest, message, user, payload.emoji.name)
            # TODO: Implement jury vote handler
            # elif current_period == ContestPeriod.QUALIF:
            #     await cast_vote_jury(message, user, payload.emoji)
        
        elif current_period == ContestPeriod.FINAL:
            # TODO: Implement final vote handler
            pass
        # elif current_period == ContestPeriod.FINAL:
        #     await cast_vote_gf(message, user, payload.emoji)

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