"""Years of prior experience, exactly as the rules played them (#370, #590).

Every field is read back off the run result ``characters.skills`` produced;
nothing here is computed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from characters.skills import SURVIVAL_RULE_DESCRIPTION


@dataclass(frozen=True)
class ExperienceYearOutcome:
    """One year of prior experience, exactly as ``characters.skills`` played it.

    Every field is read back off the run the rules recorded on the draft's
    experience log. Nothing here is computed by this module.

    Attributes:
        year: The year's number in the character's career.
        age: The character's age at the end of the year.
        track: The track key played, e.g. ``"campaigner"``.
        dice: The three survival dice, as they fell.
        roll: The dice total before the modifier.
        modifier: The best attribute modifier applied to the roll.
        total: ``roll + modifier``, the number compared with the target.
        target: The track's survivability target.
        survived: Whether the character lived through the year.
        auto_survive: Whether the natural roll forced survival.
        auto_death: Whether the natural roll forced death.
        skill: The skill (or special result) the year's d20 landed on.
        xp_awarded: XP earned in the year.
        money_earned: Coin earned in the year.
    """

    year: int
    age: int
    track: str
    dice: tuple[int, ...]
    roll: int
    modifier: int
    total: int
    target: int
    survived: bool
    auto_survive: bool
    auto_death: bool
    skill: str
    xp_awarded: int
    money_earned: int

    @property
    def track_label(self) -> str:
        """The track's name as a reader sees it."""
        return self.track.replace("_", " ")

    @property
    def verdict(self) -> str:
        """``"SURVIVED"`` or ``"DIED"`` — the code's own answer, in one word."""
        return "SURVIVED" if self.survived else "DIED"

    @property
    def natural_note(self) -> str:
        """Why the natural roll settled it, or ``""`` when it did not."""
        if self.auto_survive:
            return f"natural {self.roll} — automatic survival"
        if self.auto_death:
            return f"natural {self.roll} — automatic death"
        return ""

    @property
    def line(self) -> str:
        """The chip the player reads for this year.

        Returns:
            One line carrying the dice, the modifier, the target and the
            verdict, so a wrong call in the prose beside it is visible rather
            than arguable — the reason the roll chips exist.
        """
        faces = ", ".join(str(die) for die in self.dice)
        natural = f", {self.natural_note}" if self.natural_note else ""
        return (
            f"⚙️ {self.track_label} year {self.year} survival: 3d6 [{faces}] = "
            f"{self.roll}, modifier {self.modifier:+d}, total {self.total} vs "
            f"target {self.target} (survive on {self.target} or more)"
            f"{natural} — {self.verdict}"
        )

    @property
    def detail(self) -> str:
        """The same year written out for the model, target and rule included."""
        faces = ", ".join(str(die) for die in self.dice)
        natural = f" {self.natural_note.capitalize()}." if self.natural_note else ""
        return (
            f"Year {self.year} (age {self.age}) on the {self.track_label} track. "
            f"Survival check: 3d6 rolled {faces} = {self.roll}; best attribute "
            f"modifier {self.modifier:+d}; total {self.total}; the "
            f"{self.track_label} track's survivability target is {self.target}. "
            f"The rule is: {SURVIVAL_RULE_DESCRIPTION}{natural} "
            f"VERDICT: the character {self.verdict}. "
            f"The year's skill roll gave {self.skill}; it earned "
            f"{self.xp_awarded} XP and {self.money_earned} coin."
        )


def outcomes_from_run(run: Mapping[str, Any]) -> tuple[ExperienceYearOutcome, ...]:
    """One outcome per year a run played, from its run result.

    Args:
        run: A run result, as ``Character.apply_experience_run`` stores it in
            the experience log and ``characters.services`` returns it.
    """
    track = str(run.get("track", ""))
    outcomes: list[ExperienceYearOutcome] = []
    for year in run.get("years", []):
        check = year.get("survival_check") or {}
        outcomes.append(
            ExperienceYearOutcome(
                year=int(year.get("year", 0)),
                age=int(year.get("age", 0)),
                track=track,
                dice=tuple(int(die) for die in check.get("dice", ())),
                roll=int(check.get("roll", 0)),
                modifier=int(check.get("modifier", 0)),
                total=int(check.get("total", 0)),
                target=int(check.get("target", 0)),
                survived=bool(check.get("success")),
                auto_survive=bool(check.get("auto_success")),
                auto_death=bool(check.get("auto_death")),
                skill=str(year.get("skill", "")),
                xp_awarded=int(year.get("xp_awarded", 0)),
                money_earned=int(year.get("money_earned", 0)),
            )
        )
    return tuple(outcomes)
