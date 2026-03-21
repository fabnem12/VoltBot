import asyncio
from copy import deepcopy
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Literal, Optional

import logging
import nextcord as discord
import random
import re
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
    gen_photo_vote_details,
    gen_final_photo_vote_details,
    gen_winner_announcement_board,
)

class ContestPeriod(Enum):
    IDLE = "idle"
    SUBMISSION = "submission"
    QUALIF = "qualif"
    SEMIS = "semis"
    FINAL = "final"


async def edit_interaction_or_dm(interaction: discord.Interaction, content: Optional[str] = None, view: Optional[discord.ui.View] = None, user: Optional[discord.User | discord.Member] = None):
    """Try to edit the interaction message, if it fails (e.g., NotFound), send a DM to the user instead."""
    try:
        await interaction.response.edit_message(content=content, view=view)
    except discord.NotFound:
        # Interaction expired or message not found, send DM instead
        if user is None:
            user = interaction.user
        if user and content:
            try:
                await user.send(content)
            except Exception as e:
                print(f"Failed to send DM to user {user.id}: {e}")
    except Exception as e:
        # Other errors - try DM as fallback
        print(f"Error editing interaction message: {e}")
        if user is None:
            user = interaction.user
        if user and content:
            try:
                await user.send(content)
            except Exception:
                pass


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
else:
    # Regular contest schedule - starts March 1st, 2026 at 8AM CET (7AM UTC)
    start_time = datetime(2026, 3, 1, 7, 0)  # 7AM UTC = 8AM CET

    submission_period = Period(
        start=int(start_time.timestamp()),
        end=int((start_time + timedelta(days=7, hours=12)).timestamp()),
    )
    qualif_period = Period(
        start=int((start_time + timedelta(days=8)).timestamp()),
        end=int((start_time + timedelta(days=11, hours=12)).timestamp()),
    )
    semis_period = Period(
        start=int((start_time + timedelta(days=12)).timestamp()),
        end=int((start_time + timedelta(days=15, hours=12)).timestamp()),
    )
    final_period = Period(
        start=int((start_time + timedelta(days=16)).timestamp()),
        end=int((start_time + timedelta(days=20, hours=12)).timestamp()),
    )

    schedule = Schedule(
        submission_period=submission_period,
        qualif_period=qualif_period,
        semis_period=semis_period,
        final_period=final_period,
    )

    contest = make_contest(
        [
            1474888640214859927,
            1474889002044752182,
            1474889133972389999,
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
                    id2name[submission.author_id] = member.name
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
                        id2name[voter_id] = member.name
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
    
    cdn_url = uploaded_message.attachments[0].url
    logger.info(f"Uploaded file to save channel, CDN URL: {cdn_url}")
    
    return cdn_url


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


def reload_contest() -> Contest:
    """Reload contest from disk to get latest state, avoiding race conditions.
    
    When multiple votes are submitted concurrently, each handler should call this
    before saving to ensure they work with the most recent saved version.
    """
    return Contest.from_file("photo_contest/contest2026.yaml")


async def send_dm_safe(user: discord.User | discord.Member, content: str = "", embed: Optional[discord.Embed] = None, view: Optional[discord.ui.View] = None, file: Optional[discord.File] = None) -> bool:
    """Safely send a DM to a user, handling Forbidden and NotFound errors.
    
    Args:
        user: The user to send the DM to
        content: Text content to send (optional)
        embed: Embed to send (optional)
        view: View with buttons/components (optional)
        file: File to attach (optional)
    
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
        if file:
            kwargs["file"] = file
        await user.send(**kwargs)
        return True
    except (discord.Forbidden, discord.NotFound):
        return False


def _create_reference(message: discord.Message) -> discord.MessageReference:
    """Create a MessageReference for replying to a message."""
    return discord.MessageReference(message_id=message.id, channel_id=message.channel.id)


async def _safe_delete(message: discord.Message) -> None:
    """Safely delete a message, ignoring if already deleted or no permission."""
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden):
        pass


async def _send_error(message: discord.Message, error_text: str) -> None:
    """Send an error message to a channel with proper reference."""
    await message.channel.send(error_text, delete_after=30, reference=_create_reference(message))


async def download_missing_pictures():
    """Download pictures from Discord CDN for submissions that don't have local copies.
    
    This function is called on startup to ensure all submissions have local copies
    of their images, which may be missing due to:
    - Bot restart on a different machine
    - Local files being lost/deleted
    - Contest data restored from backup
    """
    global contest
    
    print("Checking for missing local pictures...")
    downloaded_count = 0
    error_count = 0
    
    for submission in contest.submissions:
        # Check if local file is missing or path is empty
        should_download = (
            not submission.local_save_path or 
            not os.path.exists(submission.local_save_path)
        )
        
        if should_download and submission.discord_save_path:
            try:
                # Use existing local_save_path if available, otherwise generate one
                local_filename = submission.local_save_path
                
                # Download from Discord CDN
                print(f"Downloading missing picture: {local_filename}")
                response = requests.get(submission.discord_save_path, timeout=10)
                
                if response.status_code == 200:
                    # Save locally
                    with open(local_filename, "wb") as f:
                        f.write(response.content)
                    
                    # Update submission's local_save_path
                    downloaded_count += 1
                    print(f"✓ Successfully downloaded: {local_filename}")
                else:
                    print(f"✗ Failed to download (HTTP {response.status_code}): {submission.discord_save_path}")
                    error_count += 1
                    
            except Exception as e:
                print(f"✗ Error downloading picture from {submission.discord_save_path}: {e}")
                error_count += 1
    
    if downloaded_count > 0:
        # Save contest with updated local paths
        contest.save("photo_contest/contest2026.yaml")
        print(f"Downloaded {downloaded_count} missing picture(s)")
    
    if error_count > 0:
        print(f"Failed to download {error_count} picture(s)")
    
    if downloaded_count == 0 and error_count == 0:
        print("All pictures already present locally")


async def close_period(period: ContestPeriod, bot: discord.Client):
    """Close a contest period by running period-specific cleanup.
    
    At the end of each period, prepares the next period by posting photos and reactions.
    The actual period start announcements happen in setup_period().
    
    Args:
        period: The period to close
        bot: Discord client
    """
    if period == ContestPeriod.SUBMISSION:
        # End of submission period: prepare qualification period
        await prep_qualif_period(bot)
    elif period == ContestPeriod.QUALIF:
        # Close qualif period and prepare semis period
        await close_qualif_period(bot)
        await prep_semis_period(bot)
    elif period == ContestPeriod.SEMIS:
        # Close semis period and prepare final period
        await close_semis_period(bot)
        await prep_final_period(bot)
    elif period == ContestPeriod.FINAL:
        await close_final_period(bot)


async def setup_period(new_period: ContestPeriod, old_period: Optional[ContestPeriod], bot: discord.Client):
    """Setup a new contest period by running period-specific initialization.
    
    Note: Photo posting is done in close_period() at the end of the previous period.
    This function handles period start announcements only.
    
    Args:
        new_period: The period to enter
        old_period: The previous period (for context)
        bot: Discord client
    """
    global contest
    
    logger.info(f"Setting up period: {new_period.value} (previous: {old_period.value if old_period else 'None'})")
    
    if new_period == ContestPeriod.QUALIF:
        await notify_period_start(bot, ContestPeriod.QUALIF)
    elif new_period == ContestPeriod.SEMIS:
        await notify_period_start(bot, ContestPeriod.SEMIS)
    elif new_period == ContestPeriod.FINAL:
        await notify_period_start(bot, ContestPeriod.FINAL)
    elif new_period == ContestPeriod.IDLE and old_period == ContestPeriod.FINAL:
        await announce_final_results(bot)
        await announce_final_boards(bot)
        await notify_final_results(bot, contest)
        await announce_individual_vote_boards(bot)
    
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
        
        await edit_interaction_or_dm(interaction, view=self)
    
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


async def submit(message: discord.Message, bot: discord.Client) -> Contest:
    """Handles a submission from a user.
    
    Takes a message with an image attachment or a link, processes it, and recorded the submission.
    
    The process consists of downloading the image locally, uploading it to a designated channel for submissions, recovering the link to the uploaded image, and storing the submission in the contest data.
    
    Args:
        message: The message containing the submission
        bot: The Discord bot client
    
    Returns:
        Updated contest object
    """
    global contest
    
    # Check the channel and thread to see if they are valid for submissions
    channel_id, thread_id = get_channel_and_thread(message)
    
    if (channel_id, thread_id) not in contest.channel_threads_open_for_submissions:
        return contest
    
    # Enforce deadline: only accept submissions during submission period
    current_timestamp = utcnow().timestamp()
    if not (contest.schedule.submission_period.start <= current_timestamp < contest.schedule.submission_period.end):
        ref = discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        )
        await message.channel.send(
            f"⏰ Submissions are not currently open. Please wait for the submission period to begin.",
            delete_after=30,
            reference=ref,
        )
        return contest
    
    # Enforce photo limit: maximum 6 photos per author per category
    if not contest.can_user_submit(channel_id, thread_id, message.author.id):
        ref = discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        )
        await message.channel.send(
            f"❌ You have reached the maximum limit of **{contest.MAX_SUBMISSIONS_PER_CATEGORY} photos per category**.\n\n"
            f"💡 **Tip:** You can withdraw a previous submission by reacting with ❌ to it, then submit a new photo.",
            delete_after=30,
            reference=ref,
        )
        # Delete the attempted submission message
        await _safe_delete(message)
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
                delete_after=30,
                reference=ref,
            )
            return contest
        else:
            submission_url = url
            # Try to extract extension from URL
            file_extension = os.path.splitext(url.split('?')[0])[1].lower()
            if not file_extension or file_extension not in allowed_extensions:
                await _send_error(message, "Invalid image format. Only static images in JPG, JPEG, PNG, or WebP formats are accepted. Animated images (GIF) are not allowed.")
                return contest
    else:
        attachment = message.attachments[0]
        submission_url = attachment.url
        # Get extension from attachment filename
        file_extension = os.path.splitext(attachment.filename)[1].lower()
        if not file_extension or file_extension not in allowed_extensions:
            await _send_error(message, "Invalid image format. Only static images in JPG, JPEG, PNG, or WebP formats are accepted. Animated images (GIF) are not allowed.")
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
    confirmation_msg = await message.channel.send(
        content=rules_text,
        view=confirmation_view,
        reference=ref,
        delete_after=60  # Delete after 1 minute if not interacted with
    )
    
    # Wait for user to confirm or cancel
    await confirmation_view.wait()
    
    if not confirmation_view.confirmed:
        # User cancelled or timed out - delete the original message
        await _safe_delete(message)
        # Note: confirmation_msg has delete_after=60, so it will auto-delete. Don't try to delete it manually.
        return contest
    
    # User confirmed - delete the confirmation message immediately
    try:
        await confirmation_msg.delete()
    except (discord.errors.NotFound, discord.errors.Forbidden):
        pass  # Message already deleted or no permission to delete
    
    # Download the image locally
    response = requests.get(submission_url)
    if response.status_code != 200:
        ref = discord.MessageReference(
            message_id=message.id, channel_id=message.channel.id
        )
        await message.channel.send(
            f"Failed to download the image from the provided URL. Please check the link and try again.",
            delete_after=30,
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
            delete_after=30,
            reference=discord.MessageReference(
                message_id=message.id, channel_id=message.channel.id
            ),
        )
        # Notify organizer
        await notify_organizer_error(bot, f"Failed to upload submission from user <@{message.author.id}> in <#{channel_id}>. Error: {e}")
        return contest
    
    # Create the submission object
    submission = Submission(author_id=message.author.id, submission_time=round(utcnow().timestamp()), local_save_path=local_filename, discord_save_path=uploaded_url)
    
    # Add submission with placeholder message_id to reserve its index
    # Using global contest variable - asyncio event loop ensures atomic execution
    contest = contest.add_submission(submission, channel_id, 0, thread_id)
    
    # Save immediately to persist the submission
    contest.save("photo_contest/contest2026.yaml")
    
    # Get the submission index that was just assigned
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        logger.error(f"Could not find competition after adding submission")
        return contest
    
    _, competition = res
    submission_number = len(competition.competing_entries)  # 1-indexed for display
    submission_index = submission_number - 1  # 0-indexed for data structure
    
    # Resend the message in the submission channel/thread
    if thread_id is not None:
        submission_channel = message.guild.get_channel(thread_id)
    else:
        submission_channel = message.guild.get_channel(channel_id)
    assert submission_channel is not None, "Submission channel not found"
    assert isinstance(submission_channel, (discord.TextChannel, discord.Thread)), "Submission channel is not a TextChannel or Thread"

    # Create embed with the uploaded image
    embed = discord.Embed()
    embed.set_image(url=uploaded_url)
    
    logger.info(f"Creating submission #{submission_number} with URL: {uploaded_url}")
    
    message_resend = await submission_channel.send(
        content=f"Submission #{submission_number}",
        embed=embed
    )
    
    # Update the contest with the real message_id
    contest = contest.set_message_id(channel_id, thread_id, submission_index, message_resend.id)
    contest.save("photo_contest/contest2026.yaml")
    
    # Delete the original message to maintain anonymity
    await _safe_delete(message)
    
    return contest


async def _perform_withdrawal(contest: Contest, message: Optional[discord.Message], channel_id: int, thread_id: Optional[int], bot: Optional[discord.Client] = None) -> Contest:
    """Core withdrawal logic - withdraws submission, renumbers messages, and saves contest.
    
    Args:
        contest: The contest object
        message: The Discord message of the submission (can be None if message already deleted)
        channel_id: The channel ID
        thread_id: The thread ID (None for main channels)
        bot: The discord bot client (required if message is None)
    
    Returns:
        Updated contest object
    """
    # Get submission index for message renumbering
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if not res:
        return contest
    _, competition = res
    
    # Find the message_id to withdraw
    if message is not None:
        message_id = message.id
    else:
        # Find the message_id from the competition that was at this position
        # We need to find which submission was at the current index before withdrawal
        message_id = None
        for msg_id, idx in competition.msg_to_sub.items():
            if idx == 0:  # Get the first submission to determine which one to withdraw
                message_id = msg_id
                break
        if message_id is None:
            return contest
    
    submission_index = competition.msg_to_sub[message_id]
    
    # Withdraw the submission
    contest = contest.withdraw_submission(channel_id, message_id, thread_id)
    
    # Update the message numbers for all subsequent submissions
    # Get the updated competition
    res = contest.competition_from_channel_thread(channel_id, thread_id)
    if res:
        _, updated_competition = res
        
        # Get the channel for editing messages
        channel = None
        if message is not None:
            channel = message.channel
        elif bot is not None:
            # Need to fetch the channel from the guild
            for guild in bot.guilds:
                channel = guild.get_channel(channel_id)
                if channel:
                    break
        
        if channel:
            # For each submission with a higher index, update their message
            for msg_id, idx in updated_competition.msg_to_sub.items():
                if idx >= submission_index:  # All submissions that were after the withdrawn one
                    try:
                        assert isinstance(channel, (discord.TextChannel, discord.Thread)), "Channel is not a TextChannel or Thread"
                        msg = await channel.fetch_message(msg_id)
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
    
    # Check if this message is a submission (prefer submission period competitions)
    if not contest.is_submission_message(channel_id, thread_id, message.id, prefer_type="submission"):
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
    await _safe_delete(message)
    
    return contest


class JuryConfirmView(discord.ui.View):
    """Confirmation view for jury voting."""
    
    def __init__(self, ranking: list[Submission], ranking_text: str, user_id: int, channel_id: int, thread_id: Optional[int], contest: Contest, voter_id_for_save: Optional[int] = None, period: Optional[str] = None):
        super().__init__(timeout=600)  # 10 minutes timeout
        self.ranking = ranking
        self.ranking_text = ranking_text
        self.user_id = user_id
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.contest = contest
        # If provided, use this id to save the vote instead of the DM recipient
        self.voter_id_for_save = voter_id_for_save if voter_id_for_save is not None else user_id
        self.period = period
    
    @discord.ui.button(label="Confirm Vote", style=discord.ButtonStyle.success)
    async def confirm_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This vote is not yours!", ephemeral=True)
            return
        
        global contest
        try:
            contest = reload_contest()
            save_vid = self.voter_id_for_save
            contest = contest.save_jury_vote(self.channel_id, self.thread_id, save_vid, self.ranking, period=self.period)
            contest.save("photo_contest/contest2026.yaml")
            logger.info(f"Jury vote saved: user={save_vid}, channel={self.channel_id}, thread={self.thread_id}")
            await interaction.user.send(f"{self.ranking_text}\n\n✅ Your vote has been saved successfully!")
        except ValueError as e:
            await interaction.user.send(f"{self.ranking_text}\n\n❌ Error saving vote: {e}")
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not interaction.user:
            return
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This vote is not yours!", ephemeral=True)
            return
        
        await interaction.user.send(f"{self.ranking_text}\n\n❌ Vote cancelled.")


class JuryVotingView(discord.ui.View):
    """Interactive view for jury voting with buttons for each submission."""
    
    def __init__(self, submissions: list[Submission], dm_user_id: int, channel_id: int, thread_id: Optional[int], contest: Contest, ranking_length: int = 10, submission_numbers: dict[Submission, int] | None = None, voter_id_for_save: Optional[int] = None, period: Optional[str] = None):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.submissions = submissions
        self.user_id = dm_user_id
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.contest = contest
        self.ranking: list[Submission] = []
        self.ranking_length = ranking_length  # Number of submissions to rank (10 for qualif/semis, 5 for finals)
        self.submission_numbers = submission_numbers or {}
        self.voter_id_for_save = voter_id_for_save if voter_id_for_save is not None else self.user_id
        self.period = period
        
        # Create buttons for each submission (label with original submission number)
        for i in range(len(submissions)):
            sub = submissions[i]
            display_num = self.submission_numbers.get(sub, i + 1)
            button = discord.ui.Button(
                label=f"Submission #{display_num}",
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
                f"#{i+1} Submission #{self.submission_numbers.get(sub, self.submissions.index(sub)+1)}"
                for i, sub in enumerate(self.ranking)
            )
            
            if len(self.ranking) < self.ranking_length:
                await edit_interaction_or_dm(
                    interaction,
                    content=f"**Your current ranking ({len(self.ranking)}/{self.ranking_length}):**\n{ranking_text}\n\nClick buttons to continue building your top {self.ranking_length}.",
                    view=self
                )
            else:
                # Ranking is complete, show confirmation
                confirm_view = JuryConfirmView(self.ranking, ranking_text, self.user_id, self.channel_id, self.thread_id, self.contest, voter_id_for_save=self.voter_id_for_save, period=self.period)
                await edit_interaction_or_dm(
                    interaction,
                    content=f"**Your final ranking:**\n{ranking_text}\n\nPlease confirm your vote.",
                    view=confirm_view
                )
        
        return callback


async def send_vote_reminder(user: discord.User | discord.Member, contest: Contest, channel_id: int, current_submissions: list[Submission], submission_numbers: dict[Submission, int], voter_id: Optional[int] = None):
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
    elif current_period == ContestPeriod.SEMIS:
        # Look at qualif votes (find the thread that fed into this semis)
        # Since multiple qualif threads feed into one semis, we need to check all qualif comps
        prev_comp = None
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
                vid = voter_id if voter_id is not None else user.id
                if vid in comp.votes_jury:
                    assert comp.thread_id is not None, "Qualif competition should have a thread_id"
                    all_qualif_jury_votes[comp.thread_id] = comp.votes_jury[vid]
                
                # Gather public votes from this qualif thread
                for vote in comp.votes_public:
                    if vote.voter_id == (voter_id if voter_id is not None else user.id) and vote.submission in current_submissions:
                        all_qualif_public_votes[vote.submission] = vote.nb_points
        
        # Also check if user already voted in semis
        semis_comp = None
        for comp in contest.semis_competitions:
            if comp.channel_id == channel_id:
                semis_comp = comp
                break
        if semis_comp:
            for vote in semis_comp.votes_public:
                if vote.voter_id == (voter_id if voter_id is not None else user.id) and vote.submission in current_submissions:
                    all_qualif_public_votes[vote.submission] = vote.nb_points
        
        # Send jury vote reminder - one line per thread
        if all_qualif_jury_votes:
            for thread_id, jury_vote in all_qualif_jury_votes.items():
                ranked_nums = []
                for sub in jury_vote.ranking:
                    if sub in current_submissions:
                        photo_num = submission_numbers.get(sub, "?")
                        ranked_nums.append(f"#{photo_num}")
                
                if ranked_nums:
                    ranking_str = " > ".join(ranked_nums)
                    await user.send(f"🗳️ **Your ranking in qualification thread / <#{thread_id}>:**\n{ranking_str}")
        
        # Send public vote reminder - show most recent votes (semis over qualif)
        if all_qualif_public_votes:
            reminder_lines = []
            for submission, nb_points in sorted(all_qualif_public_votes.items(), key=lambda x: x[1], reverse=True):
                photo_num = submission_numbers.get(submission, "?")
                plural = "s" if nb_points != 1 else ""
                reminder_lines.append(f"Submission #{photo_num}: **{nb_points} point{plural}**")
            
            await user.send("📝 **Your public votes:**\n" + "\n".join(reminder_lines))
    
    # For FINAL, check previous semis votes
    elif current_period == ContestPeriod.FINAL and prev_comp:
        # Check if voter had a jury vote in semis - one line format
        vid = voter_id if voter_id is not None else user.id
        if vid in prev_comp.votes_jury:
            jury_vote = prev_comp.votes_jury[vid]
            ranked_nums = []
            
            for sub in jury_vote.ranking:
                if sub in current_submissions:
                    photo_num = submission_numbers.get(sub, "?")
                    ranked_nums.append(f"#{photo_num}")
            
            if ranked_nums:
                ranking_str = " > ".join(ranked_nums)
                await user.send(f"🗳️ **Your ranking in semi-final:**\n{ranking_str}")
        
        # Check if user had public votes - get most recent (semis over qualif)
        public_vote_counts: dict[Submission, int] = {}
        
        # First get semis votes (more recent)
        for vote in prev_comp.votes_public:
            if vote.voter_id == user.id and vote.submission in current_submissions:
                public_vote_counts[vote.submission] = vote.nb_points
        
        # Then check qualif votes - only keep if no semis vote exists
        for comp in contest.qualif_competitions:
            if comp.channel_id == channel_id:
                for vote in comp.votes_public:
                    if vote.voter_id == user.id and vote.submission in current_submissions:
                        if vote.submission not in public_vote_counts:
                            public_vote_counts[vote.submission] = vote.nb_points
        
        if public_vote_counts:
            reminder_lines: list[str] = []
            for submission, nb_points in sorted(public_vote_counts.items(), key=lambda x: x[1], reverse=True):
                photo_num = submission_numbers.get(submission, "?")
                plural = "s" if nb_points != 1 else ""
                reminder_lines.append(f"Submission #{photo_num}: **{nb_points} point{plural}**")
            
            await user.send("📝 **Your public votes:**\n" + "\n".join(reminder_lines))


async def handle_jury_vote_request(contest: Contest, message: discord.Message, user: discord.Member | discord.User, bot: discord.Client, current_period: ContestPeriod, as_voter_id: Optional[int] = None):
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
    
    # Check if this is a valid competition channel/thread
    res = contest.competition_from_channel_thread(channel_id, thread_id, prefer_type=current_period.value)
    if not res:
        # Not a competition channel/thread - ignore the vote request silently
        return
    
    # Find the competition and get votable submissions
    voter_id_for_lookup = as_voter_id if as_voter_id is not None else user.id
    allow_own = (current_period == ContestPeriod.FINAL)
    votable_submissions, submission_numbers = contest.get_votable_submissions(
        channel_id, thread_id, voter_id_for_lookup, period=current_period.value, include_own=allow_own
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
        # Send all submission images in DM
        await user.send("📸 **Here are all the submissions for your review:**")

        for submission in votable_submissions:
            embed = discord.Embed()
            embed.set_image(url=submission.discord_save_path)
            
            await user.send(
                content=f"Submission #{submission_numbers[submission]}",
                embed=embed
            )

        # If voting in SEMIS or FINAL period, remind user of their previous votes
        if current_period in (ContestPeriod.SEMIS, ContestPeriod.FINAL):
            await send_vote_reminder(user, contest, channel_id, votable_submissions, submission_numbers, voter_id_for_lookup)

        # If voting in QUALIF period, show public votes in this thread
        if current_period == ContestPeriod.QUALIF:
            for comp in contest.qualif_competitions:
                if comp.thread_id == thread_id:
                    # Show public votes in this thread
                    user_public_votes: dict[Submission, int] = {}
                    for vote in comp.votes_public:
                        if vote.voter_id == voter_id_for_lookup:
                            user_public_votes[vote.submission] = vote.nb_points

                    if user_public_votes:
                        lines = []
                        for sub, pts in sorted(user_public_votes.items(), key=lambda x: x[1], reverse=True):
                            num = submission_numbers.get(sub, "?")
                            plural = "s" if pts != 1 else ""
                            lines.append(f"Submission #{num}: **{pts} point{plural}**")
                        await user.send("📝 **Your public votes in this thread:**\n" + "\n".join(lines))

                    # Show jury vote if already cast in this thread
                    if voter_id_for_lookup in comp.votes_jury:
                        jury_vote = comp.votes_jury[voter_id_for_lookup]
                        lines = []
                        for i, sub in enumerate(jury_vote.ranking, 1):
                            num = submission_numbers.get(sub, "?")
                            lines.append(f"#{i}: Submission #{num}")
                        await user.send("🗳️ **Your current jury ranking in this thread:**\n" + "\n".join(lines))
                    break

        # Send jury oath AFTER the submissions and reminders, before the voting interface
        await user.send(
            "⚖️ **Jury Oath**\n\n"
            "As a jury member, I pledge to:\n"
            "• Evaluate submissions based solely on photographic quality\n"
            "• Judge fairly and impartially\n"
            "• Consider composition, technical execution, creativity, and artistic merit\n"
            "• Set aside personal biases and vote with integrity\n\n"
            "Thank you for your commitment to maintaining the quality of this contest. 🎖️"
        )

        # Send the voting interface
        # DM recipient is `user`; use `voter_id_for_lookup` for saving when voting-as
        view = JuryVotingView(votable_submissions, user.id, channel_id, thread_id, contest, ranking_length, submission_numbers, voter_id_for_save=voter_id_for_lookup, period=current_period.value)
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
    
    # Get channel and thread
    channel_id, thread_id = get_channel_and_thread(message)
    
    # Find the competition, preferring the current period type
    period_type = current_period.value if current_period != ContestPeriod.IDLE else None
    res = contest.competition_from_channel_thread(channel_id, thread_id, prefer_type=period_type)
    
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
        # Reload contest to avoid race condition with concurrent votes
        contest = reload_contest()
        # Save the vote
        contest = contest.save_public_vote(channel_id, thread_id, user.id, points, submission, period=current_period.value)
        contest.save("photo_contest/contest2026.yaml")
        logger.info(f"Public vote saved: user={user.id}, points={points}, channel={channel_id}, thread={thread_id}")
        
        # Send confirmation DM to the user
        plural = "points" if points != 1 else "point"
        embed = discord.Embed(
            title="✅ Vote Saved!",
            description=f"You gave **{points} {plural}** to this photo.",
            color=0x57F287  # Green
        )
        embed.set_image(url=submission.discord_save_path)
        await send_dm_safe(user, embed=embed)
        
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
    # Enforce deadline: allow commentary during qualif and semis periods
    if current_period not in (ContestPeriod.QUALIF, ContestPeriod.SEMIS):
        try:
            await user.send("⏰ Commentary is only available during the qualification or semi-finals periods.")
        except discord.Forbidden:
            pass
        try:
            await message.remove_reaction("💬", user)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return
    
    # Get channel and thread
    channel_id, thread_id = get_channel_and_thread(message)
    
    # Find the competition, preferring the semis competition when available
    # to avoid matching the original submission competition (which can
    # share the same channel/thread).
    # Fall back to qualif if semis not available
    res = contest.competition_from_channel_thread(channel_id, thread_id, prefer_type="semis")
    if not res:
        res = contest.competition_from_channel_thread(channel_id, thread_id, prefer_type="qualif")
    if not res:
        return

    _, competition = res
    
    # Check if this message is a photo submission
    if message.id not in competition.msg_to_sub:
        return
    
    # Derive submission using the competition helper to avoid index mismatches
    submission = competition.get_submission_from_message(message.id)
    if submission is None:
        return
    submission_index = competition.msg_to_sub[message.id]
    
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
                await update_commentary_summary(contest, submission, message.guild)
                
                add_qualif = "\n\n💡 Summaries of all commentaries will be revealed during the Semi-Finals." if current_period == ContestPeriod.QUALIF else ""
                
                try:
                    await interaction.response.send_message(
                        "✅ Your commentary has been recorded! Thank you for your feedback." + add_qualif,
                        ephemeral=True
                    )
                except discord.NotFound:
                    # Interaction expired/unknown, try DM fallback
                    try:
                        await interaction.user.send(
                            "✅ Your commentary has been recorded! Thank you for your feedback." + add_qualif
                        )
                    except Exception as e:
                        print(f"Failed to send DM to user {interaction.user.id}: {e}")
                
            except ValueError as e:
                # Validation failed
                try:
                    await interaction.response.send_message(
                        f"❌ {str(e)}",
                        ephemeral=True
                    )
                except discord.NotFound:
                    # Interaction expired/unknown, try DM fallback
                    try:
                        await interaction.user.send(f"❌ {str(e)}")
                    except Exception as e:
                        print(f"Failed to send DM to user {interaction.user.id}: {e}")
    
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
    embed = discord.Embed(
        title=f"Add Commentary for Photo #{submission_index + 1}",
        description="Click the button below to open the commentary form.",
        color=0x7289DA
    )
    embed.set_image(url=submission.discord_save_path)
    dm_sent = await send_dm_safe(
        user,
        content="",
        embed=embed,
        view=view
    )
    
    if not dm_sent:
        # User has DMs disabled, try to send in channel
        try:
            fallback_embed = discord.Embed(
                title=f"Add Commentary for Photo #{submission_index + 1}",
                description=f"{user.mention}, click the button below to add your commentary:",
                color=0x7289DA
            )
            fallback_embed.set_image(url=submission.discord_save_path)
            await message.channel.send(
                content="",
                embed=fallback_embed,
                view=view,
                delete_after=60
            )
        except:
            pass


async def update_commentary_summary(contest: Contest, submission: Submission, guild: discord.Guild):
    """Update the commentary summary messages for a specific submission.
    
    Args:
        contest: The contest object
        submission: The submission whose summary to update
        guild: The Discord guild
    """
    # Get the commentary summary
    summary_text = contest.get_commentary_summary(submission.discord_save_path) or ""
    
    # Get all posts for this submission
    posts = contest.get_submission_posts(submission.discord_save_path)
    
    for message_id, channel_id, thread_id, is_summary in posts:
        if not is_summary:
            continue
        
        # Get the channel
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            continue
        
        # Update the summary message
        try:
            summary_msg = await channel.fetch_message(message_id)
            
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
    """Helper function to announce results without posting photos (they're already in their channels).
    
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
    
    # For finals, there's a single combined competition so don't mention category
    if competition_type == "final":
        comp = contest.final_competition
        if not comp:
            return
        
        # Post announcement for the combined Grand Final
        await announcement_channel.send(f"🎊 **{stage_name} for the {next_stage_name}**\n")
        await announcement_channel.send(f"Check the category channels to see the finalists!")
    elif competition_type == "semis":
        await announcement_channel.send(f"**Vote in the {stage_name} to determine who advances to the {next_stage_name}!**\n\nCheck the category channels to see the qualifiers!")


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


async def announce_individual_vote_boards(bot: discord.Client):
    """Add individual vote details boards to submission embeds after the final.
    
    For each qualif and semis submission, generates an individual vote board showing
    how each jury member voted for that specific photo, uploads it to the save channel,
    and updates the submission embed to include the board as a thumbnail.
    """
    global contest
    
    print("Generating individual vote boards...")
    
    # Build id2name mapping
    id2name = await build_id2name_mapping(bot, contest)
    
    # Get jury voter authors for bonus display
    qualif_jury_voter_authors = contest.get_jury_voter_authors("qualif")
    semis_jury_voter_authors = contest.get_jury_voter_authors("semis")
    
    # Process qualif competitions - post in threads
    for comp in contest.qualif_competitions:
        category_channel = bot.get_channel(comp.channel_id)
        category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
        
        # Determine target channel (thread or category channel)
        if comp.thread_id:
            target_channel = await bot.fetch_channel(comp.thread_id)
            thread_name = target_channel.name if isinstance(target_channel, discord.Thread) else None
        else:
            target_channel = category_channel
            thread_name = None
        
        assert isinstance(target_channel, (discord.TextChannel, discord.Thread)), "Target channel must be a text channel or thread"
        
        # Generate individual vote board for each submission
        for i, submission in enumerate(comp.competing_entries):
            # Generate the individual vote board
            board_path = gen_photo_vote_details(submission, comp, category_name, id2name, thread_name, jury_voter_authors=qualif_jury_voter_authors)
            
            # Upload to save channel for permanent URL
            try:
                board_url = await upload_to_save_channel(
                    target_channel.guild,
                    board_path,
                    f"Vote details - {category_name}" + (f" - {thread_name}" if thread_name else "") + f" - Photo #{i+1}"
                )
            except RuntimeError as e:
                print(f"Could not upload individual vote board: {e}")
                continue
            
            # Update the submission message to add the individual vote board
            message_id = None
            for msg_id, sub_idx in comp.msg_to_sub.items():
                if sub_idx == i:
                    message_id = msg_id
                    break
            
            if message_id and target_channel and isinstance(target_channel, (discord.TextChannel, discord.Thread)):
                try:
                    message = await target_channel.fetch_message(message_id)
                    # Update embed to include individual vote board as thumbnail
                    embed = discord.Embed()
                    embed.set_image(url=submission.discord_save_path)
                    embed.set_thumbnail(url=board_url)
                    await message.edit(
                        content=f"Submission #{i+1}",
                        embed=embed
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    print(f"Could not update message {message_id}: {e}")
    
    # Process semis competitions - post in category channels
    for comp in contest.semis_competitions:
        category_channel = await bot.fetch_channel(comp.channel_id)
        assert isinstance(category_channel, (discord.TextChannel, discord.Thread)), "Category channel must be a text channel or thread"
        category_name = category_channel.name
        
        # Generate individual vote board for each submission
        for i, submission in enumerate(comp.competing_entries):
            # Generate the individual vote board
            board_path = gen_photo_vote_details(submission, comp, category_name, id2name, None, jury_voter_authors=semis_jury_voter_authors)
            
            # Upload to save channel for permanent URL
            try:
                board_url = await upload_to_save_channel(
                    category_channel.guild,
                    board_path,
                    f"Vote details - {category_name} - Photo #{i+1}"
                )
            except RuntimeError as e:
                print(f"Could not upload individual vote board: {e}")
                continue
            
            # Update the submission message to add the individual vote board
            message_id = None
            for msg_id, sub_idx in comp.msg_to_sub.items():
                if sub_idx == i:
                    message_id = msg_id
                    break
            
            if message_id and category_channel and isinstance(category_channel, (discord.TextChannel, discord.Thread)):
                try:
                    message = await category_channel.fetch_message(message_id)
                    # Update embed to include individual vote board as thumbnail
                    embed = discord.Embed()
                    embed.set_image(url=submission.discord_save_path)
                    embed.set_thumbnail(url=board_url)
                    await message.edit(
                        content=f"Submission #{i+1}",
                        embed=embed
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    print(f"Could not update message {message_id}: {e}")
    
    # Process final competition
    final_comp = contest.final_competition
    if final_comp:
        final_channel = bot.get_channel(final_channel_id)
        if final_channel and isinstance(final_channel, discord.TextChannel):
            for i, submission in enumerate(final_comp.competing_entries):
                board_path = gen_final_photo_vote_details(
                    submission, final_comp, id2name
                )
                
                try:
                    board_url = await upload_to_save_channel(
                        final_channel.guild,
                        board_path,
                        f"Vote details - Grand Final - Photo #{i+1}"
                    )
                except RuntimeError as e:
                    print(f"Could not upload final vote board: {e}")
                    continue
                
                message_id = final_comp.msg_to_sub.get(i)
                if message_id:
                    try:
                        message = await final_channel.fetch_message(message_id)
                        embed = discord.Embed()
                        embed.set_image(url=submission.discord_save_path)
                        embed.set_thumbnail(url=board_url)
                        await message.edit(
                            content=f"Submission #{i+1}",
                            embed=embed
                        )
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                        print(f"Could not update final message {message_id}: {e}")
    
    print("Individual vote boards complete!")


async def announce_final_results(bot: discord.Client, reveal_delay: int = 30):
    """Announce final results with Eurovision-style voting reveal with live boards.
    
    Args:
        bot: Discord client
        reveal_delay: Seconds to wait between revealing each voter's results (default: 30)
    """
    global contest
    
    # Get the announcement channel
    announcement_channel = bot.get_channel(announcement_channel_id)
    if not announcement_channel or not isinstance(announcement_channel, discord.TextChannel):
        print(f"Warning: Could not find announcement channel {announcement_channel_id}")
        return
    
    # Build id2name mapping
    id2name = await build_id2name_mapping(bot, contest, include_voters=True)
    
    # Get the single combined final competition
    final_comp = contest.final_competition
    if not final_comp:
        print("Warning: No final competition found")
        return
    
    await announcement_channel.send(f"\n\n🎤 **LIVE VOTING REVEAL - GRAND FINAL** 🎤\n")
    await announcement_channel.send(f"**{len(final_comp.votes_jury)} jury votes have been cast. Let's reveal them!**\n")
    
    # Generate and post boards using live reveal
    for board_path, voter_id, is_initial, is_final in gen_live_final_reveal(final_comp, "Grand Final", id2name):
        if is_initial:
            # Initial board with all zeros
            await announcement_channel.send(
                "📊 **Starting scoreboard:**",
                file=discord.File(board_path)
            )
        elif is_final:
            # Skip the final board from live reveal - we'll show the clean one below
            pass
        elif voter_id is not None:
            # Individual voter's contribution
            await announcement_channel.send(
                f"Thank you <@{voter_id}> 🎖️ for your votes!",
                file=discord.File(board_path)
            )
            await asyncio.sleep(reveal_delay)
    
    # Generate and post the final clean results board (without "+New" column)
    final_board_path = gen_final_results_board(contest, id2name)
    await announcement_channel.send(
        "🏆 **Final Results:**",
        file=discord.File(final_board_path)
    )
    
    # Announce the winner
    jury_scores = final_comp.count_votes_jury()
    
    # Check for tie
    max_score = max(jury_scores.values()) if jury_scores else 0
    tied_entries = [sub for sub, score in jury_scores.items() if score == max_score]
    is_tie = len(tied_entries) > 1
    
    if is_tie:
        # Announce tie
        tied_mentions = "\n".join(f"• Submission #{final_comp.competing_entries.index(sub) + 1}" for sub in tied_entries)
        await announcement_channel.send(
            f"⚖️ **There is a tie!** The following photos are tied with **{max_score} points** each:\n"
            f"{tied_mentions}\n\n"
            f"A tie-break vote will follow to determine the winner."
        )
    else:
        # No tie - announce winner
        winner = max(final_comp.competing_entries, key=lambda x: (jury_scores.get(x, 0), -x.submission_time))
        
        winner_embed = discord.Embed(
            description=f"**🎉 Congratulations, this photo wins the Grand Final! 🎉**"
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
        
        # Generate winner recap board
        winner_board_path = gen_winner_announcement_board(
            winner=winner,
            all_finalists=final_comp.competing_entries,
            final_scores=jury_scores,
            category_name="Grand Final",
            id2name=id2name,
            final_competition=final_comp,
            contest=contest,
        )
        await announcement_channel.send(
            "📸 **Winner Recap:**",
            file=discord.File(winner_board_path)
        )
        
        await announcement_channel.send("\n✨ **Contest complete! Thank you to all participants!** ✨")
    
    # Add individual vote boards to all submission embeds (after final winner announcement)
    await announce_individual_vote_boards(bot)


async def announce_final_boards(bot: discord.Client):
    """Post detailed results boards for all stages in their respective channels/threads."""
    global contest
    
    # Build id2name mapping
    id2name = await build_id2name_mapping(bot, contest)
    
    # Build channel_names dict for semifinals
    channel_names: Dict[int, str] = {}
    for comp in contest.semis_competitions:
        category_channel = bot.get_channel(comp.channel_id)
        channel_names[comp.channel_id] = getattr(category_channel, "name", f"Category {comp.channel_id}")
    
    # Generate and post qualif boards - post in their respective threads
    # Get jury voter authors for bonus display
    qualif_jury_voter_authors = contest.get_jury_voter_authors("qualif")
    
    for comp in contest.qualif_competitions:
        category_channel = bot.get_channel(comp.channel_id)
        category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
        
        # Get thread name and channel
        if comp.thread_id:
            thread = await bot.fetch_channel(comp.thread_id)
            assert isinstance(thread, discord.Thread), "Thread ID does not correspond to a thread channel"
            thread_name = thread.name
            target_channel = thread  # Post in thread
        else:
            thread_name = None
            target_channel = category_channel  # Post in category channel
        
        assert isinstance(target_channel, (discord.TextChannel, discord.Thread)), "Target channel must be a text channel or thread"
        
        board_path = gen_competition_board(comp, category_name, id2name, thread_name, qualif_jury_voter_authors)
        
        with open(board_path, "rb") as f:
            await target_channel.send(
                content=f"📊 **Qualification Results: {category_name}**" + (f" - {thread_name}" if thread_name else ""),
                file=discord.File(f, filename=os.path.basename(board_path))
            )
    
    # Generate and post semi-final boards - post in category channels
    semifinal_boards = gen_semifinals_boards(contest, channel_names, id2name)
    for comp, board_path in zip(contest.semis_competitions, semifinal_boards):
        category_channel = bot.get_channel(comp.channel_id)
        assert isinstance(category_channel, (discord.TextChannel, discord.Thread)), "Category channel must be a text channel or thread"
        
        with open(board_path, "rb") as f:
            await category_channel.send(
                content=f"📊 **Semi-Final Results**",
                file=discord.File(f, filename=os.path.basename(board_path))
            )
    
    # Generate and post final board - post in final channel
    final_channel = bot.get_channel(final_channel_id)
    if final_channel and isinstance(final_channel, discord.TextChannel):
        board_path = gen_final_results_board(contest, id2name)
        
        with open(board_path, "rb") as f:
            await final_channel.send(
                content="🏆 **Final Results** 🏆",
                file=discord.File(f, filename=os.path.basename(board_path))
            )
    
    announcement_channel = bot.get_channel(announcement_channel_id)
    if announcement_channel and isinstance(announcement_channel, (discord.TextChannel, discord.Thread)):
        await announcement_channel.send("\n✨ **Contest complete! See you next year!** ✨")


async def _send_final_vote_reminder_dms(bot: discord.Client, contest: Contest):
    """Send DMs to all users who voted earlier in the contest about the final voting."""
    
    final_channel = bot.get_channel(final_channel_id)
    if not final_channel or not isinstance(final_channel, discord.TextChannel):
        return
    
    vote_msg_id = None
    async for msg in final_channel.history(limit=20):
        if "Final Voting opens soon" in msg.content and msg.author == bot.user:
            vote_msg_id = msg.id
            break
    
    voter_ids: set[int] = set()
    
    for comp in contest.qualif_competitions:
        voter_ids.update(comp.votes_jury.keys())
        voter_ids.update(v.voter_id for v in comp.votes_public)
    
    for comp in contest.semis_competitions:
        voter_ids.update(comp.votes_jury.keys())
        voter_ids.update(v.voter_id for v in comp.votes_public)
    
    deadline = contest.schedule.final_period.end
    vote_link = f"{final_channel.jump_url}" + (f"/{vote_msg_id}" if vote_msg_id else "")
    
    for voter_id in voter_ids:
        try:
            user = await bot.fetch_user(voter_id)
            await user.send(
                f"🏆 **Grand Final voting is now open!**\n\n"
                f"Rank your top 5 out of the 15 finalists.\n"
                f"Vote here: {vote_link}\n\n"
                f"Voting deadline: <t:{int(deadline)}:F>"
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass


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
        # If qualification threads were created, announce inside each thread and ping ALL contestants
        if contest.qualif_competitions:
            # Build a mention list for ALL contestants (not just thread-specific)
            all_contestant_ids = contest.contestants
            if all_contestant_ids:
                contestant_mentions = " ".join(f"<@{user_id}>" for user_id in sorted(all_contestant_ids))
            else:
                contestant_mentions = ""

            for comp in contest.qualif_competitions:
                if not comp.thread_id:
                    continue
                try:
                    thread = await bot.fetch_channel(comp.thread_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue

                try:
                    assert isinstance(thread, discord.Thread), "Thread ID does not correspond to a thread channel"
                    await thread.send(
                        "🗳️ **QUALIFICATION VOTING HAS BEGUN!** 🗳️\n\n"
                        "Vote for your favorite photos in this thread to help them advance to the Semi-Finals!\n\n"
                        "**How to vote:**\n"
                        "• React with 0️⃣, 1️⃣, 2️⃣, or 3️⃣ on any photo to give it 0-3 public points\n"
                        "• React with 💬 on any photo to add commentary about composition, lighting, and artistic merit.\n"
                        "• React with 🗳️ to this message to cast your jury vote (top 10 ranking)\n\n"
                        f"Deadline: <t:{int(contest.schedule.qualif_period.end)}:F>\n"
                        + contestant_mentions
                    )
                except (discord.Forbidden, discord.HTTPException):
                    # ignore send failures per-thread
                    pass
        else:
            # Fallback to the announcement channel if no qualif threads exist yet
            contestant_mentions = " ".join(f"<@{user_id}>" for user_id in contest.contestants)
            await announcement_channel.send(
                "🗳️ **QUALIFICATION VOTING HAS BEGUN!** 🗳️\n\n"
                "Check the qualification threads and vote for your favorites!\n\n"
                "**How to vote:**\n"
                "• React with 0️⃣, 1️⃣, 2️⃣, or 3️⃣ on any photo to give it 0-3 public points\n"
                "• React with 💬 on any photo to add commentary about composition, lighting, and artistic merit\n"
                "• React with 🗳️ to this message to cast your jury vote (top 10 ranking)\n\n"
                f"Deadline: <t:{int(contest.schedule.qualif_period.end)}:F>\n"
                + contestant_mentions
            )
    elif period == ContestPeriod.SEMIS:
        await announcement_channel.send(
            "🌟 **SEMI-FINALS HAVE BEGUN!** 🌟\n\n"
            "Vote for the qualifiers in each category!\n"
            f"Deadline: <t:{int(contest.schedule.semis_period.end)}:F>"
        )
    elif period == ContestPeriod.FINAL:
        await announcement_channel.send(
            f"<@&727613272756453407>\n\n"
            "🏆 **THE GRAND FINAL OF VOLT'S PHOTO CONTEST HAS BEGUN!** 🏆\n\n"
            "15 finalists from all categories compete together!\n"
            "Vote for your top 5 out of 15 finalists! 🎉\n\n"
            f"Voting ends: <t:{int(contest.schedule.final_period.end)}:F>"
        )
        
        await _send_final_vote_reminder_dms(bot, contest)


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
    if competition_type == "final":
        competitions = [contest.final_competition] if contest.final_competition else []
    else:
        competitions = contest.semis_competitions
    for comp in competitions:
        # For finals, there's a single combined competition so don't mention category
        if stage_name == "Grand Final":
            for submission in comp.competing_entries:
                try:
                    user = await guild.fetch_member(submission.author_id)
                    if user:
                        description = f"🎉 Your photo has qualified for the **{stage_name}**!\n\nThis is the final round - good luck! 🍀"
                        
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
        else:
            # For semis, mention the category name
            category_channel = bot.get_channel(comp.channel_id)
            category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
            
            for submission in comp.competing_entries:
                try:
                    user = await guild.fetch_member(submission.author_id)
                    if user:
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
    """Send DM notifications to finalists with their final placement and individual boards.
    
    Args:
        bot: The Discord bot client
        contest: The contest object
    """
    guild = bot.get_guild(voltServer)
    if not guild:
        return
    
    medal_emojis = ["🥇", "🥈", "🥉"]

    users = dict()
    
    # Get the single combined final competition
    comp = contest.final_competition
    if not comp:
        return
    
    # Build id2name mapping for individual boards
    id2name = await build_id2name_mapping(bot, contest, include_voters=True)
    
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
    
    # Send DM to each finalist with their placement and individual board
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
                    title = f"{medal} GRAND FINAL WINNER!"
                    description = f"🎊 **Congratulations!** Your photo won **1st place** in the Grand Final!\n\n**Total Points:** {points}"
                    color = 0xFFD700  # Gold
                elif placement == 2:
                    medal = medal_emojis[1]
                    title = f"{medal} 2nd Place - Grand Final"
                    description = f"🎉 **Amazing!** Your photo placed **2nd** in the Grand Final!\n\n**Total Points:** {points}"
                    color = 0xC0C0C0  # Silver
                elif placement == 3:
                    medal = medal_emojis[2]
                    title = f"{medal} 3rd Place - Grand Final"
                    description = f"👏 **Great work!** Your photo placed **3rd** in the Grand Final!\n\n**Total Points:** {points}"
                    color = 0xCD7F32  # Bronze
                else:
                    title = f"Final Results - Grand Final"
                    description = f"Thank you for participating! Your photo placed **{placement}th** in the Grand Final.\n\n**Total Points:** {points}"
                    color = 0x7289DA  # Discord blurple
                
                embed = discord.Embed(
                    title=title,
                    description=description,
                    color=color
                )
                embed.set_image(url=submission.discord_save_path)
                
                # Generate individual result board
                board_path = gen_final_photo_vote_details(submission, comp, id2name)
                
                await send_dm_safe(
                    user,
                    embed=embed,
                    file=discord.File(board_path)
                )
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
    # Note: When recovering, we need to run both prep_* (post photos) and notify_* (announce)
    if target_period == ContestPeriod.QUALIF and not contest.qualif_competitions:
        print("Missed qualif setup, setting up now...")
        await prep_qualif_period(bot)
        await notify_period_start(bot, ContestPeriod.QUALIF)
    elif target_period == ContestPeriod.SEMIS and not contest.semis_competitions:
        print("Missed semis setup, setting up now...")
        await prep_semis_period(bot)
        await notify_period_start(bot, ContestPeriod.SEMIS)
    elif target_period == ContestPeriod.FINAL and not contest.final_competition:
        print("Missed final setup, setting up now...")
        await prep_final_period(bot)
        await notify_period_start(bot, ContestPeriod.FINAL)
    elif target_period == ContestPeriod.IDLE and current_timestamp > contest.schedule.final_period.end:
        # Check if we missed the final results announcement
        print("Contest has ended. Results may have been announced.")
    
    current_period = target_period
    print(f"Recovery complete. Current period: {current_period.value}")


async def close_qualif_period(bot: discord.Client):
    """Close qualification period by removing voting reactions and locking threads."""
    global contest
    
    print("Closing qualification period...")
    
    for comp in contest.qualif_competitions:
        if comp.thread_id:
            thread = await bot.fetch_channel(comp.thread_id)
            if thread and isinstance(thread, discord.Thread):
                try:
                    # Archive the thread
                    await thread.edit(archived=True, locked=True)
                except (discord.Forbidden, discord.HTTPException):
                    pass


async def close_semis_period(bot: discord.Client):
    """Close semi-finals period by removing voting reactions."""
    global contest
    
    print("Closing semi-finals period...")
    
    for comp in contest.semis_competitions:
        channel = await bot.fetch_channel(comp.channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                # Send closing message
                await channel.send("🔒 **Semi-Finals voting has ended!** Results will be announced soon.")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def announce_semis_winners(bot: discord.Client):
    """Announce the winners/qualifiers from each semi-final channel.
    
    Posts which photos qualified for the Grand Final in each semi-final category channel.
    """
    global contest
    
    # Get the finalists from the final competition (they were determined by solve_semis)
    final_comp = contest.final_competition
    if not final_comp:
        print("Warning: No final competition found")
        return
    
    # Build a set of finalist Discord paths for quick lookup
    finalist_paths = {sub.discord_save_path for sub in final_comp.competing_entries}
    
    # For each semi-final competition, announce which submissions qualified
    for comp in contest.semis_competitions:
        channel = await bot.fetch_channel(comp.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            continue
        
        # Get category name
        category_name = getattr(channel, "name", f"Category {comp.channel_id}")
        
        # Find which submissions in this semi qualified for the final
        qualifiers = [sub for sub in comp.competing_entries if sub.discord_save_path in finalist_paths]
        
        if qualifiers:
            # Post the qualifiers
            await channel.send(f"🎉 **Congratulations to our Grand Finalists from {category_name}!** 🎉")
            
            for sub in qualifiers:
                # Find the message ID for this submission
                msg_id = None
                for m_id, sub_idx in comp.msg_to_sub.items():
                    if sub_idx == comp.competing_entries.index(sub):
                        msg_id = m_id
                        break
                
                if msg_id:
                    try:
                        message = await channel.fetch_message(msg_id)
                        await channel.send(f"✅ **{category_name} qualifier**: {message.jump_url}")
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        # Fallback: just send the Discord path
                        await channel.send(f"✅ **{category_name} qualifier**: {sub.discord_save_path}")
                else:
                    await channel.send(f"✅ **{category_name} qualifier**: {sub.discord_save_path}")
        
        await channel.send(f"\n🏆 These {len(qualifiers)} photos will compete in the Grand Final!")


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


async def prep_qualif_period(bot: discord.Client):
    """Prepare qualification period: create threads and post submissions with voting reactions.
    
    Called at the END of the submission period to prepare for qualification voting.
    Voting will be announced and become valid at the START of the qualification period.
    """
    global contest
    
    # Get the number of threads needed per category
    thread_counts = contest.count_qualifs()
    
    # Create threads for each category (skip categories with < 25 submissions)
    all_thread_ids = []
    category_threads: dict[int, list[discord.Thread]] = {}  # Store thread objects for later use
    
    for comp, thread_count in zip(contest.submission_competitions, thread_counts):
        channel = await bot.fetch_channel(comp.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            print(f"Warning: Could not find channel {comp.channel_id}")
            all_thread_ids.append([])
            continue
        
        # Check if this category needs qualification threads
        if thread_count == 0:
            # Category has < 25 submissions, auto-qualify all to semis
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
        threads = []
        for i in range(thread_count):
            thread = await channel.create_thread(
                name=f"Qualification Thread {i+1}",
                type=discord.ChannelType.public_thread,
                auto_archive_duration=10080  # 7 days
            )
            thread_ids.append(thread.id)
            threads.append(thread)
        all_thread_ids.append(thread_ids)
        category_threads[comp.channel_id] = threads
        
        # Post message in category channel with links to threads
        category_name = getattr(channel, "name", f"Category {comp.channel_id}")
        thread_links = "\n".join(f"• <#{thread.id}>" for thread in threads)
        await channel.send(
            f"🗳️ **Qualification voting opens soon for {category_name}!**\n\n"
            f"Voting will begin at <t:{int(contest.schedule.qualif_period.start)}:F>\n\n"
            f"Check out the qualification threads below:\n"
            f"{thread_links}\n\n"
            f"Vote for your favorite photos to help them advance to the semi-finals!"
        )
    
    # Update contest with qualifications
    
    contest = contest.make_qualifs(all_thread_ids)
    contest.save("photo_contest/contest2026.yaml")
    # For categories that have qualification threads, remove the original
    # reposts in the main category channel to avoid duplicate posts.
    for comp in contest.qualif_competitions:
        if not comp.thread_id:
            continue
        for submission in comp.competing_entries:
            key = submission.discord_save_path
            posts = contest.get_submission_posts(key)
            # posts: list of tuples (message_id, channel_id, thread_id, is_summary)
            new_posts_dicts = []
            removed = False
            for (msg_id, ch_id, th_id, is_summary) in posts:
                if ch_id == comp.channel_id and th_id is None and not is_summary:
                    # Delete the original message in the category channel
                    try:
                        channel_obj = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                        if channel_obj:
                            try:
                                assert isinstance(channel_obj, discord.TextChannel), "Channel ID does not correspond to a text channel"
                                msg_obj = await channel_obj.fetch_message(msg_id)
                                await msg_obj.delete()
                            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                                pass
                    except Exception:
                        pass
                    removed = True
                    # omit this post from the new posts list
                else:
                    new_posts_dicts.append({
                        "message_id": msg_id,
                        "channel_id": ch_id,
                        "thread_id": th_id,
                        "is_summary": is_summary,
                    })

            if removed:
                # Update contest.submission_posts immutably
                copy_contest = deepcopy(contest)
                if new_posts_dicts:
                    copy_contest.submission_posts[key] = new_posts_dicts
                else:
                    copy_contest.submission_posts.pop(key, None)
                contest = copy_contest
    
    # Post submissions in their respective threads with voting reactions
    for comp in contest.qualif_competitions:
        assert comp.thread_id is not None, "Qualification competition missing thread_id"
        
        # Fetch the thread using bot.fetch_channel to ensure we have the latest version
        thread = await bot.fetch_channel(comp.thread_id)
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
            # Add commentary reaction
            await msg.add_reaction("💬")
            
            # Update the contest with the message_id mapping
            contest = contest.set_message_id(comp.channel_id, comp.thread_id, i, msg.id)
            
            # Track the submission post
            contest = contest.add_submission_post(
                submission.discord_save_path,
                msg.id,
                comp.channel_id,
                comp.thread_id,
                is_summary=False
            )
        
        # Send voting instruction message
        vote_msg = await thread.send(
            "🗳️ **Jury Voting opens soon!**\n"
            f"Voting will begin at <t:{int(contest.schedule.qualif_period.start)}:F>\n\n"
            "React with 🗳️ to this message to cast your jury vote (top 10 ranking).\n\n"
            "**Public Voting:**\n"
            "React with 0️⃣, 1️⃣, 2️⃣, or 3️⃣ on any photo to give it 0-3 points.\n"
            "You can vote on as many photos as you wish! Your reactions will be automatically removed to keep votes secret.\n\n"
            "Note: The top 2 of public and the top 6 of jury among the remaining submissions will advance to the semi-finals."
        )
        await vote_msg.add_reaction("🗳️")
    
    # Save the updated contest with message mappings
    contest.save("photo_contest/contest2026.yaml")


async def prep_semis_period(bot: discord.Client):
    """Prepare semi-final period: solve qualifs, copy votes, and post submissions.
    
    Called at the END of the qualification period to prepare for semi-finals voting.
    Voting will be announced and become valid at the START of the semi-finals period.
    """
    global contest
    
    # Store qualif info before solving (to map semis back to threads)
    qualif_competitions = contest.qualif_competitions
    
    # Solve qualifications to determine semi-finalists (includes copying public votes to semis)
    contest, voters_transferred = contest.solve_qualifs()
    contest.save("photo_contest/contest2026.yaml")
    
    # Map channel_id to list of original thread_ids for that channel
    channel_to_thread_ids = {}
    for comp in qualif_competitions:
        if comp.channel_id not in channel_to_thread_ids:
            channel_to_thread_ids[comp.channel_id] = []
        channel_to_thread_ids[comp.channel_id].append(comp.thread_id)
    
    # Send DM notifications to voters whose votes were transferred
    for voter_id in voters_transferred:
        try:
            user = await bot.fetch_user(voter_id)
            if user:
                embed = discord.Embed(
                    title="Your votes have been transferred!",
                    description="Your public votes from the qualification round have been automatically transferred to the semi-finals! You can change them if you'd like by voting again in the semi-finals."
                )
                await send_dm_safe(user, embed=embed)
        except Exception as e:
            print(f"Could not send DM to voter {voter_id}: {e}")
    
    # Announce qualification results (random order, no authors) in announcement channel
    await announce_qualif_results(bot)
    
    # Announce qualifiers in each original qualification thread
    for comp in contest.qualif_competitions:
        assert comp.thread_id is not None, "Qualification competition missing thread_id"
        qualifiers = contest.get_qualifiers_for_thread(comp.channel_id, comp.thread_id)
        
        if not qualifiers:
            continue
        
        channel = bot.get_channel(comp.channel_id)
        try:
            thread = await bot.fetch_channel(comp.thread_id)
        except Exception:
            continue
        
        if thread:
            assert isinstance(thread, discord.Thread), "Thread ID does not correspond to a thread channel"
            await thread.send("🏆 **Qualification Results** 🏆\nThe following photos have qualified for the Semi-Finals (in no particular order):")
            
            for i, sub in enumerate(qualifiers):
                await thread.send(
                    f"Qualifier **#{i+1}**",
                    embed=discord.Embed().set_image(url=sub.discord_save_path)
                )
            
            start_time = int(contest.schedule.semis_period.start)
            timestamp_str = f"<t:{start_time}:F>"
            await thread.send(
                "📅 **Voting will begin at** " + timestamp_str
            )
    
    # Send DM notifications to qualifiers
    await notify_qualifiers(bot, contest, "semis", "Semi-Finals")
    
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
            
            # Update the contest with the message_id mapping
            contest = contest.set_message_id(comp.channel_id, comp.thread_id, i, msg.id)
            
            # Track the submission post
            contest = contest.add_submission_post(
                submission.discord_save_path,
                msg.id,
                comp.channel_id,
                None,
                is_summary=False
            )

        # Send voting instruction message
        vote_msg = await channel.send(
            "🗳️ **Voting has NOT started yet!**\n"
            f"Voting will begin at <t:{int(contest.schedule.semis_period.start)}:F>\n\n"
            "React with 🗳️ to this message to cast your jury vote (top 10 ranking).\n\n"
            "**Public Voting:**\n"
            "React with 0️⃣, 1️⃣, 2️⃣, or 3️⃣ on any photo to give it 0-3 points.\n"
            "You can vote on as many photos as you wish! Your reactions will be automatically removed to keep votes secret.\n\n"
            "Note: The top 2 of public and the top 6 of jury among the remaining submissions will advance to the semi-finals."
        )
        await vote_msg.add_reaction("🗳️")

    # Save the updated contest with message mappings
    contest.save("photo_contest/contest2026.yaml")


async def prep_final_period(bot):
    """Prepare final period: solve semis, announce results, and post finalists.
    
    Called at the END of the semi-finals period to prepare for final voting.
    Voting will be announced and become valid at the START of the final period.
    """
    global contest
    
    # Solve semi-finals to determine finalists with the correct channel_id
    contest = contest.solve_semis(final_channel_id)
    contest.save("photo_contest/contest2026.yaml")
    
    # Announce winners in each semi-final channel
    await announce_semis_winners(bot)
    
    final_comp = contest.final_competition
    if not final_comp:
        print("Warning: No final competition found")
        return
    
    # Send DM notifications to finalists
    await notify_qualifiers(bot, contest, "final", "Grand Final")
    
    # Post submissions in the final channel (single channel for all categories)
    final_channel = bot.get_channel(final_channel_id)
    if not final_channel or not isinstance(final_channel, discord.TextChannel):
        print(f"Warning: Could not find final channel {final_channel_id}")
        return
    
    # Post all finalists from all categories in the final channel
    await final_channel.send("🏆 **GRAND FINAL!** 🏆\nAll 15 finalists compete together!")
    
    # Get the final competition
    final_comp = contest.final_competition
    if not final_comp:
        print("Warning: No final competition found")
        return
    
    # Post all submissions
    for i, submission in enumerate(final_comp.competing_entries):
        msg = await final_channel.send(
            content=f"Submission #{i+1}",
            embed=discord.Embed().set_image(url=submission.discord_save_path)
        )
        
        # Update the contest with the message_id mapping
        contest = contest.set_message_id(final_channel_id, None, i, msg.id)
        
        # Track the submission post
        contest = contest.add_submission_post(
            submission.discord_save_path,
            msg.id,
            final_channel_id,
            None,
            is_summary=False
        )
    
    # Send voting instruction message
    vote_msg = await final_channel.send(
        "🗳️ **Final Voting opens soon!**\n"
        f"Voting will begin at <t:{int(contest.schedule.final_period.start)}:F>\n\n"
        "React with 🗳️ to this message to cast your vote.\n"
        "You will rank your top 5 out of the 15 finalists."
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
        await download_missing_pictures()
        autoplanner.start()

    @bot.event
    async def on_message(message):
        global contest
        await bot.process_commands(message)
        
        if message.author.bot:
            return
        
        # Handle submissions only during submission period
        if current_period == ContestPeriod.SUBMISSION:
            contest = await submit(message, bot)
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
            # Guild not found
            return
        
        user = guild.get_member(payload.user_id)
        if not user:
            # User not found
            return
        
        # Get the channel and message
        channel = bot.get_channel(payload.channel_id)
        if not channel:
            # Channel not in cache; will try fetching
            try:
                channel = await bot.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden) as e:
                # Could not fetch channel
                return
        
        # Reaction received; handle based on emoji and period
        
        try:
            assert isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread), "Channel is not a TextChannel or Thread"
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden) as e:
            # Could not fetch message
            return
        
        # Handle withdrawal reactions (❌) - allowed during any period
        if payload.emoji.name == "❌":
            contest = await withdraw(contest, message, user)
            return
        
        # Check for voting-related emojis
        voting_emojis = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "🗳️", "💬"]
        
        # Handle reactions based on current contest period
        if current_period == ContestPeriod.IDLE or current_period is None:
            # Check if this is a contest-related channel before removing reaction
            if payload.emoji.name in voting_emojis:
                # Get channel_id and thread_id correctly
                if isinstance(channel, discord.Thread):
                    assert channel.parent is not None, "Thread channel has no parent"
                    channel_id = channel.parent.id
                    thread_id = channel.id
                else:
                    channel_id = channel.id
                    thread_id = None
                
                # Check if this channel/thread is part of the contest
                res = contest.competition_from_channel_thread(channel_id, thread_id)
                
                if res is not None:
                    # This is a contest channel - remove reaction and notify user
                    try:
                        await message.remove_reaction(payload.emoji.name, user)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                    try:
                        await user.send("⏰ Voting is not currently open. Please wait for the next contest period.")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
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
            elif payload.emoji.name == "💬" and current_period in (ContestPeriod.QUALIF, ContestPeriod.SEMIS):
                await handle_commentary_request(contest, message, user, current_period)
            else:
                pass  # Emoji not recognized for this period
        
        elif current_period == ContestPeriod.FINAL:
            # Handle jury vote requests (top 5 ranking)
                if payload.emoji.name == "🗳️":
                    await handle_jury_vote_request(contest, message, user, bot, current_period)

    @bot.event
    async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
        """Handle message deletions - withdraw submission if a submission message is deleted.
        
        Interprets deletion as moderator withdrawal (same as ❌ reaction from discord_team_role).
        Only works during submission period.
        """
        global contest
        
        # Only process during submission period
        if current_period != ContestPeriod.SUBMISSION:
            return
        
        # Ignore DMs
        if not payload.guild_id:
            return
        
        # Get the guild
        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        
        # Get the channel to determine if it's a thread
        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return
        
        # Determine channel_id and thread_id
        if isinstance(channel, discord.Thread):
            channel_id = channel.parent_id
            thread_id = channel.id
        else:
            channel_id = channel.id
            thread_id = None
        
        # Check if this channel/thread is valid for submissions
        if (channel_id, thread_id) not in contest.channel_threads_open_for_submissions:
            return
        
        # Check if this message is a submission
        if not contest.is_submission_message(channel_id, thread_id, payload.message_id, prefer_type="submission"):
            return
        
        # Perform the withdrawal (message is already deleted, so pass None for message)
        contest = await _perform_withdrawal(contest, message=None, channel_id=channel_id, thread_id=thread_id, bot=bot)

    # Admin Commands for Testing #############################################
    
    def is_admin(user_id: int) -> bool:
        """Check if user is an admin (organizer or has discord team role)."""
        return user_id == organizer_id
    
    @bot.command(name="fix_qualif_timestamps")
    async def command_fix_qualif_timestamps(ctx: commands.Context):
        """Fix incorrect timestamps in 'Jury Voting opens soon' messages in qualification threads."""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        await ctx.send("🔍 Searching for messages to fix in qualification threads...")
        
        fixed_count = 0
        error_count = 0
        
        # Get the correct timestamp
        correct_timestamp = int(contest.schedule.qualif_period.start)
        
        # Iterate through all qualification competitions
        for comp in contest.qualif_competitions:
            if comp.thread_id is None:
                continue
            
            try:
                # Get the thread
                thread = bot.get_channel(comp.thread_id)
                if thread is None:
                    print(f"⚠️ Could not find thread {comp.thread_id}")
                    error_count += 1
                    continue
                
                assert isinstance(thread, discord.Thread), f"Channel {comp.thread_id} is not a thread"
                assert bot.user is not None, "Bot user is None"
                
                # Search through messages in the thread
                async for message in thread.history(limit=100, oldest_first=True):
                    # Check if this is the voting instruction message
                    if message.author.id == bot.user.id and "🗳️ **Jury Voting opens soon!**" in message.content:
                        # Extract current timestamp from message
                        timestamp_pattern = r"<t:(\d+):F>"
                        match = re.search(timestamp_pattern, message.content)
                        
                        if match:
                            current_timestamp = int(match.group(1))
                            
                            # Check if it needs fixing (if it's not the correct timestamp)
                            if current_timestamp != correct_timestamp:
                                # Replace the timestamp
                                new_content = re.sub(
                                    timestamp_pattern,
                                    f"<t:{correct_timestamp}:F>",
                                    message.content
                                )
                                
                                # Edit the message
                                await message.edit(content=new_content)
                                print(
                                    f"✅ Fixed timestamp in thread <#{comp.thread_id}>\n"
                                    f"   Old: <t:{current_timestamp}:F>\n"
                                    f"   New: <t:{correct_timestamp}:F>"
                                )
                                fixed_count += 1
                            else:
                                print(f"✓ Thread <#{comp.thread_id}> already has correct timestamp")
                        break  # Found the message in this thread, move to next thread
                        
            except Exception as e:
                print(f"❌ Error processing thread {comp.thread_id}: {e}")
                error_count += 1
        
        # Send summary
        print(
            f"\n📊 **Summary:**\n"
            f"✅ Fixed: {fixed_count}\n"
            f"❌ Errors: {error_count}\n"
            f"🔍 Total qualification threads checked: {len(contest.qualif_competitions)}"
        )
    
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
    
    @bot.command(name="contest_qualif_board")
    async def command_contest_qualif_board(ctx: commands.Context):
        f"""[ADMIN] Generate and post qualification results boards.
        
        Usage: {constantes.prefixVolt}contest_qualif_board
        
        Posts the qualification results boards to the current channel.
        """
        global contest
        
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        if not contest.qualif_competitions:
            await ctx.send("❌ No qualification competitions found.", delete_after=10)
            return
        
        await ctx.send("📊 Generating qualification results boards...", delete_after=5)
        
        # Build id2name mapping
        id2name = await build_id2name_mapping(bot, contest)
        
        # Get jury voter authors for bonus display
        qualif_jury_voter_authors = contest.get_jury_voter_authors("qualif")
        
        for comp in contest.qualif_competitions:
            # Get category name
            category_channel = bot.get_channel(comp.channel_id)
            category_name = getattr(category_channel, "name", f"Category {comp.channel_id}")
            
            # Generate board
            board_path = gen_competition_board(comp, category_name, id2name, None, qualif_jury_voter_authors)
            
            # Send board to channel
            with open(board_path, "rb") as f:
                await ctx.send(
                    f"📊 **Qualification Results - {category_name}**",
                    file=discord.File(f, filename=f"qualif_{category_name.replace(' ', '_')}.png")
                )
        
        await ctx.send("✅ Qualification boards posted!")
    
    @bot.command(name="contest_goto")
    async def command_contest_goto(ctx: commands.Context, period: str, force: bool = False):
        f"""Transition to a specific contest period.
        
        Usage: {constantes.prefixVolt}contest_goto submission|qualif|semis|final|idle [force]
        
        Use force=True to force recreation of semis/final (e.g., !contest_goto semis true)
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
        if old_period != target_period or force:
            # Don't close the previous period - just jump to the new one
            # This allows testing without triggering end-of-period cleanup
            
            # Run prep_* to set up photos/reactions, then notify for announcement
            if target_period == ContestPeriod.SUBMISSION:
                # SUBMISSION period: no prep needed, just notify
                await notify_period_start(bot, ContestPeriod.SUBMISSION)
            elif target_period == ContestPeriod.QUALIF:
                await prep_qualif_period(bot)
                await notify_period_start(bot, ContestPeriod.QUALIF)
            elif target_period == ContestPeriod.SEMIS:
                await prep_semis_period(bot)
                await notify_period_start(bot, ContestPeriod.SEMIS)
            elif target_period == ContestPeriod.FINAL:
                await prep_final_period(bot)
                await notify_period_start(bot, ContestPeriod.FINAL)
            elif target_period == ContestPeriod.IDLE and old_period == ContestPeriod.FINAL:
                # Final results announcement when going to IDLE
                await announce_final_results(bot)
                await announce_final_boards(bot)
                await notify_final_results(bot, contest)
            
            current_period = target_period
            
            await ctx.send(f"✅ Transition complete! **{old_period.value.upper() if old_period else 'NONE'}** → **{target_period.value.upper()}** (force={force})")
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
    
    # TEST MODE COMMANDS ######################################################
    
    @bot.command(name="jury_vote_as")
    async def command_jury_vote_as(ctx: commands.Context, user: discord.Member):
        f"""[TEST MODE ONLY] Trigger jury voting on behalf of another user.
        
        Usage: {constantes.prefixVolt}jury_vote_as @user
        This will send the jury voting interface to YOUR DMs, where you'll vote as the mentioned user.
        """
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        if not current_period:
            await ctx.send("❌ No active period.", delete_after=5)
            return
        
        # Try to find a voting message in recent messages
        voting_message = None
        async for msg in ctx.channel.history(limit=50):
            if "Jury Voting is now open" in msg.content and msg.author == bot.user:
                voting_message = msg
                break
        
        if not voting_message:
            await ctx.send("❌ Couldn't find a jury voting message in recent messages. Please run this command in the channel/thread where voting is active.", delete_after=10)
            return
        
        await ctx.send(f"✅ Triggering jury vote for {user.mention}. Check your DMs!", delete_after=5)

        # Trigger the jury vote handler: send the DM to the command caller
        # but perform the vote using the mentioned user's voter id.
        await handle_jury_vote_request(contest, voting_message, ctx.author, bot, current_period, as_voter_id=user.id)
    
    @bot.command(name="public_vote_as")
    async def command_public_vote_as(ctx: commands.Context, user: discord.Member, submission_number: int, points: int):
        f"""[TEST MODE ONLY] Cast a public vote on behalf of another user.
        
        Usage: {constantes.prefixVolt}public_vote_as @user <submission_number> <points>
        Example: {constantes.prefixVolt}public_vote_as @TestUser 3 2
        
        This will cast a vote giving <points> (0-3) to submission #<submission_number> as if <user> voted.
        """
        global contest
      
        if not is_admin(ctx.author.id):
            await ctx.send("❌ This command is only available to admins.", delete_after=5)
            return
        
        if current_period not in [ContestPeriod.QUALIF, ContestPeriod.SEMIS]:
            await ctx.send("❌ Public voting is only available during qualification and semi-finals periods.", delete_after=5)
            return
        
        if points not in [0, 1, 2, 3]:
            await ctx.send("❌ Points must be between 0 and 3.", delete_after=5)
            return
        
        # Get the channel and thread
        channel_id, thread_id = get_channel_and_thread(ctx.message)
        
        # Find the competition, preferring the current period type
        period_type = current_period.value if current_period != ContestPeriod.IDLE else None
        res = contest.competition_from_channel_thread(channel_id, thread_id, prefer_type=period_type)
        if not res:
            await ctx.send("❌ No active competition found in this channel/thread.", delete_after=5)
            return
        
        _, competition = res
        
        # Check if submission exists
        if submission_number < 1 or submission_number > len(competition.competing_entries):
            await ctx.send(f"❌ Invalid submission number. Must be between 1 and {len(competition.competing_entries)}.", delete_after=5)
            return
        
        submission = competition.competing_entries[submission_number - 1]
        
        # Cast points to the correct literal type
        points_literal: Literal[0, 1, 2, 3]
        if points == 0:
            points_literal = 0
        elif points == 1:
            points_literal = 1
        elif points == 2:
            points_literal = 2
        else:
            points_literal = 3
        
        # Save the vote
        try:
            # Reload contest to avoid race condition with concurrent votes
            contest = reload_contest()
            period_type = current_period.value if current_period != ContestPeriod.IDLE else None
            contest = contest.save_public_vote(channel_id, thread_id, user.id, points_literal, submission, period=period_type)
            contest.save("photo_contest/contest2026.yaml")
            logger.info(f"Public vote saved by admin for user={user.id}, points={points}, submission={submission_number}")
            await ctx.send(f"✅ Vote cast! {user.mention} gave **{points} point{'s' if points != 1 else ''}** to Submission #{submission_number}", delete_after=10)
        except ValueError as e:
            await ctx.send(f"❌ Error: {e}", delete_after=10)
    
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
            f"**`{constantes.prefixVolt}contest_qualif_board`** - Generate and post qualification results boards\n"
            f"**`{constantes.prefixVolt}contest_next`** - Advance to the next period\n"
            f"**`{constantes.prefixVolt}contest_goto <period>`** - Jump to a specific period (submission/qualif/semis/final/idle)\n"
            f"**`{constantes.prefixVolt}contest_clear_votes CONFIRM`** - Clear all votes (keeps submissions)\n"
            f"**`{constantes.prefixVolt}contest_reset CONFIRM`** - Reset all contest data (requires CONFIRM)\n\n"
        )
        
        help_text += (
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