"""DM-gated Dungeons & Ruins generator page (issue #194).

State-driven per the house UI rule: generation is a GET whose URL carries the
room count and seed, so the rendered ruin is reproducible from the address bar
alone — reload and it is all still there, and the link can be shared. A
request without a seed gets one rolled and is redirected to the fully
specified URL. Errors render back into the same form with the entered values
kept (the #192 no-dead-ends standard); nothing transient, no modals (#190).
"""

from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from common.breadcrumbs import BreadcrumbMixin
from common.mixins import DMToolsAccessMixin
from common.seeds import InvalidSeed, parse_seed, roll_seed

from .generation import DEFAULT_ROOMS, MAX_ROOMS, MIN_ROOMS, generate_ruin
from .hook_generation import (
    NoHooksForLevel,
    generate_hook,
    matched_level,
    published_levels,
)


class GeneratorFormView(BreadcrumbMixin, DMToolsAccessMixin, TemplateView):
    """The ruin and hook generator pages' shared form handling."""

    def render_error(self, message: str, form_state: dict):
        """Render the form again with a persistent error and the entered values."""
        return self.render_to_response(
            self.get_context_data(generate_error=message, **form_state)
        )


class GenerateRuinView(GeneratorFormView):
    active_page = "ruins"
    breadcrumb_label = "Ruin generator"

    template_name = "ruins/generator.html"

    def get(self, request, *args, **kwargs):
        raw_rooms = request.GET.get("rooms", "")
        raw_seed = request.GET.get("seed", "").strip()
        form_state = {
            "form_rooms": raw_rooms or DEFAULT_ROOMS,
            "form_seed": raw_seed,
            "min_rooms": MIN_ROOMS,
            "max_rooms": MAX_ROOMS,
        }
        if "rooms" not in request.GET:
            return self.render_to_response(self.get_context_data(**form_state))

        try:
            room_count = int(raw_rooms)
        except ValueError:
            return self.render_error(
                f"Room count must be a whole number, not “{raw_rooms}”.", form_state
            )
        if not MIN_ROOMS <= room_count <= MAX_ROOMS:
            return self.render_error(
                f"Room count must be between {MIN_ROOMS} and {MAX_ROOMS}.", form_state
            )
        try:
            seed = parse_seed(raw_seed)
        except InvalidSeed as invalid:
            return self.render_error(str(invalid), form_state)
        if seed is None:
            # No seed given: roll one and put it in the URL, so the page the
            # DM lands on names the exact ruin it shows.
            seed = roll_seed()
            query = urlencode({"rooms": room_count, "seed": seed})
            return redirect(f"{reverse('ruins:generate')}?{query}")

        ruin = generate_ruin(room_count=room_count, seed=seed)
        return self.render_to_response(self.get_context_data(ruin=ruin, **form_state))


class GenerateHookView(GeneratorFormView):
    """DM-gated party-matched hook generator (issue #276).

    Same state-driven shape as :class:`GenerateRuinView`: generation is a GET
    whose URL carries the party (or hand-picked level) and the seed, so the
    rendered hook is reproducible from the address bar. The matching rule is
    stated in :mod:`ruins.hook_generation`; here it only surfaces — a chosen
    party's roster average picks the level, and a party above every published
    band gets a Scenario Seeds pointer instead of a silent reroll.
    """

    breadcrumb_label = "Hook generator"
    active_page = "hooks"
    template_name = "ruins/hook_generator.html"

    def get(self, request, *args, **kwargs):
        from parties.models import Party

        raw_party = request.GET.get("party", "").strip()
        raw_level = request.GET.get("level", "").strip()
        raw_seed = request.GET.get("seed", "").strip()
        parties = [
            {"pk": party.pk, "name": party.name, "summary": party.experience_summary}
            for party in Party.objects.all().order_by("name")
        ]
        form_state = {
            "parties": parties,
            "levels": published_levels(),
            "form_party": raw_party,
            "form_level": raw_level,
            "form_seed": raw_seed,
        }
        if "party" not in request.GET and "level" not in request.GET:
            return self.render_to_response(self.get_context_data(**form_state))

        party = None
        if raw_party:
            try:
                party = Party.objects.get(pk=int(raw_party))
            except (ValueError, Party.DoesNotExist):
                return self.render_error("That party does not exist.", form_state)
            summary = party.experience_summary
            if summary is None:
                return self.render_error(
                    f"{party.name} has no members to match hooks against — "
                    "seat a character first, or pick a level instead.",
                    form_state,
                )
            level = matched_level(summary["average_level"])
        elif raw_level:
            try:
                level = int(raw_level)
            except ValueError:
                return self.render_error(
                    f"Level must be a whole number, not “{raw_level}”.", form_state
                )
        else:
            return self.render_error("Pick a party or a level.", form_state)

        try:
            seed = parse_seed(raw_seed)
        except InvalidSeed as invalid:
            return self.render_error(str(invalid), form_state)
        if seed is None:
            # No seed given: roll one and put it in the URL, so the page the
            # DM lands on names the exact hook it shows.
            seed = roll_seed()
            params: dict[str, str] = {}
            if raw_party:
                params["party"] = raw_party
            else:
                params["level"] = raw_level
            params["seed"] = str(seed)
            return redirect(f"{reverse('hooks:generate')}?{urlencode(params)}")

        try:
            generated = generate_hook(level=level, seed=seed)
        except NoHooksForLevel as outgrown:
            context = self.get_context_data(
                outgrown_level=outgrown.level,
                outgrown_party=party,
                outgrown_summary=party.experience_summary if party else None,
                **form_state,
            )
            return self.render_to_response(context)

        from reference.rendering import render_reference_markdown

        context = self.get_context_data(
            generated=generated,
            generated_party=party,
            generated_summary=party.experience_summary if party else None,
            region_html=render_reference_markdown(generated.hook.region_markdown),
            body_html=render_reference_markdown(generated.hook.body_markdown),
            **form_state,
        )
        return self.render_to_response(context)
