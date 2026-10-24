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
  const sidebar = document.querySelector('.sidebar');
  return {
    flowBorders: [getComputedStyle(flow).borderTopWidth, getComputedStyle(flow).borderBottomWidth],
    proseHeight: Math.round(prose.getBoundingClientRect().height),
    lede: getComputedStyle(document.querySelector('.study-lede')).display,
    sidebarPosition: getComputedStyle(sidebar).position,
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
    stored: localStorage.getItem('theme'),
  };
})()
"""
        )
        expect(
            "Theme toggle: a press switches to dark and stores the choice",
            flipped["theme"] == "dark" and flipped["stored"] == "dark",
        )
        expect(
            "Theme toggle: the dark theme shows the sun and relabels",
            flipped["sun"] != "none" and flipped["moon"] == "none" and flipped["label"] == "Switch to light theme",
        )
        reload(cdp)
        persisted = cdp.evaluate(
            """
({
  theme: document.documentElement.dataset.theme,
  label: document.querySelector('[data-theme-toggle]').getAttribute('aria-label'),
})
"""
        )
        expect(
            "Theme toggle: the choice survives a reload",
            persisted["theme"] == "dark" and persisted["label"] == "Switch to light theme",
        )
        expect(
            "Theme toggle: switching back returns the light theme",
            cdp.evaluate(
                "(() => { document.querySelector('[data-theme-toggle]').click(); "
                "return document.documentElement.dataset.theme; })()"
            )
            == "light",
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
        expect(f"Icons: 4 on the footer links, saw {icons['footer']}", icons["footer"] == 4)
        expect("Icons: decorative SVGs are hidden from assistive tech", icons["hidden"] is True)
        expect("Icons: no external SVG references", icons["external"] == 0)
        expect("Icons: every link keeps an accessible name", all(label for label in icons["labels"]))
        media(cdp, "")

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
        wait_for(cdp, "document.readyState === 'complete'")
        expect("the clicked row opened its study page", settle_ready(cdp, javascript=True), counted=False)
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
            f"Print: the sidebar stops sticking ({printed['sidebarPosition']})",
            printed["sidebarPosition"] == "static",
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
        expect(
            "PDF: the printed study carries its title",
            row["title"] in re.sub(r"\s+", " ", text),
        )
        media(cdp, "")

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
            "Nav: projects points at the home work section, about at the home about section",
            nav_state[0]["href"].endswith("#work") and nav_state[1]["href"].endswith("#about"),
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
        media(cdp, "")

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
