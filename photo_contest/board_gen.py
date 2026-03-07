from math import ceil
import os
from random import shuffle
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from photo_contest.photo_contest_data import CompetitionInfo, Contest, Submission, POINTS_SETS

BG_COLOR = "#502379"


def strip_emoji(text: str) -> str:
    """Remove emoji characters from text for compatibility with image generation."""
    if not text:
        return text
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "]+", flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text)

fnt_light = ImageFont.truetype("resource/Ubuntu-Light.ttf", 15)
fnt_italic = ImageFont.truetype("resource/Ubuntu-LightItalic.ttf", 15)
fnt_regular = ImageFont.truetype("resource/Ubuntu-Regular.ttf", 20)
fnt_bold_small = ImageFont.truetype("resource/Ubuntu-Bold.ttf", 20)
fnt_bold = ImageFont.truetype("resource/Ubuntu-Bold.ttf", 30)


def create_thumbnail(img_path: str, size: Tuple[int, int]) -> Image.Image:
    """Create a thumbnail with preserved aspect ratio and purple background (letterbox)."""
    img = Image.open(img_path)
    
    img.thumbnail(size, Image.Resampling.LANCZOS)
    
    bg = Image.new("RGB", size, color=BG_COLOR)
    
    x_offset = (size[0] - img.width) // 2
    y_offset = (size[1] - img.height) // 2
    
    bg.paste(img, (x_offset, y_offset))
    return bg


def calculate_board_dimensions(num_submissions: int, mode: str = "qualif") -> Tuple[int, int, int, Tuple[int, int]]:
    """Calculate board dimensions based on number of submissions.
    
    Args:
        num_submissions: Number of submissions to display
        mode: "qualif" for qualification boards, "semis" for semifinals
    
    Returns:
        Tuple of (img_height, row_spacing, start_y, thumb_size)
    """
    num_rows = ceil(num_submissions / 2)
    
    if mode == "qualif":
        # More compact spacing for qualification boards
        thumb_size = (45, 45)
        if num_rows <= 3:
            return 100 + num_rows * 70 + 200, 70, 100, thumb_size
        elif num_rows <= 6:
            return 100 + num_rows * 65 + 200, 65, 100, thumb_size
        elif num_rows <= 12:
            return 100 + num_rows * 60 + 200, 60, 100, thumb_size
        else:
            return 100 + num_rows * 55 + 200, 55, 100, thumb_size
    else:  # semis
        # More spacious for semifinals
        thumb_size = (65, 65)
        if num_rows <= 3:
            return 80 + num_rows * 100 + 200, 100, 110, thumb_size
        elif num_rows <= 6:
            return 80 + num_rows * 95 + 200, 95, 110, thumb_size
        elif num_rows <= 12:
            return 80 + num_rows * 90 + 200, 90, 110, thumb_size
        else:
            return 80 + num_rows * 85 + 200, 85, 110, thumb_size


def draw_column_headers(d: ImageDraw.ImageDraw, y: int, mode: str = "qualif"):
    """Draw Jury/Public column headers for both left and right columns.
    
    Args:
        d: ImageDraw object
        y: Y position for headers
        mode: "qualif" for standard spacing, "semis" for semifinals spacing
    """
    if mode == "qualif":
        # Left column headers
        d.text((335, y), "Jury", anchor="mm", font=fnt_bold_small, fill="white")
        d.text((395, y), "Public", anchor="mm", font=fnt_bold_small, fill="white")
        # Right column headers
        d.text((800, y), "Jury", anchor="mm", font=fnt_bold_small, fill="white")
        d.text((860, y), "Public", anchor="mm", font=fnt_bold_small, fill="white")
    else:  # semis
        # Left column headers
        d.text((335, y), "Jury", anchor="mm", font=fnt_bold_small, fill="white")
        d.text((395, y), "Public", anchor="mm", font=fnt_bold_small, fill="white")
        # Right column headers
        d.text((805, y), "Jury", anchor="mm", font=fnt_bold_small, fill="white")
        d.text((865, y), "Public", anchor="mm", font=fnt_bold_small, fill="white")


def select_fonts(qualifies: bool, num_rows: int) -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """Select appropriate fonts based on qualifier status and row count.
    
    Args:
        qualifies: Whether the submission qualifies
        num_rows: Total number of rows being displayed
    
    Returns:
        Tuple of (regular_font, bold_font, italic_font)
    """
    if qualifies:
        regular = fnt_regular if num_rows <= 12 else fnt_light
        bold = fnt_bold if num_rows <= 12 else fnt_bold_small
        italic = fnt_italic
    else:
        regular = fnt_light
        bold = fnt_regular
        italic = fnt_light
    return regular, bold, italic


def gen_competition_board(
    competition: CompetitionInfo,
    channel_name: str,
    id2name: Dict[int, str],
    thread_name: Optional[str] = None,
) -> str:
    """Generate a competition board showing current standings for a competition."""
    
    # Count votes
    jury_votes = competition.count_votes_jury()
    public_votes = competition.count_votes_public()

    # Use original submission order (not sorted by score)
    submissions_to_display = competition.competing_entries
    
    num_submissions = len(submissions_to_display)
    num_rows = ceil(num_submissions / 2)
    
    # Calculate dynamic height and spacing
    img_height, spacing, start_y, thumb_size = calculate_board_dimensions(num_submissions, "qualif")
    
    img = Image.new("RGB", (950, img_height), color=BG_COLOR)
    d = ImageDraw.Draw(img)

    # Title
    title = f"Qualification in {strip_emoji(channel_name)}" + (
        "#" + strip_emoji(thread_name) if thread_name else ""
    )
    title_font = fnt_bold if len(title) < 46 else fnt_bold_small
    title_bbox = d.textbbox((0, 0), title, font=title_font)
    title_x = (950 - (title_bbox[2] - title_bbox[0])) // 2
    d.text(
        (title_x, 20),
        title,
        anchor="lt",
        font=title_font,
        fill="white",
    )

    # volt logo - position at bottom right
    logo = Image.open("resource/logo_volt.png")
    logo_y = img_height - 165
    img.paste(logo.resize((150, 150)), (785, logo_y))
    
    # Add column headers for Jury and Public points
    draw_column_headers(d, 70)

    # Determine qualifiers: top 2 by public, then top 6 by jury from remaining
    top_public = sorted(
        competition.competing_entries,
        key=lambda x: (public_votes.get(x, 0), jury_votes.get(x, 0), -x.submission_time),
        reverse=True,
    )[:2]
    top_jury = sorted(
        [x for x in competition.competing_entries if x not in top_public],
        key=lambda x: (jury_votes.get(x, 0), public_votes.get(x, 0), -x.submission_time),
        reverse=True,
    )[:6]
    qualifiers = set(top_public + top_jury)
    
    # Display in 2 columns (top-to-bottom, left-to-right)
    for i, submission in enumerate(submissions_to_display):
        # Fill left column first (top to bottom), then right column
        if i < num_rows:
            col = 0  # left column
            row = i
        else:
            col = 1  # right column
            row = i - num_rows
        
        x_offset = 95 if col == 0 else 560
        y_pos = start_y + row * spacing
        
        # Photo thumbnail
        thumb = create_thumbnail(submission.local_save_path, thumb_size)
        img.paste(thumb, (x_offset - 60, y_pos - 3))
        
        # Photo number and author
        photo_num = i + 1
        author_name = strip_emoji(id2name.get(submission.author_id, f"User {submission.author_id}"))
        
        # Determine if this submission qualifies
        qualifies = submission in qualifiers
        
        # Select fonts and color based on qualifier status
        color = "white" if qualifies else "#888888"
        font_regular_choice, font_bold_choice, font_italic_choice = select_fonts(qualifies, num_rows)
        
        d.text(
            (x_offset, y_pos),
            f"Photo #{photo_num}",
            anchor="lm",
            font=font_bold_choice,
            fill=color,
        )
        d.text(
            (x_offset, y_pos + 20),
            f"by {author_name}",
            anchor="lm",
            font=font_italic_choice,
            fill=color,
        )

        # Points - show jury and public in separate columns
        jury_points = jury_votes.get(submission, 0)
        public_points = public_votes.get(submission, 0)
        
        # Calculate x positions based on column
        jury_x = 335 if col == 0 else 800
        public_x = 395 if col == 0 else 860

        d.text(
            (jury_x, y_pos + 10),
            str(jury_points),
            anchor="mm",
            font=font_regular_choice,
            fill=color,
        )
        d.text(
            (public_x, y_pos + 10),
            str(public_points),
            anchor="mm",
            font=font_regular_choice,
            fill=color,
        )

    # Generate filename with category name and thread name
    # Sanitize channel_name for filename (replace spaces and special chars)
    safe_channel_name = channel_name.replace(" ", "_").replace("-", "_")
    
    save_path = f"photo_contest/generated_tables/{competition.type}_{safe_channel_name}"
    if thread_name:
        save_path += f"_thread{thread_name}"
    save_path += ".png"
    img.save(save_path)
    return save_path


def gen_final_results_board(
    contest: Contest,
    id2name: Dict[int, str],
    latest_voter_points: Optional[Dict[Submission, int]] = None,
    latest_voter_name: Optional[str] = None,
    latest_voter_index: Optional[int] = None,
    total_voters: Optional[int] = None,
) -> str:
    """Generate final results board combining all category finals.
    
    Shows all finalists from all categories ranked together by their jury votes.
    
    Args:
        contest: Contest object with final competitions
        id2name: Mapping of user IDs to display names
        latest_voter_points: Optional dict mapping submissions to points from latest voter
        latest_voter_name: Optional name of the latest voter for subtitle
        latest_voter_index: Optional 1-based index of currently revealed voter
        total_voters: Optional total number of voters in the reveal sequence
    """
    
    final_comp = contest.final_competition
    if not final_comp:
        raise ValueError("No final competition found")

    # Get all entries and votes from the single grand final
    all_entries = list(final_comp.competing_entries)
    submission_to_photo_num = {sub: i+1 for i, sub in enumerate(all_entries)}
    
    # Get votes from the final competition
    all_votes = final_comp.count_votes_jury()
    
    # Rank all submissions with tie-breaking
    ranked_submissions = sorted(
        all_entries,
        key=lambda x: (all_votes.get(x, 0), -x.submission_time),
        reverse=True
    )
    
    num_submissions = len(ranked_submissions)
    
    # Calculate dynamic height based on number of rows (2 columns)
    num_rows = ceil(num_submissions / 2)
    
    # Optimized height calculation with increased spacing for thumbnails
    thumb_size = (55, 55)
    if num_rows <= 3:
        row_spacing = 90
        img_height = 80 + num_rows * row_spacing + 180
    elif num_rows <= 6:
        row_spacing = 85
        img_height = 80 + num_rows * row_spacing + 180
    else:
        row_spacing = 80
        img_height = 80 + num_rows * row_spacing + 180
    
    spacing = row_spacing
    
    img = Image.new("RGB", (950, img_height), color=BG_COLOR)
    d = ImageDraw.Draw(img)

    # Title
    title = "Final Results"
    d.text(
        (475, 15),
        title,
        anchor="mt",
        font=fnt_bold,
        fill="white",
    )
    
    # Subtitle with voter name if provided
    if latest_voter_name:
        subtitle = f"After votes from {latest_voter_name}"
        if latest_voter_index is not None and total_voters:
            subtitle += f" ({latest_voter_index}/{total_voters} voters)"
        d.text(
            (475, 45),
            subtitle,
            anchor="mt",
            font=fnt_bold_small,
            fill="white",
        )
        header_y = 75
        start_y = 105  # More space after subtitle and headers
    else:
        header_y = 60
        start_y = 90  # Standard spacing after headers

    # volt logo - position at bottom right
    logo = Image.open("resource/logo_volt.png")
    logo_y = img_height - 155
    img.paste(logo.resize((150, 150)), (785, logo_y))
    
    # Add column headers
    if latest_voter_points:
        # Show both new points and total
        # Left column headers
        d.text(
            (340, header_y),
            "+New",
            anchor="mm",
            font=fnt_regular,
            fill="white",
        )
        d.text(
            (400, header_y),
            "Total",
            anchor="mm",
            font=fnt_bold_small,
            fill="white",
        )
        # Right column headers
        d.text(
            (800, header_y),
            "+New",
            anchor="mm",
            font=fnt_regular,
            fill="white",
        )
        d.text(
            (860, header_y),
            "Total",
            anchor="mm",
            font=fnt_bold_small,
            fill="white",
        )
    else:
        # Show only total points
        # Left column header
        d.text(
            (400, header_y),
            "Points",
            anchor="mm",
            font=fnt_bold_small,
            fill="white",
        )
        # Right column header
        d.text(
            (860, header_y),
            "Points",
            anchor="mm",
            font=fnt_bold_small,
            fill="white",
        )

    # Display in 2 columns (top-to-bottom, left-to-right) - ranked order
    for i, submission in enumerate(ranked_submissions):
        # Fill left column first (top to bottom), then right column
        if i < num_rows:
            col = 0  # left column
            row = i
        else:
            col = 1  # right column
            row = i - num_rows
        
        x_offset = 95 if col == 0 else 560
        y_pos = start_y + row * spacing
        
        # Photo thumbnail
        thumb = create_thumbnail(submission.local_save_path, thumb_size)
        img.paste(thumb, (x_offset - 60, y_pos - 3))
        
        # Ranking position
        position = i + 1
        # Photo number based on original order in competing_entries
        photo_num = submission_to_photo_num[submission]
        author_name = strip_emoji(id2name.get(submission.author_id, f"User {submission.author_id}"))
        
        # Top 3 get special treatment
        is_top3 = position <= 3
        color = "white"
        font_bold_choice = fnt_bold if is_top3 else fnt_bold_small
        font_italic_choice = fnt_italic if is_top3 else fnt_light
        
        # Position text
        ordinals = {1: "1st", 2: "2nd", 3: "3rd"}
        position_text = ordinals.get(position, f"#{position}")
        
        d.text(
            (x_offset, y_pos),
            f"{position_text} - Photo #{photo_num}",
            anchor="lm",
            font=font_bold_choice,
            fill=color,
        )
        d.text(
            (x_offset, y_pos + 20),
            f"by {author_name}",
            anchor="lm",
            font=font_italic_choice,
            fill=color,
        )

        # Points - show jury points
        points_sub = all_votes.get(submission, 0)
        
        # Calculate x position based on column
        total_x = 400 if col == 0 else 860
        new_x = 340 if col == 0 else 800

        # Show total points
        d.text(
            (total_x, y_pos + 5),
            str(points_sub),
            anchor="mm",
            font=fnt_bold_small,
            fill=color,
        )
        
        # Show new points from latest voter if provided
        if latest_voter_points and submission in latest_voter_points:
            new_points = latest_voter_points[submission]
            if new_points > 0:
                # Choose font size based on points value (similar to live reveal)
                if new_points >= 10:
                    font_choice = fnt_bold
                elif new_points >= 5:
                    font_choice = fnt_bold_small
                else:
                    font_choice = fnt_regular
                
                d.text(
                    (new_x, y_pos + 5),
                    f"+{new_points}",
                    anchor="mm",
                    font=font_choice,
                    fill=color,
                )

    save_path = f"photo_contest/generated_tables/final_results.png"
    img.save(save_path)
    return save_path


def gen_semifinals_boards(
    contest: Contest,
    channel_names: Dict[int, str],
    id2name: Dict[int, str],
) -> List[str]:
    """Generate boards for all semifinal competitions."""
    
    semifinal_competitions = contest.semis_competitions
    generated_files = []

    for i, semifinal in enumerate(semifinal_competitions):
        # Get the channel name for this specific semifinal
        channel_name = strip_emoji(channel_names.get(semifinal.channel_id, f"Category {i+1}"))
        
        # Count votes
        jury_votes = semifinal.count_votes_jury()
        public_votes = semifinal.count_votes_public()

        # Use original submission order (not sorted by score)
        submissions_to_display = semifinal.competing_entries
        
        num_submissions = len(submissions_to_display)
        num_rows = ceil(num_submissions / 2)
        
        # Calculate dynamic height and spacing
        img_height, spacing, start_y, thumb_size = calculate_board_dimensions(num_submissions, "semis")
        
        img = Image.new("RGB", (950, img_height), color=BG_COLOR)
        d = ImageDraw.Draw(img)

        # Title
        semi_title = f"Semi final for {channel_name}"
        semi_bbox = d.textbbox((0, 0), semi_title, font=fnt_bold)
        semi_x = (950 - (semi_bbox[2] - semi_bbox[0])) // 2
        d.text(
            (semi_x, 20),
            semi_title,
            anchor="lt",
            font=fnt_bold,
            fill="white",
        )

        # volt logo - position at bottom right
        logo = Image.open("resource/logo_volt.png")
        logo_y = img_height - 165
        img.paste(logo.resize((150, 150)), (785, logo_y))
        
        # Add column headers for Jury and Public points
        draw_column_headers(d, 80, "semis")
        
        # Determine qualifiers: top 2 by public, then top 3 by jury from remaining = 5 total
        top_public = sorted(
            semifinal.competing_entries,
            key=lambda x: (public_votes.get(x, 0), jury_votes.get(x, 0), -x.submission_time),
            reverse=True,
        )[:2]
        top_jury = sorted(
            [x for x in semifinal.competing_entries if x not in top_public],
            key=lambda x: (jury_votes.get(x, 0), public_votes.get(x, 0), -x.submission_time),
            reverse=True,
        )[:3]
        qualifiers = set(top_public + top_jury)

        # Display submissions in 2 columns (top-to-bottom, left-to-right)
        for j, submission in enumerate(submissions_to_display):
            # Fill left column first (top to bottom), then right column
            if j < num_rows:
                col = 0  # left column
                row = j
            else:
                col = 1  # right column
                row = j - num_rows
            
            x_offset = 110 if col == 0 else 565
            y_pos = start_y + row * spacing
            
            # Photo thumbnail
            thumb = create_thumbnail(submission.local_save_path, thumb_size)
            img.paste(thumb, (x_offset - 80, y_pos - 3))
            
            photo_num = j + 1
            author_name = strip_emoji(id2name.get(submission.author_id, f"User {submission.author_id}"))
            
            # Qualification status
            jury_points = jury_votes.get(submission, 0)
            public_points = public_votes.get(submission, 0)
            
            qualifies = submission in qualifiers
            
            color = "white" if qualifies else "#888888"
            font_regular_choice = fnt_bold_small if qualifies else fnt_regular
            font_bold_choice = fnt_bold if qualifies else fnt_bold_small
            font_italic_choice = fnt_italic if qualifies else fnt_light
            
            d.text(
                (x_offset, y_pos),
                f"Photo #{photo_num}",
                anchor="lm",
                font=font_bold_choice,
                fill=color,
            )
            d.text(
                (x_offset, y_pos + 20),
                f"by {author_name}",
                anchor="lm",
                font=font_italic_choice,
                fill=color,
            )
            
            # Points - show jury and public in separate columns
            # Calculate x positions based on column
            jury_x = 355 if col == 0 else 805
            public_x = 415 if col == 0 else 865
            
            d.text(
                (jury_x, y_pos + 10),
                str(jury_points),
                anchor="mm",
                font=font_regular_choice,
                fill=color,
            )
            d.text(
                (public_x, y_pos + 10),
                str(public_points),
                anchor="mm",
                font=font_regular_choice,
                fill=color,
            )

        # Generate filename with category name
        safe_channel_name = channel_name.replace(" ", "_").replace("-", "_")
        save_path = f"photo_contest/generated_tables/semifinal_{safe_channel_name}.png"
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
    title = f"Photo #{photo_num} in {strip_emoji(channel_name)}" + (
        "#" + strip_emoji(thread_name) if thread_name else ""
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
        f"submitted by {strip_emoji(id2name.get(submission.author_id, f'User {submission.author_id}'))}",
        anchor="mt",
        font=fnt_italic,
        fill="white",
    )

    # volt logo
    logo = Image.open("resource/logo_volt.png")
    img.paste(logo.resize((150, 150)), (585, 435))

    # snippet of the photo (preserve aspect ratio with letterbox)
    snippet = create_thumbnail(submission.local_save_path, (360, 250))
    img.paste(snippet, (20, 100))

    # Get jury vote breakdown from the competition data
    jury_votes_by_id = competition.get_jury_votes_per_juror(submission)
    jury_points_per_juror: Dict[str, int] = {
        strip_emoji(id2name.get(voter_id, f"User {voter_id}")): points
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
    
    # Group jury voters by points
    jury_by_points = {}
    for juror, points in jury_points_per_juror.items():
        if points not in jury_by_points:
            jury_by_points[points] = []
        jury_by_points[points].append(juror)
    
    y_offset = 395
    point_groups = sorted(jury_by_points.keys(), reverse=True)
    
    # Use two columns only if there are more than 5 different point values
    if len(point_groups) > 5:
        mid_point = (len(point_groups) + 1) // 2  # Split into two columns
        
        # Left column
        y_left = y_offset
        for i, points in enumerate(point_groups[:mid_point]):
            voters = jury_by_points[points]
            fnt = (
                fnt_bold_small
                if points >= 12
                else (fnt_regular if points >= 8 else fnt_light)
            )
            
            # Wrap text to fit within available width (170 pixels for left column)
            prefix = f"{points} pt: "
            max_width = 170
            
            # Build lines by adding voters one at a time
            lines = []
            current_line = prefix
            for j, voter in enumerate(voters):
                test_text = current_line + (voter if j == 0 else ", " + voter)
                bbox = d.textbbox((0, 0), test_text, font=fnt)
                text_width = bbox[2] - bbox[0]
                
                if text_width > max_width and current_line != prefix:
                    # Line is too long, start a new line
                    lines.append(current_line)
                    current_line = "  " + voter  # Indent continuation lines
                else:
                    current_line = test_text
            
            if current_line:
                lines.append(current_line)
            
            # Draw all lines
            for line in lines:
                d.text(
                    (50, y_left),
                    line,
                    anchor="lt",
                    fill="white",
                    font=fnt,
                )
                y_left += 25
        
        # Right column
        y_right = y_offset
        for i, points in enumerate(point_groups[mid_point:]):
            voters = jury_by_points[points]
            fnt = (
                fnt_bold_small
                if points >= 12
                else (fnt_regular if points >= 8 else fnt_light)
            )
            
            # Wrap text to fit within available width (170 pixels for right column)
            prefix = f"{points} pt: "
            max_width = 170
            
            # Build lines by adding voters one at a time
            lines = []
            current_line = prefix
            for j, voter in enumerate(voters):
                test_text = current_line + (voter if j == 0 else ", " + voter)
                bbox = d.textbbox((0, 0), test_text, font=fnt)
                text_width = bbox[2] - bbox[0]
                
                if text_width > max_width and current_line != prefix:
                    # Line is too long, start a new line
                    lines.append(current_line)
                    current_line = "  " + voter  # Indent continuation lines
                else:
                    current_line = test_text
            
            if current_line:
                lines.append(current_line)
            
            # Draw all lines
            for line in lines:
                d.text(
                    (230, y_right),
                    line,
                    anchor="lt",
                    fill="white",
                    font=fnt,
                )
                y_right += 25
    else:
        # Single column for 5 or fewer point groups
        for points in point_groups:
            voters = jury_by_points[points]
            fnt = (
                fnt_bold_small
                if points >= 12
                else (fnt_regular if points >= 8 else fnt_light)
            )
            
            # Wrap text to fit within available width (350 pixels from x=50)
            prefix = f"{points} pt: "
            max_width = 350
            
            # Build lines by adding voters one at a time
            lines = []
            current_line = prefix
            for i, voter in enumerate(voters):
                test_text = current_line + (voter if i == 0 else ", " + voter)
                bbox = d.textbbox((0, 0), test_text, font=fnt)
                text_width = bbox[2] - bbox[0]
                
                if text_width > max_width and current_line != prefix:
                    # Line is too long, start a new line
                    lines.append(current_line)
                    current_line = "    " + voter  # Indent continuation lines
                else:
                    current_line = test_text
            
            if current_line:
                lines.append(current_line)
            
            # Draw all lines
            for line in lines:
                d.text(
                    (50, y_offset),
                    line,
                    anchor="lt",
                    fill="white",
                    font=fnt,
                )
                y_offset += 25

    # Get public vote breakdown from the competition data
    public_votes_by_id = competition.get_public_votes_per_voter(submission)
    public_points_per_voter: Dict[str, int] = {
        strip_emoji(id2name.get(voter_id, f"User {voter_id}")): points
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
    
    # Group public voters by points
    public_by_points = {}
    for voter, points in public_points_per_voter.items():
        if points not in public_by_points:
            public_by_points[points] = []
        public_by_points[points].append(voter)
    
    y_offset = 135
    for points in sorted(public_by_points.keys(), reverse=True):
        voters = public_by_points[points]
        fnt = (
            fnt_bold_small
            if points == 3
            else (fnt_regular if points == 2 else fnt_light)
        )
        
        # Wrap text to fit within available width (330 pixels from x=400)
        prefix = f"{points} pt: "
        max_width = 330
        
        # Build lines by adding voters one at a time
        lines = []
        current_line = prefix
        for i, voter in enumerate(voters):
            test_text = current_line + (voter if i == 0 else ", " + voter)
            bbox = d.textbbox((0, 0), test_text, font=fnt)
            text_width = bbox[2] - bbox[0]
            
            if text_width > max_width and current_line != prefix:
                # Line is too long, start a new line
                lines.append(current_line)
                current_line = "    " + voter  # Indent continuation lines
            else:
                current_line = test_text
        
        if current_line:
            lines.append(current_line)
        
        # Draw all lines
        for line in lines:
            d.text(
                (400, y_offset),
                line,
                anchor="lt",
                fill="white",
                font=fnt,
            )
            y_offset += 25
        
        # Add extra spacing between different point groups (public votes)
        y_offset += 10

    # Save in generated_tables with category-based naming
    safe_category = re.sub(r"[^A-Za-z0-9]+", "", strip_emoji(channel_name))
    if not safe_category:
        safe_category = f"Category{competition.channel_id}"

    if competition.type == "qualif" and competition.thread_id is not None:
        filename = (
            f"photo_contest/generated_tables/"
            f"photo_{competition.type}_{safe_category}_{competition.thread_id}_{photo_num}.png"
        )
    else:
        filename = (
            f"photo_contest/generated_tables/"
            f"photo_{competition.type}_{safe_category}_{photo_num}.png"
        )
    img.save(filename)
    return filename


def gen_winner_announcement_board(
    winner: Submission,
    all_finalists: list[Submission],
    final_scores: Dict[Submission, int],
    category_name: str,
    id2name: Dict[int, str],
    final_competition: Optional[CompetitionInfo] = None,
) -> str:
    """Generate winner announcement board with winner photo and support recap."""
    
    img = Image.new("RGB", (900, 500), color=BG_COLOR)
    d = ImageDraw.Draw(img)

    # Title
    d.text(
        (450, 20),
        f"Winner for {strip_emoji(category_name)}",
        anchor="mt",
        font=fnt_bold,
        fill="white",
    )

    def same_submission(left: Submission, right: Submission) -> bool:
        if left == right:
            return True
        if left.discord_save_path and right.discord_save_path:
            if left.discord_save_path.rstrip("/") == right.discord_save_path.rstrip("/"):
                return True
        if left.local_save_path and right.local_save_path:
            if left.local_save_path == right.local_save_path:
                return True
            if os.path.basename(left.local_save_path) == os.path.basename(right.local_save_path):
                return True
        return left.author_id == right.author_id and left.submission_time == right.submission_time

    def score_for_submission(submission: Submission) -> int:
        if submission in final_scores:
            return final_scores[submission]
        for scored_submission, points in final_scores.items():
            if same_submission(scored_submission, submission):
                return points
        return 0

    # Sort finalists by score
    ranked = sorted(
        all_finalists,
        key=lambda x: (score_for_submission(x), -x.submission_time),
        reverse=True
    )
    resolved_winner = ranked[0] if ranked else winner

    # Winner photo (large, on the left) - preserve aspect ratio with letterboxing
    winner_photo = Image.open(resolved_winner.local_save_path)
    winner_photo.thumbnail((400, 350), Image.Resampling.LANCZOS)
    bg = Image.new("RGB", (400, 350), color=BG_COLOR)
    x_offset = (400 - winner_photo.width) // 2
    y_offset = (350 - winner_photo.height) // 2
    bg.paste(winner_photo, (x_offset, y_offset))
    img.paste(bg, (20, 60))

    # Winner text
    author_name = strip_emoji(id2name.get(resolved_winner.author_id, f"User {resolved_winner.author_id}"))
    congrats_text = f"Congratulations, {author_name}!"
    congrats_font = fnt_bold
    max_congrats_width = 390
    for candidate_font in (fnt_bold, fnt_bold_small, fnt_regular, fnt_light):
        bbox = d.textbbox((0, 0), congrats_text, font=candidate_font)
        if bbox[2] - bbox[0] <= max_congrats_width:
            congrats_font = candidate_font
            break
    else:
        fallback_name = author_name
        while fallback_name:
            fallback_name = fallback_name[:-1]
            truncated = f"Congratulations, {fallback_name}...!"
            bbox = d.textbbox((0, 0), truncated, font=fnt_light)
            if bbox[2] - bbox[0] <= max_congrats_width:
                congrats_text = truncated
                congrats_font = fnt_light
                break
        if not fallback_name:
            congrats_text = "Congratulations!"
            congrats_font = fnt_light

    d.text(
        (220, 440),
        congrats_text,
        anchor="mm",
        font=congrats_font,
        fill="white",
    )

    # Winner recap on the right
    d.text(
        (680, 60),
        "Why this photo won",
        anchor="mt",
        font=fnt_bold_small,
        fill="white",
    )

    # Winner total and lead over second place
    winner_points = score_for_submission(resolved_winner)
    second_points = score_for_submission(ranked[1]) if len(ranked) > 1 else 0
    lead = winner_points - second_points

    d.text((470, 100), f"Total: {winner_points} pts", anchor="lt", font=fnt_bold_small, fill="white")
    if len(ranked) > 1:
        if lead > 0:
            lead_text = f"Lead: +{lead} over #2"
        elif lead == 0:
            lead_text = "Lead: tied with #2"
        else:
            lead_text = f"Lead: {lead} vs #2"
        d.text((470, 128), lead_text, anchor="lt", font=fnt_regular, fill="white")

    # Jury support recap for top point groups (7 and 5)
    votes_by_id: Dict[int, int] = {}

    if final_competition:
        for voter_id, jury_vote in final_competition.votes_jury.items():
            submission_points = jury_vote.points_to_submissions()
            for submission, points in submission_points.items():
                if points > 0 and same_submission(submission, resolved_winner):
                    votes_by_id[voter_id] = points
                    break

    supporters_by_points: Dict[int, List[str]] = {}
    for voter_id, points in votes_by_id.items():
        voter_name = strip_emoji(id2name.get(voter_id, f"User {voter_id}"))
        supporters_by_points.setdefault(points, []).append(voter_name)

    for points in supporters_by_points:
        supporters_by_points[points].sort(key=str.casefold)

    preferred_groups = [7, 5, 3]
    has_preferred_support = any(supporters_by_points.get(points) for points in preferred_groups)
    if has_preferred_support:
        groups_to_show = preferred_groups
    else:
        groups_to_show = sorted(
            [points for points, names in supporters_by_points.items() if names],
            reverse=True,
        )[:2]
        if not groups_to_show:
            groups_to_show = preferred_groups

    def draw_wrapped_support(prefix: str, names: List[str], y_start: int) -> int:
        if not names:
            d.text((470, y_start), f"{prefix} —", anchor="lt", font=fnt_light, fill="#DDDDDD")
            return y_start + 22

        lines: List[str] = []
        current_line = f"{prefix} "
        for idx, name in enumerate(names):
            candidate = current_line + (name if idx == 0 else f", {name}")
            bbox = d.textbbox((0, 0), candidate, font=fnt_regular)
            if bbox[2] - bbox[0] > 390 and current_line != f"{prefix} ":
                lines.append(current_line)
                current_line = f"   {name}"
            else:
                current_line = candidate

        if current_line:
            lines.append(current_line)

        y_cursor = y_start
        for line in lines:
            d.text((470, y_cursor), line, anchor="lt", font=fnt_regular, fill="white")
            y_cursor += 22
        return y_cursor

    y_offset = 170
    d.text((470, y_offset), "Top support", anchor="lt", font=fnt_bold_small, fill="white")
    y_offset += 28
    for idx, points in enumerate(groups_to_show):
        y_offset = draw_wrapped_support(f"{points} pts:", supporters_by_points.get(points, []), y_offset)
        if idx == 0:
            y_offset += 4

    # Volt logo
    logo = Image.open("resource/logo_volt.png")
    img.paste(logo.resize((100, 100)), (780, 385))

    # Save
    safe_name = category_name.replace(" ", "_").replace("-", "_")
    filename = f"photo_contest/generated_tables/winner_{safe_name}.png"
    img.save(filename)
    return filename


def gen_live_final_reveal(
    competition: CompetitionInfo,
    category_name: str,
    id2name: Dict[int, str],
) -> Iterator[Tuple[str, Optional[int], bool, bool]]:
    """Generate live Eurovision-style voting reveal boards for final competition.
    
    Yields boards one at a time as votes are revealed in random order.
    
    Args:
        competition: The final competition to reveal results for
        category_name: Name of the category for display
        id2name: Mapping of user IDs to display names
    
    Yields:
        Tuple of (board_path, voter_id, is_initial, is_final)
        - board_path: Path to the generated PNG file
        - voter_id: ID of the voter whose votes are being shown (None for initial/final)
        - is_initial: True if this is the initial zero-score board
        - is_final: True if this is the final results board
    """
    # Get jury votes and randomize order for suspense
    jury_votes_list = list(competition.votes_jury.items())
    shuffle(jury_votes_list)
    
    # Get competing entries
    entries = competition.competing_entries
    num_entries = len(entries)
    
    # Determine points system based on number of entries
    # Uses POINTS_SETS from photo_contest_data.py (same as JuryVote.points_to_submissions())
    # Find the closest valid ranking length (should match JuryVote validation)
    if num_entries >= 10:
        points = POINTS_SETS[10][:num_entries]
    elif num_entries >= 6:
        points = POINTS_SETS[6][:num_entries]
    else:
        points = POINTS_SETS[4][:num_entries]
    
    # Calculate layout
    half_nb_entries = ceil(num_entries / 2)
    
    # Create mapping from submission to photo number
    sub_to_num = {sub: i + 1 for i, sub in enumerate(entries)}
    
    # Initialize running totals
    running_totals: Dict[Submission, int] = {sub: 0 for sub in entries}
    
    def draw_base() -> Tuple[Any, Any]:
        """Create base image with title and logo."""
        img = Image.new("RGB", (750, 600), color=BG_COLOR)
        d = ImageDraw.Draw(img)
        
        title = f"Winner for {category_name}"
        d.text((375, 20), title, anchor="mt", font=fnt_bold, fill="white")
        
        # Volt logo
        logo = Image.open("resource/logo_volt.png")
        img.paste(logo.resize((150, 150)), (585, 435))
        
        return img, d
    
    def draw_scores(d: Any, new_voter_points: Optional[Dict[Submission, int]] = None, voter_top_points: Optional[Tuple[int, int]] = None):
        """Draw current scores and optionally highlight new points.
        
        Args:
            new_voter_points: Points from the latest voter to highlight
            voter_top_points: Tuple of (top_point, second_point) from voter's ranking for font highlighting
        """
        # Sort by score (highest first), then by submission time (earliest first as tiebreaker)
        ranked_entries = sorted(
            entries,
            key=lambda x: (running_totals[x], -x.submission_time),
            reverse=True
        )
        
        # Determine the top point values to use for highlighting
        if voter_top_points:
            top_p1, top_p2 = voter_top_points
        else:
            top_p1, top_p2 = points[0], points[1]  # Fallback to competition-based points
        
        for i, sub in enumerate(ranked_entries):
            photo_num = sub_to_num[sub]
            title = f"Photo #{photo_num}"
            
            # Position in grid (2 columns)
            x_title = 50 if i < half_nb_entries else 400
            y = 150 + 125 * (i % half_nb_entries)
            
            # Draw photo number and total score
            d.text((x_title, y), title, anchor="lm", font=fnt_bold, fill="white")
            d.text((x_title + 325, y), str(running_totals[sub]), anchor="rm", font=fnt_bold, fill="white")
            
            # Underline the leader
            if i == 0:
                x1, _, x2, y2 = d.textbbox((x_title, y), title, anchor="lm", font=fnt_bold)
                d.line((x1, y2 + 5, x2, y2 + 5), width=2, fill="white")
            
            # Show new points if provided
            if new_voter_points and sub in new_voter_points and new_voter_points[sub] > 0:
                nbP = new_voter_points[sub]
                font_choice = fnt_bold if nbP == top_p1 else (fnt_bold_small if nbP == top_p2 else fnt_regular)
                d.text((x_title + 250, y), str(nbP), anchor="lm", font=font_choice, fill="white")
    
    # Generate save path
    safe_category = category_name.replace(" ", "_").replace("-", "_")
    save_file = f"photo_contest/generated_tables/live_reveal_{safe_category}.png"
    
    # Initial board with all zeros
    img, d = draw_base()
    for i, sub in enumerate(entries):
        photo_num = sub_to_num[sub]
        x_title = 50 if i < half_nb_entries else 400
        y = 150 + 125 * (i % half_nb_entries)
        d.text((x_title, y), f"Photo #{photo_num}", anchor="lm", font=fnt_bold, fill="white")
        d.text((x_title + 325, y), "0", anchor="rm", font=fnt_bold, fill="white")
    
    img.save(save_file)
    yield save_file, None, True, False
    
    # Reveal each voter's contribution
    total_voters = len(jury_votes_list)
    for vote_index, (voter_id, jury_vote) in enumerate(jury_votes_list, start=1):
        # Calculate points from this voter using the same logic as count_votes_jury()
        # This uses the ranking length from each vote, not the number of entries
        voter_points: Dict[Submission, int] = jury_vote.points_to_submissions()
        
        # Get the voter's top point values for font highlighting
        sorted_points = sorted(voter_points.values(), reverse=True) if voter_points else [0, 0]
        voter_top_points = (sorted_points[0], sorted_points[1] if len(sorted_points) > 1 else 0)
        
        # Add to running totals
        for sub, nbP in voter_points.items():
            running_totals[sub] += nbP
        
        # Generate board
        img, d = draw_base()
        voter_name = strip_emoji(id2name.get(voter_id, f"Juror {voter_id}"))
        d.text(
            (375, 60),
            f"After votes from {voter_name} ({vote_index}/{total_voters} voters)",
            anchor="mt",
            font=fnt_bold_small,
            fill="white",
        )
        draw_scores(d, voter_points, voter_top_points)
        
        img.save(save_file)
        yield save_file, voter_id, False, False
    
    # Final results board
    img, d = draw_base()
    d.text((375, 60), "Final results", anchor="mt", font=fnt_bold, fill="white")
    draw_scores(d)
    
    img.save(save_file)
    yield save_file, None, False, True
