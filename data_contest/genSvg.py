from cairosvg import svg2png
from math import ceil
from random import shuffle
import os

songs = ["Cyprus", "Serbia", "Lithuania", "Ireland", "Ukraine", "Poland", "Croatia", "Iceland", "Slovenia", "Finland", "Moldova", "Azerbaijan", "Australia", "Portugal", "Luxembourg"]

beginningSvg = """<svg xmlns="http://www.w3.org/2000/svg" width="700" height="500" viewBox="0 0 700 500">
    <style type="text/css">
        @import url('https://fonts.googleapis.com/css2?family=Ubuntu+Sans:ital,wght@0,100..800;1,100..800');
        text{font-family:'Ubuntu Sans';} 
    </style>
    <rect x="0" y="0" width="700" height="500" style="fill: #502379;" />

    <text x="350" y="20" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="30">"""

def readVotes():
    voters = set()
    def extractVote(line):
        idSong, voter, points = line.split(";")
        if voter != "public": voters.add(voter)
        return (songs[int(idSong)-1], voter, int(points))

    with open(os.path.join(os.path.dirname(__file__), "votes_new.csv"), "r") as f:
        f.readline() #first line is useless
        return list(map(extractVote, f.readlines())), list(voters)

def seekFlag(countryName, x, y):
    with open(os.path.join(os.path.dirname(__file__), countryName.replace(" ", "_") + ".svg"), "r") as f:
        code = "".join(f.readlines())
    
    return code[:4] + f' height="48" width="48" x="{x}" y="{y}" preserveAspectRatio="none"' + code[4:]

def svgCountry(countryName, x, y, currentPointsCountry, newPointsCountry, highlight = False) -> str:
    codeCountry = f"""    <rect x="{10+x*350}" y="{50+y*50}" width="330" height="50" style="fill: #502379; stroke-width:2;stroke:white;" />
    {seekFlag(countryName, 11+x*350, 51+y*50)}
    <rect x="{290+x*350}" y="{51+y*50}" height="48" width="48" fill="#3F086F" />
    <text x="{70+x*350}" y="{75+y*50}" dominant-baseline="middle" fill="white" font-size="25">
    {countryName}
    </text>
    <text x="{315+x*350}" y="{75+y*50}" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="23">
    {currentPointsCountry}
    </text>
"""
    if newPointsCountry is not None:
        if highlight: codeCountry += f"""<rect x="{240+x*350}" y="{51+y*50}" height="48" width="48" fill="white" />\n"""
        codeCountry += f"""<text x="{265+x*350}" y="{75+y*50}" text-anchor="middle" dominant-baseline="middle" fill="{'#3F086F' if highlight else 'white'}" font-size="23">{newPointsCountry}</text>"""

    return codeCountry

def xyFromI(i):
    nbCountriesLeft = ceil(len(songs) / 2)
    return i // nbCountriesLeft , i % nbCountriesLeft

def genSvgUser(votes, currentPoints, username) -> str:
    votesUser = {country: points for country, voter, points in votes if voter == username}
    for country, points in votesUser.items():
        currentPoints[country] += points

    svgCode = [beginningSvg]
    def printF(*args): 
        svgCode[0] += " ".join(str(x) for x in args) + "\n"

    printF("Votes of", username, "</text>")
    printF()

    for i, (country, points) in enumerate(sorted(currentPoints.items(), key=lambda x: x[1], reverse=True)):
        pointsFromUser = votesUser.get(country, None)
        printF(svgCountry(country, *xyFromI(i), points, pointsFromUser, pointsFromUser == 12 and username != "public"))
    
    printF("</svg>")

    with open(os.path.join(os.path.dirname(__file__), "test.svg"), "w") as f:
        f.write(svgCode[0])
    
    return svgCode[0]

def generateSvgs():
    votes, voters = readVotes()
    shuffle(voters)
    currentPoints = {country: 0 for country in songs}

    for i, voter in enumerate(voters):
        svg2png(bytestring = genSvgUser(votes, currentPoints, voter), write_to = os.path.join(os.path.dirname(__file__), "test.png"))
        yield os.path.join(os.path.dirname(__file__), "test.png"), voter, voters[i+1] if i < len(voters)-1 else None
    
    svg2png(bytestring = genSvgUser(votes, currentPoints, "public"), write_to=os.path.join(os.path.dirname(__file__), "test.png"))
    yield os.path.join(os.path.dirname(__file__), "test.png"), "public", None

if __name__ == "__main__":
    for _ in generateSvgs():
        input()