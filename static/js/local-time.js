/**
 * Progressive-enhancement local time formatting (issue #225).
 *
 * The server renders every user-facing timestamp -- "Signed in since",
 * character created/deleted dates, battle listings, user last-login -- as
 *
 *   <time class="local-time" datetime="...full ISO-8601 UTC..."
 *         data-local-time-style="datetime|date">UTC fallback text</time>
 *
 * (see common/templatetags/local_time.py, the {% local_time %} tag). Django's
 * TIME_ZONE stays UTC (settings.py) -- this script is what actually shows
 * each visitor their own local time: on load it reformats every
 * time.local-time element's text from its machine-readable `datetime`
 * attribute, using the browser's own Intl.DateTimeFormat and time zone. If
 * this script never runs (JS disabled, blocked, errors before this point),
 * the server-rendered UTC text stands on its own as a complete, correctly
 * labeled timestamp.
 *
 * data-local-time-style picks the re-rendered granularity:
 *   "datetime" (default) -- date and time of day, e.g. "Feb 03, 2026, 09:14"
 *   "date"               -- calendar date only, e.g. "Feb 03, 2026". The
 *                            underlying instant still moves into the
 *                            viewer's zone (a value just after UTC midnight
 *                            can land on the previous day for a visitor west
 *                            of UTC) -- only the clock-time portion of the
 *                            *enhanced* display is suppressed.
 */
(function () {
  const STYLE_OPTIONS = {
    datetime: {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    },
    date: {
      year: "numeric",
      month: "short",
      day: "2-digit",
    },
  };

  function formatInViewerTimeZone(isoString, style, timeZone) {
    const parsedInstant = new Date(isoString);
    if (isNaN(parsedInstant.getTime())) {
      return null;
    }
    const options = Object.assign({}, STYLE_OPTIONS[style] || STYLE_OPTIONS.datetime);
    if (timeZone) {
      options.timeZone = timeZone;
    }
    return new Intl.DateTimeFormat(undefined, options).format(parsedInstant);
  }

  /**
   * Rewrite every time.local-time element under `root` to the viewer's local
   * zone. Exposed on window so any page that inserts timestamps after load
   * can re-run it over just the new markup, and so tests can call it
   * directly.
   * @param {ParentNode} [root] - Defaults to the whole document.
   * @param {{timeZone?: string}} [options] - `timeZone` pins the IANA zone
   *     instead of the viewer's own; page callers omit it (the viewer's zone
   *     is the point), and it keeps the zone-shift behavior testable on a
   *     runner in any zone.
   */
  function applyLocalTimes(root, options) {
    const scope = root || document;
    const timeZone = options && options.timeZone;
    scope.querySelectorAll("time.local-time[datetime]").forEach((element) => {
      const style = element.getAttribute("data-local-time-style") || "datetime";
      const formatted = formatInViewerTimeZone(element.getAttribute("datetime"), style, timeZone);
      if (formatted) {
        element.textContent = formatted;
      }
    });
  }

  applyLocalTimes(document);

  window.applyLocalTimes = applyLocalTimes;
})();
