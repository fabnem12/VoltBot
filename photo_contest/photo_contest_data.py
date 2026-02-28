from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass, fields
from itertools import groupby
from random import shuffle
from time import time
from typing import Any, Literal, Optional, Union

import os
import yaml
from dacite import from_dict, Config

from mistralai import Mistral


# Points awarded based on ranking position for different competition sizes
POINTS_SETS: dict[int, list[int]] = {
    10: [12, 10, 8, 7, 6, 5, 4, 3, 2, 1],
    5: [7, 5, 3, 2, 1],
    3: [4, 2, 1],
}


def _call_mistral_api(
    prompt: str, 
    temperature: float = 0.3, 
    max_tokens: int = 100,
    timeout: int = 15
) -> Optional[str]:
    """Utility function to call Mistral API via OpenRouter.
    
    Args:
        prompt: The user prompt to send
        temperature: Temperature setting for the model
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds
        
    Returns:
        The model's response text, or None if API key is missing or request fails
    """
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        return None
    
    try:
        client = Mistral(api_key=api_key)
        
        response = client.chat.complete(
            model="mistral-medium-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        res = response.choices[0].message.content
        if res is None:
            return None
        else:
            return res.strip()
    except Exception as e:
        print(f"Warning: Mistral API call failed: {e}")
        return None


def validate_commentary(commentary_text: str) -> bool:
    """Validate that a commentary is appropriate for a photo contest.
    
    Uses Mistral API to check if the text is a reasonable photo commentary.
    
    Args:
        commentary_text: The commentary text to validate
        
    Returns:
        True if valid, False otherwise
    """
    prompt = (
        f"You are a moderator for a photo contest. "
        f"Determine if the following text is a valid commentary about a photograph. "
        f"Valid commentaries discuss aspects like composition, lighting, subject, "
        f"technical quality, artistic merit, or provide constructive criticism. "
        f"Invalid commentaries are spam, unrelated content, offensive language, "
        f"or completely nonsensical. \n\n"
        f"Commentary: {commentary_text}\n\n"
        f"Respond with only 'VALID' or 'INVALID'."
    )
    
    answer = _call_mistral_api(prompt, temperature=0.1, max_tokens=10, timeout=10)
    
    if answer is None:
        return True
    
    answer_upper = answer.upper().strip()
    return "VALID" in answer_upper and "INVALID" not in answer_upper


@dataclass(frozen=True)
class Submission:
    author_id: int
    submission_time: int  # timestamp
    local_save_path: str
    discord_save_path: str  # URL to the message of the discord saved image


@dataclass
class JuryVote:
    voter_id: int
    ranking: list[Submission]

    def __post_init__(self):
        valid_ranking_lengths = POINTS_SETS.keys()

        ranking = self.ranking
        if len(ranking) not in valid_ranking_lengths:
            raise ValueError(
                f"The length ({len(ranking)}) of the ranking is not valid."
            )

        if any(self.voter_id == submission.author_id for submission in ranking):
            raise ValueError(f"A juror cannot vote for their own submissions.")


    def points_to_submissions(self):
        ranking = self.ranking
        return {
            submission: points
            for submission, points in zip(ranking, POINTS_SETS[len(ranking)])
        }


@dataclass
class PublicVote:
    voter_id: int
    nb_points: Union[Literal[0], Literal[1], Literal[2], Literal[3]]
    submission: Submission


@dataclass
class Period:
    start: int  # timestamp
    end: int  # timestamp
    
    def __post_init__(self):
        self.start = int(self.start)
        self.end = int(self.end)


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
    votes_public: list[PublicVote] = field(default_factory=list)  # list of public votes
    # Cached vote breakdowns for efficient querying (not serialized)
    _jury_breakdown: dict[Submission, dict[int, int]] = field(default_factory=dict, init=False, repr=False, compare=False)  # submission -> (voter_id -> points)
    _public_breakdown: dict[Submission, dict[int, int]] = field(default_factory=dict, init=False, repr=False, compare=False)  # submission -> (voter_id -> points)

    def _rebuild_vote_breakdowns(self):
        """Rebuild cached vote breakdowns from raw votes. Called after deserialization."""
        self._jury_breakdown = {}
        for voter_id, jury_vote in self.votes_jury.items():
            for submission, points in jury_vote.points_to_submissions().items():
                if submission not in self._jury_breakdown:
                    self._jury_breakdown[submission] = {}

                self._jury_breakdown[submission][voter_id] = points

        self._public_breakdown = {}
        for public_vote in self.votes_public:
            submission = public_vote.submission
            voter_id = public_vote.voter_id
            if submission not in self._public_breakdown:
                self._public_breakdown[submission] = {}

            current = self._public_breakdown[submission].get(voter_id, 0)
            self._public_breakdown[submission][voter_id] = current + public_vote.nb_points

    @property
    def needs_qualification(self) -> bool:
        """Check if this category needs qualification rounds (has >= 25 submissions)."""
        return len(self.competing_entries) >= 25

    def add_sub(self, submission: Submission, message_id: int) -> "CompetitionInfo":
        copy = deepcopy(self)
        copy.competing_entries.append(submission)
        copy.msg_to_sub[message_id] = len(copy.competing_entries) - 1

        return copy

    def get_submission_count(self) -> int:
        """Return the current number of submissions in this competition."""
        return len(self.competing_entries)

    def get_submission_from_message(self, message_id: int) -> Optional[Submission]:
        """Get a submission by its Discord message ID.
        
        Args:
            message_id: The Discord message ID to look up
        
        Returns:
            The Submission if found, None otherwise
        """
        if message_id not in self.msg_to_sub:
            return None
        
        submission_index = self.msg_to_sub[message_id]
        return self.competing_entries[submission_index]

    def set_message_id(self, submission_index: int, message_id: int) -> "CompetitionInfo":
        """Set the message_id for a submission at the given index.
        
        Args:
            submission_index: The index of the submission in competing_entries
            message_id: The Discord message ID to associate with this submission
        
        Returns:
            Updated CompetitionInfo with the message_id mapping added
        """
        if submission_index < 0 or submission_index >= len(self.competing_entries):
            raise ValueError(
                f"Invalid submission index {submission_index}. "
                f"Valid range is 0-{len(self.competing_entries) - 1}"
            )
        
        copy = deepcopy(self)
        copy.msg_to_sub[message_id] = submission_index
        return copy

    def add_jury_vote(self, vote: JuryVote) -> "CompetitionInfo":
        copy = deepcopy(self)
        copy.votes_jury[vote.voter_id] = vote

        for submission, points in vote.points_to_submissions().items():
            if submission not in copy._jury_breakdown:
                copy._jury_breakdown[submission] = {}
            copy._jury_breakdown[submission][vote.voter_id] = points

        return copy

    def add_public_vote(self, vote: PublicVote) -> "CompetitionInfo":
        if vote.voter_id == vote.submission.author_id:
            raise ValueError("Not allowed to vote for yourself")

        copy = deepcopy(self)
        
        # Remove any existing vote from this voter for this submission
        copy.votes_public = [
            v for v in copy.votes_public 
            if not (v.voter_id == vote.voter_id and v.submission == vote.submission)
        ]
        copy.votes_public.append(vote)
        
        # Update cached breakdown
        if vote.submission not in copy._public_breakdown:
            copy._public_breakdown[vote.submission] = {}
        copy._public_breakdown[vote.submission][vote.voter_id] = vote.nb_points

        return copy

    def count_votes_jury(self) -> dict[Submission, int]:
        points: dict[Submission, int] = dict()

        for vote in self.votes_jury.values():
            for submission, nb_points in vote.points_to_submissions().items():
                points[submission] = points.get(submission, 0) + nb_points

        return points

    def count_votes_public(self) -> dict[Submission, int]:
        points: dict[Submission, int] = dict()

        for vote in self.votes_public:
            sub = vote.submission
            points[sub] = points.get(sub, 0) + vote.nb_points

        return points

    def get_jury_votes_per_juror(self, submission: Submission) -> dict[int, int]:
        """Get a breakdown of jury points for a specific submission by juror.
        
        Returns:
            Dict mapping voter_id -> points awarded to this submission
        """
        return self._jury_breakdown.get(submission, {})

    def get_public_votes_per_voter(self, submission: Submission) -> dict[int, int]:
        """Get a breakdown of public points for a specific submission by voter.
        
        Returns:
            Dict mapping voter_id -> total points awarded to this submission
        """
        return self._public_breakdown.get(submission, {})

    def withdraw_sub(self, message_id: int) -> tuple["CompetitionInfo", Submission]:
        """Withdraw a submission by message_id and return the updated competition and the withdrawn submission."""
        if message_id not in self.msg_to_sub:
            raise ValueError(
                f"Unable to find a submission from the message_id provided: {message_id}"
            )

        index = self.msg_to_sub[message_id]
        submission = self.competing_entries[index]

        copy = deepcopy(self)
        copy.competing_entries.pop(index)
        copy.msg_to_sub = {
            mid: (idx if idx < index else idx - 1)
            for mid, idx in copy.msg_to_sub.items()
            if mid != message_id
        }

        return copy, submission


@dataclass
class Schedule:  # all of the fields are tuples of timestamps (start, end)
    submission_period: Period
    qualif_period: Period
    semis_period: Period
    final_period: Period


def split_entries_categ(categ_info: CompetitionInfo) -> list[list[Submission]]:
    # shuffle the list of entries, and group by contestant
    # so that each contestant's entries are evenly split among threads
    # Work on a shallow copy to avoid mutating the original competition's
    # `competing_entries` order (which is used elsewhere for indexing).
    entries = list(categ_info.competing_entries)
    print(f"DEBUG split_entries: Initial entries count: {len(entries)}")
    shuffle(entries)

    # Group entries by contestant - use defaultdict instead of groupby
    # because groupby returns iterators that can only be consumed once
    from collections import defaultdict
    entries_per_contestant: dict[int, list[Submission]] = defaultdict(list)
    for entry in entries:
        entries_per_contestant[entry.author_id].append(entry)
    
    print(f"DEBUG split_entries: Grouped by {len(entries_per_contestant)} contestants")
    for author_id, author_entries in entries_per_contestant.items():
        print(f"DEBUG split_entries:   Author {author_id}: {len(author_entries)} entries")
    
    contestants = list(entries_per_contestant.keys())
    shuffle(contestants)

    entries_randomized = [
        e for contestant in contestants for e in entries_per_contestant[contestant]
    ]
    shuffle(entries_randomized)
    print(f"DEBUG split_entries: entries_randomized count: {len(entries_randomized)}")

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
    print(f"DEBUG split_entries: thread_lengths: {thread_lenghts}")
    threads: list[list[Submission]] = [[] for _ in range(len(thread_lenghts))]

    for i, e in enumerate(entries_randomized):
        threads[i % len(threads)].append(e)
    
    print(f"DEBUG split_entries: Final thread counts: {[len(t) for t in threads]}")

    return threads


@dataclass
class Contest:
    MAX_SUBMISSIONS_PER_CATEGORY = 6  # Maximum photos per user per category
    
    competitions: list[CompetitionInfo]
    schedule: Schedule
    commentaries: dict[str, dict[int, str]] = field(default_factory=dict)  # key: discord_save_path, value: {author_id: commentary_text}
    commentary_summaries: dict[str, str] = field(default_factory=dict)  # key: discord_save_path, value: summary_text
    submission_posts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # key: discord_save_path, value: list of {"message_id": int, "channel_id": int, "thread_id": Optional[int], "is_summary": bool}
    
    @property
    def submissions(self) -> list[Submission]:
        """Get a flat list of all submissions across all competitions."""
        return [sub for comp in self.submission_competitions for sub in comp.competing_entries]

    @property
    def contestants(self):
        return set(x.author_id for x in self.submissions)

    def _competitions_by_type(self, comp_type: str) -> list[CompetitionInfo]:
        """Helper to filter competitions by type."""
        return [comp for comp in self.competitions if comp.type == comp_type]

    @property
    def current_competitions(self) -> list[CompetitionInfo]:
        ts = time()
        return list(
            filter(lambda x: x.start_time <= ts and ts < x.end_time, self.competitions)
        )

    @property
    def submission_competitions(self) -> list[CompetitionInfo]:
        return self._competitions_by_type("submission")

    @property
    def qualif_competitions(self) -> list[CompetitionInfo]:
        return self._competitions_by_type("qualif")

    @property
    def semis_competitions(self) -> list[CompetitionInfo]:
        return self._competitions_by_type("semis")

    @property
    def final_competition(self) -> Optional[CompetitionInfo]:
        """Return the single grand final competition, or None if not created yet."""
        finals = self._competitions_by_type("final")
        return finals[0] if finals else None

    @property
    def channel_threads_open_for_submissions(self) -> list[tuple[int, Optional[int]]]:
        ts = time()
        if not (
            self.schedule.submission_period.start
            <= ts
            < self.schedule.submission_period.end
        ):
            return []
        
        return [
            (comp.channel_id, comp.thread_id)
            for comp in self.current_competitions
        ]

    @staticmethod
    def from_file(path: str) -> "Contest":
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if data is None:
            raise ValueError(
                f"YAML file '{path}' is empty or invalid. "
                f"Please delete it to allow regeneration."
            )

        # Use dacite to convert dicts to dataclasses automatically
        contest = from_dict(
            data_class=Contest,
            data=data,
            config=Config(
                cast=[tuple],  # Allow casting to tuples for dict keys
            )
        )
        
        # Rebuild cached vote breakdowns for each competition
        for competition in contest.competitions:
            competition._rebuild_vote_breakdowns()

        return contest

    def competition_from_channel_thread(
        self, channel_id: int, thread_id: Optional[int] = None, ignore_time: bool = False
    ) -> Optional[tuple[int, CompetitionInfo]]:
        """Find a competition by channel_id and thread_id.
        
        Args:
            channel_id: The channel ID to search for
            thread_id: The thread ID to search for (None for main channels)
            ignore_time: If True, search all competitions regardless of current time.
                        If False, only search competitions active at current time.
        
        Returns:
            Tuple of (index, competition) if found, None otherwise
        """
        competitions = self.competitions if ignore_time else self.current_competitions
        for i, competition in enumerate(competitions):
            if (competition.channel_id, competition.thread_id) == (
                channel_id,
                thread_id,
            ):
                return i, competition
        return None

    def get_submission_count(
        self, channel_id: int, thread_id: Optional[int] = None
    ) -> int:
        """Get the current number of submissions for a specific competition."""
        res = self.competition_from_channel_thread(channel_id, thread_id)
        if res:
            _, competition = res
            return competition.get_submission_count()
        else:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )
    
    def can_user_submit(
        self, channel_id: int, thread_id: Optional[int], user_id: int, max_submissions: Optional[int] = None
    ) -> bool:
        """Check if a user can submit more photos to a specific competition.
        
        Args:
            channel_id: The channel ID of the competition
            thread_id: The thread ID of the competition (None for main channels)
            user_id: The Discord user ID to check
            max_submissions: Maximum allowed submissions per user (default: MAX_SUBMISSIONS_PER_CATEGORY)
        
        Returns:
            True if the user can submit more, False if they've reached the limit
        """
        if max_submissions is None:
            max_submissions = self.MAX_SUBMISSIONS_PER_CATEGORY
        
        res = self.competition_from_channel_thread(channel_id, thread_id)
        if res:
            _, competition = res
            user_submission_count = sum(int(sub.author_id == user_id) for sub in competition.competing_entries)
            return user_submission_count < max_submissions
        else:
            # If competition not found, allow submission (will be handled elsewhere)
            return True
    
    def is_submission_message(
        self, channel_id: int, thread_id: Optional[int], message_id: int
    ) -> bool:
        """Check if a message_id corresponds to a submission.
        
        Args:
            channel_id: The channel ID
            thread_id: The thread ID (None for main channels)
            message_id: The Discord message ID to check
        
        Returns:
            True if the message is a submission, False otherwise
        """
        # Prefer semis competitions (search the full `self.competitions` list
        # so the returned index maps to `self.competitions`). Only fall back to
        # other lookups in TEST_MODE or when no semis comp matches.
        res = None
        # Always prefer semis competitions when possible so that lookups in a
        # semis channel/thread resolve to the semis competition rather than an
        # earlier submission competition with the same channel/thread.
        for i, c in enumerate(self.competitions):
            if c.type == "semis" and (c.channel_id, c.thread_id) == (channel_id, thread_id):
                res = (i, c)
                break

        if res is None:
            # In TEST_MODE we may need to lookup across all competitions; otherwise
            # use the normal time-constrained lookup.
            res = self.competition_from_channel_thread(channel_id, thread_id, ignore_time=bool(globals().get('TEST_MODE', False)))

        if res:
            _, comp = res
            return message_id in comp.msg_to_sub
        return False

    def get_submission_from_message(
        self, channel_id: int, thread_id: Optional[int], message_id: int
    ) -> Optional[Submission]:
        """Get a submission by its Discord message ID.
        
        Args:
            channel_id: The channel ID
            thread_id: The thread ID (None for main channels)
            message_id: The Discord message ID to look up
        
        Returns:
            The Submission if found, None otherwise
        """
        res = self.competition_from_channel_thread(channel_id, thread_id)
        if res:
            _, competition = res
            return competition.get_submission_from_message(message_id)
        return None

    def set_message_id(
        self, channel_id: int, thread_id: Optional[int], submission_index: int, message_id: int
    ) -> "Contest":
        """Set the message_id for a submission in a specific competition.
        
        Args:
            channel_id: The channel ID of the competition
            thread_id: The thread ID of the competition (None for main channels)
            submission_index: The index of the submission in the competition
            message_id: The Discord message ID to associate with this submission
        
        Returns:
            Updated Contest with the message_id mapping added
        """
        # Use ignore_time=True to find competitions during setup (before their start time)
        res = self.competition_from_channel_thread(channel_id, thread_id, ignore_time=True)
        if res:
            i, competition = res
            competition_new = competition.set_message_id(submission_index, message_id)
            
            copy = deepcopy(self)
            copy.competitions[i] = competition_new
            return copy
        else:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )

    def add_submission(
        self, submission: Submission, channel_id: int, message_id: int, thread_id: Optional[int] = None
    ) -> "Contest":
        # Check that we are in a submission period
        ts = time()
        if not (
            self.schedule.submission_period.start
            <= ts
            < self.schedule.submission_period.end
        ):
            raise ValueError("Not in a submission period.")
        
        # Find the right competition and add the submission
        
        res = self.competition_from_channel_thread(channel_id, thread_id)

        if res:
            i, competition = res
            competition_new = competition.add_sub(submission, message_id)

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
            competition_new, submission = competition.withdraw_sub(message_id)

            copy = deepcopy(self)
            copy.competitions[i] = competition_new

            return copy
        else:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )

    def count_qualifs(self) -> list[int]:
        ret = []
        for comp in self.submission_competitions:
            # Categories with < 25 submissions skip qualification (auto-qualify to semis)
            if not comp.needs_qualification:
                ret.append(0)  # No threads needed
            else:
                list_subs_qualif = split_entries_categ(comp)
                ret.append(len(list_subs_qualif))
        return ret

    def make_qualifs(self, list_thread_ids: list[list[int]]) -> "Contest":
        submission_competitions = self.submission_competitions

        qualifs: list[CompetitionInfo] = []
        for comp, threads in zip(submission_competitions, list_thread_ids):
            # Skip qualification for categories with too few submissions (< 25)
            # Those will automatically qualify to semis
            if comp.needs_qualification:
                print(f"DEBUG: Category {comp.channel_id} needs qualification with {len(comp.competing_entries)} submissions")
                print(f"DEBUG: Thread IDs provided: {threads}")
                list_subs_qualif = split_entries_categ(comp)
                print(f"DEBUG: split_entries_categ returned {len(list_subs_qualif)} thread groups")
                for i, subs in enumerate(list_subs_qualif):
                    print(f"DEBUG:   Thread group {i} has {len(subs)} submissions")
                
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
                print(f"DEBUG: Created {len([x for x in qualifs if x.channel_id == comp.channel_id])} qualif competitions for channel {comp.channel_id}")

        copy = deepcopy(self)
        copy.competitions += qualifs

        return copy

    def save_jury_vote(
        self, channel_id: int, thread_id: Optional[int], voter_id: int, ranking: list[Submission]
    ) -> "Contest":
        """Save a jury vote for a competition.
        
        Args:
            channel_id: The channel ID of the competition
            thread_id: The thread ID of the competition (None for main channels)
            voter_id: The ID of the voter
            ranking: The ranked list of submissions
            
        Returns:
            Updated Contest with the vote saved
        """
        vote = JuryVote(voter_id=voter_id, ranking=ranking)
        
        res = self.competition_from_channel_thread(channel_id, thread_id, ignore_time=True)
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
        self, channel_id: int, thread_id: Optional[int], voter_id: int, nb_points: Union[Literal[0], Literal[1], Literal[2], Literal[3]], submission: Submission
    ) -> "Contest":
        """Save a public vote for a competition.
        
        Args:
            channel_id: The channel ID of the competition
            thread_id: The thread ID of the competition (None for main channels)
            voter_id: The ID of the voter
            nb_points: Number of points (0-3)
            submission: The submission being voted for
            
        Returns:
            Updated Contest with the vote saved
        """
        vote = PublicVote(voter_id=voter_id, nb_points=nb_points, submission=submission)
        
        res = self.competition_from_channel_thread(channel_id, thread_id, ignore_time=True)

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
        qualif_competitions = self.qualif_competitions
        submission_competitions = self.submission_competitions
        
        qualifs_per_categ: dict[int, list[Submission]] = (
            dict()
        )  # channel_id -> [submissions]

        # Process qualification competitions (categories with >= 12 submissions)
        for qualif in qualif_competitions:
            channel_id = qualif.channel_id
            # determine the top 4 of the jury and the top 1 of the public
            # with the vote of the other voter category being used in case of a tie
            # in case of a new tie, the submission submitted earlier wins

            res_jury = qualif.count_votes_jury()
            res_public = qualif.count_votes_public()

            top_jury = sorted(
                qualif.competing_entries,
                key=lambda x: (res_jury.get(x, 0), res_public.get(x, 0), -x.submission_time),
                reverse=True,
            )[:4]
            top_public = sorted(
                [x for x in qualif.competing_entries if x not in top_jury],
                key=lambda x: (res_public.get(x, 0), res_jury.get(x, 0), -x.submission_time),
                reverse=True,
            )[:1]

            top = top_jury + top_public
            shuffle(top)

            # save the qualifiers of the thread
            qualifs_per_categ[channel_id] = qualifs_per_categ.get(channel_id, []) + top

        # Auto-qualify categories with < 25 submissions (no qualification threads were created)
        channels_with_qualifs = set(q.channel_id for q in qualif_competitions)
        for submission_comp in submission_competitions:
            if submission_comp.channel_id not in channels_with_qualifs:
                # This category had < 25 submissions, auto-qualify all to semis
                all_subs = list(submission_comp.competing_entries)
                shuffle(all_subs)
                qualifs_per_categ[submission_comp.channel_id] = all_subs

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

    def solve_semis(self, final_channel_id: int) -> "Contest":
        """Solve semi-finals and create the grand final competition.
        
        Creates ONE combined final competition with all qualifiers from all categories
        competing together, rather than separate finals per category.
        
        Args:
            final_channel_id: The Discord channel ID where the grand final will take place
        """
        semis_competitions = self.semis_competitions
        qualifs_per_semi: dict[int, list[Submission]] = (
            dict()
        )  # channel_id -> [submissions]

        for semi in semis_competitions:
            channel_id = semi.channel_id
            # determine the top 3 of the jury and the top 2 of the public
            # with the vote of the other voter category being used in case of a tie
            # in a case of a new tie, the submission submitted earlier wins

            res_jury = semi.count_votes_jury()
            res_public = semi.count_votes_public()

            top_jury = sorted(
                semi.competing_entries,
                key=lambda x: (res_jury[x], res_public[x], -x.submission_time),
                reverse=True,
            )[:3]
            top_public = sorted(
                [x for x in semi.competing_entries if x not in top_jury],
                key=lambda x: (res_public[x], res_jury[x], -x.submission_time),
                reverse=True,
            )[:2]

            top = top_jury + top_public
            shuffle(top)

            # save the qualifiers of the semi
            qualifs_per_semi[channel_id] = qualifs_per_semi.get(channel_id, []) + top

        # Create ONE combined final with all qualifiers from all categories
        all_finalists = []
        for subs in qualifs_per_semi.values():
            all_finalists.extend(subs)
        
        # Shuffle to mix categories together
        shuffle(all_finalists)
        
        # Create the combined grand final with all qualifiers
        final = CompetitionInfo(
            "final",
            final_channel_id,
            self.schedule.final_period.start,
            self.schedule.final_period.end,
            competing_entries=all_finalists,
        )

        copy = deepcopy(self)
        copy.competitions.append(final)

        return copy

    def get_votable_submissions(
        self, channel_id: int, thread_id: Optional[int], user_id: int
    ) -> tuple[list[Submission], dict[Submission, int]]:
        """Get submissions that a user can vote on (excludes their own submissions).
        
        Args:
            channel_id: The channel ID
            thread_id: The thread ID (None for main channels)
            user_id: The user ID
            
        Returns:
            Tuple of (votable_submissions, submission_numbers_dict)
        """
        # Use ignore_time only in TEST_MODE to allow manual testing; otherwise
        # respect the current active competitions so we don't pick an older
        # submission competition when semis/qualif competitions exist.
        res = self.competition_from_channel_thread(channel_id, thread_id, ignore_time=False)
        if res:
            _, comp = res
            votable_submissions = []
            submission_numbers = {}
            for i, sub in enumerate(comp.competing_entries):
                if sub.author_id != user_id:
                    votable_submissions.append(sub)
                    submission_numbers[sub] = i + 1
            return votable_submissions, submission_numbers
        return [], {}

    def _submission_key(self, submission: Submission) -> str:
        """Returns submission.discord_save_path as the unique key."""
        return submission.discord_save_path

    def _make_submission_post(self, message_id: int, channel_id: int, thread_id: Optional[int], is_summary: bool = False) -> dict[str, Any]:
        """Create a dict for storage."""
        return {
            "message_id": message_id,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "is_summary": is_summary
        }

    def _parse_submission_post(self, post: dict[str, Any]) -> tuple[int, int, Optional[int], bool]:
        """Parse dict back to tuple."""
        return (
            post["message_id"],
            post["channel_id"],
            post.get("thread_id"),
            post.get("is_summary", False)
        )

    def _generate_summary_for_submission(self, discord_save_path: str) -> str:
        """Generate an AI summary for a submission's commentaries.
        
        Args:
            discord_save_path: The submission's discord_save_path
            
        Returns:
            The generated summary text
        """
        commentaries = self.commentaries.get(discord_save_path, {})
        
        if not commentaries or len(commentaries) < 2:
            return ""
        
        commentary_texts = "\n\n".join(
            f"Comment: {text}"
            for text in commentaries.values()
        )
        
        prompt = (
            f"Summarize the following photo critiques to help judges compare entries. "
            f"In max 50 words, highlight the photo's strongest qualities, main weaknesses, "
            f"and standout characteristics (composition, technical execution, artistic merit, emotional impact). "
            f"Be objective and balanced.\n\n{commentary_texts}"
        )
        
        summary = _call_mistral_api(prompt, temperature=0.3, max_tokens=100, timeout=15)
        
        return summary if summary else commentary_texts

    def add_commentary(
        self, channel_id: int, thread_id: Optional[int], submission: Submission, author_id: int, text: str
    ) -> "Contest":
        """Add a jury commentary after validating it makes sense as a photo commentary.
        
        Args:
            channel_id: The channel ID of the competition
            thread_id: The thread ID of the competition (None for main channels)
            submission: The submission to add commentary to
            author_id: The Discord user ID of the commenter
            text: The commentary text
            
        Returns:
            Updated Contest object
            
        Raises:
            ValueError: If competition not found or commentary validation fails
        """
        res = self.competition_from_channel_thread(channel_id, thread_id, ignore_time=True)
        if not res:
            raise ValueError(
                f"Unable to find a valid competition from the (channel_id, thread_id) provided: ({channel_id}, {thread_id})"
            )
        
        if author_id == submission.author_id:
            raise ValueError("Cannot comment on your own submission")
        
        if not validate_commentary(text):
            raise ValueError(
                "Commentary does not appear to be a valid photo critique. "
                "Please provide constructive feedback about the photo."
            )
        
        copy = deepcopy(self)
        key = self._submission_key(submission)
        
        if key not in copy.commentaries:
            copy.commentaries[key] = {}
        copy.commentaries[key][author_id] = text
        
        copy.commentary_summaries[key] = copy._generate_summary_for_submission(key)
        
        return copy

    def get_commentaries(self, discord_save_path: str) -> dict[int, str]:
        """Get all commentaries for a submission.
        
        Args:
            discord_save_path: The submission's discord_save_path
            
        Returns:
            Dict mapping author_id to commentary text
        """
        return deepcopy(self.commentaries.get(discord_save_path, {}))

    def get_commentary_summary(self, discord_save_path: str) -> Optional[str]:
        """Get cached summary for a submission.
        
        Args:
            discord_save_path: The submission's discord_save_path
            
        Returns:
            The summary text, or None if not found
        """
        return self.commentary_summaries.get(discord_save_path)

    def get_all_commentaries_summaries(self) -> list[tuple[Submission, str]]:
        """Get all commentary summaries for all submissions in all competitions.
        
        Returns:
            List of tuples (submission, summary_text)
        """
        result = []
        for comp in self.competitions:
            for submission in comp.competing_entries:
                key = self._submission_key(submission)
                if key in self.commentary_summaries:
                    result.append((submission, self.commentary_summaries[key]))
        return result

    def add_submission_post(
        self, discord_save_path: str, message_id: int, channel_id: int, thread_id: Optional[int], is_summary: bool = False
    ) -> "Contest":
        """Track where a submission is posted.
        
        Args:
            discord_save_path: The submission's discord_save_path
            message_id: The Discord message ID
            channel_id: The Discord channel ID
            thread_id: The Discord thread ID (None for main channels)
            is_summary: Whether this is a summary message
            
        Returns:
            Updated Contest
        """
        copy = deepcopy(self)
        
        if discord_save_path not in copy.submission_posts:
            copy.submission_posts[discord_save_path] = []
        
        post = self._make_submission_post(message_id, channel_id, thread_id, is_summary)
        
        existing = copy.submission_posts[discord_save_path]
        for i, p in enumerate(existing):
            if p["message_id"] == message_id:
                existing[i] = post
                break
        else:
            existing.append(post)
        
        return copy

    def get_submission_posts(self, discord_save_path: str) -> list[tuple[int, int, Optional[int], bool]]:
        """Get all posts for a submission.
        
        Args:
            discord_save_path: The submission's discord_save_path
            
        Returns:
            List of tuples (message_id, channel_id, thread_id, is_summary)
        """
        posts = self.submission_posts.get(discord_save_path, [])
        return [self._parse_submission_post(p) for p in posts]

    def save(self, path: str):
        """Save contest to YAML file, excluding cached vote breakdowns."""
        def safe_asdict(obj):
            # Dataclass: convert to dict, skipping private fields
            if is_dataclass(obj):
                result = {}
                for f in fields(obj):
                    if f.name.startswith("_") or f.name in ("_jury_breakdown", "_public_breakdown"):
                        continue
                    value = getattr(obj, f.name)
                    result[f.name] = safe_asdict(value)
                return result

            # dict: ensure keys are serializable (convert problematic keys to strings)
            if isinstance(obj, dict):
                new = {}
                for k, v in obj.items():
                    # Keep simple types as-is
                    if isinstance(k, (str, int, float, bool)):
                        key = k
                    # Handle dict keys that are dataclasses (like Submission)
                    elif is_dataclass(k):
                        key = safe_asdict(k)
                    # For other unhashable types (dict, list), convert to string representation
                    else:
                        key = str(k)
                    new[key] = safe_asdict(v)
                return new

            # lists/tuples
            if isinstance(obj, (list, tuple)):
                return [safe_asdict(x) for x in obj]

            # primitives
            return obj

        data = safe_asdict(self)
        with open(path, "w") as f:
            yaml.dump(data, f)


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
