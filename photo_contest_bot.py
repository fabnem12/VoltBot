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
from photo_contest.photo_contest_data import CompetitionInfo, Contest, Period, Schedule, Submission, make_contest

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


async def notify_organizer_dm_failed(user: discord.Member | discord.User, message: discord.Message, reason: str = "jury voting"):
    """Notify the organizer when DMs fail to be sent to a user.
    
    Args:
        user: The user who couldn't receive DMs
        message: The original message context
        reason: Description of what the DM was for
    """
    try:
        guild = message.guild
        if guild:
            save_channel = guild.get_channel(save_channel_id)
            if save_channel and isinstance(save_channel, discord.TextChannel):
                await save_channel.send(
                    f"<@{organizer_id}> User {user.mention} ({user.id}) could not receive {reason} DMs. They have DMs disabled."
                )
    except:
        pass


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


class JuryConfirmView(discord.ui.View):
    """Confirmation view for jury voting."""
    
    def __init__(self, ranking: list[Submission], user_id: int, channel_id: int, thread_id: Optional[int], contest: Contest):
        super().__init__(timeout=600)  # 10 minutes timeout
        self.ranking = ranking
        self.user_id = user_id
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.contest = contest
    
    @discord.ui.button(label="Confirm Vote", style=discord.ButtonStyle.success)
    async def confirm_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This vote is not yours!", ephemeral=True)
            return
        
        # Save the vote
        global contest
        try:
            contest = contest.save_jury_vote(self.channel_id, self.thread_id, self.user_id, self.ranking)
            contest.save("photo_contest/contest2026.yaml")
            
            await interaction.response.edit_message(
                content="✅ Your vote has been saved successfully!",
                view=None
            )
        except ValueError as e:
            await interaction.response.edit_message(
                content=f"❌ Error saving vote: {e}",
                view=None
            )
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This vote is not yours!", ephemeral=True)
            return
        
        await interaction.response.edit_message(
            content="❌ Vote cancelled.",
            view=None
        )


class JuryVotingView(discord.ui.View):
    """Interactive view for jury voting with buttons for each submission."""
    
    def __init__(self, submissions: list[Submission], user_id: int, channel_id: int, thread_id: Optional[int], contest: Contest):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.submissions = submissions
        self.user_id = user_id
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.contest = contest
        self.ranking: list[Submission] = []
        
        # Create buttons for each submission
        for i in range(len(submissions)):
            button = discord.ui.Button(
                label=f"Submission #{i+1}",
                custom_id=f"vote_{i}",
                style=discord.ButtonStyle.primary
            )
            button.callback = self.make_callback(i, button)
            self.add_item(button)
    
    def make_callback(self, submission_index: int, button: discord.ui.Button):
        """Create a callback function for a specific submission button."""
        async def callback(interaction: discord.Interaction):
            if not interaction.user:
                return
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This vote is not yours!", ephemeral=True)
                return
            
            submission = self.submissions[submission_index]
            self.ranking.append(submission)
            
            # Disable the button that was just clicked
            button.disabled = True
            
            # Update the message to show current ranking
            ranking_text = "\n".join(
                f"#{i+1} Submission #{self.submissions.index(sub)+1}"
                for i, sub in enumerate(self.ranking)
            )
            
            if len(self.ranking) < 10:
                await interaction.response.edit_message(
                    content=f"**Your current ranking ({len(self.ranking)}/10):**\n{ranking_text}\n\nClick buttons to continue building your top 10.",
                    view=self
                )
            else:
                # Ranking is complete, show confirmation
                confirm_view = JuryConfirmView(self.ranking, self.user_id, self.channel_id, self.thread_id, self.contest)
                await interaction.response.edit_message(
                    content=f"**Your final ranking:**\n{ranking_text}\n\nPlease confirm your vote.",
                    view=confirm_view
                )
        
        return callback


async def handle_jury_vote_request(contest: Contest, message: discord.Message, user: discord.Member | discord.User, bot: discord.Client):
    """Handle a jury vote request (🗳️ reaction) by sending voting UI in DM.
    
    Args:
        contest: The current contest
        message: The voting message that was reacted to
        user: The user who wants to vote
        bot: The bot client
    """
    # Get channel and thread
    channel_id, thread_id = get_channel_and_thread(message)
    
    # Find the competition
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        return
    
    _, competition = res
    
    # Filter out submissions by the same user and build mapping in a single pass
    votable_submissions = []
    submission_numbers = {}
    for i, sub in enumerate(competition.competing_entries):
        if sub.author_id != user.id:
            votable_submissions.append(sub)
            submission_numbers[sub] = i + 1
    
    if len(votable_submissions) < 10:
        try:
            await user.send(
                f"❌ Not enough submissions to vote on. You need at least 10 submissions (excluding your own) to cast a jury vote."
            )
        except discord.Forbidden:
            await notify_organizer_dm_failed(user, message, "jury voting notification")
        return
    
    try:
        # Send all submission images in DM
        await user.send("📸 **Here are all the submissions for your review:**")
        
        for submission in votable_submissions:
            await user.send(
                content=f"Submission #{submission_numbers[submission]}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )
        
        # Send the voting interface
        view = JuryVotingView(votable_submissions, user.id, channel_id, thread_id, contest)
        await user.send(
            content="**Click the buttons below to build your top 10 ranking.**\nSelect submissions in order from your most preferred to your 10th preferred.",
            view=view
        )
        
    except discord.Forbidden:
        # User has DMs disabled
        try:
            await message.channel.send(
                f"{user.mention}, I couldn't send you a DM. Please enable DMs from server members to vote.",
                delete_after=30
            )
        except:
            pass
        
        await notify_organizer_dm_failed(user, message, "jury voting")


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
    
    try:
        # Save the vote
        contest = contest.save_public_vote(channel_id, thread_id, user.id, points, submission)
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


async def handle_commentary_request(contest: Contest, message: discord.Message, user: discord.Member | discord.User):
    """Handle a commentary request (💬 reaction) by opening a modal for text input.
    
    Args:
        contest: The current contest
        message: The photo message that was reacted to
        user: The user who wants to comment
    """
    # Get channel and thread
    channel_id, thread_id = get_channel_and_thread(message)
    
    # Find the competition
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        return
    
    _, competition = res
    
    # Check if this message is a photo submission
    if message.id not in competition.msg_to_sub:
        return
    
    submission_index = competition.msg_to_sub[message.id]
    submission = competition.competing_entries[submission_index]
    
    # Check if user is trying to comment on their own photo
    if user.id == submission.author_id:
        try:
            await user.send("❌ You cannot comment on your own photo.")
        except discord.Forbidden:
            pass
        return
    
    # Create and send a modal for commentary input
    class CommentaryModal(discord.ui.Modal):
        def __init__(self):
            super().__init__(
                title=f"Add Commentary for Photo #{submission_index + 1}",
                timeout=300
            )
            
            self.commentary_input = discord.ui.TextInput(
                label="Your Commentary",
                style=discord.TextInputStyle.paragraph,
                placeholder="Share your thoughts on composition, lighting, technical quality, artistic merit...",
                required=True,
                min_length=10,
                max_length=1000
            )
            self.add_item(self.commentary_input)
        
        async def callback(self, interaction: discord.Interaction):
            global contest
            
            commentary_text = self.commentary_input.value
            if not commentary_text:
                await interaction.response.send_message(
                    "❌ Commentary cannot be empty.",
                    ephemeral=True
                )
                return
            
            assert interaction.user is not None, "Interaction user is None"
            
            # Try to add the commentary (includes Mistral validation)
            try:
                contest, updated_comp = contest.add_commentary(
                    channel_id=channel_id,
                    thread_id=thread_id,
                    submission=submission,
                    author_id=interaction.user.id,
                    text=commentary_text
                )
                contest.save("photo_contest/contest2026.yaml")
                
                # Update the summary for this specific submission
                assert message.guild is not None, "Message guild is None"
                await update_commentary_summary(updated_comp, submission, message.guild)
                
                await interaction.response.send_message(
                    "✅ Your commentary has been recorded! Thank you for your feedback.",
                    ephemeral=True
                )
                
            except ValueError as e:
                # Validation failed
                await interaction.response.send_message(
                    f"❌ {str(e)}",
                    ephemeral=True
                )
    
    # Since we can't directly trigger a modal from a reaction, we need to use an interaction
    # Create a view with a button that opens the modal
    class CommentaryButton(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
        
        @discord.ui.button(label="Add Commentary", style=discord.ButtonStyle.primary, emoji="💬")
        async def commentary_button(self, button: discord.ui.Button, interaction: discord.Interaction):
            await interaction.response.send_modal(CommentaryModal())
    
    # Send an ephemeral message with the button to open the modal
    try:
        # We need to create an interaction to send ephemeral messages
        # Since we're coming from a reaction, send a DM instead
        view = CommentaryButton()
        await user.send(
            f"💬 **Add a commentary for Photo #{submission_index + 1}**\n"
            f"Click the button below to open the commentary form.",
            view=view
        )
    except discord.Forbidden:
        # User has DMs disabled, try to send in channel
        try:
            view = CommentaryButton()
            msg = await message.channel.send(
                f"{user.mention}, click the button below to add your commentary:",
                view=view,
                delete_after=60
            )
        except:
            pass


async def update_commentary_summary(competition: CompetitionInfo, submission: Submission, guild: discord.Guild):
    """Update the commentary summary message for a specific submission.
    
    Args:
        competition: The competition containing the submission
        submission: The submission whose summary to update
        guild: The Discord guild
    """
    # Get the summary for this submission
    summaries = competition.get_all_commentaries_summaries()
    summaries_dict = {sub: summary for sub, summary in summaries}
    summary_text = summaries_dict.get(submission, "")
    
    # Get the channel
    channel = guild.get_channel(competition.channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return
    
    # Get the summary message ID for this submission
    summary_msg_id = competition.submission_to_summary_msg.get(submission)
    if summary_msg_id is None:
        return
    
    # Update the summary message
    try:
        summary_msg = await channel.fetch_message(summary_msg_id)
        
        if summary_text:
            content = (
                f"📝 **Commentary Summary**\n"
                f"{summary_text}\n\n"
                f"💬 React above to add your commentary"
            )
        else:
            content = (
                f"📝 **Commentary Summary**\n"
                f"_No commentaries yet_\n\n"
                f"💬 React above to add your commentary"
            )
        
        await summary_msg.edit(content=content)
    except (discord.NotFound, discord.Forbidden):
        pass


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
        
        # Send voting instruction message
        vote_msg = await thread.send(
            "🗳️ **Jury Voting is now open!**\n"
            "React with 🗳️ to this message to cast your jury vote (top 10 ranking)."
        )
        await vote_msg.add_reaction("🗳️")
    
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
            # Add commentary reaction
            await msg.add_reaction("💬")
            
            # Post commentary summary message below the photo
            summary_msg = await channel.send(
                "📝 **Commentary Summary**\n"
                "_No commentaries yet_\n\n"
                "💬 React above to add your commentary"
            )
            
            # Update the contest with the message_id mapping
            contest = contest.set_message_id(comp.channel_id, comp.thread_id, i, msg.id)
            
            # Update the contest with the summary message ID
            contest = contest.set_summary_message_id(comp.channel_id, comp.thread_id, submission, summary_msg.id)
        
        # Send voting instruction message
        vote_msg = await channel.send(
            "🗳️ **Jury Voting is now open!**\n"
            "React with 🗳️ to this message to cast your jury vote (top 10 ranking)."
        )
        await vote_msg.add_reaction("🗳️")
    
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
            # Handle jury vote requests
            elif payload.emoji.name == "🗳️":
                await handle_jury_vote_request(contest, message, user, bot)
            # Handle commentary requests (semis only)
            elif payload.emoji.name == "💬" and current_period == ContestPeriod.SEMIS:
                await handle_commentary_request(contest, message, user)
        
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