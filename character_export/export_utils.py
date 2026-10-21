"""Utilities for exporting character data to various formats."""

import html as html_lib
import json

import bleach

from characters.constants import CHARACTER_ATTRIBUTE_FIELDS, CHARACTER_EXPORT_FORMAT
from characters.equipment import format_pounds
from characters.magic import known_spells
from characters.purse import format_purse
from characters.rules import social_class_display
from characters.skills import track_label
from characters.templatetags.dice_display import survival_roll
from common.instants import labelled_instant

# Formatting tags allowed in the exported HTML/PDF. No <img> or <a>, so a
# malicious skill/equipment name can't inject an external/file resource that
# WeasyPrint would fetch (XSS / SSRF / local-file inclusion).
EXPORT_ALLOWED_TAGS = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "span",
    "div",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
]
EXPORT_ALLOWED_ATTRIBUTES = {
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}

# Persistent character fields that are imported back round-trip. Excludes
# derived properties, audit metadata (created_at, deleted state), and the
# owner FK — the importer becomes the owner.
EXPORTABLE_FIELDS = (
    "name",
    *CHARACTER_ATTRIBUTE_FIELDS,
    "appearance_roll",
    "height_roll",
    "weight_roll",
    "fatigue_roll",
    "provenance_class",
    "provenance_detail",
    "location",
    # The 3d6 literacy roll and whether one was ever made (issue #741):
    # records, so they round-trip, and an import keeps the player's lock.
    "literacy_roll",
    "literacy_rolled",
    # A Game Master's ruling at "Consult GM" (issue #743), which a Game
    # Master's import keeps and a player's drops (issue #744).
    "literacy_ruling",
    # Whether the background was rolled (issue #749): kept by a Game Master's
    # import, False on a player's. The Origin's (issue #754) is kept by a
    # Game Master's import and fresh (recorded) on a player's, whose file
    # names the Origin they chose.
    "provenance_recorded",
    "location_recorded",
    # The skill holding the Origin's grant (issue #754): kept by a Game
    # Master's import, set from the grant on a player's.
    "origin_skill",
    # A player's people (#810) and the Game Master's origin ruling (#801),
    # which a player's import drops. is_dragonborn is deliberately not
    # exported: the file is the player's to read.
    "people",
    "origin_ruling",
    # Points a lowered XP could not take back (#769), paid from the next XP.
    "skill_points_owed",
    "wealth_recorded",
    "wealth_level",
    "starting_coin",
    "dice_log",
    "skills",
    "free_skill_points",
    "free_spell_points",
    # The spells known and the purse's movements (issues #774, #757).
    "spells",
    "coin_adjustment",
    "equipment",
    "pack_dropped",
    "total_xp",
    "age",
    "experience_terms",
    "experience_log",
    "money_earned",
    "aging_str_penalty",
    "aging_dex_penalty",
    "aging_int_penalty",
    "aging_wis_penalty",
    "aging_con_penalty",
    "is_played",
    "is_alive",
    "death_cause",
    # The wounds the character carries (issue #751), so a round trip does
    # not bring a hurt character back unhurt -- the same reason is_alive is
    # here.
    "fatigue_damage",
    "body_damage",
    # Mana spent and not yet restored, for the same reason, and mana
    # borrowed and not yet paid back (tarmar-engine #28).
    "mana_spent",
    "mana_owed",
)

# Version stamp so future schema changes can detect old exports cleanly.
CHARACTER_EXPORT_FORMAT_VERSION = 1


def generate_character_json(character) -> str:
    """Serialize a character to a round-trippable JSON string."""
    payload = {
        "_format": CHARACTER_EXPORT_FORMAT,
        "_version": CHARACTER_EXPORT_FORMAT_VERSION,
        "data": {field: getattr(character, field) for field in EXPORTABLE_FIELDS},
    }
    return json.dumps(payload, indent=2, sort_keys=False, default=str)


def _injury_cell(threshold: int | None) -> str:
    """Render one injury-threshold cell.

    A pool small enough that half of it lands inside the flat 1-5 band has no
    -1 range at all, so that threshold prints as a dash rather than a number
    that would contradict the -2 band.
    """
    return "-" if threshold is None else str(threshold)


def _experience_run_heading(track: str, years) -> str:
    """The heading for one experience-log run: track label plus age range.

    Matches the editor history panel's shape (issue #221/#235: experience is
    bought a year at a time, so a "term" is really however many years the
    entry's ``years`` list holds). One year in the run reads "Track, Age N";
    more than one reads "Track, Age N-M", built from the first and last
    year's own recorded ages -- never assumed from a fixed term length. An
    entry with no usable ``years`` (a pre-#221 shape, or a malformed row)
    falls back to the track name alone rather than inventing ages that were
    never recorded (issue #236).
    """
    label = track_label(track)
    if not isinstance(years, list) or not years:
        return label
    first_year, last_year = years[0], years[-1]
    if not isinstance(first_year, dict) or not isinstance(last_year, dict):
        return label
    first_age, last_age = first_year.get("age"), last_year.get("age")
    if not isinstance(first_age, int) or isinstance(first_age, bool):
        return label
    if not isinstance(last_age, int) or isinstance(last_age, bool):
        return label
    if first_age == last_age:
        return f"{label}, Age {first_age}"
    return f"{label}, Age {first_age}–{last_age}"


def _experience_year_line(year: dict) -> str:
    """One line for a single played year, matching the editor's history list.

    Reuses ``survival_roll`` (issue #175/#236) -- the one place the
    survival-roll string is built -- so a check recorded under the old 2d6
    rule renders truthfully as 2d6 instead of being relabelled 3d6.
    """
    year_number = year.get("year", "?")
    skill = year.get("skill", "Unknown")
    skill = skill if isinstance(skill, str) else str(skill)
    roll = year.get("roll", "?")
    line = f"- Year {year_number}: {skill} (rolled {roll})"
    survival_check = year.get("survival_check")
    if isinstance(survival_check, dict):
        roll_text = survival_roll(survival_check)
        if roll_text:
            outcome = "survived" if survival_check.get("success") else "died"
            line += f" — survival {roll_text}: {outcome}"
    return line


def generate_character_markdown(character) -> str:
    """Generate a comprehensive markdown export of a character."""
    lines = []

    # Header
    lines.append(f"# {character.name}")
    lines.append("")
    lines.append(f"**Player:** {character.user.username}")
    lines.append(f"**Created:** {labelled_instant(character.created_at)}")
    lines.append(f"**Status:** {character.status}")
    lines.append("")

    # Core Attributes
    lines.append("## Attributes")
    lines.append("")
    lines.append("| Attribute | Base | Aging Penalty | Effective |")
    lines.append("|-----------|------|---------------|-----------|")
    lines.append(
        f"| Strength | {character.strength} | "
        f"{character.aging_str_penalty} | {character.effective_strength} |"
    )
    lines.append(
        f"| Dexterity | {character.dexterity} | "
        f"{character.aging_dex_penalty} | {character.effective_dexterity} |"
    )
    lines.append(
        f"| Intelligence | {character.intelligence} | "
        f"{character.aging_int_penalty} | {character.effective_intelligence} |"
    )
    lines.append(
        f"| Wisdom | {character.wisdom} | "
        f"{character.aging_wis_penalty} | {character.effective_wisdom} |"
    )
    lines.append(
        f"| Constitution | {character.constitution} | "
        f"{character.aging_con_penalty} | {character.effective_constitution} |"
    )
    lines.append(f"| Charisma | {character.charisma} | - | {character.charisma} |")
    lines.append("")

    # Derived Stats
    lines.append("## Derived Statistics")
    lines.append("")
    lines.append(f"- **Fatigue:** {character.fatigue}")
    lines.append(f"- **Body:** {character.body}")
    condition = character.condition
    if condition is not None:
        # What the character's fights have left them (issue #751).
        mana = f"; {character.mana_text}" if character.mana_text else ""
        lines.append(f"- **Now:** {condition.summary}{mana}")
    lines.append(f"- **Physical:** {character.physical}")
    lines.append(f"- **Mental:** {character.mental}")
    lines.append(f"- **Age:** {character.age}")
    lines.append(f"- **Years of Experience:** {character.experience_years}")
    lines.append(f"- **Total XP:** {character.total_xp}")
    lines.append("")

    # Injury Thresholds
    injury = character.injury_thresholds
    fatigue_thresholds = injury["fatigue"]
    body_thresholds = injury["body"]
    lines.append("### Injury Thresholds")
    lines.append("")
    lines.append("| As a pool falls | Fatigue | Body |")
    lines.append("|-----------------|---------|------|")
    lines.append(
        f"| -1 to all rolls at or below | "
        f"{_injury_cell(fatigue_thresholds['minus_one_at'])} | "
        f"{_injury_cell(body_thresholds['minus_one_at'])} |"
    )
    lines.append(
        f"| -2 to all rolls from | {fatigue_thresholds['minus_two_at']} | "
        f"{body_thresholds['minus_two_at']} |"
    )
    lines.append(
        f"| Unconscious at | {fatigue_thresholds['unconscious_at']} | "
        f"{body_thresholds['unconscious_at']} |"
    )
    lines.append(
        f"| Survival save each turn at or below | "
        f"{fatigue_thresholds['survival_save_at']} | "
        f"{body_thresholds['survival_save_at']} |"
    )
    lines.append(
        f"| Saves penalized at or below | "
        f"{fatigue_thresholds['penalized_save_at']} | "
        f"{body_thresholds['penalized_save_at']} |"
    )
    lines.append("")
    lines.append(
        "The -2 band runs from 5 down to 1. Survival save is 3d6 <= "
        f"{fatigue_thresholds['survival_save_target']} (CON), penalized past the "
        "last row by how far past it you are. Whichever pool is worse governs."
    )
    lines.append("")

    # Physical Description
    lines.append("## Physical Description")
    lines.append("")
    lines.append(f"- **Height:** {character.height_display}")
    lines.append(f"- **Weight:** {character.weight_lbs} lbs")
    lines.append(f"- **Appearance Roll:** {character.appearance_roll}")
    lines.append("")

    # Background
    lines.append("## Background")
    lines.append("")
    lines.append(
        f"- **Provenance Class:** {social_class_display(character.provenance_class)}"
    )
    lines.append(f"- **Provenance Detail:** {character.provenance_detail}")
    lines.append(f"- **Location:** {character.location}")
    lines.append(f"- **Literacy:** {character.literacy_summary}")
    lines.append(f"- **Wealth Level:** {character.wealth_level}")
    # A Rich character's coin is "Consult GM" until set (issue #749).
    coin = "Consult GM" if character.starting_coin is None else character.starting_coin
    lines.append(f"- **Starting Coin:** {coin}")
    lines.append(f"- **Purse:** {format_purse(character)}")
    lines.append("")

    # Skills
    lines.append("## Skills")
    lines.append("")
    if character.skills:
        lines.append(
            f"**Free Skill Points:** {character.free_skill_points} | "
            f"**Total Allocated:** {character.total_allocated_skill_points}"
        )
        lines.append("")
        lines.append("| Skill | Points |")
        lines.append("|-------|--------|")
        for skill, points in sorted(character.skills.items()):
            lines.append(f"| {skill.replace('_', ' ').title()} | {points} |")
    else:
        lines.append("*No skills allocated*")
    lines.append("")

    # Spell points only exist for characters who have taken a Magic-track
    # term; the field defaults to 0 for everyone else, so it's shown only
    # when nonzero (same convention as the skills/equipment/log sections).
    if character.free_spell_points:
        lines.append(f"**Free Spell Points:** {character.free_spell_points}")
        lines.append("")

    # The spells the character knows (issue #774), with the Mana Pool above.
    spells = known_spells(character)
    if spells:
        lines.append("## Spells")
        lines.append("")
        lines.append("| Spell | School | Level (mana) | Roll | Mastery |")
        lines.append("|-------|--------|--------------|------|---------|")
        for spell in spells:
            lines.append(
                f"| {spell.name} | {spell.school} | {spell.spell_level} | "
                f"3d6 ≤ {spell.attribute} | {spell.mastery_display} |"
            )
        lines.append("")

    # Equipment & Encumbrance
    lines.append("## Equipment & Encumbrance")
    lines.append("")
    lines.append(f"- **Encumbrance Level:** {character.encumbrance_level.title()}")
    lines.append(
        f"- **Total Equipment Weight:** {character.total_equipment_weight} lbs"
    )
    lines.append(f"- **Carried Weight:** {character.carried_weight} lbs")
    lines.append(f"- **Pack Weight:** {character.pack_weight} lbs")
    lines.append(f"- **Pack Dropped:** {'Yes' if character.pack_dropped else 'No'}")
    lines.append(f"- **Movement Modifier:** {character.movement_modifier:+d}")
    lines.append(f"- **Movement Penalty:** {character.movement_penalty}")
    gaits = character.gait_readout
    gait_text = " | ".join(
        f"{gait['label']} {'n/a' if gait['na'] else gait['distance']}" for gait in gaits
    )
    lines.append(f"- **Gaits (hexes):** {gait_text}")
    lines.append("")

    # Encumbrance Thresholds
    thresholds = character.encumbrance_thresholds
    lines.append("### Encumbrance Thresholds")
    lines.append("")
    lines.append("| Level | Max Weight |")
    lines.append("|-------|------------|")
    # The loaded canon's levels, lightest first, by their own keys (issue #75).
    for level, pounds in thresholds.items():
        lines.append(f"| {level.title()} | {format_pounds(pounds)} lbs |")
    lines.append("")

    # Movement Restrictions
    if character.movement_restrictions:
        lines.append("### Movement Restrictions")
        lines.append("")
        lines.append(f"- {character.movement_restriction_label}")
        lines.append("")

    # Combat Modifiers
    lines.append("### Combat Modifiers")
    lines.append("")
    lines.append(f"- **DEX Combat Mod (to-hit aim):** {character.dex_modifier:+d}")
    lines.append(f"- **Dodge (added to attacker TN):** {character.dodge_modifier:+d}")
    lines.append(f"- **Armor Stops:** {character.armor_stops}")
    lines.append(f"- **Armor DEX Penalty:** {character.armor_dex_penalty}")
    lines.append(f"- **Weapon To-Hit Mod:** {character.weapon_to_hit_mod}")
    lines.append(f"- **Weapon Damage Mod:** {character.weapon_damage_mod}")
    lines.append(f"- **Weapon Critical Mod:** {character.weapon_critical_mod}")
    lines.append("")

    # Equipment List
    if character.equipment:
        lines.append("### Equipment List")
        lines.append("")
        lines.append("| Item | Qty | Weight | In Pack | Equipped | Category |")
        lines.append("|------|-----|--------|---------|----------|----------|")
        # Rows come from the model, which resolves a catalog item's name,
        # weight and category out of the catalog -- the stored entry carries
        # none of them, and reading them off it printed every catalog item as
        # "Unknown | 0 lbs | -", contradicting the Total Equipment Weight
        # above (issue #633).
        for row in character.equipment_rows:
            in_pack = "Yes" if row.in_pack else "No"
            equipped = "Yes" if row.equipped else "No"
            lines.append(
                f"| {row.name or 'Unknown'} | {row.quantity} | "
                f"{row.total_weight} lbs | "
                f"{in_pack} | {equipped} | {row.category or '-'} |"
            )
        lines.append("")

    # Status
    if not character.is_alive:
        lines.append("## Death")
        lines.append("")
        lines.append(f"**Cause of Death:** {character.death_cause or 'Unknown'}")
        lines.append("")

    # Experience Log
    if character.experience_log:
        lines.append("## Experience Log")
        lines.append("")
        for entry in character.experience_log:
            if isinstance(entry, dict):
                # Import now guarantees these types on anything saved going
                # forward (characters/views/characters.py:character_import), but rows
                # written before that guarantee (or by another path) may still
                # carry bad data — coerce defensively rather than trust it, so
                # export degrades to a readable value instead of crashing.
                track = entry.get("track", "Unknown")
                track = track if isinstance(track, str) else str(track)
                xp_gained = entry.get("xp_awarded", 0)
                if not isinstance(xp_gained, int | float) or isinstance(
                    xp_gained, bool
                ):
                    xp_gained = 0
                skills_gained = entry.get("skills_gained", {})
                if not isinstance(skills_gained, dict):
                    skills_gained = {}
                # One entry per run of years (issue #221/#235), matching the
                # editor's history panel: the heading and per-year survival
                # lines are built from the ages/rolls actually stored in the
                # entry, not assumed from a fixed term length.
                years = entry.get("years")
                lines.append(f"### {_experience_run_heading(track, years)}")
                if isinstance(years, list):
                    for year in years:
                        if isinstance(year, dict):
                            lines.append(_experience_year_line(year))
                lines.append(f"- **XP Gained:** {xp_gained:,}")
                if skills_gained:
                    lines.append("- **Skills Gained:**")
                    for skill, points in skills_gained.items():
                        skill_label = skill if isinstance(skill, str) else str(skill)
                        if not isinstance(points, int | float) or isinstance(
                            points, bool
                        ):
                            points = 0
                        lines.append(
                            f"  - {skill_label.replace('_', ' ').title()}: +{points}"
                        )
            else:
                lines.append(f"- {entry}")
        lines.append("")

    # Dice Log
    if character.dice_log:
        lines.append("## Dice Log")
        lines.append("")
        lines.append("```")
        lines.append(character.dice_log)
        lines.append("```")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Exported on {labelled_instant()} from Tarmar Studio*")

    return "\n".join(lines)


def generate_character_html(character) -> str:
    """Generate HTML version of the character export for PDF conversion."""
    import markdown

    md_content = generate_character_markdown(character)
    html_body = bleach.clean(
        markdown.markdown(md_content, extensions=["tables"]),
        tags=EXPORT_ALLOWED_TAGS,
        attributes=EXPORT_ALLOWED_ATTRIBUTES,
        strip=True,
    )
    title = html_lib.escape(character.name)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title} - Character Sheet</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            color: #333;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #2980b9;
            border-bottom: 1px solid #bdc3c7;
            padding-bottom: 5px;
            margin-top: 30px;
        }}
        h3 {{
            color: #7f8c8d;
            margin-top: 20px;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 15px 0;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px 12px;
            text-align: left;
        }}
        th {{
            background-color: #3498db;
            color: white;
        }}
        tr:nth-child(even) {{
            background-color: #f9f9f9;
        }}
        tr:hover {{
            background-color: #f5f5f5;
        }}
        code, pre {{
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }}
        pre {{
            padding: 15px;
            overflow-x: auto;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        ul {{
            padding-left: 25px;
        }}
        li {{
            margin: 5px 0;
        }}
        strong {{
            color: #2c3e50;
        }}
        hr {{
            border: none;
            border-top: 1px solid #eee;
            margin: 30px 0;
        }}
        em {{
            color: #7f8c8d;
        }}
        @media print {{
            body {{
                padding: 0;
            }}
            h2 {{
                page-break-before: auto;
            }}
            table {{
                page-break-inside: avoid;
            }}
        }}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""

    return html
