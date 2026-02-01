from math import ceil
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from photo_contest_data import CompetitionInfo, Contest, Submission

BG_COLOR = "#502379"

fnt_light = ImageFont.truetype("resource/Ubuntu-Light.ttf", 15)
fnt_italic = ImageFont.truetype("resource/Ubuntu-LightItalic.ttf", 15)
fnt_regular = ImageFont.truetype("resource/Ubuntu-Regular.ttf", 20)
fnt_bold_small = ImageFont.truetype("resource/Ubuntu-Bold.ttf", 20)
fnt_bold = ImageFont.truetype("resource/Ubuntu-Bold.ttf", 30)


def gen_competition_board(
    competition: CompetitionInfo,
    channel_name: str,
    id2name: Dict[int, str],
    thread_name: Optional[str] = None,
) -> str:
    """Generate a competition board showing current standings for a competition."""
    
    img = Image.new("RGB", (750, 600), color=BG_COLOR)
    d = ImageDraw.Draw(img)

    # Title
    title = f"{competition.type.title()} in {channel_name}" + (
        "#" + thread_name if thread_name else ""
    )
    d.text(
        (375, 20),
        title,
        anchor="mt",
        font=fnt_bold if len(title) < 46 else fnt_bold_small,
        fill="white",
    )

    # volt logo
    logo = Image.open("resource/logo_volt.png")
    img.paste(logo.resize((150, 150)), (585, 435))

    # Count votes
    jury_votes = competition.count_votes_jury()
    public_votes = competition.count_votes_public()

    # Create ranking
    ranked_submissions = sorted(
        competition.competing_entries,
        key=lambda x: (jury_votes.get(x, 0), public_votes.get(x, 0), -x.submission_time),
        reverse=True,
    )

    # Display rankings
    max_display = min(6, len(ranked_submissions))
    for i, submission in enumerate(ranked_submissions[:max_display]):
        y_pos = 100 + i * 60
        
        # Photo number and author
        photo_num = i + 1
        author_name = id2name.get(submission.author_id, f"User {submission.author_id}")
        
        d.text(
            (50, y_pos),
            f"Photo #{photo_num}",
            anchor="lm",
            font=fnt_bold,
            fill="white",
        )
        d.text(
            (50, y_pos + 25),
            f"by {author_name}",
            anchor="lm",
            font=fnt_italic,
            fill="white",
        )

        # Points
        jury_points = jury_votes.get(submission, 0)
        public_points = public_votes.get(submission, 0)
        total_points = jury_points + public_points

        d.text(
            (400, y_pos),
            f"{jury_points} + {public_points} = {total_points}",
            anchor="lm",
            font=fnt_regular,
            fill="white",
        )

    save_path = f"photo_contest/generated_tables/{competition.type}_{competition.channel_id}.png"
    img.save(save_path)
    return save_path


def gen_final_results_board(
    contest: Contest,
    channel_name: str,
    id2name: Dict[int, str],
) -> str:
    """Generate final results board for the final competition."""
    
    final_competitions = contest.final_competitions
    if not final_competitions:
        raise ValueError("No final competition found")

    final = final_competitions[0]
    
    img = Image.new("RGB", (750, 600), color=BG_COLOR)
    d = ImageDraw.Draw(img)

    # Title
    d.text((375, 20), f"Final Results for {channel_name}", anchor="mt", font=fnt_bold, fill="white")

    # volt logo
    logo = Image.open("resource/logo_volt.png")
    img.paste(logo.resize((150, 150)), (585, 435))

    # Count votes
    jury_votes = final.count_votes_jury()
    public_votes = final.count_votes_public()

    # Create ranking
    ranked_submissions = sorted(
        final.competing_entries,
        key=lambda x: (jury_votes.get(x, 0), public_votes.get(x, 0), -x.submission_time),
        reverse=True,
    )

    # Display podium
    medals = ["🥇", "🥈", "🥉"]
    for i, submission in enumerate(ranked_submissions[:3]):
        y_pos = 120 + i * 80
        
        # Medal and position
        medal_text = medals[i] if i < 3 else f"#{i+1}"
        
        # Photo number and author
        photo_num = i + 1
        author_name = id2name.get(submission.author_id, f"User {submission.author_id}")
        
        d.text(
            (50, y_pos),
            medal_text,
            anchor="lm",
            font=fnt_bold,
            fill="white",
        )
        d.text(
            (100, y_pos),
            f"Photo #{photo_num}",
            anchor="lm",
            font=fnt_bold_small,
            fill="white",
        )
        d.text(
            (100, y_pos + 25),
            f"by {author_name}",
            anchor="lm",
            font=fnt_italic,
            fill="white",
        )

        # Points
        jury_points = jury_votes.get(submission, 0)
        public_points = public_votes.get(submission, 0)
        total_points = jury_points + public_points

        d.text(
            (400, y_pos),
            f"Jury: {jury_points}  Public: {public_points}",
            anchor="lm",
            font=fnt_regular,
            fill="white",
        )
        d.text(
            (400, y_pos + 25),
            f"Total: {total_points}",
            anchor="lm",
            font=fnt_bold_small,
            fill="white",
        )

    save_path = f"photo_contest/generated_tables/final_results.png"
    img.save(save_path)
    return save_path


def gen_semifinals_boards(
    contest: Contest,
    channel_name: str,
    id2name: Dict[int, str],
) -> List[str]:
    """Generate boards for all semifinal competitions."""
    
    semifinal_competitions = contest.semis_competitions
    generated_files = []

    for i, semifinal in enumerate(semifinal_competitions):
        img = Image.new("RGB", (750, 600), color=BG_COLOR)
        d = ImageDraw.Draw(img)

        # Title
        d.text(
            (375, 20),
            f"Semifinal {i+1} for {channel_name}",
            anchor="mt",
            font=fnt_bold,
            fill="white",
        )

        # volt logo
        logo = Image.open("resource/logo_volt.png")
        img.paste(logo.resize((150, 150)), (585, 435))

        # Count votes
        jury_votes = semifinal.count_votes_jury()
        public_votes = semifinal.count_votes_public()

        # Create ranking
        ranked_submissions = sorted(
            semifinal.competing_entries,
            key=lambda x: (jury_votes.get(x, 0), public_votes.get(x, 0), -x.submission_time),
            reverse=True,
        )

        # Display submissions with qualification status
        for j, submission in enumerate(ranked_submissions):
            y_pos = 80 + j * 40
            
            author_name = id2name.get(submission.author_id, f"User {submission.author_id}")
            
            # Qualification status (top 4 jury + top 2 public qualify)
            jury_points = jury_votes.get(submission, 0)
            public_points = public_votes.get(submission, 0)
            
            # Simple qualification check (simplified)
            qualifies = j < 6  # Top 6 usually qualify
            
            color = "white" if qualifies else "#888888"
            font = fnt_regular if qualifies else fnt_light
            
            d.text(
                (50, y_pos),
                f"#{j+1}",
                anchor="lm",
                font=font,
                fill=color,
            )
            d.text(
                (100, y_pos),
                author_name,
                anchor="lm",
                font=font,
                fill=color,
            )
            d.text(
                (400, y_pos),
                f"J:{jury_points} P:{public_points}",
                anchor="lm",
                font=font,
                fill=color,
            )
            d.text(
            (550, y_pos),
                "✓" if qualifies else "",
                anchor="lm",
                font=fnt_bold,
                fill="green" if qualifies else color,
            )

        save_path = f"photo_contest/generated_tables/semifinal_{semifinal.channel_id}_{i}.png"
        img.save(save_path)
        generated_files.append(save_path)

    return generated_files


def gen_photo_vote_details(
    submission: Submission,
    competition: CompetitionInfo,
    channel_name: str,
    id2name: Dict[int, str],
    thread_name: Optional[str] = None,
    photo_num: Optional[int] = None,
) -> str:
    """Generate detailed vote board for a specific photo with snippet, similar to genSemiThread."""
    
    img = Image.new("RGB", (750, 600), color=BG_COLOR)
    d = ImageDraw.Draw(img)

    # Find the photo number if not provided
    if photo_num is None:
        photo_num = competition.competing_entries.index(submission) + 1

    # Title
    title = f"Photo #{photo_num} in {channel_name}" + (
        "#" + thread_name if thread_name else ""
    )
    d.text(
        (375, 20),
        title,
        anchor="mt",
        font=fnt_bold if len(title) < 46 else fnt_bold_small,
        fill="white",
    )
    d.text(
        (375, 50),
        f"submitted by {id2name.get(submission.author_id, f'User {submission.author_id}')}",
        anchor="mt",
        font=fnt_italic,
        fill="white",
    )

    # volt logo
    logo = Image.open("resource/logo_volt.png")
    img.paste(logo.resize((150, 150)), (585, 435))

    # snippet of the photo
    img_snippet = Image.open(submission.local_save_path)
    width_snip = min(round(250 * img_snippet.size[0] / img_snippet.size[1]), 360)
    left_img = 20 + (360 - width_snip) // 2
    img.paste(img_snippet.resize((width_snip, 250)), (left_img, 100))

    # Get jury vote breakdown from the competition data
    jury_votes_by_id = competition.get_jury_votes_per_juror(submission)
    jury_points_per_juror: Dict[str, int] = {
        id2name.get(voter_id, f"User {voter_id}"): points
        for voter_id, points in jury_votes_by_id.items()
    }
    total_jury_points = sum(jury_points_per_juror.values())

    d.text(
        (50, 365),
        f"{total_jury_points} point{'s' if total_jury_points != 1 else ''} from the jury",
        anchor="lt",
        font=fnt_bold_small,
        fill="white",
    )
    for i, (juror, points) in enumerate(
        sorted(jury_points_per_juror.items(), key=lambda x: x[1], reverse=True)
    ):
        fnt = (
            fnt_bold_small
            if points >= 12
            else (fnt_regular if points >= 8 else fnt_light)
        )
        d.text(
            (50, 395 + i * 25),
            f"{points} point{'s' if points != 1 else ''} from {juror}",
            anchor="lt",
            fill="white",
            font=fnt,
        )

    # Get public vote breakdown from the competition data
    public_votes_by_id = competition.get_public_votes_per_voter(submission)
    public_points_per_voter: Dict[str, int] = {
        id2name.get(voter_id, f"User {voter_id}"): points
        for voter_id, points in public_votes_by_id.items()
    }
    total_public_points = sum(public_points_per_voter.values())

    d.text(
        (400, 100),
        f"{total_public_points} point{'s' if total_public_points != 1 else ''} from the global vote",
        anchor="lt",
        font=fnt_bold_small,
        fill="white",
    )
    for i, (voter, points) in enumerate(
        sorted(public_points_per_voter.items(), key=lambda x: x[1], reverse=True)
    ):
        fnt = (
            fnt_bold_small
            if points == 3
            else (fnt_regular if points == 2 else fnt_light)
        )
        d.text(
            (400, 135 + i * 25),
            f"{points} point{'s' if points != 1 else ''} from {voter}",
            anchor="lt",
            fill="white",
            font=fnt,
        )

    # Save in generated_tables folder with a meaningful name
    filename = f"photo_contest/generated_tables/photo_{competition.type}_{competition.channel_id}_{photo_num}_details.png"
    img.save(filename)
    return filename


def generate_all_boards(
    contest: Contest,
    channel_name: str,
    id2name: Dict[int, str],
) -> Dict[str, List[str]]:
    """Generate boards for all active competitions."""
    
    generated_files = {
        "submission": [],
        "qualification": [],
        "semifinal": [],
        "final": [],
        "photo_details": []
    }

    # Generate boards for each competition type
    for competition in contest.current_competitions:
        if competition.competing_entries:  # Only generate if there are entries
            board_path = gen_competition_board(competition, channel_name, id2name)
            generated_files[competition.type].append(board_path)

    # Generate special boards for finals and semifinals if they exist
    if contest.final_competitions:
        final_board = gen_final_results_board(contest, channel_name, id2name)
        generated_files["final"].append(final_board)

    if contest.semis_competitions:
        semifinal_boards = gen_semifinals_boards(contest, channel_name, id2name)
        generated_files["semifinal"].extend(semifinal_boards)

    # Generate detailed photo boards for current competitions with votes
    for competition in contest.current_competitions:
        if competition.competing_entries and (competition.votes_jury or competition.votes_public):
            thread_name = f"thread{competition.thread_id}" if competition.thread_id else None
            for i, submission in enumerate(competition.competing_entries):
                detail_board = gen_photo_vote_details(
                    submission, competition, channel_name, id2name, thread_name, i + 1
                )
                generated_files["photo_details"].append(detail_board)

    return generated_files