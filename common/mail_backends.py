"""The mail backend the browser tests read from (issue #967).

Django's ``filebased.EmailBackend`` names each connection's file after the
second it opened and ``id(connection)``, and opens it for appending.
``send_mail`` makes a connection per call and drops it at once, so the next
call's connection is often at the same address: two messages sent in one
second then share a file. ``e2e/mail.js`` takes a message by removing its
file, so a spec that took its own message could remove another worker's with
it, and that worker then waited for mail that had already been thrown away.

This backend keeps everything else Django's does and names the file so that
no two connections can share one, whatever the clock or the allocator does.
"""

from __future__ import annotations

import datetime
import os
import uuid

from django.core.mail.backends.filebased import EmailBackend as FileEmailBackend


class OneFilePerConnectionEmailBackend(FileEmailBackend):
    """Django's file backend, with a file name that cannot repeat."""

    def _get_filename(self) -> str:
        """The connection's file: a timestamp for reading by eye, a uuid for uniqueness.

        Returns:
            The absolute path, fixed on the first call for the connection's
            lifetime, as the parent's is.
        """
        if self._fname is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self._fname = os.path.join(
                self.file_path, f"{timestamp}-{uuid.uuid4().hex}.log"
            )
        return self._fname
