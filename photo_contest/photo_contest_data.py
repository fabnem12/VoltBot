from copy import deepcopy
from dataclasses import asdict, dataclass, field
from itertools import groupby
from random import shuffle
from time import time
from typing import Literal, Optional, Union

import yaml


@dataclass
class Submission:
    author_id: int
    submission_time: int  # timestamp
    local_save_path: str
    discord_save_path: str


@dataclass
class JuryVote:
    voter_id: int
    ranking: list[Submission]

    def __post_init__(self):
        valid_ranking_lengths = {10, 6, 4}

        ranking = self.ranking
        if len(ranking) not in valid_ranking_lengths:
            raise ValueError(
                f"The length ({len(ranking)}) of the ranking is not valid."
            )

        if any(self.voter_id == submission.author_id for submission in ranking):
            raise ValueError(f"A juror cannot vote for their own submissions.")

    def points_to_submissions(self):
        ranking = self.ranking
        points_sets: dict[int, list[int]] = {
            10: [12, 10, 8, 7, 6, 5, 4, 3, 2, 1],
            6: [7, 5, 3, 2, 1, 0],
            4: [4, 2, 1, 0],
        }

        return {
            submission: points
            for submission, points in zip(ranking, points_sets[len(ranking)])
        }


@dataclass
class PublicVote:
    voter_id: int
    nb_points: Union[Literal[0], Literal[1], Literal[2], Literal[3]]
    submission: Submission


@dataclass
class JuryCommentary:
    author_id: int
    submission: Submission
    text: str


@dataclass
class Period:
    start: int  # timestamp
    end: int  # timestamp


@dataclass
class CompetitionInfo:
    type: Union[
        Literal["submission"], Literal["qualif"], Literal["semis"], Literal["final"]
    ]
    channel_id: int
    start_time: int  # timestamp
    end_time: int  # timestamp
    thread_id: Optional[int] = None
    competing_entries: list[Submission] = field(default_factory=list)
    msg_to_sub: dict[int, int] = field(default_factory=dict)  # message_id -> index within self.competing_entries
    votes_jury: dict[int, JuryVote] = field(default_factory=dict)  # voter_id -> vote
    votes_public: dict[tuple[int, Submission], PublicVote] = field(default_factory=dict)  # (voter_id, submission) -> public_vote
    jury_commentaries: list[JuryCommentary] = field(default_factory=list)

    def add_sub(self, submission: Submission) -> "CompetitionInfo":
        copy = deepcopy(self)
        copy.competing_entries.append(submission)

        return copy

    def add_jury_vote(self, vote: JuryVote) -> "CompetitionInfo":
        copy = deepcopy(self)
        copy.votes_jury[vote.voter_id] = vote

        return copy

    def add_public_vote(self, vote: PublicVote) -> "CompetitionInfo":
        copy = deepcopy(self)
        if vote.voter_id == vote.submission.author_id:
            raise ValueError("Not allowed to vote for yourself")

        copy.votes_public[vote.voter_id, vote.submission] = vote

        return copy

    def count_votes_jury(self) -> dict[Submission, int]:
        points: dict[Submission, int] = dict()

        for vote in self.votes_jury.values():
            for sub, nb_points in vote.points_to_submissions().items():
                points[sub] = points.get(sub, 0) + nb_points

        return points

    def count_votes_public(self) -> dict[Submission, int]:
        points: dict[Submission, int] = dict()

        for vote in self.votes_public.values():
            sub = vote.submission
            points[sub] = points.get(sub, 0) + vote.nb_points

        return points


@dataclass
class Schedule:  # all of the fields are tuples of timestamps (start, end)
    submission_period: Period
    qualif_period: Period
    semis_period: Period
    final_period: Period


def split_entries_categ(categ_info: CompetitionInfo) -> list[list[Submission]]:
    # shuffle the list of entries, and group by contestant
    # so that each contestant's entries are evenly split among threads
    entries = categ_info.competing_entries
    shuffle(entries)

    entries_per_contestant = dict(groupby(entries, key=lambda x: x.author_id))
    contestants = list(entries_per_contestant.keys())
    shuffle(contestants)

    entries_randomized = [
        e for contestant in contestants for e in entries_per_contestant[contestant]
    ]

    def split_into_threads(
        n_photos: int, min_thread_size: int = 12, max_thread_size: int = 24
    ):
        best_distribution = None

        for n_threads in range(1, n_photos + 1):
            base_size = n_photos // n_threads
            extra = n_photos % n_threads

            # Check that the "biggest" thread is above max_thread_size
            if base_size + 1 > max_thread_size:
                continue
            if base_size < min_thread_size:
                break

            sizes = [base_size + 1] * extra + [base_size] * (n_threads - extra)

            if all(min_thread_size <= s <= max_thread_size for s in sizes):
                best_distribution = sizes
                break

        if not best_distribution:
            # fallback simple : threads as evenly spread as possible
            n_threads = max(1, n_photos // max_thread_size)
            base_size = n_photos // n_threads
            extra = n_photos % n_threads
            best_distribution = [base_size + 1] * extra + [base_size] * (
                n_threads - extra
            )

        return best_distribution

    thread_lenghts = split_into_threads(len(entries))
    threads: list[list[Submission]] = [[] for _ in range(len(thread_lenghts))]

    for i, e in enumerate(entries_randomized):
        threads[i % len(threads)].append(e)

    return threads


@dataclass
class Contest:
    competitions: list[CompetitionInfo]
    schedule: Schedule
    submissions: list[Submission] = field(default_factory=list)

    @property
    def contestants(self):
        return set(x.author_id for x in self.submissions)

    @property
    def current_competitions(self) -> list[CompetitionInfo]:
        ts = time()
        return list(
            filter(lambda x: x.start_time <= ts and ts < x.end_time, self.competitions)
        )

    @staticmethod
    def from_file(path: str) -> "Contest":
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        schedule = Schedule(
            **{
                key: Period(**value)
                for key, value in data["schedule"].items()
            }
        )
        competitions = [CompetitionInfo(**comp) for comp in data["competitions"]]
        submissions = [Submission(**sub) for sub in data.get("submissions", [])]

        return Contest(competitions, schedule, submissions)

    def competition_from_channel_thread(
        self, channel_id: int, thread_id: Optional[int] = None
    ) -> Optional[tuple[int, CompetitionInfo]]:
        for i, competition in enumerate(self.current_competitions):
            if (competition.channel_id, competition.thread_id) == (
                channel_id,
                thread_id,
            ):
                return i, competition

    def add_submission(
        self, submission: Submission, channel_id: int, thread_id: Optional[int] = None
    ) -> "Contest":
        res = self.competition_from_channel_thread(channel_id, thread_id)

        if res:
            i, competition = res
            competition_new = competition.add_sub(submission)

            copy = deepcopy(self)
            copy.competitions[i] = competition_new

            return copy
        else:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )

    def withdraw_submission(
        self, channel_id: int, message_id: int, thread_id: Optional[int] = None
    ) -> "Contest":
        # remove a single submission from a contestant in a given competition
        res = self.competition_from_channel_thread(channel_id, thread_id)

        if res:
            i, competition = res

            if message_id not in competition.msg_to_sub:
                raise ValueError(
                    f"Unable to find a submission from the message_id provided: {message_id}"
                )

            index = competition.msg_to_sub[message_id]
            submission = competition.competing_entries[index]

            competition_new = deepcopy(competition)
            competition_new.competing_entries.pop(index)
            competition_new.msg_to_sub = {
                mid: (idx if idx < index else idx - 1)
                for mid, idx in competition_new.msg_to_sub.items()
                if mid != message_id
            }

            copy = deepcopy(self)
            copy.competitions[i] = competition_new
            copy.submissions = [sub for sub in copy.submissions if sub != submission]

            return copy
        else:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )

    def count_qualifs(self) -> list[int]:
        submission_competitions = list(
            filter(lambda x: x.type == "submission", self.competitions)
        )
        ret = []

        for comp in submission_competitions:
            list_subs_qualif = split_entries_categ(comp)
            ret.append(len(list_subs_qualif))

        return ret

    def make_qualifs(self, list_thread_ids) -> "Contest":
        submission_competitions = list(
            filter(lambda x: x.type == "submission", self.competitions)
        )

        qualifs: list[CompetitionInfo] = []
        for comp, threads in zip(submission_competitions, list_thread_ids):
            list_subs_qualif = split_entries_categ(comp)
            qualifs += [
                CompetitionInfo(
                    "qualif",
                    comp.channel_id,
                    self.schedule.qualif_period.start,
                    self.schedule.qualif_period.end,
                    thread_id=thread_id,
                    competing_entries=subs,
                )
                for subs, thread_id in zip(list_subs_qualif, threads)
            ]

        copy = deepcopy(self)
        copy.competitions += qualifs

        return copy

    def save_jury_vote(
        self, channel_id: int, thread_id: Optional[int], vote: JuryVote
    ) -> "Contest":
        res = self.competition_from_channel_thread(channel_id, thread_id)

        if res:
            i, competition = res
            competition_new = competition.add_jury_vote(vote)

            copy = deepcopy(self)
            copy.competitions[i] = competition_new

            return copy
        else:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )

    def save_public_vote(
        self, channel_id: int, thread_id: Optional[int], vote: PublicVote
    ) -> "Contest":
        res = self.competition_from_channel_thread(channel_id, thread_id)

        if res:
            i, competition = res
            competition_new = competition.add_public_vote(vote)

            copy = deepcopy(self)
            copy.competitions[i] = competition_new

            return copy
        else:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )

    def solve_qualifs(self) -> "Contest":
        qualif_competitions = list(
            filter(lambda x: x.type == "qualif", self.competitions)
        )
        qualifs_per_categ: dict[int, list[Submission]] = (
            dict()
        )  # channel_id -> [submissions]

        for qualif in qualif_competitions:
            channel_id = qualif.channel_id
            # determine the top 4 of the jury and the top 1 of the public
            # with the vote of the other voter category being used in case of a tie
            # in case of a new tie, the submission submitted earlier wins

            res_jury = qualif.count_votes_jury()
            res_public = qualif.count_votes_public()

            top_jury = sorted(
                qualif.competing_entries,
                key=lambda x: (res_jury[x], res_public[x], -x.submission_time),
                reverse=True,
            )[:4]
            top_public = sorted(
                [x for x in qualif.competing_entries if x not in top_jury],
                key=lambda x: (res_public[x], res_jury[x], -x.submission_time),
                reverse=True,
            )[:1]

            top = top_jury + top_public
            shuffle(top)

            # save the qualifiers of the thread
            qualifs_per_categ[channel_id] = qualifs_per_categ.get(channel_id, []) + top

        semis = []
        for categ, subs in qualifs_per_categ.items():
            semi = CompetitionInfo(
                "semis",
                categ,
                self.schedule.semis_period.start,
                self.schedule.semis_period.end,
                competing_entries=subs,
            )
            semis.append(semi)

        copy = deepcopy(self)
        copy.competitions += semis

        return copy

    def solve_semis(self) -> "Contest":
        semis_competitions = list(
            filter(lambda x: x.type == "semis", self.competitions)
        )
        qualifs_per_semi: dict[int, list[Submission]] = (
            dict()
        )  # channel_id -> [submissions]

        for semi in semis_competitions:
            channel_id = semi.channel_id
            # determine the top 4 of the jury and the top 2 of the public
            # with the vote of the other voter category being used in case of a tie
            # in a case of a new tie, the submission submitted earlier wins

            res_jury = semi.count_votes_jury()
            res_public = semi.count_votes_public()

            top_jury = sorted(
                semi.competing_entries,
                key=lambda x: (res_jury[x], res_public[x], -x.submission_time),
                reverse=True,
            )[:4]
            top_public = sorted(
                [x for x in semi.competing_entries if x not in top_jury],
                key=lambda x: (res_public[x], res_jury[x], -x.submission_time),
                reverse=True,
            )[:2]

            top = top_jury + top_public
            shuffle(top)

            # save the qualifiers of the semi
            qualifs_per_semi[channel_id] = qualifs_per_semi.get(channel_id, []) + top

        finals = []
        for categ, subs in qualifs_per_semi.items():
            final = CompetitionInfo(
                "final",
                categ,
                self.schedule.final_period.start,
                self.schedule.final_period.end,
                competing_entries=subs,
            )
            finals.append(final)

        copy = deepcopy(self)
        copy.competitions += finals

        return copy

    def save(self, path: str):
        with open(path, "w") as f:
            yaml.dump(asdict(self), f)

# The contest contains everything
# Each contest consists of several competitions:
# - one per category for the submission phase (photos are sent to the competition representing their category)
# - when the submission period is over, new competitions are created: one per thread.
#   there is at least one thread per category, there can be more depending on the number of submissions
# - then those competitions are run to determine semi-finalists. once again there will be one competition per category to determine finalists (3 per category)
# - a final competition is run for the Grand Final


def make_contest(channel_ids: list[int], schedule: Schedule):
    submissions = [
        CompetitionInfo(
            "submission",
            c,
            schedule.submission_period.start,
            schedule.submission_period.end,
        )
        for c in channel_ids
    ]
    # at this step, the only competitions are submissions in each channel
    # the competitions that will follow will be made later on, depending on the outcome of each phase
    return Contest(submissions, schedule)
