import asyncio
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Literal, Optional

import logging
import nextcord as discord
import requests
from arrow import utcnow
from nextcord.ext import commands, tasks

import constantes

from photo_contest.photo_contest_data import JuryVote, Contest, Period, Schedule, Submission, make_contest
from photo_contest.board_gen import (
    gen_competition_board,
    gen_semifinals_boards,
    gen_final_results_board,
    gen_live_final_reveal,
)

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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('photo_contest_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('photo_contest_bot')

# constants
TEST_MODE = True  # Set to False for production
voltServer = 567021913210355745
organizer_id = 619574125622722560
discord_team_role_id = 674583505446895616
save_channel_id = 1421893549573537842
announcement_channel_id = 1474888237565743385
final_channel_id = announcement_channel_id

# Ensure required directories exist
os.makedirs("photo_contest/pictures", exist_ok=True)
os.makedirs("photo_contest/generated_tables", exist_ok=True)
os.makedirs("photo_contest/assets", exist_ok=True)

# Global state for contest period
current_period = None  # Will be set to ContestPeriod enum value

if os.path.exists("photo_contest/contest2026.yaml"):
    contest = Contest.from_file("photo_contest/contest2026.yaml")
    print(contest)
else:
    # Regular contest schedule - 7 days per period
    start_time = datetime(2026, 3, 1, 19, 0)  # Start March 1st, 2026 at 7pm

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

async def build_id2name_mapping(bot: discord.Client, contest: Contest, include_voters: bool = False) -> Dict[int, str]:
    """Build a mapping from user IDs to display names for all participants.
    
    Args:
        bot: Discord client
        contest: Contest object with submissions
        include_voters: Whether to include jury voters in the mapping
        
    Returns:
        Dict mapping user IDs to display names
    """
    guild = bot.get_guild(voltServer)
    if not guild:
        return {}
    
    id2name: Dict[int, str] = {}
    
    # Add submission authors
    for submission in contest.submissions:
        if submission.author_id not in id2name:
            try:
                member = await guild.fetch_member(submission.author_id)
                if member:
                    id2name[submission.author_id] = member.display_name
                else:
                    id2name[submission.author_id] = f"User <@{submission.author_id}>"
            except:
                id2name[submission.author_id] = f"User <@{submission.author_id}>"
    
    # Add voter names if requested
    if include_voters:
        for comp in contest.competitions:
            for voter_id in comp.votes_jury.keys():
                if voter_id not in id2name:
                    try:
                        member = await guild.fetch_member(voter_id)
                        id2name[voter_id] = member.display_name
                    except:
                        id2name[voter_id] = f"Juror <@{voter_id}>"
    
    return id2name


async def upload_to_save_channel(guild: discord.Guild, file_path: str, description: str) -> str:
    """Upload a file to the save channel and return its permanent URL.
    
    Args:
        guild: Discord guild
        file_path: Path to the file to upload
        description: Description for the upload message
        
    Returns:
        Permanent Discord CDN URL for the uploaded file
        
    Raises:
        RuntimeError: If save channel not found or upload fails
    """
    save_channel = guild.get_channel(save_channel_id)
    if not save_channel or not isinstance(save_channel, discord.TextChannel):
        raise RuntimeError("Save channel not found")
    
    with open(file_path, "rb") as f:
        uploaded_message = await save_channel.send(
            content=description,
            file=discord.File(f, filename=os.path.basename(file_path))
        )
    
    if not uploaded_message.attachments:
        raise RuntimeError("Upload failed - no attachments in message")
    
    return uploaded_message.attachments[0].url


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


async def notify_organizer_error(bot: discord.Client, error_message: str):
    """Notify the organizer about an error.
    
    Args:
        bot: Discord client
        error_message: Description of the error
    """
    try:
        organizer = await bot.fetch_user(organizer_id)
        if organizer:
            await organizer.send(f"⚠️ **Bot Error:** {error_message}")
    except:
        pass


async def send_dm_safe(user: discord.User | discord.Member, content: str = "", embed: Optional[discord.Embed] = None, view: Optional[discord.ui.View] = None) -> bool:
    """Safely send a DM to a user, handling Forbidden and NotFound errors.
    
    Args:
        user: The user to send the DM to
        content: Text content to send (optional)
        embed: Embed to send (optional)
        view: View with buttons/components (optional)
    
    Returns:
        True if message was sent successfully, False otherwise
    """
    try:
        kwargs = {}
        if content:
            kwargs["content"] = content
        if embed:
            kwargs["embed"] = embed
        if view:
            kwargs["view"] = view
        await user.send(**kwargs)
        return True
    except (discord.Forbidden, discord.NotFound):
        return False


async def close_period(period: ContestPeriod, bot: discord.Client):
    """Close a contest period by running period-specific cleanup.
    
    Args:
        period: The period to close
        bot: Discord client
    """
    if period == ContestPeriod.QUALIF:
        await close_qualif_period(bot)
    elif period == ContestPeriod.SEMIS:
        await close_semis_period(bot)
    elif period == ContestPeriod.FINAL:
        await close_final_period(bot)


async def setup_period(new_period: ContestPeriod, old_period: Optional[ContestPeriod], bot: discord.Client):
    """Setup a new contest period by running period-specific initialization.
    
    Args:
        new_period: The period to enter
        old_period: The previous period (for context)
        bot: Discord client
    """
    global contest
    
    logger.info(f"Setting up period: {new_period.value} (previous: {old_period.value if old_period else 'None'})")
    
    if new_period == ContestPeriod.QUALIF:
        await setup_qualif_period(bot)
    elif new_period == ContestPeriod.SEMIS:
        await setup_semis_period(bot)
    elif new_period == ContestPeriod.FINAL:
        await setup_final_period(bot)
    elif new_period == ContestPeriod.IDLE and old_period == ContestPeriod.FINAL:
        await announce_final_results(bot)
        await announce_final_boards(bot)
        await notify_final_results(bot, contest)
    
    logger.info(f"Period setup complete: {new_period.value}")


class SubmissionConfirmView(discord.ui.View):
    """View with buttons to confirm or cancel a submission."""
    
    def __init__(self, message: discord.Message):
        super().__init__(timeout=60)  # 1 minute timeout
        self.message = message
        self.confirmed = False
    
    @discord.ui.button(label="I confirm my submission complies with the rules", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle confirmation of submission."""
        if interaction.user is None:
            return

        if interaction.user.id != self.message.author.id:
            await interaction.response.send_message(
                "Only the submission author can confirm.",
                ephemeral=True
            )
            return
        
        self.confirmed = True
        self.stop()
        
        # Disable buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        
        await interaction.response.edit_message(view=self)
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Handle cancellation of submission."""
        if interaction.user is None:
            return

        if interaction.user.id != self.message.author.id:
            await interaction.response.send_message(
                "Only the submission author can cancel.",
                ephemeral=True
            )
            return
        
        self.confirmed = False
        self.stop()
        
        # Disable buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        
        await interaction.response.edit_message(content="Submission cancelled.", view=self)


async def submit(contest: Contest, message: discord.Message, bot: discord.Client) -> Contest:
    """Handles a submission from a user.
    
    Takes a message with an image attachment or a link, processes it, and recorded the submission.
    
    The process consists of downloading the image locally, uploading it to a designated channel for submissions, recovering the link to the uploaded image, and storing the submission in the contest data.
    
    Args:
        contest: The contest object
        message: The message containing the submission
        bot: The Discord bot client
    
    Returns:
        Updated contest object
    """
    
    # Enforce deadline: only accept submissions during submission period
    current_timestamp = utcnow().timestamp()
    if not (contest.schedule.submission_period.start <= current_timestamp < contest.schedule.submission_period.end):
        ref = discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        )
        await message.channel.send(
            f"⏰ Submissions are not currently open. Please wait for the submission period to begin.",
            delete_after=60,
            reference=ref,
        )
        return contest
    
    # Check the channel and thread to see if they are valid for submissions
    channel_id, thread_id = get_channel_and_thread(message)
    
    if (channel_id, thread_id) not in contest.channel_threads_open_for_submissions:
        return contest
    
    # Enforce photo limit: maximum 6 photos per author per category
    if not contest.can_user_submit(channel_id, thread_id, message.author.id):
        ref = discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        )
        await message.channel.send(
            f"❌ You have reached the maximum limit of **{contest.MAX_SUBMISSIONS_PER_CATEGORY} photos per category**.\n\n"
            f"💡 **Tip:** You can withdraw a previous submission by reacting with ❌ to it, then submit a new photo.",
            delete_after=60,
            reference=ref,
        )
        # Delete the attempted submission message
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass
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
    
    # Ask for confirmation that the submission complies with the rules
    rules_text = (
        "Are you sure that:\n- you took this photo **__yourself__**?\n"
        "- the photo somewhat **fits the category of this channel**?\n"
        "- the photo doesn't have human **faces clearly visible**?\n"
        "- the photo **did NOT compete in previous editions of the contest**?\n"
        "\n"
        "By clicking 'I confirm', you acknowledge that your submission follows all contest rules."
    )
    
    confirmation_view = SubmissionConfirmView(message)
    
    ref = discord.MessageReference(
        message_id=message.id, channel_id=message.channel.id
    )
    await message.channel.send(
        content=rules_text,
        view=confirmation_view,
        reference=ref,
        delete_after=600  # Delete after 10 minutes
    )
    
    # Wait for user to confirm or cancel
    await confirmation_view.wait()
    
    if not confirmation_view.confirmed:
        # User cancelled or timed out - delete the original message
        try:
            await message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass  # Message already deleted or no permission to delete
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
    try:
        uploaded_url = await upload_to_save_channel(
            message.guild,
            local_filename,
            f"Submission {message.id}{file_extension}"
        )
    except RuntimeError as e:
        await message.channel.send(
            f"❌ Failed to save your submission. Please try again or contact an organizer.",
            delete_after=300,
            reference=discord.MessageReference(
                message_id=message.id, channel_id=message.channel.id
            ),
        )
        # Notify organizer
        await notify_organizer_error(bot, f"Failed to upload submission from user <@{message.author.id}> in <#{channel_id}>. Error: {e}")
        return contest
    
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
        embed=discord.Embed().set_image(url=uploaded_url)
    )
    
    # Save the submission in the contest
    submission = Submission(author_id=message.author.id, submission_time=round(utcnow().timestamp()), local_save_path=local_filename, discord_save_path=uploaded_url)
    
    # Delete the original message to maintain anonymity
    try:
        await message.delete()
    except (discord.errors.NotFound, discord.errors.Forbidden):
        pass  # Message already deleted or no permission to delete
    
    return contest.add_submission(submission, channel_id, message_resend.id, thread_id)


async def _perform_withdrawal(contest: Contest, message: discord.Message, channel_id: int, thread_id: Optional[int]) -> Contest:
    """Core withdrawal logic - withdraws submission, renumbers messages, and saves contest.
    
    Args:
        contest: The contest object
        message: The Discord message of the submission
        channel_id: The channel ID
        thread_id: The thread ID (None for main channels)
    
    Returns:
        Updated contest object
    """
    # Get submission index for message renumbering
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        return contest
    _, competition = res
    submission_index = competition.msg_to_sub[message.id]
    
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
    
    # Save the updated contest
    contest.save("photo_contest/contest2026.yaml")
    
    return contest


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
    
    # Check if this message is a submission
    if not contest.is_submission_message(channel_id, thread_id, message.id):
        return contest
    
    # Get the submission
    submission = contest.get_submission_from_message(channel_id, thread_id, message.id)
    if not submission:
        return contest
    
    # Get submission index for later message renumbering
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        return contest
    _, competition = res
    submission_index = competition.msg_to_sub[message.id]
    
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
    
    # Perform the withdrawal
    contest = await _perform_withdrawal(contest, message, channel_id, thread_id)
    
    # Delete the original submission message
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden):
        pass
    
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
            logger.info(f"Jury vote saved: user={self.user_id}, channel={self.channel_id}, thread={self.thread_id}")
            
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
    
    def __init__(self, submissions: list[Submission], user_id: int, channel_id: int, thread_id: Optional[int], contest: Contest, ranking_length: int = 10):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.submissions = submissions
        self.user_id = user_id
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.contest = contest
        self.ranking: list[Submission] = []
        self.ranking_length = ranking_length  # Number of submissions to rank (10 for qualif/semis, 5 for finals)
        
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
            
            if len(self.ranking) < self.ranking_length:
                await interaction.response.edit_message(
                    content=f"**Your current ranking ({len(self.ranking)}/{self.ranking_length}):**\n{ranking_text}\n\nClick buttons to continue building your top {self.ranking_length}.",
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


async def send_vote_reminder(user: discord.User | discord.Member, contest: Contest, channel_id: int, current_submissions: list[Submission], submission_numbers: dict[Submission, int]):
    """Send a reminder to the user about their previous votes from the previous stage.
    
    Args:
        user: The user to send the reminder to
        contest: The current contest
        channel_id: The channel ID for which we're voting
        current_submissions: List of submissions currently being voted on
        submission_numbers: Mapping of submissions to their display numbers
    """
    global current_period
    # Find the previous competition for this channel
    if current_period == ContestPeriod.FINAL:
        # Look at semis votes
        prev_comp = None
        for comp in contest.semis_competitions:
            if comp.channel_id == channel_id:
                prev_comp = comp
                break
        stage_name = "semi-final"
    elif current_period == ContestPeriod.SEMIS:
        # Look at qualif votes (find the thread that fed into this semis)
        # Since multiple qualif threads feed into one semis, we need to check all qualif comps
        prev_comp = None
        stage_name = "qualification"
    else:
        return  # No reminder for qualif period
    
    if current_period == ContestPeriod.FINAL and not prev_comp:
        return  # No semis data found for this channel
    
    # For SEMIS, gather votes from all qualif threads
    if current_period == ContestPeriod.SEMIS:
        all_qualif_jury_votes: dict[int, JuryVote] = {}
        all_qualif_public_votes: dict[Submission, int] = {}
        
        for comp in contest.qualif_competitions:
            if comp.channel_id == channel_id:
                # Gather jury votes from this qualif thread
                if user.id in comp.votes_jury:
                    assert comp.thread_id is not None, "Qualif competition should have a thread_id"
                    all_qualif_jury_votes[comp.thread_id] = comp.votes_jury[user.id]
                
                # Gather public votes from this qualif thread
                for (voter_id, submission), vote in comp.votes_public.items():
                    if voter_id == user.id and submission in current_submissions:
                        all_qualif_public_votes[submission] = vote.nb_points
        
        # Send jury vote reminder
        if all_qualif_jury_votes:
            for thread_id, jury_vote in all_qualif_jury_votes.items():
                recap: list[tuple[Submission, None | int]] = []
                for i, sub in enumerate(jury_vote.ranking):
                    if sub in current_submissions:
                        recap.append((sub, i + 1))
                
                if recap:
                    reminder_lines: list[str] = []
                    for sub, order in recap:
                        photo_num = submission_numbers.get(sub, "?")
                        reminder_lines.append(f"Submission #{photo_num} was **your #{order}** in {stage_name} / <#{thread_id}>")
                    
                    if reminder_lines:
                        await user.send("\n".join(reminder_lines))
        
        # Send public vote reminder
        if all_qualif_public_votes:
            reminder_lines = []
            for submission, nb_points in sorted(all_qualif_public_votes.items(), key=lambda x: x[1], reverse=True):
                photo_num = submission_numbers.get(submission, "?")
                plural = "s" if nb_points != 1 else ""
                reminder_lines.append(f"You gave Submission #{photo_num} **{nb_points} point{plural}** in {stage_name}")
            
            await user.send("\n".join(reminder_lines))
    
    # For FINAL, check previous semis votes
    elif current_period == ContestPeriod.FINAL and prev_comp:
        # Check if user had a jury vote in semis
        if user.id in prev_comp.votes_jury:
            jury_vote = prev_comp.votes_jury[user.id]
            recap = []
            
            # Find which submissions from the current final were in their semis ranking
            for i, sub in enumerate(jury_vote.ranking):
                if sub in current_submissions:
                    recap.append((sub, i + 1))
            
            # Find submissions in current final that weren't in their semis top 10
            for sub in current_submissions:
                if sub not in jury_vote.ranking:
                    recap.append((sub, None))
            
            # Send the reminder
            if recap:
                reminder_lines: list[str] = []
                for sub, order in recap:
                    photo_num = submission_numbers.get(sub, "?")
                    if order:
                        reminder_lines.append(f"Submission #{photo_num} was **your #{order}** in the {stage_name}")
                    else:
                        reminder_lines.append(f"Submission #{photo_num} was **not in your top 10** in the {stage_name}")
                
                await user.send("\n".join(reminder_lines))
        
        # Check if user had public votes in semis
        public_vote_counts: dict[Submission, int] = {}
        for (voter_id, submission), vote in prev_comp.votes_public.items():
            if voter_id == user.id and submission in current_submissions:
                public_vote_counts[submission] = public_vote_counts.get(submission, 0) + vote.nb_points
        
        if public_vote_counts:
            reminder_lines: list[str] = []
            for submission, nb_points in sorted(public_vote_counts.items(), key=lambda x: x[1], reverse=True):
                photo_num = submission_numbers.get(submission, "?")
                plural = "s" if nb_points != 1 else ""
                reminder_lines.append(f"You gave Submission #{photo_num} **{nb_points} point{plural}** in the {stage_name}")
            
            await user.send("\n".join(reminder_lines))


async def handle_jury_vote_request(contest: Contest, message: discord.Message, user: discord.Member | discord.User, bot: discord.Client, current_period: ContestPeriod):
    """Handle a jury vote request (🗳️ reaction) by sending voting UI in DM.
    
    Args:
        contest: The current contest
        message: The voting message that was reacted to
        user: The user who wants to vote
        bot: The bot client
        current_period: The current contest period
    """
    # Enforce deadline: only allow jury votes during qualif, semis, or final periods
    if current_period not in [ContestPeriod.QUALIF, ContestPeriod.SEMIS, ContestPeriod.FINAL]:
        try:
            await user.send("⏰ Jury voting is not currently open.")
        except discord.Forbidden:
            pass
        try:
            await message.remove_reaction("🗳️", user)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return
    
    # Get channel and thread
    channel_id, thread_id = get_channel_and_thread(message)
    
    # Find the competition and get votable submissions
    votable_submissions, submission_numbers = contest.get_votable_submissions(
        channel_id, thread_id, user.id
    )
    
    # Determine ranking length based on period and number of votable submissions
    # Finals have max 5, Qualif/Semis have max 10
    # Adapt downward if user has many of their own submissions
    num_votable = len(votable_submissions)
    
    if current_period == ContestPeriod.FINAL:
        # Finals: maximum 5 ranking
        if num_votable >= 5:
            ranking_length = 5
        elif num_votable >= 3:
            ranking_length = 3
        else:
            # Not enough submissions to vote
            try:
                await user.send(
                    f"❌ Not enough submissions to vote on. You need at least 3 submissions (excluding your own) to cast a jury vote."
                )
            except discord.Forbidden:
                await notify_organizer_dm_failed(user, message, "jury voting notification")
            return
    else:
        # Qualif/Semis: maximum 10 ranking
        if num_votable >= 10:
            ranking_length = 10
        elif num_votable >= 5:
            ranking_length = 5
        elif num_votable >= 3:
            ranking_length = 3
        else:
            # Not enough submissions to vote
            try:
                await user.send(
                    f"❌ Not enough submissions to vote on. You need at least 3 submissions (excluding your own) to cast a jury vote."
                )
            except discord.Forbidden:
                await notify_organizer_dm_failed(user, message, "jury voting notification")
            return
    
    try:
        # Send jury oath
        await user.send(
            "⚖️ **Jury Oath**\n\n"
            "As a jury member, I pledge to:\n"
            "• Evaluate submissions based solely on photographic quality\n"
            "• Judge fairly and impartially\n"
            "• Consider composition, technical execution, creativity, and artistic merit\n"
            "• Set aside personal biases and vote with integrity\n\n"
            "Thank you for your commitment to maintaining the quality of this contest. 🎖️"
        )
        
        # Send all submission images in DM
        await user.send("📸 **Here are all the submissions for your review:**")
        
        for submission in votable_submissions:
            await user.send(
                content=f"Submission #{submission_numbers[submission]}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )
        
        # If voting in SEMIS or FINAL period, remind user of their previous votes
        if current_period in (ContestPeriod.SEMIS, ContestPeriod.FINAL):
            await send_vote_reminder(user, contest, channel_id, votable_submissions, submission_numbers)
        
        # Send the voting interface
        view = JuryVotingView(votable_submissions, user.id, channel_id, thread_id, contest, ranking_length)
        await user.send(
            content=f"**Click the buttons below to build your top {ranking_length} ranking.**\nSelect submissions in order from your most preferred to your {ranking_length}th preferred.",
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


async def handle_public_vote(contest: Contest, message: discord.Message, user: discord.Member | discord.User, emoji: str, current_period: ContestPeriod) -> Contest:
    """Handle a public vote (0-3 points) during qualif or semis periods.
    
    Args:
        contest: The current contest
        message: The submission message that was reacted to
        user: The user who reacted
        emoji: The emoji name (should be 0️⃣, 1️⃣, 2️⃣, or 3️⃣)
        current_period: The current contest period
    
    Returns:
        Updated contest object
    """
    # Enforce deadline: only allow votes during qualif or semis periods
    if current_period not in [ContestPeriod.QUALIF, ContestPeriod.SEMIS]:
        try:
            await message.remove_reaction(emoji, user)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return contest
    
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
    
    # During qualif period, only contestants can vote
    if current_period == ContestPeriod.QUALIF:
        contestant_ids = contest.contestants
        if user.id not in contestant_ids:
            # Not a contestant, remove their reaction
            try:
                await message.remove_reaction(emoji, user)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            return contest
    
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
        logger.info(f"Public vote saved: user={user.id}, points={points}, channel={channel_id}, thread={thread_id}")
        
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


async def handle_commentary_request(contest: Contest, message: discord.Message, user: discord.Member | discord.User, current_period: ContestPeriod):
    """Handle a commentary request (💬 reaction) by opening a modal for text input.
    
    Args:
        contest: The current contest
        message: The photo message that was reacted to
        user: The user who wants to comment
        current_period: The current contest period
    """
    # Enforce deadline: only allow commentary during semis period
    if current_period != ContestPeriod.SEMIS:
        try:
            await user.send("⏰ Commentary is only available during the semi-finals period.")
        except discord.Forbidden:
            pass
        try:
            await message.remove_reaction("💬", user)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return
    
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
                contest = contest.add_commentary(
                    channel_id=channel_id,
                    thread_id=thread_id,
                    submission=submission,
                    author_id=interaction.user.id,
                    text=commentary_text
                )
                contest.save("photo_contest/contest2026.yaml")
                logger.info(f"Commentary added: user={interaction.user.id}, channel={channel_id}, thread={thread_id}")
                
                # Update the summary for this specific submission
                assert message.guild is not None, "Message guild is None"
                await update_commentary_summary(contest, channel_id, thread_id, submission, message.guild)
                
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
    view = CommentaryButton()
    dm_sent = await send_dm_safe(
        user,
        f"💬 **Add a commentary for Photo #{submission_index + 1}**\n"
        f"Click the button below to open the commentary form.",
        view=view
    )
    
    if not dm_sent:
        # User has DMs disabled, try to send in channel
        try:
            await message.channel.send(
                f"{user.mention}, click the button below to add your commentary:",
                view=view,
                delete_after=60
            )
        except:
            pass


async def update_commentary_summary(contest: Contest, channel_id: int, thread_id: Optional[int], submission: Submission, guild: discord.Guild):
    """Update the commentary summary message for a specific submission.
    
    Args:
        contest: The contest object
        channel_id: The channel ID
        thread_id: The thread ID (None for main channels)
        submission: The submission whose summary to update
        guild: The Discord guild
    """
    # Get the commentary summaries
    summaries = contest.get_competition_commentaries_summaries(channel_id, thread_id)
    summaries_dict = {sub: summary for sub, summary in summaries}
    summary_text = summaries_dict.get(submission, "")
    
    # Get the channel
    channel = guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return
    
    # Get the summary message ID for this submission
    submission_to_summary = contest.get_submission_to_summary_msg(channel_id, thread_id)
    summary_msg_id = submission_to_summary.get(submission)
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


async def announce_stage_results(bot: discord.Client, contest: Contest, competition_type: Literal["semis", "final"], stage_name: str, next_stage_name: str):
    """Helper function to announce results by posting qualifying photos in random order (no authors).
    
    Args:
        bot: The Discord bot client
        contest: The contest object
        competition_type: Type of competitions to announce ("semis" or "final")
        stage_name: Name of the current stage (e.g., "Qualifiers", "Finalists")
        next_stage_name: Name of the next stage (e.g., "Semi-Finals", "Grand Final")
    """
    # Get the announcement channel
    announcement_channel = bot.get_channel(announcement_channel_id)
    if not announcement_channel or not isinstance(announcement_channel, discord.TextChannel):
        print(f"Warning: Could not find announcement channel {announcement_channel_id}")
        return
    
    # For each category, post qualifiers (already in random order from solve_qualifs/solve_semis)
    competitions = contest.semis_competitions if competition_type == "semis" else contest.final_competitions
    for comp in competitions:
        # Get category name
        category_channel = bot.get_channel(comp.channel_id)
        category_name = getattr(category_channel, "name", f"Category <#{comp.channel_id}>")
        
        # Post announcement
        await announcement_channel.send(f"🎊 **{stage_name} for {category_name}**")
        
        # Post each qualifier without author info (order already randomized when competition was created)
        for i, submission in enumerate(comp.competing_entries):
            e = discord.Embed()
            e.set_image(url=submission.discord_save_path)
            await announcement_channel.send(
                content=f"{stage_name.rstrip('s')} #{i+1}",
                embed=e
            )


async def announce_qualif_results(bot: discord.Client):
    """Announce qualification results by posting qualifying photos in random order (no authors)."""
    global contest
    
    # Announce results using helper (contest should already be solved)
    await announce_stage_results(bot, contest, "semis", "Qualifiers", "Semi-Finals")


async def announce_semis_results(bot: discord.Client):
    """Announce semi-final results by posting finalists in random order (no authors)."""
    global contest
    
    # Announce results using helper (contest should already be solved)
    await announce_stage_results(bot, contest, "final", "Finalists", "Grand Final")


async def announce_final_results(bot: discord.Client, reveal_delay: int = 15):
    """Announce final results with Eurovision-style voting reveal with live boards.
    
    Args:
        bot: Discord client
        reveal_delay: Seconds to wait between revealing each voter's results (default: 15)
    """
    global contest
    
    # Use shorter delay in test mode
    if TEST_MODE:
        reveal_delay = 2
        logger.info("TEST_MODE: Using 2 second reveal delay")
    
    # Get the announcement channel
    announcement_channel = bot.get_channel(announcement_channel_id)
    if not announcement_channel or not isinstance(announcement_channel, discord.TextChannel):
        print(f"Warning: Could not find announcement channel {announcement_channel_id}")
        return
    
    # Build id2name mapping
    id2name = await build_id2name_mapping(bot, contest, include_voters=True)
    
    # Live board reveal for each category
    for comp in contest.final_competitions:
        # Get category name
        category_channel = bot.get_channel(comp.channel_id)
        category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
        
        await announcement_channel.send(f"\n\n🎤 **LIVE VOTING REVEAL for {category_name}** 🎤\n")
        await announcement_channel.send(f"**{len(comp.votes_jury)} jury votes have been cast. Let's reveal them!**\n")
        
        # Generate and post boards using live reveal
        for board_path, voter_id, is_initial, is_final in gen_live_final_reveal(comp, category_name, id2name):
            if is_initial:
                # Initial board with all zeros
                await announcement_channel.send(
                    "📊 **Starting scoreboard:**",
                    file=discord.File(board_path)
                )
            elif is_final:
                # Final results board
                await announcement_channel.send(
                    "🏆 **Final Results:**",
                    file=discord.File(board_path)
                )
            elif voter_id is not None:
                # Individual voter's contribution
                await announcement_channel.send(
                    f"Thank you <@{voter_id}> 🎖️ for your votes!",
                    file=discord.File(board_path)
                )
                await asyncio.sleep(reveal_delay)
        
        # Announce the winner
        jury_scores = comp.count_votes_jury()
        winner = max(comp.competing_entries, key=lambda x: (jury_scores.get(x, 0), -x.submission_time))
        
        winner_embed = discord.Embed(
            description=f"**🎉 Congratulations, this photo won the {category_name} category! 🎉**"
        )
        winner_embed.set_image(url=winner.discord_save_path)
        await announcement_channel.send(embed=winner_embed)
        
        # DM the winner
        try:
            winner_user = await bot.fetch_user(winner.author_id)
            dm_channel = winner_user.dm_channel or await winner_user.create_dm()
            await dm_channel.send(embed=winner_embed)
        except Exception as e:
            print(f"Could not send DM to winner {winner.author_id}: {e}")
    
    await announcement_channel.send("\n✨ **All categories complete! Thank you to all participants!** ✨")


async def announce_final_boards(bot: discord.Client):
    """Post detailed results boards for all stages."""
    global contest
    
    # Get the announcement channel
    announcement_channel = bot.get_channel(announcement_channel_id)
    if not announcement_channel or not isinstance(announcement_channel, discord.TextChannel):
        print(f"Warning: Could not find announcement channel {announcement_channel_id}")
        return
    
    # Build id2name mapping
    id2name = await build_id2name_mapping(bot, contest)
    
    # Generate and post all boards
    await announcement_channel.send("\n\n📊 **DETAILED RESULTS BOARDS** 📊\n")
    
    # Build channel_names dict for semifinals
    channel_names: Dict[int, str] = {}
    for comp in contest.semis_competitions:
        category_channel = bot.get_channel(comp.channel_id)
        channel_names[comp.channel_id] = getattr(category_channel, "name", f"Category {comp.channel_id}")
    
    # Generate and post qualif boards
    for comp in contest.qualif_competitions:
        category_channel = bot.get_channel(comp.channel_id)
        category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
        
        # Get thread name
        if comp.thread_id:
            thread = await bot.fetch_channel(comp.thread_id)
            assert isinstance(thread, discord.Thread), "Thread ID does not correspond to a thread channel"
            thread_name = thread.name
        else:
            thread_name = None
        
        board_path = gen_competition_board(comp, category_name, id2name, thread_name)
        
        with open(board_path, "rb") as f:
            await announcement_channel.send(
                content=f"Qualification Results: {category_name}" + (f" - Thread {thread_name}" if thread_name else ""),
                file=discord.File(f, filename=os.path.basename(board_path))
            )
    
    # Generate and post semi-final boards
    semifinal_boards = gen_semifinals_boards(contest, channel_names, id2name)
    for board_path in semifinal_boards:
        with open(board_path, "rb") as f:
            await announcement_channel.send(
                content="Semi-Final Results",
                file=discord.File(f, filename=os.path.basename(board_path))
            )
    
    # Generate and post final board (one board total)
    board_path = gen_final_results_board(contest, id2name)
    
    with open(board_path, "rb") as f:
        await announcement_channel.send(
            content="🏆 **Final Results** 🏆",
            file=discord.File(f, filename=os.path.basename(board_path))
        )
    
    await announcement_channel.send("\n✨ **Contest complete! See you next year!** ✨")


async def notify_period_start(bot: discord.Client, period: ContestPeriod):
    """Send announcement when a new period begins."""
    announcement_channel = bot.get_channel(announcement_channel_id)
    if not announcement_channel or not isinstance(announcement_channel, discord.TextChannel):
        return
    
    if period == ContestPeriod.SUBMISSION:
        await announcement_channel.send(
            "📸 **SUBMISSION PERIOD HAS BEGUN!** 📸\n\n"
            "Submit your photos in the designated category channels!\n"
            f"Deadline: <t:{int(contest.schedule.submission_period.end)}:F>"
        )
    elif period == ContestPeriod.QUALIF:
        await announcement_channel.send(
            "🗳️ **QUALIFICATION VOTING HAS BEGUN!** 🗳️\n\n"
            "Check the qualification threads and vote for your favorites!\n"
            f"Deadline: <t:{int(contest.schedule.qualif_period.end)}:F>"
        )
    elif period == ContestPeriod.SEMIS:
        await announcement_channel.send(
            "🌟 **SEMI-FINALS HAVE BEGUN!** 🌟\n\n"
            "Vote for the qualifiers in each category!\n"
            f"Deadline: <t:{int(contest.schedule.semis_period.end)}:F>"
        )
    elif period == ContestPeriod.FINAL:
        await announcement_channel.send(
            "🏆 **GRAND FINAL HAS BEGUN!** 🏆\n\n"
            "All categories compete together! Cast your votes!\n"
            f"Deadline: <t:{int(contest.schedule.final_period.end)}:F>"
        )


async def notify_qualifiers(bot: discord.Client, contest: Contest, competition_type: Literal["semis", "final"], stage_name: str):
    """Send DM notifications to users for each photo that qualified.
    
    Args:
        bot: The Discord bot client
        contest: The contest object
        competition_type: Type of competitions ("semis" or "final")
        stage_name: Name of the stage they qualified for (e.g., "Semi-Finals", "Grand Final")
    """
    guild = bot.get_guild(voltServer)
    if not guild:
        return
    
    # Send DM for each qualified photo
    competitions = contest.semis_competitions if competition_type == "semis" else contest.final_competitions
    for comp in competitions:
        # Get category name
        category_channel = bot.get_channel(comp.channel_id)
        category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
        
        for submission in comp.competing_entries:
            try:
                user = await guild.fetch_member(submission.author_id)
                if user:
                    # Adapt message based on whether this is the final or not
                    if stage_name == "Grand Final":
                        description = f"Your photo from **{category_name}** has qualified for the **{stage_name}**!\n\nThis is the final round - good luck! 🍀"
                    else:
                        description = f"Your photo from **{category_name}** has qualified for the **{stage_name}**!\n\nGood luck in the next round! 🍀"
                    
                    embed = discord.Embed(
                        title=f"🎉 Photo Qualified!",
                        description=description,
                        color=0x00ff00
                    )
                    embed.set_image(url=submission.discord_save_path)
                    await send_dm_safe(user, embed=embed)
            except Exception:
                # User not found, skip
                pass


async def notify_final_results(bot: discord.Client, contest: Contest):
    """Send DM notifications to finalists with their final placement.
    
    Args:
        bot: The Discord bot client
        contest: The contest object
    """
    guild = bot.get_guild(voltServer)
    if not guild:
        return
    
    medal_emojis = ["🥇", "🥈", "🥉"]

    users = dict()
    
    # Calculate placements for each category
    for comp in contest.final_competitions:
        # Get category name
        category_channel = bot.get_channel(comp.channel_id)
        category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
        
        # Calculate total points for each submission (finals only have jury votes)
        jury_votes = comp.count_votes_jury()
        
        total_points: Dict[Submission, int] = {}
        for submission in comp.competing_entries:
            total_points[submission] = jury_votes.get(submission, 0)
        
        # Rank submissions
        ranked_submissions = sorted(
            comp.competing_entries,
            key=lambda x: (total_points[x], -x.submission_time),
            reverse=True,
        )
        
        # Send DM to each finalist with their placement
        for i, submission in enumerate(ranked_submissions):
            placement = i + 1
            points = total_points[submission]
            
            try:
                if submission.author_id in users:
                    user = users[submission.author_id]
                else:
                    user = await guild.fetch_member(submission.author_id)
                    users[submission.author_id] = user

                if user:
                    # Create placement message
                    if placement == 1:
                        medal = medal_emojis[0]
                        title = f"{medal} WINNER - {category_name}!"
                        description = f"🎊 **Congratulations!** Your photo won **1st place** in **{category_name}**!\n\n**Total Points:** {points}"
                        color = 0xFFD700  # Gold
                    elif placement == 2:
                        medal = medal_emojis[1]
                        title = f"{medal} 2nd Place - {category_name}"
                        description = f"🎉 **Amazing!** Your photo placed **2nd** in **{category_name}**!\n\n**Total Points:** {points}"
                        color = 0xC0C0C0  # Silver
                    elif placement == 3:
                        medal = medal_emojis[2]
                        title = f"{medal} 3rd Place - {category_name}"
                        description = f"👏 **Great work!** Your photo placed **3rd** in **{category_name}**!\n\n**Total Points:** {points}"
                        color = 0xCD7F32  # Bronze
                    else:
                        title = f"Final Results - {category_name}"
                        description = f"Thank you for participating! Your photo placed **{placement}th** in **{category_name}**.\n\n**Total Points:** {points}"
                        color = 0x7289DA  # Discord blurple
                    
                    embed = discord.Embed(
                        title=title,
                        description=description,
                        color=color
                    )
                    embed.set_image(url=submission.discord_save_path)
                    await send_dm_safe(user, embed=embed)
            except Exception:
                # User not found, skip
                pass


async def recover_state(bot: discord.Client):
    """Recover bot state after restart or crash. Catches up on any missed period transitions."""
    global contest, current_period
    
    current_timestamp = utcnow().timestamp()
    
    # Determine where we should be
    if contest.schedule.submission_period.start <= current_timestamp < contest.schedule.submission_period.end:
        target_period = ContestPeriod.SUBMISSION
    elif contest.schedule.qualif_period.start <= current_timestamp < contest.schedule.qualif_period.end:
        target_period = ContestPeriod.QUALIF
    elif contest.schedule.semis_period.start <= current_timestamp < contest.schedule.semis_period.end:
        target_period = ContestPeriod.SEMIS
    elif contest.schedule.final_period.start <= current_timestamp < contest.schedule.final_period.end:
        target_period = ContestPeriod.FINAL
    else:
        target_period = ContestPeriod.IDLE
    
    print(f"Recovering state... Current period should be: {target_period.value}")
    
    # Check if we need to set up periods that we missed
    if target_period == ContestPeriod.QUALIF and not contest.qualif_competitions:
        print("Missed qualif setup, setting up now...")
        await setup_qualif_period(bot)
    elif target_period == ContestPeriod.SEMIS and not contest.semis_competitions:
        print("Missed semis setup, setting up now...")
        await setup_semis_period(bot)
    elif target_period == ContestPeriod.FINAL and not contest.final_competitions:
        print("Missed final setup, setting up now...")
        await setup_final_period(bot)
    elif target_period == ContestPeriod.IDLE and current_timestamp > contest.schedule.final_period.end:
        # Check if we missed the final results announcement
        print("Contest has ended. Results may have been announced.")
    
    current_period = target_period
    print(f"Recovery complete. Current period: {current_period.value}")


async def close_qualif_period(bot: discord.Client):
    """Close qualification period by removing voting reactions and locking threads."""
    global contest
    
    print("Closing qualification period...")
    
    # Build id2name mapping
    id2name = await build_id2name_mapping(bot, contest)
    
    for comp in contest.qualif_competitions:
        if comp.thread_id:
            thread = await bot.fetch_channel(comp.thread_id)
            if thread and isinstance(thread, discord.Thread):
                # Get category name
                category_channel = bot.get_channel(comp.channel_id)
                category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
                
                # Generate the results board
                board_path = gen_competition_board(comp, category_name, id2name, thread.name)
                
                # Upload the board to save channel for permanent URL
                try:
                    board_url = await upload_to_save_channel(
                        thread.guild,
                        board_path,
                        f"Results board for {category_name} - {thread.name}"
                    )
                except RuntimeError as e:
                    print(f"Could not upload board: {e}")
                    await notify_organizer_error(bot, f"Failed to upload qualification results board for {category_name} - {thread.name}: {e}")
                    continue
                
                # Update each submission message to add the results board
                for i, submission in enumerate(comp.competing_entries):
                    # Find the message ID for this submission
                    message_id = None
                    for msg_id, sub_idx in comp.msg_to_sub.items():
                        if sub_idx == i:
                            message_id = msg_id
                            break
                    
                    if message_id:
                        try:
                            message = await thread.fetch_message(message_id)
                            # Update embed to show both submission and results board
                            embed = discord.Embed()
                            embed.set_image(url=submission.discord_save_path)
                            embed.set_thumbnail(url=board_url)
                            await message.edit(
                                content=f"Submission #{i+1}",
                                embed=embed
                            )
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                            print(f"Could not update message {message_id}: {e}")
                
                try:
                    # Archive the thread
                    await thread.edit(archived=True, locked=True)
                except (discord.Forbidden, discord.HTTPException):
                    pass


async def close_semis_period(bot: discord.Client):
    """Close semi-finals period by removing voting reactions."""
    global contest
    
    print("Closing semi-finals period...")
    
    # Build id2name mapping
    id2name = await build_id2name_mapping(bot, contest)
    
    for comp in contest.semis_competitions:
        channel = await bot.fetch_channel(comp.channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            # Get category name
            category_name = getattr(channel, "name", f"Category {comp.channel_id}")
            
            # Generate the results board
            board_path = gen_competition_board(comp, category_name, id2name, None)
            
            # Upload the board to save channel for permanent URL
            try:
                board_url = await upload_to_save_channel(
                    channel.guild,
                    board_path,
                    f"Results board for {category_name}"
                )
            except RuntimeError as e:
                print(f"Could not upload board: {e}")
                await notify_organizer_error(bot, f"Failed to upload semi-final results board for {category_name}: {e}")
                continue
            
            # Update each submission message to add the results board
            for i, submission in enumerate(comp.competing_entries):
                # Find the message ID for this submission
                message_id = None
                for msg_id, sub_idx in comp.msg_to_sub.items():
                    if sub_idx == i:
                        message_id = msg_id
                        break
                
                if message_id:
                    try:
                        message = await channel.fetch_message(message_id)
                        # Update embed to show both submission and results board
                        embed = discord.Embed()
                        embed.set_image(url=submission.discord_save_path)
                        embed.set_thumbnail(url=board_url)
                        await message.edit(
                            content=f"Submission #{i+1}",
                            embed=embed
                        )
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                        print(f"Could not update message {message_id}: {e}")
            
            try:
                # Send closing message
                await channel.send("🔒 **Semi-Finals voting has ended!** Results will be announced soon.")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def close_final_period(bot: discord.Client):
    """Close final period by removing voting reactions."""
    global contest
    
    print("Closing final period...")
    
    final_channel = await bot.fetch_channel(final_channel_id)
    if final_channel and isinstance(final_channel, discord.TextChannel):
        try:
            await final_channel.send("🔒 **Final voting has ended!** Results will be revealed shortly.")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup_qualif_period(bot: discord.Client):
    """Set up qualification threads and post submissions."""
    global contest
    
    # Send period start announcement
    await notify_period_start(bot, ContestPeriod.QUALIF)
    
    # Get the number of threads needed per category
    thread_counts = contest.count_qualifs()
    
    # Create threads for each category (skip categories with < 12 submissions)
    all_thread_ids = []
    
    for comp, thread_count in zip(contest.submission_competitions, thread_counts):
        channel = await bot.fetch_channel(comp.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            print(f"Warning: Could not find channel {comp.channel_id}")
            all_thread_ids.append([])
            continue
        
        # Check if this category needs qualification threads
        if thread_count == 0:
            # Category has < 12 submissions, auto-qualify all to semis
            all_thread_ids.append([])
            
            # Announce auto-qualification in the category channel
            category_name = getattr(channel, "name", f"Category {comp.channel_id}")
            await channel.send(
                f"🎉 **All {len(comp.competing_entries)} submission(s) automatically qualify for the Semi-Finals!**\n\n"
                f"No qualification voting is needed for this category due to the low number of submissions. "
                f"All entries will proceed directly to the semi-finals. Good luck!"
            )
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
    for comp in contest.qualif_competitions:
        assert comp.thread_id is not None, "Qualification competition missing thread_id"
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
            "React with 🗳️ to this message to cast your jury vote (top 10 ranking).\n"
            "Note: The top 4 jury picks and top 1 public pick will advance to the semi-finals."
        )
        await vote_msg.add_reaction("🗳️")
    
    # Save the updated contest with message mappings
    contest.save("photo_contest/contest2026.yaml")


def copy_qualif_public_votes_to_semis():
    """Automatically copy public votes from qualification to semi-finals.
    
    This ensures users don't need to re-vote for photos they already voted for in qualifs.
    """
    global contest
    
    logger.info("Copying public votes from qualif to semis...")
    
    # For each semis competition
    for semis_comp in contest.semis_competitions:
        # Find all qualif competitions for this channel
        for qualif_comp in contest.qualif_competitions:
            if qualif_comp.channel_id == semis_comp.channel_id:
                # Copy public votes for submissions that advanced to semis
                for (voter_id, submission), vote in qualif_comp.votes_public.items():
                    if submission in semis_comp.competing_entries:
                        # Save this public vote in the semis competition
                        try:
                            contest = contest.save_public_vote(
                                semis_comp.channel_id,
                                None,
                                voter_id,
                                vote.nb_points,
                                submission
                            )
                        except ValueError:
                            # Vote already exists or voter is the author, skip
                            pass
    
    contest.save("photo_contest/contest2026.yaml")
    logger.info("Public votes copied successfully")


async def setup_semis_period(bot: discord.Client):
    """Set up semi-final competitions and post qualified submissions."""
    global contest
    
    # Solve qualifications to determine semi-finalists
    contest = contest.solve_qualifs()
    contest.save("photo_contest/contest2026.yaml")
    
    # Copy public votes from qualif to semis automatically
    copy_qualif_public_votes_to_semis()
    
    # Announce qualification results (random order, no authors)
    await announce_qualif_results(bot)
    
    # Send DM notifications to qualifiers
    await notify_qualifiers(bot, contest, "semis", "Semi-Finals")
    
    # Send period start announcement
    await notify_period_start(bot, ContestPeriod.SEMIS)
    
    # Post submissions in semi-final channels
    for comp in contest.semis_competitions:
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
            "React with 🗳️ to this message to cast your jury vote (top 10 ranking).\n"
            "React with 💬 to add commentary about a photo.\n"
            "Note: The top 3 jury picks and top 2 public picks will advance to the Grand Final."
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
    
    # Announce semi-final results (random order, no authors)
    await announce_semis_results(bot)
    
    # Send DM notifications to finalists
    await notify_qualifiers(bot, contest, "final", "Grand Final")
    
    # Send period start announcement
    await notify_period_start(bot, ContestPeriod.FINAL)
    
    # Post submissions in the final channel (single channel for all categories)
    final_channel = bot.get_channel(final_channel_id)
    if not final_channel or not isinstance(final_channel, discord.TextChannel):
        print(f"Warning: Could not find final channel {final_channel_id}")
        return
    
    # Post all finalists from all categories in the final channel
    await final_channel.send("🏆 **GRAND FINAL!** 🏆\nAll categories compete together!")
    
    submission_counter = 1
    for comp in contest.final_competitions:
        # Get category name for context
        category_channel = bot.get_channel(comp.channel_id)
        category_name = category_channel.name if category_channel else f"Category {comp.channel_id}"
        
        await final_channel.send(f"\n**{category_name} Finalists:**")
        
        for i, submission in enumerate(comp.competing_entries):
            msg = await final_channel.send(
                content=f"Submission #{submission_counter}",
                embed=discord.Embed().set_image(url=submission.discord_save_path)
            )
            
            # Update the contest with the message_id mapping
            contest = contest.set_message_id(comp.channel_id, comp.thread_id, i, msg.id)
            
            submission_counter += 1
    
    # Send voting instruction message
    vote_msg = await final_channel.send(
        "🗳️ **Final Voting is now open!**\n"
        "React with 🗳️ to this message to cast your vote (top 5 ranking)."
    )
    await vote_msg.add_reaction("🗳️")
    
    # Save the updated contest
    contest.save("photo_contest/contest2026.yaml")


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
            
            # Close the previous period
            if old_period:
                await close_period(old_period, bot)
            
            # Setup the new period
            await setup_period(period, old_period, bot)
        
        return period

    @tasks.loop(minutes=1.0)
    async def autoplanner():
        now = utcnow().to("Europe/Brussels")
        await planner(now, bot)

    @bot.event
    async def on_ready():
        print(f"Bot is ready. Logged in as {bot.user}")
        await recover_state(bot)
        autoplanner.start()

    @bot.event
    async def on_message(message):
        global contest
        await bot.process_commands(message)
        
        if message.author.bot:
            return
        
        # Handle submissions only during submission period
        if current_period == ContestPeriod.SUBMISSION:
            contest = await submit(contest, message, bot)
            contest.save("photo_contest/contest2026.yaml")

    @bot.event
    async def on_raw_reaction_add(payload):
        global contest
        
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
        
        # Handle withdrawal reactions (❌) - allowed during any period
        if payload.emoji.name == "❌":
            contest = await withdraw(contest, message, user)
            return
        
        # Handle reactions based on current contest period
        if current_period == ContestPeriod.IDLE or current_period is None:
            return  # No active period, ignore other reactions
        
        # Handle different reactions based on current period
        if current_period == ContestPeriod.SUBMISSION:
            # No additional reactions during submission period
            pass
        
        elif current_period == ContestPeriod.QUALIF or current_period == ContestPeriod.SEMIS:
            # Handle public votes (0-3 points)
            if payload.emoji.name in ["0️⃣", "1️⃣", "2️⃣", "3️⃣"]:
                contest = await handle_public_vote(contest, message, user, payload.emoji.name, current_period)
            # Handle jury vote requests
            elif payload.emoji.name == "🗳️":
                await handle_jury_vote_request(contest, message, user, bot, current_period)
            # Handle commentary requests (semis only)
            elif payload.emoji.name == "💬" and current_period == ContestPeriod.SEMIS:
                await handle_commentary_request(contest, message, user, current_period)
        
        elif current_period == ContestPeriod.FINAL:
            # Handle jury vote requests (top 5 ranking)
            if payload.emoji.name == "🗳️":
                await handle_jury_vote_request(contest, message, user, bot, current_period)

    @bot.event
    async def on_message_delete(message: discord.Message):
        """Handle message deletions - withdraw submission if a submission message is deleted."""
        global contest
        
        # Only process during submission period
        if current_period != ContestPeriod.SUBMISSION:
            return
        
        # Ignore DMs
        if not message.guild:
            return
        
        # Check the channel and thread
        channel_id, thread_id = get_channel_and_thread(message)
        
        # Check if this channel/thread is valid for submissions
        if (channel_id, thread_id) not in contest.channel_threads_open_for_submissions:
            return
        
        # Check if this message is a submission
        if not contest.is_submission_message(channel_id, thread_id, message.id):
            return
        
        # Perform the withdrawal (message is already deleted, so no need to delete it again)
        contest = await _perform_withdrawal(contest, message, channel_id, thread_id)

    @bot.command(name="setup")
    async def command_setup(ctx: commands.Context, *channels: discord.TextChannel):
        if ctx.author.id == organizer_id:
            pass  # await setup(*channels)

    # Admin Commands for Testing #############################################
    
    def is_admin(user_id: int) -> bool:
        """Check if user is an admin (organizer or has discord team role)."""
        return user_id == organizer_id
    
    @bot.command(name="contest_status")
    async def command_contest_status(ctx: commands.Context):
        """Show current contest period and schedule."""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        # Map enum values to display text
        period_display = {
            ContestPeriod.SUBMISSION: "🖼️ SUBMISSION",
            ContestPeriod.QUALIF: "🗳️ QUALIFICATION",
            ContestPeriod.SEMIS: "⭐ SEMIFINALS",
            ContestPeriod.FINAL: "🏆 FINAL",
            ContestPeriod.IDLE: "💤 IDLE",
            None: "💤 IDLE"
        }
        
        period_name = period_display.get(current_period, "❓ UNKNOWN")
        
        # Format schedule
        def format_time(timestamp: float) -> str:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
        
        schedule_info = (
            f"**Current Period:** {period_name}\n\n"
            f"**📅 Schedule:**\n"
            f"🖼️ **Submission:** {format_time(contest.schedule.submission_period.start)} → {format_time(contest.schedule.submission_period.end)}\n"
            f"🗳️ **Qualification:** {format_time(contest.schedule.qualif_period.start)} → {format_time(contest.schedule.qualif_period.end)}\n"
            f"⭐ **Semifinals:** {format_time(contest.schedule.semis_period.start)} → {format_time(contest.schedule.semis_period.end)}\n"
            f"🏆 **Final:** {format_time(contest.schedule.final_period.start)} → {format_time(contest.schedule.final_period.end)}\n\n"
        )
        
        await ctx.send(schedule_info)
    
    @bot.command(name="contest_timeline")
    async def command_contest_timeline(ctx):
        """Show contest timeline with Discord timestamps."""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        current_time = utcnow().timestamp()
        
        # Determine current period indicator
        def get_period_indicator(start: int, end: int) -> str:
            if start <= current_time < end:
                return "⏩ **ACTIVE**"
            elif current_time >= end:
                return "✅ Completed"
            else:
                return "⏳ Upcoming"
        
        # Define periods with their icons and schedule references
        periods = [
            ("🖼️ Submission Period", contest.schedule.submission_period),
            ("🗳️ Qualification Period", contest.schedule.qualif_period),
            ("⭐ Semifinals Period", contest.schedule.semis_period),
            ("🏆 Final Period", contest.schedule.final_period),
        ]
        
        # Build timeline by iterating through periods
        timeline_parts = ["**📅 Contest Timeline**\n"]
        for name, period in periods:
            indicator = get_period_indicator(period.start, period.end)
            timeline_parts.append(
                f"{name} {indicator}\n"
                f"  • Start: <t:{int(period.start)}:F>\n"
                f"  • End: <t:{int(period.end)}:F>\n"
                f"  • Relative: <t:{int(period.start)}:R> → <t:{int(period.end)}:R>\n"
            )
        
        timeline_parts.append(f"⏰ Current time: <t:{int(current_time)}:F>")
        timeline = "\n".join(timeline_parts)
        
        await ctx.send(timeline)
    
    @bot.command(name="contest_goto")
    async def command_contest_goto(ctx: commands.Context, period: str):
        f"""Transition to a specific contest period.
        
        Usage: {constantes.prefixVolt}contest_goto submission|qualif|semis|final|idle
        """
        global contest, current_period
        
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        period = period.lower()
        valid_periods = ["submission", "qualif", "semis", "final", "idle"]
        
        if period not in valid_periods:
            await ctx.send(f"❌ Invalid period. Choose from: {', '.join(valid_periods)}", delete_after=10)
            return
        
        old_period = current_period
        
        # Map period names to ContestPeriod enum
        period_map = {
            "submission": ContestPeriod.SUBMISSION,
            "qualif": ContestPeriod.QUALIF,
            "semis": ContestPeriod.SEMIS,
            "final": ContestPeriod.FINAL,
            "idle": ContestPeriod.IDLE
        }
        
        target_period = period_map[period]
        
        # Manually trigger the transition logic
        if old_period != target_period:
            # Close the previous period
            if old_period:
                await close_period(old_period, bot)
            
            current_period = target_period
            
            # Setup the new period
            await setup_period(target_period, old_period, bot)
            
            await ctx.send(f"✅ Transition complete! **{old_period.value.upper() if old_period else 'NONE'}** → **{target_period.value.upper()}**")
        else:
            await ctx.send(f"ℹ️ Already in **{target_period.value.upper()}** period.")
    
    @bot.command(name="contest_next")
    async def command_contest_next(ctx):
        """Advance to the next contest period."""
        global contest, current_period
        
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        old_period = current_period
        
        # Determine next period
        period_sequence = [
            ContestPeriod.IDLE,
            ContestPeriod.SUBMISSION,
            ContestPeriod.QUALIF,
            ContestPeriod.SEMIS,
            ContestPeriod.FINAL,
            ContestPeriod.IDLE
        ]
        
        try:
            current_index = period_sequence.index(current_period if current_period else ContestPeriod.IDLE)
            next_period = period_sequence[current_index + 1]
        except (ValueError, IndexError):
            await ctx.send("❌ Cannot advance from current period.")
            return
        
        # Close the previous period
        if old_period:
            await close_period(old_period, bot)
        
        current_period = next_period
        
        # Setup the new period
        await setup_period(next_period, old_period, bot)
        
        await ctx.send(f"✅ Advanced to next period! **{old_period.value.upper() if old_period else 'NONE'}** → **{next_period.value.upper()}**")
    
    @bot.command(name="contest_reset")
    async def command_contest_reset(ctx: commands.Context, confirm: str = ""):
        f"""Reset the contest to initial state. Requires confirmation.
        
        Usage: {constantes.prefixVolt}contest_reset CONFIRM
        """
        global contest
        
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        if confirm != "CONFIRM":
            await ctx.send(f"⚠️ This will reset ALL contest data. To confirm, use: `{constantes.prefixVolt}contest_reset CONFIRM`", delete_after=10)
            return
        
        # Create a fresh contest with the same channels
        channel_ids = [comp.channel_id for comp in contest.submission_competitions]
        current_schedule = contest.schedule  # Keep the current schedule
        
        contest = make_contest(channel_ids, current_schedule)
        contest.save("photo_contest/contest2026.yaml")
        
        await ctx.send("✅ Contest has been reset! All submissions, votes, and competition data cleared.")
    
    @bot.command(name="contest_clear_votes")
    async def command_contest_clear_votes(ctx: commands.Context, confirm: str = ""):
        f"""Clear all votes while keeping submissions. Requires confirmation.
        
        Usage: {constantes.prefixVolt}contest_clear_votes CONFIRM
        """
        global contest
        
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        if confirm != "CONFIRM":
            await ctx.send(f"⚠️ This will clear ALL votes (jury & public). To confirm, use: `{constantes.prefixVolt}contest_clear_votes CONFIRM`", delete_after=10)
            return
        
        # Clear all votes from all competitions
        votes_cleared = 0
        for comp in contest.competitions:
            votes_cleared += len(comp.votes_jury) + len(comp.votes_public)
            comp.votes_jury.clear()
            comp.votes_public.clear()
        
        contest.save("photo_contest/contest2026.yaml")
        logger.info(f"All votes cleared by admin: <@{ctx.author.id}> ({votes_cleared} votes removed)")
        
        await ctx.send(f"✅ All votes have been cleared! ({votes_cleared} votes removed)")
    
    @bot.command(name="contest_help")
    async def command_contest_help(ctx: commands.Context):
        """Show all available admin contest commands."""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        help_text = (
            "**🛠️ Admin Contest Commands**\n\n"
            f"**`{constantes.prefixVolt}contest_status`** - Show current period and full schedule\n"
            f"**`{constantes.prefixVolt}contest_timeline`** - Show timeline with Discord timestamps (localized)\n"
            f"**`{constantes.prefixVolt}contest_next`** - Advance to the next period\n"
            f"**`{constantes.prefixVolt}contest_goto <period>`** - Jump to a specific period (submission/qualif/semis/final/idle)\n"
            f"**`{constantes.prefixVolt}contest_clear_votes CONFIRM`** - Clear all votes (keeps submissions)\n"
            f"**`{constantes.prefixVolt}contest_reset CONFIRM`** - Reset all contest data (requires CONFIRM)\n\n"
            "**Examples:**\n"
            f"`{constantes.prefixVolt}contest_next` - Move from submission to qualif\n"
            f"`{constantes.prefixVolt}contest_goto qualif` - Jump directly to qualification period\n"
            f"`{constantes.prefixVolt}contest_clear_votes CONFIRM` - Clear all votes for re-testing\n"
            f"`{constantes.prefixVolt}contest_goto idle` - Return to idle state"
        )
        
        await ctx.send(help_text)

    return bot, constantes.TOKENVOLT


if __name__ == "__main__":  # to run the bot
    bot, token = main()

    loop = asyncio.get_event_loop()
    loop.create_task(bot.start(token))
    loop.run_forever()