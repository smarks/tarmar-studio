"""Playing True Magic on the default canon, turn by turn (tarmar-studio #208).

The tests of the True cast, the craft and their narration need whole turns:
a craft's working goes off at the end of the Actions phase of its last turn,
a hit forces a roll to keep it, and a choice of anything else loses it. These
helpers run such turns the way the app does, through the Tarmar profile
(:meth:`battle.profiles.TarmarProfile.run_turn`), so the events a test reads
are the events a battle would store.

Everything is the default canon's (``reference/default_canon``): the workings
Sear, Knit and Figment, and invented numbers a public repository may print.
A test seats them with ``seated_rules(DEFAULT_MAGIC_RULES)``.
"""

from collections.abc import Callable, Mapping, Sequence

from tarmar_engine.actions import CRAFT_OPTION
from tarmar_engine.magic import TrueMagicRules
from tarmar_engine.policy import Candidate, Decision
from tarmar_engine.state import CraftState

from accounts.models import User
from battle import profiles
from battle.engine.state import BattleState, CombatantState
from battle.models import Battle, BattleCombatant
from battle.persistence import TARMAR_PROFILE
from battle.services import advance_turn, claim_seat, set_seat_ready, start_battle
from characters.skills import points_for_level
from common.test_helpers import create_character

from .magic_fixtures import DEFAULT_MAGIC_RULES
from .test_engine import ScriptedRoller, StubRecord
from .test_lobby import open_battle
from .test_state import make_combatant

_TRUE_MAGIC = DEFAULT_MAGIC_RULES.true_magic
assert _TRUE_MAGIC is not None, "the default canon has True Magic"
#: The default canon's True Magic rules, for a working's key at any tier
#: (``TRUE_MAGIC.working_key("sear", 3)``) and for rules built from them.
TRUE_MAGIC: TrueMagicRules = _TRUE_MAGIC
_CHANNEL = DEFAULT_MAGIC_RULES.channel
assert _CHANNEL is not None, "the default canon bounds a casting by Channel"

#: The skill the default canon's True Magic reads, as its Channel names it.
TRUE_SKILL = _CHANNEL.derivations[TRUE_MAGIC.channel_kind].source_skill
assert TRUE_SKILL is not None

#: The default canon's workings at their first tier, and Sear at its second.
SEAR = TRUE_MAGIC.working_key("sear", 1)
KNIT = TRUE_MAGIC.working_key("knit", 1)
FIGMENT = TRUE_MAGIC.working_key("figment", 1)
SEAR_TWO = TRUE_MAGIC.working_key("sear", 2)

#: The figures of :func:`board`.
MAGE, FOE, FRIEND = 1, 2, 3

#: Fields of a mage who knows Fire Missile, for its recipe cast and its True
#: cast: ``board(mage=MISSILE_MAGE)``.
MISSILE_MAGE: Mapping[str, object] = {
    "spells": ["fire_missile"],
    "spell_skill_levels": {"fire_missile": 1},
}

#: A choice of option for one figure: the first of its menu this accepts.
Pick = Callable[[Candidate], bool]


def true_mage(combatant_id: int = MAGE, **fields: object) -> CombatantState:
    """A True mage of the default canon: two levels in the True skill, WIS 14
    and INT 12, ten mana, and Shield known at level 1.

    Args:
        combatant_id: The figure's id.
        **fields: Any field of the figure, replacing the above.
    """
    figure: dict[str, object] = {
        "name": "Mage",
        "team": "party",
        "wisdom": 14,
        "intelligence": 12,
        "mana": 10,
        "max_mana": 10,
        "skill_levels": {TRUE_SKILL: 2},
        "spells": ["shield"],
        "spell_skill_levels": {"shield": 1},
    }
    figure.update(fields)
    return make_combatant(combatant_id, **figure)


def board(
    *,
    mage: Mapping[str, object] | None = None,
    foe: Mapping[str, object] | None = None,
    friend: bool = False,
) -> BattleState:
    """The mage (1) facing a foe (2) five hexes off, and a teammate (3)
    behind the mage when asked for.

    Args:
        mage: Fields of the mage, replacing :func:`true_mage`'s.
        foe: Fields of the foe.
        friend: Whether the mage has a teammate, hurt to Body 10.
    """
    figures = [
        true_mage(MAGE, **(mage or {})),
        make_combatant(
            FOE, **{"name": "Foe", "team": "foes", "q": 5, "r": 0, **(foe or {})}
        ),
    ]
    if friend:
        figures.append(
            make_combatant(FRIEND, name="Friend", team="party", q=-2, r=0, body=10)
        )
    return BattleState(arena_radius=8, combatants=figures)


def crafting_sear(
    *,
    push: int = 2,
    borrow: bool = False,
    turns_done: int = 1,
    **mage: object,
) -> BattleState:
    """:func:`board` with a craft of Sear's first tier standing on the mage,
    aimed at the foe, every share so far paid from the pool.

    As it comes, the mage is one turn into Sear with two mana pushed: three
    mana over three turns, one gathered, and nine left in the pool.

    Args:
        push: The mana pushed into the craft.
        borrow: Whether the craft borrows a share its pool cannot pay.
        turns_done: The craft actions already taken.
        **mage: Fields of the mage, replacing the above.
    """
    standing = CraftState(SEAR, push, borrow, turns_done, 0, FOE)
    return board(mage={"crafting": standing, "mana": 9, **mage})


#: The foe beside the mage, where its weapon reaches.
BESIDE = {"q": 1, "r": 0}


class PurposeRoller(ScriptedRoller):
    """A roller whose scripted faces are named by what the roll is for.

    A turn makes many rolls in an order a test should not have to count
    (initiative, each attack, each casting), so a test scripts the ones it
    means by their purpose (``{"casting": [(5, 5, 5)]}``) and every other
    roll lands on its dice's middle face.
    """

    def __init__(self, faces_by_purpose: Mapping[str, Sequence[Sequence[int]]]) -> None:
        super().__init__()
        self.faces_by_purpose = {
            purpose: list(queue) for purpose, queue in faces_by_purpose.items()
        }

    def roll(
        self,
        specification: str,
        *,
        purpose: str,
        modifier: int = 0,
        target_number: int | None = None,
        outcome: str | None = None,
    ) -> StubRecord:
        queue = self.faces_by_purpose.get(purpose)
        self.faces_queue = [queue.pop(0)] if queue else []
        return super().roll(
            specification,
            purpose=purpose,
            modifier=modifier,
            target_number=target_number,
            outcome=outcome,
        )


def craft(
    working: str, target: int | None = None, *, push: int = 0, borrow: bool = False
) -> Pick:
    """The craft action for ``working``: its beginning, or its continuation.

    Args:
        working: The working's key.
        target: The figure the action names, or ``None``.
        push: The mana pushed into it.
        borrow: Whether the craft borrows.
    """
    return lambda candidate: (
        candidate.letter == CRAFT_OPTION
        and candidate.spell_key == working
        and candidate.target_id == target
        and candidate.push_mana == push
        and candidate.borrow == borrow
    )


def letter(wanted: str) -> Pick:
    """Any option of one letter: ``c`` to dodge, ``j`` to attack."""
    return lambda candidate: candidate.letter == wanted


def true_cast(spell: str, *, push: int = 0) -> Pick:
    """The True cast of a spell, with ``push`` mana pushed into it."""
    return lambda candidate: (
        candidate.true_cast
        and candidate.spell_key == spell
        and candidate.push_mana == push
        and not candidate.borrow
    )


def recipe_cast(spell: str) -> Pick:
    """The cast of a known spell by its recipe, from the pool, nothing pushed."""
    return lambda candidate: (
        candidate.spell_key == spell
        and not candidate.true_cast
        and not candidate.push_mana
        and not candidate.borrow
    )


def counter(candidate: Candidate) -> bool:
    """Any counter the menu offers."""
    return candidate.counter_mana is not None


def idle(candidate: Candidate) -> bool:
    """A turn in which a figure does nothing to anyone: it dodges, or beside
    an enemy, where no dodge is offered, defends."""
    return candidate.letter in ("c", "k")


#: The foe attacks the figure beside it.
ATTACK = letter("j")
#: A foe's attack roll that hits whatever the Target Number.
HIT = {"attack": [(19,)]}


def play_turn(
    state: BattleState,
    picks: Mapping[int, Pick],
    faces: Mapping[str, Sequence[Sequence[int]]] | None = None,
    *,
    battle: Battle | None = None,
) -> list[dict]:
    """Run one turn of ``state`` as the app runs it; return its events.

    Each figure takes the first option of its seated player's menu (every
    target, described) that its pick accepts, and a figure with no pick the
    AI's own choice.

    Args:
        state: The board, changed in place.
        picks: A choice per figure, by id.
        faces: The scripted dice, by the roll's purpose
            (:class:`PurposeRoller`).
        battle: The battle whose rulings the turn reads; a battle with none
            when left out.

    Raises:
        AssertionError: A figure's menu holds no option its pick accepts.
    """
    choose = profiles.decider_for(TARMAR_PROFILE, every_target=True)

    def decide(turn_state: BattleState, actor: CombatantState) -> Decision:
        decision = choose(turn_state, actor)
        pick = picks.get(actor.combatant_id)
        if pick is None:
            return decision
        for candidate in decision.candidates:
            if pick(candidate):
                return Decision(chosen=candidate, candidates=decision.candidates)
        raise AssertionError(
            f"{actor.name}'s menu holds no such option: "
            f"{[(option.letter, option.name) for option in decision.candidates]}"
        )

    profile = profiles.TarmarProfile(battle if battle is not None else Battle())
    return profile.run_turn(state, PurposeRoller(faces or {}), decide)


def play(
    state: BattleState,
    turns: Sequence[Mapping[int, Pick]],
    faces: Mapping[int, Mapping[str, Sequence[Sequence[int]]]] | None = None,
    *,
    battle: Battle | None = None,
) -> list[dict]:
    """Run several turns; return every event, in order.

    Args:
        state: The board, changed in place.
        turns: Each turn's picks.
        faces: The scripted dice of a turn, by the turn's index from 0.
        battle: The battle whose rulings the turns read.
    """
    events: list[dict] = []
    for index, picks in enumerate(turns):
        events += play_turn(state, picks, (faces or {}).get(index), battle=battle)
    return events


def awaiting_true_mage(
    game_master: User,
    player: User,
    *,
    seated_by: User | None = None,
    mana: int = 10,
    seed: int = 11,
) -> tuple[Battle, BattleCombatant]:
    """A battle a Game Master opened, paused on a saved True mage's choice.

    The mage has two levels in the True skill, Fire Missile at level 1 and a
    Mana Pool of ``mana``, and faces one generated fighter. Needs the default
    canon's rules seated, for the seat to carry the mage's level.

    Args:
        game_master: Who opens the battle and runs it.
        player: Who owns the mage.
        seated_by: Who sits in the mage's seat with a mage of their own;
            ``player`` when left out.
        mana: The mage's Mana Pool.
        seed: The battle's seed.

    Returns:
        The paused battle and the mage's seat.
    """
    sitter = seated_by if seated_by is not None else player
    mage = create_character(
        sitter,
        name="Mage",
        skills={TRUE_SKILL: points_for_level(2, TRUE_SKILL)},
        spells={"fire_missile": points_for_level(1)},
        free_spell_points=mana,
    )
    battle = open_battle(game_master, seats=1, count=1, seed=seed)
    open_seat = battle.combatants.get(is_open_seat=True)
    seat = claim_seat(battle.pk, open_seat.pk, sitter, character_id=mage.pk)
    set_seat_ready(battle.pk, seat.pk, sitter, ready=True)
    start_battle(battle.pk)
    battle, _events = advance_turn(battle.pk)
    assert battle.status == Battle.STATUS_AWAITING, battle.status
    assert battle.pending_input_json["combatant_id"] == seat.pk
    return battle, seat
