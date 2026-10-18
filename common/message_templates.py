"""Messages shown to players that the environment may reword (issue #64).

Django-free on purpose, like :mod:`common.base_url`: settings reads these at
import, before apps load. A message is configuration under the house rule on
strings, with its default in ``settings.py``. Every one is filled in with
``str.format`` and its named fields only -- one with no fields too, so a
literal brace is always written doubled -- and the fields are always whole
numbers. A template naming any other field, with a stray brace, or with a
format spec or conversion a number cannot take would raise in front of a
player mid-turn, and leave the turn's record running; it is refused at startup
instead, as :class:`MessageTemplateError`.
"""

import string
from collections.abc import Mapping


class MessageTemplateError(ValueError):
    """A configured message names a field it may not use, or does not parse."""


def message_template_from_environment(
    environ: Mapping[str, str], name: str, default: str, *fields: str
) -> str:
    """Read a message template from the environment, refusing a bad one.

    Args:
        environ: The environment to read, normally ``os.environ``.
        name: The variable naming the message.
        default: The message when the variable is unset or empty.
        *fields: The fields the message may use. It may leave any out.

    Returns:
        The template as configured, or the default.

    Raises:
        MessageTemplateError: The template does not parse, names a field not
            in ``fields``, or cannot be filled in with whole numbers.
    """
    configured = environ.get(name) or default
    try:
        named = {
            field_name
            for _text, field_name, _spec, _conversion in string.Formatter().parse(
                configured
            )
            if field_name is not None
        }
    except ValueError as error:
        raise MessageTemplateError(
            f"{name} is {configured!r}, which is not a valid template: {error}."
        ) from error
    unknown = sorted(named - set(fields))
    if unknown:
        allowed = ", ".join(f"{{{field_name}}}" for field_name in fields) or "none"
        named_unknown = ", ".join(f"{{{field_name}}}" for field_name in unknown)
        raise MessageTemplateError(
            f"{name} names {named_unknown}; the fields it may use are {allowed}."
        )
    # The names are right; a trial fill-in finds what the parse does not: a
    # field nested in a format spec, a spec or conversion a number refuses.
    try:
        configured.format(**{field_name: 0 for field_name in fields})
    except (KeyError, IndexError, ValueError, AttributeError) as error:
        raise MessageTemplateError(
            f"{name} is {configured!r}, which cannot be filled in: {error!r}."
        ) from error
    return configured
