from cairosvg import svg2png
from math import ceil
from random import shuffle
import os

#songs = ["Iceland", "Poland", "Slovenia", "Estonia", "Ukraine", "Sweden", "Portugal", "Norway", "Belgium", "Azerbaijan", "San Marino", "Albania", "Netherlands", "Croatia", "Cyprus"]
#songs = ["Australia", "Montenegro", "Ireland", "Latvia", "Armenia", "Austria", "Greece", "Lithuania", "Malta", "Georgia", "Denmark", "Czechia", "Luxembourg", "Israel", "Serbia", "Finland"]
songs = ["Norway", "Luxembourg", "Estonia", "Israel", "Lithuania", "Spain", "Ukraine", "UK", "Austria", "Iceland", "Latvia", "Netherlands", "Finland", "Italy", "Poland", "Germany", "Greece", "Armenia", "Switzerland", "Malta", "Portugal", "Denmark", "Sweden", "France", "San Marino", "Albania"]

boardHeight = 100+57*ceil(len(songs) / 2)
beginningSvg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="700" height="{boardHeight}" viewBox="0 0 700 {boardHeight}">
    <style type="text/css">
        @import url('https://fonts.googleapis.com/css2?family=Ubuntu+Sans:ital,wght@0,100..800;1,100..800');
        text{{font-family:'Ubuntu Sans';}} 
    </style>
    <rect x="0" y="0" width="700" height="{boardHeight}" style="fill: #502379;" />

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
    
    return code[:4] + f' height="55" width="55" x="{x}" y="{y}" preserveAspectRatio="none"' + code[4:]

def svgCountry(countryName, x, y, currentPointsCountry, newPointsCountry, highlight = False) -> str:
    codeCountry = f"""    <rect x="{10+x*350}" y="{50+y*57}" width="330" height="57" style="fill: #502379; stroke-width:2;stroke:white;" />
    {seekFlag(countryName, 11+x*350, 51+y*57)}
    <rect x="{283+x*350}" y="{51+y*57}" height="55" width="55" fill="#3F086F" />
    <text x="{77+x*350}" y="{78.5+y*57}" dominant-baseline="middle" fill="white" font-size="25">
    {countryName}
    </text>
    <text x="{312.5+x*350}" y="{78.5+y*57}" text-anchor="middle" dominant-baseline="middle" fill="white" font-size="23">
    {currentPointsCountry}
    </text>
"""
    if newPointsCountry is not None:
        if highlight: codeCountry += f"""<rect x="{229.5+x*350}" y="{51+y*57}" height="55" width="55" fill="white" />\n"""
        codeCountry += f"""<text x="{258+x*350}" y="{78.5+y*57}" text-anchor="middle" dominant-baseline="middle" fill="{'#3F086F' if highlight else 'white'}" font-size="23">{newPointsCountry}</text>"""

    return codeCountry

def xyFromI(i):
    nbCountriesLeft = ceil(len(songs) / 2)
    return i // nbCountriesLeft , i % nbCountriesLeft

def genSvgUser(votes, currentPoints, username, changePoints = True) -> str:
    votesUser = {country: points for country, voter, points in votes if voter == username}
    if changePoints:
        for country, points in votesUser.items():
            currentPoints[country] += points

    svgCode = [beginningSvg]
    def printF(*args): 
        svgCode[0] += " ".join(str(x) for x in args) + "\n"

    printF("Votes of", username, "</text>")
    printF()

    for i, (country, points) in enumerate(sorted(currentPoints.items(), key=lambda x: (x[1], -votesUser.get("public", 0), -songs.index(x[0])), reverse=True)):
        pointsFromUser = votesUser.get(country, None)
        printF(svgCountry(country, *xyFromI(i), points, pointsFromUser, pointsFromUser == 12 and username != "public"))
    
    printF("</svg>")

    with open(os.path.join(os.path.dirname(__file__), "test.svg"), "w") as f:
        f.write(svgCode[0])
    
    return svgCode[0]

pathPng = os.path.join(os.path.dirname(__file__), "test.png")

def generateSvgs():
    votes, voters = readVotes()
    shuffle(voters)
    currentPoints = {country: 0 for country in songs}

    for i, voter in enumerate(voters):
        svg2png(bytestring = genSvgUser(votes, currentPoints, voter), write_to = pathPng)
        yield pathPng, voter, voters[i+1] if i < len(voters)-1 else None
    
    svg2png(bytestring = genSvgUser(votes, currentPoints, "the jury"), write_to = pathPng)
    yield pathPng, "jurors", None

    juryTop = sorted(currentPoints, key=lambda x: (currentPoints[x], -songs.index(x)))
    votesPublic = {song: points for song, voter, points in votes if voter == "public"}
    countedPublicVotes = []
    for i, song in enumerate(juryTop):
        votesPublicSong = votesPublic[song]
        currentPoints[song] += votesPublicSong
        countedPublicVotes.append((song, "public", votesPublicSong))

        svg2png(bytestring = genSvgUser(countedPublicVotes, currentPoints, "public", False), write_to = pathPng)
        yield pathPng, "public", (song, votesPublicSong) if i+1 < len(juryTop) else None

    svg2png(bytestring = genSvgUser(votes, currentPoints, "the server"), write_to = pathPng)
    yield pathPng, "voters from the server", None

if __name__ == "__main__":
    for _ in generateSvgs():
        input()