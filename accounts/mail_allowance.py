"""How many messages one inbox may receive from this site in an hour.

Password reset (#929) and email confirmation (#931) both send mail to an
address somebody typed, so both could be used to fill a stranger's inbox. They
share one counter per target address, keyed on a hash of the lowercased
address: two flows with an allowance each would simply double what one inbox
can be sent. The key keeps the prefix #929 gave it, so a counter already open
when this shipped still counts.

Sharing is by design, with a consequence worth knowing: three reset requests
for an address in an hour (anyone's, from anywhere) block that address's
confirmation link, and its change notice, until the window closes. The change
form says so and nothing is changed; the holder tries again later.
"""

from __future__ import annotations

import hashlib

from common.rate_limit import charge, current_count

#: Messages one address may be sent in a window, across every flow that mails.
MAX_SENDS_PER_ADDRESS = 3
#: The window, in seconds.
ADDRESS_WINDOW_SECONDS = 3600

_KEY_PREFIX = "rate_limit:password_reset:email_"


def address_digest(address: str) -> str:
    """The hash an address is counted under, so the key never holds it.

    Args:
        address: The address as typed.

    Returns:
        The hex SHA-256 of the address, stripped and lowercased.
    """
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


def address_allowance_spent(address: str) -> bool:
    """Has ``address`` been sent its share for the current window?

    Args:
        address: The address a message would go to.

    Returns:
        True when no further message may go to it until the window closes.
    """
    return current_count(_KEY_PREFIX + address_digest(address)) >= (
        MAX_SENDS_PER_ADDRESS
    )


def charge_address(address: str) -> None:
    """Count one message to ``address`` against its allowance.

    Args:
        address: The address a message is about to go to.
    """
    charge(_KEY_PREFIX + address_digest(address), ADDRESS_WINDOW_SECONDS)
