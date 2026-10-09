"""Work the queue of GM sessions sent to Claude for review (issue #402).

The reviewer on the LLM host drives this over ssh; nothing here talks to a
model. One command with a verb for each step, so the queue's rules live in
one place::

    manage.py gm_review list                 # claimable review ids, oldest first
    manage.py gm_review show 12              # note, frozen session and findings, as JSON
    manage.py gm_review claim 12 --runner quantumfoam
    manage.py gm_review complete 12 < findings.md
    manage.py gm_review fail 12 --reason "the model run timed out"
"""

import json
import sys

from django.core.management.base import BaseCommand, CommandError, CommandParser

from gm.models import GMReviewRequest


class Command(BaseCommand):
    help = "List, show, claim, complete or fail GM sessions sent to Claude for review."

    def add_arguments(self, parser: CommandParser) -> None:
        verbs = parser.add_subparsers(dest="verb", required=True)
        verbs.add_parser("list", help="Claimable review ids, oldest first.")
        show = verbs.add_parser("show", help="One review as JSON.")
        show.add_argument("review_id", type=int)
        claim = verbs.add_parser("claim", help="Take a review for a runner.")
        claim.add_argument("review_id", type=int)
        claim.add_argument("--runner", required=True)
        complete = verbs.add_parser("complete", help="Record findings read from stdin.")
        complete.add_argument("review_id", type=int)
        fail = verbs.add_parser("fail", help="Record why a review could not be done.")
        fail.add_argument("review_id", type=int)
        fail.add_argument("--reason", required=True)

    def handle(self, *args: object, **options: object) -> None:
        verb = options["verb"]
        if verb == "list":
            for review_id in GMReviewRequest.claimable().values_list("pk", flat=True):
                self.stdout.write(str(review_id))
            return
        review_id = int(str(options["review_id"]))
        if verb == "claim":
            if not GMReviewRequest.claim(review_id, str(options["runner"])):
                raise CommandError(f"Review {review_id} is not claimable")
            self.stdout.write(f"claimed {review_id}")
            return
        review = self._review(review_id)
        if verb == "show":
            self.stdout.write(
                json.dumps(
                    self._payload(review), indent=2, ensure_ascii=False, default=str
                )
            )
        elif verb == "complete":
            self._require_claimed(review)
            findings = sys.stdin.read().strip()
            if not findings:
                raise CommandError(f"No findings given for review {review_id}")
            review.complete(findings)
            self.stdout.write(f"completed {review_id}")
        elif verb == "fail":
            self._require_claimed(review)
            review.fail(str(options["reason"]))
            self.stdout.write(f"failed {review_id}")

    def _review(self, review_id: int) -> GMReviewRequest:
        try:
            return GMReviewRequest.objects.get(pk=review_id)
        except GMReviewRequest.DoesNotExist as error:
            raise CommandError(f"No review {review_id}") from error

    def _require_claimed(self, review: GMReviewRequest) -> None:
        if review.status != GMReviewRequest.STATUS_CLAIMED:
            raise CommandError(
                f"Review {review.pk} is {review.status}, not claimed; claim it first"
            )

    def _payload(self, review: GMReviewRequest) -> dict[str, object]:
        return {
            "review": {
                "id": review.pk,
                "status": review.status,
                "note": review.note,
                "session_id": review.session_id,
                "requested_by": review.requested_by.get_username()
                if review.requested_by
                else None,
                "created_at": review.created_at.isoformat(),
                "findings": review.findings,
                "failure": review.failure,
            },
            "snapshot": review.snapshot,
        }
