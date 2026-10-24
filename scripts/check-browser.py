# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=13,<18", "pypdf>=5"]
# ///
"""Behavioural check for what the static checks cannot see.

Drives the built dist/ in headless Chromium over CDP and asserts the no-JS fallback on
all three surfaces (the profile cards, the work index, the study pages), print media,
the real printed PDF, the theme toggle, icons, the sticky fact headers, and the header
midline.

The PDF assertion prints through Page.printToPDF and reads the text layer with pypdf;
`websockets` is capped below 18 because the CDP session outlives a `with` block and uses
`connect(..., legacy=True)`.

Run with `uv run scripts/check-browser.py` (or `npm run check:browser`). Skips with a
notice when no Chromium is found or SKIP_BROWSER_CHECK is set.
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pypdf import PdfReader
from websockets.sync.client import connect

ROOT = Path.cwd()
DIST = ROOT / "dist"
FAILURES: list[str] = []
ASSERTIONS = 0


def expect(label: str, condition: object, counted: bool = True) -> None:
    """Record a failure. Preconditions pass counted=False so the total means checks."""
    global ASSERTIONS
    if counted:
        ASSERTIONS += 1
    if not condition:
        FAILURES.append(label)


def base_path() -> str:
    config = (ROOT / "astro.config.mjs").read_text(encoding="utf-8")
    match = re.search(r"""\bbase\s*:\s*['"]([^'"]*)['"]""", config)
    return (match.group(1) if match else "").rstrip("/")


def find_chrome() -> str | None:
    """Read the shared candidate list so the JS and Python paths cannot drift."""
    paths = json.loads((ROOT / "scripts" / "lib" / "chrome.json").read_text(encoding="utf-8"))
    candidates: list[str] = []
    if os.environ.get("CHROME_PATH"):
        candidates.append(os.environ["CHROME_PATH"])
    if sys.platform == "win32":
        for name in paths["windowsRoots"]:
            root = os.environ.get(name)
            if root:
                candidates.extend(str(Path(root) / relative) for relative in paths["win32"])
    elif sys.platform == "darwin":
        candidates.extend(paths["darwin"])
    else:
        for name in paths["linux"]:
            found = shutil.which(name)
            if found:
                candidates.append(found)
    return next((path for path in candidates if Path(path).is_file()), None)


class QuietServer(ThreadingHTTPServer):
    """A client that navigates away mid-response is normal here, not a traceback."""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class StaticServer:
    """Serves dist/ under the configured base path on an ephemeral port."""

    def __init__(self, directory: Path, base: str) -> None:
        self.directory = directory
        self.base = base
        handler = self._handler()
        self.httpd = QuietServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def _handler(self):
        directory = self.directory
        base = self.base

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # noqa: D102 - silence the default log
                return

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                path = urllib.parse.unquote(self.path.split("?", 1)[0])
                if base and path.startswith(base):
                    path = path[len(base) :]
                if path in ("", "/"):
                    path = "/index.html"
                if path.endswith("/"):
                    path += "index.html"
                file = (directory / path.lstrip("/")).resolve()
                if directory.resolve() not in file.parents and file != directory.resolve():
                    self.send_error(403)
                    return
                if not file.is_file():
                    self.send_error(404)
                    return
                body = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def start(self) -> str:
        self.thread.start()
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}{self.base}/"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class CDP:
    """Minimal request/response client: one call at a time, no event subscriptions."""

    def __init__(self, url: str) -> None:
        # legacy=True: the session outlives a with-block, so the socket is held by this object.
        self.socket = connect(url, max_size=None, open_timeout=10, legacy=True)
        self.next_id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        message_id = self.next_id
        self.socket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{message['error'].get('message')} ({message['error'].get('code')})")
            return message.get("result", {})

    def evaluate(self, expression: str):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True, "userGesture": True},
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise RuntimeError(details.get("exception", {}).get("description") or details.get("text") or "evaluate failed")
        return result.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.socket.close()
        except Exception:  # noqa: BLE001 - teardown never fails the run
            pass


def wait_for(cdp: CDP, expression: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if cdp.evaluate(expression):
                return True
        except RuntimeError:
            pass
        time.sleep(0.04)
    return False


def settle_ready(cdp: CDP, javascript: bool) -> bool:
    """Each page carries its own render signal: three study cards on the profile, six
    index rows on the work page, and the study's flow on every study page."""
    path = cdp.evaluate("location.pathname")
    if re.search(r"^/work/[^/]+", path):
        return wait_for(cdp, "document.querySelectorAll('.case-flow li').length >= 3")
    if path.rstrip("/").endswith("/work"):
        return wait_for(cdp, "document.querySelectorAll('.project-row').length === 6")
    return wait_for(cdp, "document.querySelectorAll('.project-card').length === 3")


def goto(cdp: CDP, url: str, javascript: bool = True) -> None:
    cdp.call("Page.navigate", {"url": url})
    wait_for(cdp, "document.readyState === 'complete'")
    expect(f"{url} finished loading and rendered the case-study rows", settle_ready(cdp, javascript), counted=False)


def reload(cdp: CDP, javascript: bool = True) -> None:
    cdp.call("Page.reload", {"ignoreCache": True})
    wait_for(cdp, "document.readyState === 'complete'")
    expect("reloaded page rendered the case-study rows", settle_ready(cdp, javascript), counted=False)


def viewport(cdp: CDP, width: int, height: int) -> None:
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
    )


def scripts_enabled(cdp: CDP, value: bool) -> None:
    cdp.call("Emulation.setScriptExecutionDisabled", {"value": not value})


def media(cdp: CDP, media_type: str, features: list[dict] | None = None) -> None:
    cdp.call("Emulation.setEmulatedMedia", {"media": media_type, "features": features or []})


PRINT_STATE = """
(async () => {
  const flow = document.querySelector('.case-flow');
  const prose = document.querySelector('.prose');
  const meta = document.querySelector('.study-meta');
  return {
    flowBorders: [getComputedStyle(flow).borderTopWidth, getComputedStyle(flow).borderBottomWidth],
    proseHeight: Math.round(prose.getBoundingClientRect().height),
    lede: getComputedStyle(document.querySelector('.study-lede')).display,
    metaDisplay: getComputedStyle(meta).display,
    metaH: Math.round(meta.getBoundingClientRect().height),
    navLinks: document.querySelectorAll('.study-nav a').length,
  };
})()
"""


CHROME_ARGS = (
    "--headless=new",
    "--disable-gpu",
    "--disable-dev-shm-usage",  # CI containers mount a 64 MB /dev/shm
    "--no-sandbox",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--mute-audio",
    "--remote-debugging-port=0",
)


def launch_chrome(chrome: str, cache: Path) -> tuple[subprocess.Popen, Path, int]:
    """Start Chromium on a throwaway profile and wait for its debugging port.

    A cold CI runner can lose the first launch to a slow start or a crashed renderer,
    so try three times with a fresh profile. Each attempt keeps its own stderr so the
    failure message says why rather than only that nothing listened.
    """
    report = []
    for attempt in range(1, 4):
        profile = Path(tempfile.mkdtemp(prefix="chrome-browser-", dir=cache))
        log = profile / "chrome.log"
        with log.open("wb") as stderr:
            process = subprocess.Popen(
                [chrome, *CHROME_ARGS, f"--user-data-dir={profile}", "about:blank"],
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        port = 0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if port_file.is_file():
                port = int(port_file.read_text(encoding="utf-8").split("\n")[0] or 0)
                if port:
                    return process, profile, port
            time.sleep(0.05)

        if process.poll() is None:
            process.kill()
            process.wait()
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-4:]
        report.append(f"attempt {attempt}: exit {process.returncode}: {' | '.join(tail) or 'no output'}")
        shutil.rmtree(profile, ignore_errors=True)

    raise RuntimeError("Chromium never opened a debugging port:\n" + "\n".join(report))


def main() -> int:
    if not DIST.is_dir():
        print("Browser check needs a build. Run npm run build first.")
        return 1

    if os.environ.get("SKIP_BROWSER_CHECK"):
        print("Browser check skipped: SKIP_BROWSER_CHECK is set.")
        return 0

    chrome = find_chrome()
    if not chrome:
        print("Browser check failed: no Chromium found. Set CHROME_PATH, or SKIP_BROWSER_CHECK=1 to skip.")
        return 1

    server = StaticServer(DIST, base_path())
    origin = server.start()

    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)

    process: subprocess.Popen | None = None
    profile: Path | None = None
    cdp: CDP | None = None
    try:
        try:
            process, profile, port = launch_chrome(chrome, cache)
        except RuntimeError as error:
            print(f"Browser check failed: {error}")
            return 1

        targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10))
        page = next((target for target in targets if target.get("type") == "page"), None)
        if not page:
            print("Browser check failed: no page target.")
            return 1

        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        viewport(cdp, 1280, 900)
        goto(cdp, origin)

        # Headless Chrome defaults prefers-reduced-motion to reduce, which silences the
        # site's motion by design; set the no-motion preference the way a real browser
        # arrives, once, before any further load. The reduced-motion assertions override
        # it explicitly and restore it afterwards.
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "no-preference"}])

        # No JavaScript: the cards are plain links, a study page is a plain document,
        # and the toggle stays hidden.
        scripts_enabled(cdp, False)
        reload(cdp, javascript=False)
        card_links = cdp.evaluate("[...document.querySelectorAll('.project-card .card-link')].map((a) => a.pathname)")
        expect(
            "No-JS: the profile shows its three featured cards",
            cdp.evaluate("document.querySelectorAll('.project-card').length") == 3,
        )
        expect(
            f"No-JS: each card links to a distinct study page ({json.dumps(card_links)})",
            len(card_links) == 3
            and len(set(card_links)) == 3
            and all(re.match(r"^/work/[^/]+/$", link) for link in card_links),
        )
        expect(
            "No-JS: the theme toggle stays hidden",
            cdp.evaluate("document.querySelector('[data-theme-toggle]').hidden") is True,
        )
        # A study page is a real document: it carries its own content without any script.
        cdp.call("Page.navigate", {"url": f"{origin}{card_links[0].lstrip('/')}"})
        wait_for(cdp, "document.readyState === 'complete'")
        expect(
            "No-JS: a study page renders its own document (h1, flow, prose)",
            cdp.evaluate(
                "(() => {"
                " const h1 = document.querySelector('h1');"
                " return document.querySelectorAll('.case-flow li').length >= 3"
                " && document.querySelectorAll('.prose h2').length >= 6"
                " && h1 && h1.textContent.trim().length > 0;"
                "})()"
            )
            is True,
        )
        # All six studies stay reachable from the work page index without JavaScript.
        cdp.call("Page.navigate", {"url": f"{origin}work/"})
        wait_for(cdp, "document.readyState === 'complete'")
        row_links = cdp.evaluate("[...document.querySelectorAll('.project-row .row-link')].map((a) => a.pathname)")
        expect(
            "No-JS: the work page lists all six studies",
            cdp.evaluate("document.querySelectorAll('.project-row').length") == 6,
        )
        expect(
            f"No-JS: each work row links to a distinct study page ({len(row_links)} links)",
            len(row_links) == 6 and len(set(row_links)) == 6,
        )
        scripts_enabled(cdp, True)
        goto(cdp, origin)

        # Theme toggle: an icon button that names its action, flips the root attribute,
        # and remembers the choice.
        viewport(cdp, 320, 720)
        media(cdp, "", [{"name": "prefers-color-scheme", "value": "light"}])
        reload(cdp)
        theme_initial = cdp.evaluate(
            """
(() => {
  const button = document.querySelector('[data-theme-toggle]');
  const box = button.getBoundingClientRect();
  return {
    hidden: button.hidden,
    width: Math.round(box.width),
    height: Math.round(box.height),
    label: button.getAttribute('aria-label'),
    theme: document.documentElement.dataset.theme,
    sun: getComputedStyle(button.querySelector('.theme-icon-sun')).display,
    moon: getComputedStyle(button.querySelector('.theme-icon-moon')).display,
    pressed: button.hasAttribute('aria-pressed'),
  };
})()
"""
        )
        expect("Theme toggle: revealed for JS visitors", theme_initial["hidden"] is False)
        expect(
            f"Theme toggle: tap target is {theme_initial['width']}x{theme_initial['height']}",
            theme_initial["width"] >= 40 and theme_initial["height"] >= 40,
        )
        expect(
            "Theme toggle: names the action instead of a pressed state",
            theme_initial["label"] == "Switch to dark theme" and theme_initial["pressed"] is False,
        )
        expect(
            "Theme toggle: the light theme shows the moon",
            theme_initial["theme"] == "light" and theme_initial["moon"] != "none" and theme_initial["sun"] == "none",
        )

        # Everyone lands light (owner decision, 2026-10-24): the OS scheme and storage
        # are ignored on load; a press is a session choice that survives swaps but is
        # never stored, so the next visit opens light again.
        cdp.evaluate("(() => { try { localStorage.removeItem('theme'); } catch (e) {} return true; })()")
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "no-preference"}, {"name": "prefers-color-scheme", "value": "dark"}])
        reload(cdp)
        fresh_dark_os = cdp.evaluate("document.documentElement.dataset.theme")
        expect(
            f"Theme: a fresh visitor on a dark-OS machine opens light (theme {fresh_dark_os})",
            fresh_dark_os == "light",
        )

        flipped = cdp.evaluate(
            """
(() => {
  const button = document.querySelector('[data-theme-toggle]');
  button.click();
  return {
    theme: document.documentElement.dataset.theme,
    label: button.getAttribute('aria-label'),
    sun: getComputedStyle(button.querySelector('.theme-icon-sun')).display,
    moon: getComputedStyle(button.querySelector('.theme-icon-moon')).display,
    session: window.__fmSessionTheme || null,
    stored: (() => { try { return localStorage.getItem('theme'); } catch (e) { return null; } })(),
  };
})()
"""
        )
        expect(
            "Theme toggle: a press switches to dark, sets the session choice, stores nothing",
            flipped["theme"] == "dark" and flipped["session"] == "dark" and flipped["stored"] is None,
        )
        expect(
            "Theme toggle: the dark theme shows the sun and relabels",
            flipped["sun"] != "none" and flipped["moon"] == "none" and flipped["label"] == "Switch to light theme",
        )
        # The session choice survives in-site swaps: the visitor's press stays theirs
        # while they read the studies, and nothing lands in storage.
        card = cdp.evaluate("document.querySelector('.project-card .card-link')?.getAttribute('href')")
        cdp.evaluate("(() => { document.querySelector('.project-card .card-link').click(); return true; })()")
        wait_for(cdp, f"location.pathname === '{card.rstrip('/')}/'")
        session_swap = cdp.evaluate(
            "document.documentElement.dataset.theme + '|' + (window.__fmSessionTheme || null)"
        )
        expect(
            f"Theme: the session choice survives a swap into a study page ({session_swap})",
            session_swap == "dark|dark",
        )
        cdp.evaluate("(() => { history.back(); return true; })()")
        wait_for(cdp, "!!document.querySelector('[data-copy-email]')")
        expect(
            "Theme: the session choice survives the swap back",
            cdp.evaluate("document.documentElement.dataset.theme") == "dark",
        )
        # A reload is a new visit: light wins again.
        reload(cdp)
        fresh_again = cdp.evaluate(
            """
({
  theme: document.documentElement.dataset.theme,
  label: document.querySelector('[data-theme-toggle]').getAttribute('aria-label'),
})
"""
        )
        expect(
            f"Theme: after a reload everyone lands light again (theme {fresh_again['theme']})",
            fresh_again["theme"] == "light" and fresh_again["label"] == "Switch to dark theme",
        )

        # The swap round trip, from a clean session: the light landing and the toggle
        # survive into a study page (the swap replaces the html attributes and re-serves
        # the header fresh). The re-apply hooks must keep both alive.
        cdp.evaluate(
            "(() => { window.__fmSessionTheme = null; try { localStorage.removeItem('theme'); } catch (e) {} return true; })()"
        )
        card = cdp.evaluate("document.querySelector('.project-card .card-link')?.getAttribute('href')")
        cdp.evaluate("(() => { document.querySelector('.project-card .card-link').click(); return true; })()")
        wait_for(cdp, f"location.pathname === '{card.rstrip('/')}/'")
        swapped = cdp.evaluate(
            """
(() => {
  const button = document.querySelector('[data-theme-toggle]');
  const box = button.getBoundingClientRect();
  return {
    theme: document.documentElement.dataset.theme || 'none',
    toggleHidden: button.hidden,
    toggleW: Math.round(box.width),
    toggleH: Math.round(box.height),
  };
})()
"""
        )
        expect(
            f"View transition swaps: the light theme and the toggle survive into a study page (theme {swapped['theme']}, toggle hidden {swapped['toggleHidden']}, {swapped['toggleW']}x{swapped['toggleH']})",
            swapped["theme"] == "light"
            and swapped["toggleHidden"] is False
            and swapped["toggleW"] > 0
            and swapped["toggleH"] > 0,
        )
        # The toggle still works on the swapped page: a press switches the theme there.
        swapped_flip = cdp.evaluate(
            "(() => { const b = document.querySelector('[data-theme-toggle]'); b.click(); "
            "const t = document.documentElement.dataset.theme; b.click(); return t; })()"
        )
        expect(
            f"View transition swaps: the toggle still works on the swapped page (press returned {swapped_flip})",
            swapped_flip == "dark",
        )
        # The same defect class, measured on 2026-10-24: home -> study -> back left the
        # copy-email button bound to nothing (body scripts do not re-run after a swap).
        # Back-navigation is a swap here, so the flash must still fire on the way back.
        cdp.evaluate("(() => { history.back(); return true; })()")
        wait_for(cdp, "!!document.querySelector('[data-copy-email]')")
        copy_before = cdp.evaluate("document.querySelector('[data-copy-email]')?.textContent")
        cdp.evaluate("(() => { document.querySelector('[data-copy-email]')?.click(); return true; })()")
        time.sleep(0.25)
        copy_after = cdp.evaluate("document.querySelector('[data-copy-email]')?.textContent")
        expect(
            f"View transition swaps: the copy button survives the round trip ({copy_before} -> {copy_after})",
            copy_before == "Copy email" and copy_after not in ("Copy email", None),
        )
        # Same swap class, measured 2026-10-24: the swap replaces <html>, killing the
        # measured --header-h, and the sticky fact bars rode the 7rem fallback — a 25px
        # gap at desktop, 11px under the phone menu. After the round trip the open bar
        # must sit flush under the header again.
        cdp.evaluate("(() => { document.querySelector('#certifications > .fact-summary')?.click(); return true; })()")
        time.sleep(0.2)
        # Same swap class, measured 2026-10-24: a swap replaces <html>, killing the
        # measured --header-h, and the sticky fact bars (plus every scroll jump) then
        # ride the 7rem fallback - a 25px gap at desktop, 11px under the phone menu.
        # BaseLayout's surviving after-swap hook re-measures; assert the variable equals
        # the real header height, which pins every consumer flush by construction.
        sticky_var = cdp.evaluate(
            """
(() => {
  const header = document.querySelector('.site-header').getBoundingClientRect().height;
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--header-h').trim();
  const parsed = parseFloat(raw);
  return { raw, parsed, header: Math.round(header), toggleY: Math.round(document.querySelector('[data-theme-toggle]')?.getBoundingClientRect().y ?? -1), scrollY: Math.round(scrollY), markerPos: getComputedStyle(document.querySelector('.nav-underline') || document.body).position, markerHidden: document.querySelector('.nav-underline')?.hidden, navKids: [...document.querySelectorAll('.site-nav > *')].map((el) => el.className || el.tagName), fixed: raw.endsWith('px') && !isNaN(parsed) };
})()
"""
        )
        expect(
            f"View transition swaps: the header height variable is re-measured ({sticky_var['raw']} vs header {sticky_var['header']}px, toggleY {sticky_var['toggleY']}, markerPos {sticky_var['markerPos']}, hidden {sticky_var['markerHidden']}, kids {sticky_var['navKids']})",
            sticky_var["fixed"]
            and abs(sticky_var["parsed"] - sticky_var["header"]) <= 2,
        )
        cdp.evaluate("(() => { try { localStorage.removeItem('theme'); } catch (e) {} return true; })()")
        cdp.evaluate("(() => { try { localStorage.removeItem('theme'); } catch (e) {} return true; })()")
        goto(cdp, origin)

        # The phone-width header: the menu label is short enough that the toggle keeps
        # the nav's row instead of dropping to a lone third row (the defect the owner
        # reported on a phone at 390). Asserted at 390, inside the narrowed header's
        # own media range; at 320 and below the toggle still drops, by design.
        viewport(cdp, 390, 700)
        header_phone = cdp.evaluate(
            """
(() => {
  const row = (el) => Math.round(el.getBoundingClientRect().y);
  const nav = document.querySelector('.site-nav');
  const toggle = document.querySelector('.theme-toggle');
  return { navY: row(nav), toggleY: row(toggle), headerH: Math.round(document.querySelector('.site-header').getBoundingClientRect().height), label: nav.textContent.trim() };
})()
"""
        )
        expect(
            f"Theme toggle: at phone width the toggle keeps the nav's row, header {header_phone['headerH']}px (label '{header_phone['label'][:16]}')",
            header_phone["toggleY"] == header_phone["navY"] and header_phone["headerH"] < 150,
        )

        icons = cdp.evaluate(
            """
(() => {
  const contact = [...document.querySelectorAll('.contact-links svg')];
  const footer = [...document.querySelectorAll('.footer-links svg')];
  return {
    contact: contact.length,
    footer: footer.length,
    hidden: [...contact, ...footer].every((el) => el.getAttribute('aria-hidden') === 'true'),
    external: [...document.querySelectorAll('svg use, svg image')].filter((el) => {
      const href = el.getAttribute('href') || el.getAttribute('xlink:href') || '';
      return href !== '' && !href.startsWith('#');
    }).length,
    labels: [...document.querySelectorAll('.contact-links a, .footer-links a')].map(
      (link) => (link.getAttribute('aria-label') || link.textContent).trim(),
    ),
  };
})()
"""
        )
        expect(f"Icons: 3 on the contact links, saw {icons['contact']}", icons["contact"] == 3)
        expect(f"Icons: none on the footer, links live in the contact section (saw {icons['footer']})", icons["footer"] == 0)
        expect("Icons: decorative SVGs are hidden from assistive tech", icons["hidden"] is True)
        expect("Icons: no external SVG references", icons["external"] == 0)
        expect("Icons: every link keeps an accessible name", all(label for label in icons["labels"]))
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "no-preference"}])

        # The index page: at rest nothing is highlighted, and a row opens the study's
        # own page.
        goto(cdp, f"{origin}work/")
        expect(
            "Breadcrumb: hidden at rest on the work page (nothing is highlighted either)",
            cdp.evaluate("document.querySelector('.nav-current').hidden") is True,
        )
        expect(
            "Nav: nothing highlighted on the work page (the work link is not in the menu)",
            wait_for(
                cdp,
                "document.querySelectorAll('.site-nav a[aria-current]').length === 0",
                timeout=2,
            ),
        )
        row = cdp.evaluate(
            "(() => {"
            " const row = document.querySelector('.project-row .row-link');"
            " return { title: row.querySelector('.project-title').textContent.trim(), href: row.pathname };"
            "})()"
        )
        cdp.evaluate("document.querySelector('.project-row .row-link').click()")
        # Not settle_ready: during a View-Transition swap the pathname still reads the
        # index for a moment, which would settle instantly on the index's own six rows
        # and probe mid-swap. The index never carries the study's flow; waiting for it
        # here is race-free under both a full load and a client-side swap.
        expect(
            "the clicked row opened its study page",
            wait_for(cdp, "document.querySelectorAll('.case-flow li').length >= 3", timeout=5),
            counted=False,
        )
        study = cdp.evaluate(
            """
(() => ({
  h1: document.querySelector('h1')?.textContent.trim(),
  back: document.querySelector('.back-link a')?.getAttribute('href'),
  crumbHidden: document.querySelector('.nav-current').hidden,
  nav: [...document.querySelectorAll('.site-nav a[aria-current]')].map((a) => a.dataset.navKey),
  sections: document.querySelectorAll('.prose h2').length,
  metrics: document.querySelectorAll('.metric-grid .metric').length,
}))()
"""
        )
        expect(
            f"Study page: the clicked row opens its own study ({study['h1']!r})",
            study["h1"] == row["title"] and study["sections"] >= 6 and study["metrics"] >= 2,
        )
        expect(
            f"Study page: the back link returns to the index ({study['back']})",
            study["back"] == "/work",
        )
        expect(
            f"Study page: nothing highlighted, crumb hidden ({study['nav']})",
            study["nav"] == [] and study["crumbHidden"] is True,
        )
        slug = row["href"].strip("/").split("/")[-1]
        pair = cdp.evaluate(
            "(() => { const svg = document.querySelector('.study-art svg');"
            " return { name: getComputedStyle(svg).viewTransitionName, normalised: svg.querySelectorAll('[pathLength=\"1\"]').length, shapes: svg.querySelectorAll('path, rect, circle').length, drawing: svg.hasAttribute('data-drawing') }; })()"
        )
        expect(
            f"Study page: the opening art pairs with its card and carries its full geometry ({pair['name']}, {pair['normalised']}/{pair['shapes']})",
            pair["name"] == f"study-{slug}"
            and pair["normalised"] == pair["shapes"]
            and pair["shapes"] >= 6,
        )
        nav_row = cdp.evaluate(
            """
(() => [...document.querySelectorAll('.study-nav .study-nav-link')].map((a) => ({ href: a.getAttribute('href'), title: a.querySelector('.study-nav-title').textContent.trim() })))()
"""
        )
        expect(
            f"Study page: the chain opens and closes this page ({json.dumps(nav_row)})",
            len(nav_row) == 2
            and nav_row[0]["href"] == "/#work"
            and nav_row[1]["href"] == "/work/ai-development-harness/",
        )

        # The last study by order: both ends return home, the previous slot pointing at
        # study five. The load is query-differentiated so Chrome carries no scroll in:
        # the page opens at the top, and at a desktop viewport the art sits in view, so
        # the draw runs immediately on load.
        viewport(cdp, 1280, 900)
        goto(cdp, f"{origin}work/ai-budget-predicter/?load")
        last = cdp.evaluate(
            """
(() => ({
  nav: [...document.querySelectorAll('.study-nav .study-nav-link')].map((a) => a.getAttribute('href')),
  artName: getComputedStyle(document.querySelector('.study-art svg')).viewTransitionName,
  artDrawing: document.querySelector('.study-art svg').hasAttribute('data-drawing'),
}))()
"""
        )
        expect(
            f"Study page: the last study returns home on both ends ({json.dumps(last['nav'])})",
            last["nav"] == ["/work/multimodal-moderation/", "/#work"],
        )
        expect(
            f"Study page: the last study's art pairs with its card ({last['artName']})",
            last["artName"] == "study-ai-budget-predicter",
        )
        # The art sits in view at a top load, so it draws immediately: motion is
        # guaranteed to be seen on every study page.
        expect(
            f"Study page: the opening art draws on load (drawing {last['artDrawing']})",
            last["artDrawing"] is True,
        )

        # The no-wrap guarantee at a desktop width: the study title keeps one line and
        # the exhibit spans the section width like the prose beside it.
        nowrap_study = cdp.evaluate(
            """
(() => {
  const h1 = document.querySelector('h1');
  const lh = parseFloat(getComputedStyle(h1).lineHeight);
  const wrap = document.querySelector('.study .wrap').getBoundingClientRect();
  const art = document.querySelector('.study-art').getBoundingClientRect();
  return { lines: Math.round(h1.getBoundingClientRect().height / lh), artW: Math.round(art.width), wrapW: Math.round(wrap.width) };
})()
"""
        )
        expect(
            f"Study page: the title keeps one line at desktop ({nowrap_study['lines']} lines)",
            nowrap_study["lines"] == 1,
        )
        expect(
            f"Study page: the exhibit spans the section width ({nowrap_study['artW']} of {nowrap_study['wrapW']})",
            nowrap_study["artW"] == nowrap_study["wrapW"],
        )
        last = cdp.evaluate(
            """
(() => {
  const h1 = document.querySelector('h1').offsetTop;
  const strip = document.querySelector('.study-meta').offsetTop;
  const nav = document.querySelector('.study-nav').offsetTop;
  const toggle = document.querySelector('.theme-toggle');
  const tr = toggle.getBoundingClientRect();
  return { h1Top: h1, stripTop: strip, navTop: nav, toggle: { hidden: toggle.hidden, w: Math.round(tr.width), h: Math.round(tr.height), x: Math.round(tr.x) } };
})()
"""
        )
        expect(
            f"Study page: the strip closes the page before the chain (h1 {last['h1Top']}, strip {last['stripTop']}, chain {last['navTop']})",
            last["stripTop"] > last["h1Top"] + 250 and last["navTop"] > last["stripTop"],
        )
        expect(
            f"Theme: the toggle sits visible on a study page too ({last['toggle']['x']}, {last['toggle']['w']}x{last['toggle']['h']}, hidden {last['toggle']['hidden']})",
            last["toggle"]["hidden"] is False and last["toggle"]["w"] > 0 and last["toggle"]["h"] > 0 and last["toggle"]["x"] > 1000,
        )

        # Print: the study keeps its timeline spacing without a rule of its own, and
        # its content stays on the page.
        viewport(cdp, 794, 1123)
        media(cdp, "print")
        printed = cdp.evaluate(PRINT_STATE)
        expect(
            f"Print: the case-flow keeps its spacing without a border of its own ({'/'.join(printed['flowBorders'])})",
            all(value == "0px" for value in printed["flowBorders"]),
        )
        expect(
            f"Print: the study prose stays visible ({printed['proseHeight']}px, lede {printed['lede']})",
            printed["proseHeight"] > 0 and printed["lede"] != "none",
        )
        expect(
            f"Print: the strip and the chain print ({printed['metaDisplay']}, {printed['metaH']}px, {printed['navLinks']} links)",
            printed["metaDisplay"] != "none" and printed["metaH"] > 0 and printed["navLinks"] == 2,
        )

        # The real print output, not just the emulated media.
        pdf_result = cdp.call("Page.printToPDF", {"printBackground": False, "preferCSSPageSize": True})
        page_count = 0
        text = ""
        try:
            reader = PdfReader(io.BytesIO(base64.b64decode(pdf_result["data"])))
            page_count = len(reader.pages)
            text = "".join(page.extract_text() or "" for page in reader.pages)
        except Exception as error:  # noqa: BLE001 - reported as a failed assertion
            expect(f"PDF: the printed study could be parsed ({error})", False)
        expect(f"PDF: the printed study spans several A4 pages ({page_count} pages)", page_count > 1)
        printed_h1 = cdp.evaluate("document.querySelector('h1')?.textContent.trim() ?? ''")
        expect(
            "PDF: the printed study carries its title",
            printed_h1 != "" and printed_h1 in re.sub(r"\s+", " ", text),
        )
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "no-preference"}])

        # The header bar keeps one midline: the wordmark, the nav links, and the toggle
        # all share a vertical center instead of hanging off the wordmark's baseline.
        viewport(cdp, 1280, 900)
        goto(cdp, origin)
        nav_state = cdp.evaluate(
            """
(() => [...document.querySelectorAll('.site-nav a')].map((a) => ({ key: a.dataset.navKey, label: a.textContent.trim(), href: a.pathname + a.hash })))()
"""
        )
        expect(
            f"Nav: three links, projects first "
            f"({json.dumps([item["label"] for item in nav_state])})",
            [item["key"] for item in nav_state] == ["projects", "about", "contact"],
        )
        expect(
            "Nav: projects points at the home work section, about opens the person cluster at the story",
            nav_state[0]["href"].endswith("#work") and nav_state[1]["href"].endswith("#story"),
        )
        # The merged About cluster: the owner folded the story and the facts into one
        # section (dropping the restating About heading); the facts must live inside the
        # story section, and the old standalone section must not return.
        expect(
            "About cluster: the facts sit inside the story section and no standalone About section remains",
            cdp.evaluate("!!document.querySelector('#story .facts') && !document.querySelector('#about')"),
        )
        expect(
            "Nav: the work item stays out of the menu (commented by decision)",
            cdp.evaluate("document.querySelectorAll('.site-nav a[data-nav-key=\"work\"]').length") == 0,
        )
        cdp.evaluate("(() => { document.querySelector('#work').scrollIntoView({ block: 'start' }); return true; })()")
        expect(
            "Nav: projects highlights at the home work section",
            wait_for(
                cdp,
                "document.querySelector('.site-nav a[data-nav-key=\"projects\"]')?.hasAttribute('aria-current')",
                timeout=2,
            ),
        )

        # Motion: reveal and self-draw. The hero never starts hidden, art below the fold
        # waits hidden and draws once on entry, the effect shifts no layout, and reduced
        # motion holds everything in its finished state. The short viewport puts the
        # cards below the fold deterministically; the media override matters too:
        # headless defaults to reduce, so the motion assertions set no-preference
        # explicitly, the way a real browser arrives.
        viewport(cdp, 1280, 700)
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "no-preference"}])
        # Chrome carries the outgoing document's recorded scroll into a same-URL
        # navigation (and the record is debounced, so a scroll-to-top does not help).
        # A query-differentiated URL is a fresh history entry: it always loads at the
        # top, where the arts sit at ~933px: below this viewport's fold, waiting.
        goto(cdp, f"{origin}?motion")
        motion = cdp.evaluate(
            """
(() => {
  const arts = [...document.querySelectorAll('svg[data-draw]')];
  return {
    total: arts.length,
    pending: arts.filter((svg) => svg.hasAttribute('data-draw-pending')).length,
    drawing: arts.filter((svg) => svg.hasAttribute('data-drawing')).length,
    tops: arts.map((svg) => Math.round(svg.getBoundingClientRect().top)),
    scrollY: Math.round(window.scrollY),
    innerH: window.innerHeight,
    paths: arts.filter((svg) => [...svg.querySelectorAll(':scope > :is(path, rect, circle)')].slice(0, 3).every((shape) => shape.getAttribute('pathLength') === '1')).length,
    names: [...arts].map((svg) => getComputedStyle(svg).viewTransitionName),
    capTop: document.getElementById('capabilities').offsetTop,
  };
})()
"""
        )
        expect(
            f"Motion: three card arts wait below the fold to self-draw (pending {motion['pending']}/{motion['total']}, tops {motion['tops']}, scroll {motion['scrollY']}, drawing {motion['drawing']})",
            motion["total"] == 3 and motion["pending"] == 3 and motion["scrollY"] == 0,
        )
        expect(
            f"Motion: every card art normalises its dash via pathLength ({motion['paths']}/3)",
            motion["paths"] == 3,
        )
        expect(
            f"Motion: the card arts pair with their study pages ({json.dumps(motion['names'])})",
            all(name.startswith("study-") for name in motion["names"]),
        )
        frame = cdp.evaluate(
            """
(() => {
  const ul = document.querySelector('.project-cards');
  const ulCs = getComputedStyle(ul);
  const card = getComputedStyle(document.querySelector('.project-card'));
  const toggle = document.querySelector('.theme-toggle');
  const tr = toggle.getBoundingClientRect();
  return {
    listStyle: ulCs.listStyleType,
    ulPad: ulCs.paddingLeft,
    borderTop: card.borderTopStyle,
    bg: card.backgroundColor,
    radius: card.borderRadius,
    toggle: { hidden: toggle.hidden, w: Math.round(tr.width), h: Math.round(tr.height), x: Math.round(tr.x), y: Math.round(tr.y) },
  };
})()
"""
        )
        expect(
            f"Cards: the list shows no bullets and the row keeps its framed surface (list-style {frame['listStyle']}, pad {frame['ulPad']}, border {frame['borderTop']}, radius {frame['radius']})",
            frame["listStyle"] == "none"
            and frame["ulPad"] == "0px"
            and frame["borderTop"] != "none"
            and frame["bg"] != "rgba(0, 0, 0, 0)"
            and frame["radius"] == "12px",
        )
        expect(
            f"Theme: the toggle sits visible in the header ({frame['toggle']['x']}, {frame['toggle']['y']}, {frame['toggle']['w']}x{frame['toggle']['h']}, hidden {frame['toggle']['hidden']})",
            frame["toggle"]["hidden"] is False and frame["toggle"]["w"] > 0 and frame["toggle"]["h"] > 0 and frame["toggle"]["x"] > 1000,
        )
        # An instant scroll: the page's smooth-scroll behavior makes scrollIntoView a
        # long glide, and the IO events landing mid-glide are exactly the
        # nondeterminism this block must not read through. scroll-padding-top snaps the
        # jump short of the section's true top, and the check scrolls a further viewport
        # only for the drawn state: with the third art inside the observer's cut, all
        # three must show drawn; before that point a pending third art is correct.
        cdp.evaluate(
            "(() => {"
            " document.querySelector('#work').scrollIntoView({ block: 'start', behavior: 'instant' });"
            " return true; })()"
        )
        drawn = wait_for(
            cdp,
            "document.querySelectorAll('svg[data-draw][data-drawing]').length >= 2",
            timeout=4,
        )
        expect(
            f"Motion: the arts draw as they enter the viewport ({'two entered and drawing' if drawn else 'did not draw in time'})",
            drawn,
        )
        hover = cdp.evaluate(
            """
(() => {
  const card = document.querySelector('.project-card');
  const box = card.getBoundingClientRect();
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, settled: box.top };
})()
"""
        )
        cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": hover["x"], "y": hover["y"]})
        time.sleep(0.25)
        lifted = cdp.evaluate(
            "(() => { const c = document.querySelector('.project-card'); return { transform: getComputedStyle(c).transform, shadow: getComputedStyle(c, '::after').opacity }; })()"
        )
        expect(
            f"Motion: the card lifts on hover (transform {lifted['transform']}, shadow layer {lifted['shadow']})",
            lifted["transform"] != "none" and lifted["shadow"] == "1",
        )
        cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 4, "y": 4})
        # The no-wrap guarantee at a desktop width: card titles keep one line, and the
        # art sits beside the text (its column ends before the text column starts).
        nowrap = cdp.evaluate(
            """
(() => {
  const lines = (el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight));
  const cards = [...document.querySelectorAll('.project-card')];
  const art = cards[0].querySelector('.card-art').getBoundingClientRect();
  const text = cards[0].querySelector('.card-text').getBoundingClientRect();
  return {
    titleLines: cards.map((c) => lines(c.querySelector('.card-title'))),
    artRight: Math.round(art.right),
    textLeft: Math.round(text.x),
  };
})()
"""
        )
        expect(
            f"Cards: the study titles keep one line at desktop ({json.dumps(nowrap['titleLines'])})",
            all(lines == 1 for lines in nowrap["titleLines"]) and len(nowrap["titleLines"]) == 3,
        )
        expect(
            f"Cards: the art sits beside its text ({nowrap['artRight']} <= {nowrap['textLeft']})",
            nowrap["artRight"] <= nowrap["textLeft"],
        )
        # The third art rides below the -10% cut at this rest point; one more viewport
        # brings it in, and the chain's state machine must complete at a full stroke.
        cdp.evaluate("(() => { window.scrollBy({ top: window.innerHeight, behavior: 'instant' }); return true; })()")
        fully = wait_for(
            cdp,
            "(() => { const arts = [...document.querySelectorAll('svg[data-draw]')];"
            " return arts.length === 3 && arts.every((s) => s.hasAttribute('data-drawing'))"
            " && getComputedStyle(document.querySelector('svg[data-drawing] :is(path, rect, circle)')).strokeDashoffset === '0px'; })()",
            timeout=4,
        )
        expect("Motion: every art finished drawing at a full stroke", fully)
        midline = cdp.evaluate(
            """
(async () => {
  await document.fonts.ready;
  const center = (el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2; };
  return {
    wordmark: center(document.querySelector('.wordmark')),
    nav: center(document.querySelector('.site-nav a')),
    toggle: center(document.querySelector('.theme-toggle')),
  };
})()
"""
        )
        expect(
            f"Header: one midline for wordmark, nav and toggle "
            f"(nav {midline['nav'] - midline['wordmark']:+.1f}px, toggle {midline['toggle'] - midline['wordmark']:+.1f}px)",
            abs(midline["nav"] - midline["wordmark"]) <= 1 and abs(midline["toggle"] - midline["wordmark"]) <= 1,
        )

        # The contact section ends the page: the space from its content to the footer
        # rule must match the space from the rule above it, so the block does not hug
        # its top border over a doubled void. (The last-section bottom spacing is the
        # section's own padding; the footer carries no top margin.)
        balance = cdp.evaluate(
            """
(() => {
  const section = document.querySelector('#contact').getBoundingClientRect();
  const head = document.querySelector('#contact .section-head').getBoundingClientRect();
  const links = document.querySelector('#contact .contact-links').getBoundingClientRect();
  const footer = document.querySelector('.site-footer').getBoundingClientRect();
  return { above: Math.round(head.top - section.top), below: Math.round(footer.top - links.bottom) };
})()
"""
        )
        expect(
            f"Contact: one separation, not two (above {balance['above']}px, below {balance['below']}px)",
            balance["below"] <= balance["above"] + 8,
        )

        # The hero is not a .section, so the work seam needs naming (the About section used
        # to sit between them and carried the rule; the merge deleted it with the seam).
        work_seam = cdp.evaluate(
            "getComputedStyle(document.querySelector('#work')).borderTopWidth"
        )
        expect(
            f"Hero: the featured-projects section keeps its top rule (border {work_seam})",
            work_seam == "1px",
        )

        # Footer: one copy line (privacy + copyright), no links (the contact section owns
        # them), centered in the container, one line at desktop width.
        footer_rows = cdp.evaluate(
            """
(() => {
  const wrap = document.querySelector('.site-footer .wrap');
  const copy = document.querySelector('.site-footer .footer-copy');
  if (!copy) return { missing: true };
  const row = copy.getBoundingClientRect();
  const wrapRect = wrap.getBoundingClientRect();
  const range = document.createRange();
  range.selectNodeContents(copy);
  const wrapCenter = (wrapRect.left + wrapRect.right) / 2;
  const offCenter = Math.max(
    ...[...range.getClientRects()]
      .filter((r) => r.width > 1)
      .map((r) => Math.abs((r.left + r.right) / 2 - wrapCenter)),
  );
  return {
    offCenter: Math.round(offCenter * 10) / 10,
    copyOnly: document.querySelectorAll('.site-footer .footer-copy').length === 1,
    noLinks: document.querySelectorAll('.site-footer .footer-links, .site-footer a').length === 0,
    singleLine: Math.round(row.height) < 1.6 * parseFloat(getComputedStyle(copy).lineHeight),
  };
})()
"""
        )
        expect(
            f"Footer: one copy line, no links, centered, single line at desktop ({footer_rows})",
            footer_rows.get("copyOnly")
            and footer_rows.get("noLinks")
            and footer_rows.get("singleLine")
            and footer_rows.get("offCenter", 99) < 2,
        )

        # The scrollspy underline: one shared marker slides to the active link instead of
        # each link painting its own border. Asserted at two scroll points: the marker
        # must sit on the active link's box (±2px) and move between sections.
        marker_track = []
        for target in ("#work", "#story"):
            cdp.evaluate(
                f"(() => {{ window.scrollTo({{ top: document.querySelector('{target}').getBoundingClientRect().top + scrollY + 40, behavior: 'instant' }}); return true; }})()"
            )
            time.sleep(0.3)
            marker_track.append(
                cdp.evaluate(
                    """
(() => {
  const marker = document.querySelector('.nav-underline');
  const active = document.querySelector('.site-nav a[aria-current]');
  if (!marker || marker.hidden || !active) return { active: null, dx: null, dw: null };
  const m = marker.getBoundingClientRect();
  const a = active.getBoundingClientRect();
  return { active: active.textContent.trim(), dx: Math.round(m.left - a.left), dw: Math.round(m.width - a.width) };
})()
"""
                )
            )
        expect(
            f"Menu marker: the sliding underline sits on the active link at two scroll points ({json.dumps(marker_track)})",
            all(track["dx"] is not None and abs(track["dx"]) <= 2 and abs(track["dw"]) <= 2 for track in marker_track)
            and {track["active"] for track in marker_track} == {"Projects", "About me"},
        )

        # Sticky fact header: an open About section keeps its title pinned under the site
        # header while its body scrolls, and releases at the section's end.
        cdp.evaluate("(() => { document.querySelector('#experience > .fact-summary').click(); return true; })()")
        time.sleep(0.1)
        fact_pinned = cdp.evaluate(
            """
(async () => {
  const summary = document.querySelector('#experience > .fact-summary');
  const header = document.querySelector('.site-header').getBoundingClientRect().height;
  window.scrollTo({ top: summary.getBoundingClientRect().top + window.scrollY + 300, behavior: 'instant' });
  await new Promise((done) => setTimeout(done, 150));
  return {
    open: document.querySelector('#experience').open,
    stuck: summary.hasAttribute('data-stuck'),
    shadow: getComputedStyle(summary).boxShadow,
    top: Math.round(summary.getBoundingClientRect().top),
    header: Math.round(header),
  };
})()
"""
        )
        expect(
            f"Sticky fact header: pinned under the site header (top {fact_pinned['top']}, header {fact_pinned['header']})",
            fact_pinned["open"]
            and fact_pinned["stuck"]
            and fact_pinned["shadow"] != "none"
            and abs(fact_pinned["top"] - fact_pinned["header"]) <= 1.5,
        )
        expect(
            f"Sticky fact header: released again past the section",
            cdp.evaluate(
                """
(async () => {
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
  await new Promise((done) => setTimeout(done, 150));
  const summary = document.querySelector('#experience > .fact-summary');
  return !summary.hasAttribute('data-stuck') && getComputedStyle(summary).boxShadow === 'none';
})()
"""
            )
            is True,
        )
        # Motion under reduced-motion emulation: everything sits in its final state,
        # nothing ever waits hidden.
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "reduce"}])
        cdp.evaluate("(() => { window.scrollTo({ top: 0, behavior: 'instant' }); return true; })()")
        reload(cdp)
        reduced = cdp.evaluate(
            """
(() => {
  const arts = [...document.querySelectorAll('svg[data-draw]')];
  const wraps = [...document.querySelectorAll('[data-reveal]')];
  return {
    pendingArt: arts.filter((svg) => svg.hasAttribute('data-draw-pending')).length,
    pendingReveal: wraps.filter((el) => el.hasAttribute('data-pending')).length,
    revealed: wraps.length > 0 && wraps.every((el) => el.hasAttribute('data-revealed')),
    cardOpacity: getComputedStyle(document.querySelector('.project-card')).opacity,
  };
})()
"""
        )
        expect(
            f"Motion: reduced motion holds everything in its final state (pending {reduced['pendingArt']} + {reduced['pendingReveal']})",
            reduced["pendingArt"] == 0
            and reduced["pendingReveal"] == 0
            and reduced["revealed"]
            and reduced["cardOpacity"] == "1",
        )
        expect(
            "Motion: no third-party scripts or stylesheets",
            cdp.evaluate(
                "[...document.scripts].every((s) => !s.src || s.src.startsWith(location.origin))"
                " && [...document.querySelectorAll('link[rel=\"stylesheet\"], link[rel=\"icon\"]')].every((l) => l.href.startsWith(location.origin))"
            )
            is True,
        )
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "no-preference"}])
        media(cdp, "print")
        printed_fact = cdp.evaluate(
            "(() => { const s = document.querySelector('#experience > .fact-summary'); "
            "return { position: getComputedStyle(s).position, shadow: getComputedStyle(s).boxShadow }; })()"
        )
        expect(
            f"Sticky fact header: static and unruled in print ({printed_fact['position']})",
            printed_fact["position"] == "static" and printed_fact["shadow"] == "none",
        )
        expect(
            "Portrait: kept off the printed page",
            cdp.evaluate("getComputedStyle(document.querySelector('.story-portrait')).display") == "none",
        )
        card_art = cdp.evaluate(
            "(() => {"
            " const art = document.querySelector('.card-art');"
            " return { display: getComputedStyle(art).display, height: Math.round(art.getBoundingClientRect().height) };"
            "})()"
        )
        expect(
            f"Print: the card art stays on the page ({card_art['display']}, {card_art['height']}px)",
            card_art["display"] != "none" and card_art["height"] > 0,
        )
        media(cdp, "", [{"name": "prefers-reduced-motion", "value": "no-preference"}])

        # The committed CV PDF must still describe the page it claims to represent.
        # Text only: this catches a stale artifact, not a font difference. Page counts are
        # not compared across platforms because Linux falls back from Georgia and prints
        # one page longer; check:pdf owns the committed file's own page count.
        cdp.call("Page.navigate", {"url": f"{origin}cv/"})
        wait_for(cdp, "document.readyState === 'complete'")
        fresh_pdf = cdp.call(
            "Page.printToPDF",
            {"printBackground": False, "preferCSSPageSize": True, "displayHeaderFooter": False},
        )

        def pdf_shape(data: bytes) -> tuple[int, str]:
            reader = PdfReader(io.BytesIO(data))
            text = "".join(page.extract_text() or "" for page in reader.pages)
            # NFKC folds ligatures and other compatibility glyphs, so the same page printed
            # on two platforms compares equal ("ﬁed" vs "fied" was the first difference).
            text = unicodedata.normalize("NFKC", text)
            return len(reader.pages), re.sub(r"\s+", "", text)

        fresh_pages, fresh_text = pdf_shape(base64.b64decode(fresh_pdf["data"]))
        committed_path = ROOT / "public" / "Francisco_Mateus_CV.pdf"
        committed_pages, committed_text = pdf_shape(committed_path.read_bytes())
        drift = ""
        if committed_text != fresh_text:
            limit = min(len(committed_text), len(fresh_text))
            index = next((i for i in range(limit) if committed_text[i] != fresh_text[i]), limit)
            drift = (
                f"; first difference at {index}: committed {committed_text[index:index + 24]!r} "
                f"vs fresh {fresh_text[index:index + 24]!r}"
            )
        expect(
            f"PDF: the committed CV text matches a fresh print of /cv/ "
            f"({len(committed_text)} chars; committed {committed_pages} pages, fresh {fresh_pages}){drift}",
            committed_text == fresh_text,
        )
    finally:
        if cdp:
            cdp.close()
        if process and process.poll() is None:
            process.kill()
            process.wait()
        if profile:
            shutil.rmtree(profile, ignore_errors=True)
        server.stop()

    if FAILURES:
        print("Browser check failed:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1

    print(
        f"Browser check OK: {ASSERTIONS} assertions over the no-JS fallback on the cards, the index and the study pages, print layout "
        "and PDF text, theme toggle, icons, the sticky fact headers, and the header midline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
