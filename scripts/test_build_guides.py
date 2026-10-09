"""Unit tests for the guide generator's pure transforms and leak check."""

import build_guides


def test_demote_headings_adds_one_level():
    source = "# Title\n\ntext\n\n## Sub\n"
    result = build_guides.demote_headings(source)
    assert "## Title" in result
    assert "### Sub" in result


def test_demote_headings_caps_at_six_and_skips_code_fences():
    source = "###### Deep\n\n```\n# not a heading\n```\n"
    result = build_guides.demote_headings(source)
    assert "###### Deep" in result  # capped, not seven hashes
    assert "# not a heading" in result  # untouched inside fence


def test_clean_links_strips_site_links_and_images():
    source = "See [Dragons](/tarmar/reference/the-world-of-tarmar/dragons/) now.\n"
    assert build_guides.clean_links(source) == "See Dragons now."


def test_clean_links_converts_images_and_keeps_external():
    source = "![World Map](/static/x.png)\n[site](https://example.com)\n"
    result = build_guides.clean_links(source)
    assert "*(image: World Map)*" in result
    assert "[site](https://example.com)" in result  # external link preserved


def test_clean_links_drops_draft_notes():
    assert build_guides.clean_links("*Draft — first pass.*\nkeep\n").strip() == "keep"


# Made-up terms: the real list is the Tarmar canon's, and names its secrets.
MADE_UP_TERMS = """\
# a comment, and a blank line below

Quorvex
Glimmering Accord
match-case: the Lattice
lantern
allow: Lantern of the Seven Fords
"""


def made_up_rules() -> build_guides.LeakRules:
    return build_guides.parse_forbidden_terms(MADE_UP_TERMS)


def test_find_leaks_detects_forbidden_terms():
    leaks = build_guides.find_leaks(
        "The quorvex is real; so is the Glimmering Accord.", made_up_rules()
    )
    assert leaks == ["Quorvex", "Glimmering Accord"]


def test_find_leaks_matches_whole_words_only():
    assert build_guides.find_leaks("Quorvexian lanterns", made_up_rules()) == []


def test_a_match_case_term_matches_only_as_written():
    assert build_guides.find_leaks("the lattice of vines", made_up_rules()) == []
    assert build_guides.find_leaks("the Lattice wakes", made_up_rules()) == [
        "the Lattice"
    ]


def test_find_leaks_allows_the_phrase_but_flags_the_bare_term():
    rules = made_up_rules()
    assert build_guides.find_leaks("the Lantern of the Seven Fords", rules) == []
    assert build_guides.find_leaks("a lantern installed after", rules) == ["lantern"]


def test_find_leaks_clean_text_passes():
    rules = made_up_rules()
    assert build_guides.find_leaks("The dragons watched and took no side.", rules) == []


def test_no_term_list_means_no_terms(tmp_path):
    rules = build_guides.load_forbidden_terms(tmp_path / "forbidden-terms.txt")
    assert rules == build_guides.LeakRules()
    assert build_guides.find_leaks("Quorvex", rules) == []


def test_the_term_list_is_read_from_the_canons_author_notes(tmp_path):
    term_file = tmp_path / "forbidden-terms.txt"
    term_file.write_text(MADE_UP_TERMS, encoding="utf-8")
    assert build_guides.load_forbidden_terms(term_file) == made_up_rules()
    assert build_guides.FORBIDDEN_TERMS_FILE == (
        build_guides.DM_RULES_DIR / "author" / "forbidden-terms.txt"
    )


def test_collect_pages_resolves_paths_and_inherits_guides():
    nav = [
        {
            "title": "Public Rules",
            "slug": "public-rules",
            "guides": ["players_guide"],
            "children": [
                {"title": "Combat", "slug": "combat"},
                {"title": "Magic", "slug": "magic", "guides": ["gms_guide"]},
            ],
        }
    ]
    pages = {p.path: p for p in build_guides.collect_pages(nav)}
    assert pages["public-rules/combat"].guides == ["players_guide"]  # inherited
    assert pages["public-rules/magic"].guides == ["gms_guide"]  # overridden


# --- Who each guide is for, and where it is written (issue #921) ---


def test_only_the_tarmar_canon_builds_the_private_guides():
    assert build_guides.builds_private_guides("tarmar") is True
    assert build_guides.builds_private_guides("default") is False


def test_private_guides_go_to_the_canon_and_public_ones_to_build():
    by_key = {spec.key: spec for spec in build_guides.NAV_GUIDES}
    assert build_guides.output_path_for(by_key["players_guide"]) == (
        build_guides.BUILD_DIR / "tarmar_players_guide.md"
    )
    assert build_guides.output_path_for(by_key["the_world_of_tarmar"]) == (
        build_guides.BUILD_DIR / "the_world_of_tarmar.md"
    )
    assert build_guides.output_path_for(by_key["gms_guide"]) == (
        build_guides.CANON_GUIDES_DIR / "tarmar_gms_guide.md"
    )
    assert build_guides.CANON_GUIDES_DIR == build_guides.DM_RULES_DIR / "guides"
    assert build_guides.COSMOLOGY_SOURCE == (
        build_guides.DM_RULES_DIR / "author" / "cosmology-notes.md"
    )


def test_without_the_private_guides_only_build_is_written():
    rendered = build_guides.generate_all(include_private=False)
    assert [path for path, _content, _result in rendered] == [
        build_guides.BUILD_DIR / "tarmar_players_guide.md",
        build_guides.BUILD_DIR / "the_world_of_tarmar.md",
    ]


def test_a_player_facing_guide_refuses_a_canon_page():
    spec = build_guides.GuideSpec(
        key="players_guide",
        title="T",
        blurb="",
        output_name="t.md",
        leak_check=True,
    )
    page = build_guides.Page(
        path="dm-rules/the-world",
        title="The World",
        guides=["players_guide"],
        dm_only=True,
    )
    try:
        build_guides.build_nav_guide(spec, [page])
    except ValueError as error:
        assert "dm-rules/the-world" in str(error)
    else:
        raise AssertionError("a canon page reached a player-facing guide")


def test_the_committed_build_is_current_under_whatever_canon_is_loaded(capsys):
    # build/ no longer depends on the canon, so this holds under both.
    assert build_guides.main(check_only=True) == 0
    output = capsys.readouterr().out
    if build_guides.CANON_NAME == "default":
        assert "skipped" in output
        assert "tarmar_gms_guide.md" not in output
    else:
        assert str(build_guides.CANON_GUIDES_DIR / "tarmar_gms_guide.md") in output
