"""The rulings' words are configuration, checked at startup (issues #867, #37)."""

import tempfile
import tomllib
from dataclasses import replace
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from battle import wording

SHIPPED = Path(settings.BASE_DIR) / "config" / "battle-rulings.toml"


class LoadRulingsWordingTest(SimpleTestCase):
    """``load_rulings_wording`` refuses a bad file by name, at startup."""

    def write(self, directory: Path, text: str, name: str = "rulings.toml") -> Path:
        path = directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def shipped_with(self, table: str, key: str | None, value: str | None) -> str:
        """The shipped file's text with one key changed, or dropped (None), or
        with a whole table dropped (``key`` None)."""
        raw = tomllib.loads(SHIPPED.read_text(encoding="utf-8"))
        if key is None:
            del raw[table]
        elif value is None:
            del raw[table][key]
        else:
            raw[table][key] = value
        return "\n".join(
            f"[{name}]\n"
            + "\n".join(
                f"{field} = {_toml_string(text)}" for field, text in keys.items()
            )
            for name, keys in raw.items()
        )

    def test_the_shipped_file_loads_and_settings_hold_the_named_file(self):
        """The shipped file passes every check; its sentences are Spencer's
        and are not pinned here."""
        wording.load_rulings_wording(SHIPPED)
        self.assertEqual(
            wording.load_rulings_wording(settings.BATTLE_RULINGS_WORDING_FILE),
            settings.BATTLE_RULINGS_WORDING,
        )

    def test_a_bad_file_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            cases = {
                "missing": directory / "absent.toml",
                "not TOML": self.write(
                    directory, "this is = = not toml", "not-toml.toml"
                ),
                "unknown table": self.write(
                    directory,
                    SHIPPED.read_text(encoding="utf-8") + '\n[extra]\nword = "x"\n',
                    "extra-table.toml",
                ),
                "missing table": self.write(
                    directory,
                    self.shipped_with("common", None, None),
                    "no-common.toml",
                ),
            }
            for label, path in cases.items():
                with (
                    self.subTest(label),
                    self.assertRaisesMessage(ImproperlyConfigured, str(path)),
                ):
                    wording.load_rulings_wording(path)
            for label, (table, key, value, message) in {
                "missing key": ("magic_bounds", "heading", None, "missing keys"),
                "unknown key": ("magic_bounds", "headline", "x", "unknown keys"),
                "blank": ("hth_agreement", "heading", "  ", "is not a sentence"),
                "stray name": (
                    "magic_bounds",
                    "log_lifted",
                    "{by} lifts {what} for {name}",
                    "[magic_bounds] log_lifted cannot be filled (KeyError('what'))",
                ),
                "unbalanced brace": (
                    "magic_bounds",
                    "log_lifted",
                    "{by lifts {bounds} for {name}",
                    "[magic_bounds] log_lifted cannot be filled (ValueError(",
                ),
                "nested spec": (
                    "magic_bounds",
                    "none_lifted",
                    "Nothing lifted: {bounds:{zz}}",
                    "[magic_bounds] none_lifted cannot be filled (KeyError('zz'))",
                ),
                "nested spec from an allowed name": (
                    "magic_bounds",
                    "log_lifted",
                    "{by:>{name}} lifts {bounds}",
                    "[magic_bounds] log_lifted has a field that is not a bare name",
                ),
                "attribute access": (
                    "magic_bounds",
                    "log_lifted",
                    "{by.upper} lifts {bounds} for {name}",
                    "[magic_bounds] log_lifted has a field that is not a bare name",
                ),
                "index": (
                    "magic_bounds",
                    "log_lifted",
                    "{by[0]} lifts {bounds} for {name}",
                    # The empty trial value already has no index 0.
                    "[magic_bounds] log_lifted cannot be filled (IndexError(",
                ),
                "conversion": (
                    "magic_bounds",
                    "log_lifted",
                    "{by!r} lifts {bounds} for {name}",
                    "[magic_bounds] log_lifted has a field that is not a bare name",
                ),
                "positional": (
                    "hth_agreement",
                    "pairs_line",
                    "Pairs: {}",
                    "[hth_agreement] pairs_line cannot be filled (IndexError(",
                ),
                # A join that drops the sentence it carries (#90).
                "dropped line": (
                    "tarmar_menu",
                    "yielded",
                    "Moving in Final Movement instead",
                    "[tarmar_menu] yielded must use ['line']",
                ),
                "dropped ruling": (
                    "tarmar_menu",
                    "cost_past_ruling",
                    "{cost}",
                    "[tarmar_menu] cost_past_ruling must use ['past']",
                ),
                "dropped guards": (
                    "tarmar_menu",
                    "dodge",
                    "Dodge: +4 to your Target Number",
                    "[tarmar_menu] dodge must use ['guards']",
                ),
            }.items():
                path = self.write(directory, self.shipped_with(table, key, value))
                with (
                    self.subTest(label),
                    self.assertRaisesMessage(ImproperlyConfigured, message),
                ):
                    wording.load_rulings_wording(path)

    def test_a_sentence_may_leave_out_a_name(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = self.write(
                Path(scratch),
                self.shipped_with("magic_bounds", "log_lifted", "{by} lifts bounds"),
            )
            loaded = wording.load_rulings_wording(path)
        self.assertEqual(loaded.magic_bounds.log_lifted, "{by} lifts bounds")


class WordsTest(SimpleTestCase):
    """The helpers read the loaded words, never their own copy."""

    def test_lists_and_names_follow_the_configuration(self):
        changed = wording.load_rulings_wording(SHIPPED)
        changed = replace(
            changed,
            common=replace(
                changed.common,
                and_word="und",
                game_master="Spielleiter {name}",
                unknown_figure="niemand",
            ),
        )
        with override_settings(BATTLE_RULINGS_WORDING=changed):
            self.assertEqual(wording.listed(["A", "B", "C"]), "A, B und C")
            self.assertEqual(wording.listed(["A"]), "A")
            self.assertEqual(wording.game_master_name("dm"), "Spielleiter dm")
            self.assertEqual(wording.name_of({1: "Aldwin"}, 2), "niemand")


def _toml_string(text: str) -> str:
    """``text`` as a TOML basic string."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
