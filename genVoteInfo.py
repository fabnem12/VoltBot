from PIL import Image, ImageDraw, ImageFont
from typing import Dict, List, Optional, Set, Tuple
from random import shuffle
from math import ceil

BG_COLOR = "#502379"
Submission = Tuple[str, int, int]
PhotoId = Tuple[int, str, str, int] #photo #, url, local path and id of the author

fnt_light = ImageFont.truetype('resource/Ubuntu-Light.ttf', 15)
fnt_italic = ImageFont.truetype('resource/Ubuntu-LightItalic.ttf', 15)
fnt_regular = ImageFont.truetype('resource/Ubuntu-Regular.ttf', 20)
fnt_bold_small = ImageFont.truetype('resource/Ubuntu-Bold.ttf', 20)
fnt_bold = ImageFont.truetype('resource/Ubuntu-Bold.ttf', 30)

points_full = [12, 10, 8, 7, 6, 5, 4, 3, 2, 1]
points_part = [7, 5, 3, 2, 1, 0]

def genSemiThread(channel_name: str, photo_id: PhotoId, id2name: Dict[int, str], votes_jury: Dict[int, List[Submission]], votes_normal: List[Tuple[int, str]], thread_name: Optional[str] = None) -> str:
    photo_num, photo_url, photo_path, photo_author = photo_id

    img = Image.new("RGB", (750, 600), color = BG_COLOR)
    d = ImageDraw.Draw(img) #draw on the image

    title = f"Photo #{photo_num} in {channel_name}" + ("#"+thread_name if thread_name else "")
    d.text((375, 20), title, anchor="mt", font=fnt_bold if len(title) < 46 else fnt_bold_small, fill="white")
    d.text((375, 50), f"submitted by {id2name[photo_author]}", anchor="mt", font=fnt_italic, fill="white")

    #volt logo
    logo = Image.open("resource/logo_volt.png")
    img.paste(logo.resize((150, 150)), (585, 435))

    #snippet of the photo
    img_snippet = Image.open(photo_path)
    width_snip = min(round(250 * img_snippet.size[0] / img_snippet.size[1]), 360)
    left_img = 20 + (360 - width_snip)//2
    img.paste(img_snippet.resize((width_snip, 250)), (left_img, 100))

    #jury votes under the image
    points_per_juror: Dict[str, int] = {id2name[i]: 0 for i in votes_jury if int(i) != int(photo_author)}
    for voter_id, ranking in votes_jury.items():
        if isinstance(ranking, list): #vote with full ranking
            for nbP, (url, _, _) in zip(points_full, ranking):
                if url == photo_url:
                    points_per_juror[id2name[voter_id]] = nbP
                    break
        else: #vote with upvote only
            points_per_juror[id2name[voter_id]] = ranking
    total_jury_points = sum(points_per_juror.values())
    
    d.text((50, 365), f"{total_jury_points} point{'s' if total_jury_points != 1 else ''} from the jury", anchor="lt", font=fnt_bold_small, fill="white")
    for i, (juror, points) in enumerate(sorted(points_per_juror.items(), key=lambda x: x[1], reverse=True)):
        fnt = fnt_bold_small if points == 12 else (fnt_regular if points in (8, 10) else fnt_light)
        d.text((50, 395+i*25), f"{points} point{'s' if points != 1 else ''} from {juror}", anchor="lt", fill="white", font=fnt)

    #global votes
    points_per_voter: Dict[str, int] = dict()
    for voter_id, url in votes_normal:
        if url == photo_url and voter_id != photo_author: #the 2nd test is for initial votes, because such bad votes are still there yet not counted
            voter = id2name[voter_id]
            points_per_voter[voter] = points_per_voter.get(voter, 0) + 1
    total_points = sum(points_per_voter.values())

    d.text((400, 100), f"{total_points} point{'s' if total_points != 1 else ''} from the global vote", anchor="lt", font=fnt_bold_small, fill="white")
    for i, (voter, points) in enumerate(sorted(points_per_voter.items(), key=lambda x: x[1], reverse=True)):
        fnt = fnt_bold_small if points == 3 else (fnt_regular if points == 2 else fnt_light)
        d.text((400, 135+i*25), f"{points} point{'s' if points != 1 else ''} from {voter}", anchor="lt", fill="white", font=fnt)

    img.save(photo_path+"_result.png")
    return photo_path+"_result.png"

def genVoteAnimFinal(channel_name: str, sub2photoid: Dict[Submission, int], id2name: Dict[int, str], votes: Dict[int, List[Submission]], jurors: Set[int], gf1: bool = True):
    points = [7, 5, 3, 2, 1, 0] if gf1 else [4, 2, 1, 0]
    nb_entries = len(points)
    half_nb_entries = ceil(nb_entries / 2)
    ordered_votes = list(votes.items())
    shuffle(ordered_votes)
    save_file = f"data_contest/{channel_name}.png"

    def drawBase() -> Tuple[Image.Image, ImageDraw.Draw]:
        img = Image.new("RGB", (750, 600), color = BG_COLOR)
        d = ImageDraw.Draw(img)

        title = f"Winner for {channel_name}"
        d.text((375, 20), title, anchor="mt", font=fnt_bold, fill="white")

        #volt logo
        logo = Image.open("resource/logo_volt.png")
        img.paste(logo.resize((150, 150)), (585, 435))

        return img, d

    def partialPoints(i: Optional[int] = None) -> Tuple[Dict[Submission, int], Dict[Submission, int], Dict[Submission, int]]:
        count_glob: Dict[Submission, int] = dict()
        count_jurors: Dict[Submission, int] = dict()

        def points_from_voter(ranking: List[Submission]):
            return {tuple(sub): nbP for sub, nbP in zip(ranking, points)}

        new_points: Dict[Submission, int] = dict()
        for voter, ranking in ordered_votes[:(i+1) if i is not None else len(ordered_votes)]:
            isJuror = voter in jurors
            new_points = points_from_voter(ranking)

            for sub, nbP in new_points.items():
                count_glob[sub] = count_glob.get(sub, 0) + nbP

                if isJuror:
                    count_jurors[sub] = count_jurors.get(sub, 0) + nbP
        
        return count_glob, count_jurors, new_points if i else {}

    def drawPoints(d: ImageDraw.Draw, glob: Dict[Submission, int], jury: Dict[Submission, int], new_points: Dict[Submission, int]):
        ranked_subs = sorted(glob.items(), key=lambda x: (x[1], jury.get(x[0], 0), -x[0][2]), reverse=True)

        for i, (sub, nbP) in enumerate(ranked_subs):
            title = f"Photo #{sub2photoid[sub]}"
            x_title = 50 if i < half_nb_entries else 400
            y = 150 + 125 * (i % half_nb_entries)
            d.text((x_title, y), title, anchor="lm", font=fnt_bold, fill="white")
            d.text((x_title + 325, y), str(nbP), anchor="rm", font=fnt_bold, fill="white")
            
            if i == 0: #underline the winner
                x1, _, x2, y2 = d.textbbox((x_title, y), title, anchor="lm", font=fnt_bold)
                d.line((x1, y2+5, x2, y2+5), width=2, fill="white")

            if new_points.get(sub):
                nbP = new_points[sub]
                d.text((x_title + 250, y), str(nbP), anchor="lm", font=fnt_bold if nbP == points[0] else (fnt_bold_small if nbP == points[1] else fnt_regular), fill="white")

    #initial score board
    img, d = drawBase()
    for i in range(len(points)):
        d.text((50 + 350 * (i >= half_nb_entries), 150 + 125 * (i % half_nb_entries)), f"Photo #{i+1}", anchor="lm", font=fnt_bold, fill="white")
        d.text((375 + 350 * (i < half_nb_entries), 150 + 125 * (i % half_nb_entries)), "0", anchor="rm", font=fnt_bold, fill="white")
    
    img.save(save_file)
    yield save_file, None
    
    #votes of each voter
    for i, (voter, _) in enumerate(ordered_votes):
        img, d = drawBase()
        d.text((375, 60), f"Votes by {id2name[voter]}", anchor="mt", font=fnt_bold_small, fill="white")

        glob, jury, new_points = partialPoints(i)
        drawPoints(d, glob, jury, new_points)

        img.save(save_file)
        yield save_file, voter
    
    #final results
    img, d = drawBase()
    d.text((375, 60), "Final results", anchor="mt", font=fnt_bold, fill="white")
    drawPoints(d, *partialPoints())

    img.save(save_file)
    yield save_file, -1