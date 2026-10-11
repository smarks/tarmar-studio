"""The browser tests' mail backend writes one file per connection (issue #967).

Django's file backend names a file after the second and ``id(connection)``,
so connections made and dropped in quick succession can share a name and
append to one file. The project's backend must never do that.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from django.core import mail
from django.test import SimpleTestCase, override_settings

from common.mail_backends import OneFilePerConnectionEmailBackend

BACKEND = "common.mail_backends.OneFilePerConnectionEmailBackend"


class OneFilePerConnectionTest(SimpleTestCase):
    def test_many_messages_in_one_second_land_in_as_many_files(self):
        with TemporaryDirectory() as directory:
            with override_settings(EMAIL_BACKEND=BACKEND, EMAIL_FILE_PATH=directory):
                # Each send_mail makes and drops a connection, the pattern
                # that reuses an object address under Django's naming.
                for index in range(200):
                    mail.send_mail(
                        "subject", "body", None, [f"recipient-{index}@example.test"]
                    )
            files = sorted(Path(directory).glob("*.log"))
            self.assertEqual(len(files), 200)
            recipients = {
                line
                for file in files
                for line in file.read_text().splitlines()
                if line.startswith("To: ")
            }
            self.assertEqual(len(recipients), 200)

    def test_a_connection_keeps_its_file_name(self):
        with TemporaryDirectory() as directory:
            backend = OneFilePerConnectionEmailBackend(file_path=directory)
            first = backend._get_filename()
            self.assertEqual(backend._get_filename(), first)
            self.assertTrue(first.startswith(directory))
            self.assertTrue(first.endswith(".log"))
