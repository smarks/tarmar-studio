"""The GM page's conversation and resolved threads, a page at a time (#972).

Spencer's ruling on #972, 2026-10-10: "the GM page renders the latest
PAGE_SIZE turns and resolved threads; a persistent 'Show earlier' control
loads the previous page above (no transient UI); the transcript export keeps
everything."

Only what the page renders is paged. The model's history, the recap and the
fold read the stored history (``GMSession.history``), the transcript export
reads every entry, and the map detector reads the whole visible history, so
none of them sees a page.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from django.db.models import Q

from common.pagination import PAGE_SIZE, latest_window

from .models import GMSession, StoryThread
from .retrieval import ChatMessage


@dataclass(frozen=True)
class HistoryTurn:
    """One turn of a session's visible conversation, and where it stands.

    Attributes:
        number: The turn's place in the conversation, counted from 0. History
            is only ever appended to, so a turn's number does not move; the
            page marks each entry with it (``data-history-turn``), which is
            how it knows which turns it holds (issue #89).
        entries: The turn's visible entries, oldest first.
    """

    number: int
    entries: list[ChatMessage]


@dataclass(frozen=True)
class HistoryPage:
    """Some consecutive turns of a session's visible conversation.

    Attributes:
        turns: The page's turns, oldest first, as the chat renders them.
        start: The first turn on the page, counted from 0; also how many
            turns come before it, which the "Show earlier" control offers.
        total: How many turns the conversation holds.
        continues_the_page: The page goes on from turns the reader's page
            already holds (:func:`history_since`), so it offers no control
            for the turns above it: they are on the page.
    """

    turns: list[HistoryTurn]
    start: int
    total: int
    continues_the_page: bool = False

    @property
    def entries(self) -> list[ChatMessage]:
        """The page's visible entries, oldest first, turn after turn."""
        return [entry for turn in self.turns for entry in turn.entries]

    @property
    def offers_earlier(self) -> bool:
        """Whether turns above the page are not on the reader's page."""
        return bool(self.start) and not self.continues_the_page


def history_turns(visible: Sequence[ChatMessage]) -> list[list[ChatMessage]]:
    """The visible conversation as turns: each player message and what follows.

    A turn starts at each entry the player wrote (``role`` ``user``, a typed
    ``/roll`` among them) and holds everything up to the next: the GM's
    reply, the dice chips, notices, a fold's recap. Entries before the first
    player message (a notice filed before anyone spoke) are a turn of their
    own, so paging never parts a reply from the message it answers.

    Args:
        visible: The entries a reader sees (``gm.views._visible_history``).

    Returns:
        The turns, oldest first; empty for an empty conversation.
    """
    turns: list[list[ChatMessage]] = []
    for entry in visible:
        if entry.get("role") == "user" or not turns:
            turns.append([])
        turns[-1].append(entry)
    return turns


def history_page(
    visible: Sequence[ChatMessage], *, before: int | None = None, size: int = PAGE_SIZE
) -> HistoryPage:
    """The page of turns ending just before turn ``before``.

    Args:
        visible: The entries a reader sees, oldest first.
        before: The first turn already on the page, for the page above it;
            ``None`` for the latest page, which the GM page opens on. History
            is only ever appended to, so a turn's number does not move.
        size: Turns a page holds.

    Returns:
        The page.
    """
    turns = history_turns(visible)
    start, end = latest_window(len(turns), before, size)
    return HistoryPage(
        turns=_numbered(turns, start, end), start=start, total=len(turns)
    )


def history_since(visible: Sequence[ChatMessage], since: int) -> HistoryPage:
    """Every turn from turn ``since`` to the end (issue #89).

    For a page that already holds the turns before ``since`` and must show
    the conversation as the server now holds it -- after a turn it lost sight
    of, or a fight that wrote to the chat -- without a reload, which would
    throw away the earlier pages the reader loaded. It holds every turn from
    there, however many, since what the page holds below it is replaced.

    Args:
        visible: The entries a reader sees, oldest first.
        since: The last turn the page holds as the server rendered it; it is
            asked for again, as it may have gained entries since. Clamped
            into the conversation.

    Returns:
        The page, which offers no control above it.
    """
    turns = history_turns(visible)
    start = max(0, min(since, len(turns)))
    return HistoryPage(
        turns=_numbered(turns, start, len(turns)),
        start=start,
        total=len(turns),
        continues_the_page=True,
    )


def _numbered(
    turns: list[list[ChatMessage]], start: int, end: int
) -> list[HistoryTurn]:
    """Turns ``start`` up to ``end``, each with its number."""
    return [
        HistoryTurn(number=number, entries=turns[number])
        for number in range(start, end)
    ]


@dataclass(frozen=True)
class ResolvedThreadsPage:
    """Some consecutive resolved threads of a session, in the tab's order.

    Attributes:
        threads: The page's threads, oldest opened first, as the Campaign
            tab lists them (``StoryThread.Meta.ordering``).
        earlier: How many resolved threads come before the page.
        total: How many resolved threads the session holds.
    """

    threads: list[StoryThread]
    earlier: int
    total: int

    @property
    def earlier_before(self) -> int | None:
        """The cursor for the page above: the first thread shown, if any."""
        return self.threads[0].pk if self.earlier and self.threads else None


def resolved_threads_page(
    session: GMSession,
    *,
    before: StoryThread | None = None,
    since: StoryThread | None = None,
    size: int = PAGE_SIZE,
) -> ResolvedThreadsPage:
    """The page of resolved threads listed just before ``before``.

    Keyed on the thread rather than on a position, since threads are
    resolved and reopened while the tab is open: a position would move under
    the reader, a thread's place in the order does not.

    ``since`` is for the tab refetched with pages already loaded (issue #982):
    it holds every resolved thread from that thread's place to the end, so
    the pages a reader loaded stay and the threads resolved since are added,
    and never fewer than the latest page.

    Args:
        session: The session.
        before: The first thread already shown, for the page above it;
            ``None`` for the latest page. Not used when ``since`` names
            a longer run.
        since: The first thread of the earliest page loaded, for the whole
            run the reader has loaded; ``None`` for the latest page.
        size: Threads a page holds.

    Returns:
        The page.
    """
    resolved = StoryThread.objects.filter(session=session, resolved=True)
    total = resolved.count()
    if since is not None:
        loaded = resolved.filter(
            Q(created_at__gt=since.created_at)
            | Q(created_at=since.created_at, pk__gte=since.pk)
        )
        threads = list(loaded.order_by("created_at", "pk"))
        if len(threads) > size:
            return ResolvedThreadsPage(
                threads=threads,
                earlier=total - len(threads),
                total=total,
            )
    listed = resolved
    if before is not None:
        listed = resolved.filter(
            Q(created_at__lt=before.created_at)
            | Q(created_at=before.created_at, pk__lt=before.pk)
        )
    threads = list(listed.order_by("-created_at", "-pk")[:size])[::-1]
    return ResolvedThreadsPage(
        threads=threads,
        earlier=listed.count() - len(threads),
        total=total,
    )
