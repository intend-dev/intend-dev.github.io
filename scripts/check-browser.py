# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=13,<18", "pypdf>=5"]
# ///
"""Behavioural check for what the static checks cannot see.

Drives the built dist/ in headless Chromium over CDP and asserts the no-JS fallback on
both pages, print media, the real printed PDF, the theme toggle, icons, the sticky
case-study and fact headers, and the header midline.

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
    """The case-study rows are static markup, so their count is the render signal:
    three on the profile page, six on the work page."""
    expected = 6 if "/work" in cdp.evaluate("location.pathname") else 3
    return wait_for(cdp, f"document.querySelectorAll('.project-row').length === {expected}")


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
  document.querySelector('details.case-disclosure summary').click();
  await new Promise((done) => setTimeout(done, 250));
  const row = document.querySelector('details.case-disclosure[open]');
  const summary = row.querySelector('summary');
  const line = row.querySelector('.flow > li');
  const headerBottom = summary.getBoundingClientRect().bottom;
  const lineTop = line.getBoundingClientRect().top;
  const doubled = [...row.querySelectorAll('*')].filter((el) => {
    const computed = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return parseFloat(computed.borderTopWidth) > 0 && computed.borderTopStyle !== 'none'
      && rect.width > 500 && rect.top > headerBottom + 0.5 && rect.top < lineTop - 0.5;
  }).length;
  return {
    flowBorders: [
      getComputedStyle(row.querySelector('.case-flow')).borderTopWidth,
      getComputedStyle(row.querySelector('.case-flow')).borderBottomWidth,
    ],
    doubled,
    summaryShadow: getComputedStyle(summary).boxShadow,
    summaryPosition: getComputedStyle(summary).position,
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

        # No JavaScript: the content stays readable and the toggle stays hidden.
        scripts_enabled(cdp, False)
        reload(cdp, javascript=False)
        expect(
            "No-JS: the profile shows its three featured case studies",
            cdp.evaluate("document.querySelectorAll('.case-disclosure').length") == 3,
        )
        expect(
            "No-JS: a case study opens natively",
            cdp.evaluate(
                "(() => {"
                " const d = document.querySelector('.case-disclosure');"
                " d.querySelector('summary').click();"
                " return d.open && d.querySelector('.row-body').getBoundingClientRect().height > 0;"
                "})()"
            )
            is True,
        )
        expect(
            "No-JS: the theme toggle stays hidden",
            cdp.evaluate("document.querySelector('[data-theme-toggle]').hidden") is True,
        )
        # All six studies live on the work page; they must stay present and openable
        # there without JavaScript too.
        cdp.call("Page.navigate", {"url": f"{origin}work/"})
        wait_for(cdp, "document.readyState === 'complete'")
        expect(
            "No-JS: the work page shows all six case studies",
            cdp.evaluate("document.querySelectorAll('.case-disclosure').length") == 6,
        )
        expect(
            "No-JS: a work-page case study opens natively",
            cdp.evaluate(
                "(() => {"
                " const d = document.querySelector('.case-disclosure');"
                " d.querySelector('summary').click();"
                " return d.open && d.querySelector('.row-body').getBoundingClientRect().height > 0;"
                "})()"
            )
            is True,
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

        # Switching rows: opening another case study closes the first, and the new row
        # must start under the site header rather than mid-body. The six-study set
        # lives on the work page.
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
        cdp.evaluate("document.querySelectorAll('.case-disclosure')[0].open = true")
        time.sleep(0.15)
        cdp.evaluate(
            "(() => {"
            " const ds = [...document.querySelectorAll('.case-disclosure')];"
            " window.scrollTo({ top: ds[0].getBoundingClientRect().top + window.scrollY + 300, behavior: 'instant' });"
            " const s = ds[2].querySelector('summary').getBoundingClientRect();"
            " window.scrollBy({ top: s.top - 700, behavior: 'instant' });"
            " ds[2].querySelector('summary').click();"
            "})()"
        )
        time.sleep(0.3)
        switched = cdp.evaluate(
            "(() => {"
            " const d = [...document.querySelectorAll('.case-disclosure')][2];"
            " const s = d.querySelector('summary').getBoundingClientRect();"
            " const b = d.querySelector('.row-body').getBoundingClientRect();"
            " const h = document.querySelector('.site-header').getBoundingClientRect().height;"
            " return { open: d.open, summaryTop: Math.round(s.top), bodyTop: Math.round(b.top), header: Math.round(h) };"
            "})()"
        )
        expect(
            f"Switching rows: the opened row aligns under the header (summary {switched['summaryTop']}, header {switched['header']})",
            switched["open"] and abs(switched["summaryTop"] - switched["header"]) <= 2,
        )
        expect(
            f"Switching rows: the opened body starts below its summary (body {switched['bodyTop']}, summary {switched['summaryTop']})",
            switched["bodyTop"] > switched["summaryTop"],
        )
        crumb = cdp.evaluate(
            """
(() => {
  const label = document.querySelector('.nav-current');
  const ds = [...document.querySelectorAll('.case-disclosure')];
  return { text: label.textContent, current: ds[2].dataset.navLabel, hidden: label.hidden, nav: [...document.querySelectorAll('.site-nav a[aria-current]')].map((a) => a.dataset.navKey) };
})()
"""
        )
        expect(
            f"Breadcrumb: the sub-level only ({crumb['text']!r})",
            crumb["text"] == crumb["current"] and crumb["hidden"] is False,
        )
        expect(
            f"Nav: no link highlights inside a study (the work link is out of the menu) ({crumb['nav']})",
            crumb["nav"] == [],
        )

        # Print: the case study flow and the row header keep their behaviour.
        viewport(cdp, 794, 1123)
        media(cdp, "print")
        printed = cdp.evaluate(PRINT_STATE)
        expect(
            f"Print: the case-flow wraps without its own rule ({'/'.join(printed['flowBorders'])})",
            all(value == "0px" for value in printed["flowBorders"]),
        )
        expect(
            f"Print: no extra rule between the row header and the timeline ({printed['doubled']})",
            printed["doubled"] == 0,
        )
        expect(
            "Print: the row header paints no stuck edge",
            printed["summaryShadow"] == "none" and printed["summaryPosition"] == "static",
        )

        # The real print output, not just the emulated media.
        pdf_result = cdp.call("Page.printToPDF", {"printBackground": False, "preferCSSPageSize": True})
        page_count = 0
        try:
            reader = PdfReader(io.BytesIO(base64.b64decode(pdf_result["data"])))
            page_count = len(reader.pages)
        except Exception as error:  # noqa: BLE001 - reported as a failed assertion
            expect(f"PDF: the printed file could be parsed ({error})", False)
        expect(f"PDF: the print produced a multi-page A4 document ({page_count} pages)", page_count > 1)
        media(cdp, "")

        # Sticky row header: the edge appears while pinned and disappears again.
        viewport(cdp, 1568, 900)
        reload(cdp)
        cdp.evaluate(
            "(() => { const details = document.querySelector('details.case-disclosure'); "
            "details.querySelector('summary').click(); window.scrollTo(0, 0); return true; })()"
        )
        time.sleep(0.35)
        # Let the scroll handler settle after the row expands, then read the resting state
        # from wherever the page actually ended up.
        at_rest_cleared = wait_for(
            cdp,
            "!document.querySelector('details.case-disclosure[open] > summary').hasAttribute('data-stuck')",
            timeout=2,
        )

        def read_summary() -> dict:
            return cdp.evaluate(
                """
(() => {
  const summary = document.querySelector('details.case-disclosure[open] > summary');
  return {
    stuck: summary.hasAttribute('data-stuck'),
    shadow: getComputedStyle(summary).boxShadow,
    height: Math.round(summary.getBoundingClientRect().height),
    borderBottom: getComputedStyle(summary).borderBottomWidth,
  };
})()
"""
            )

        at_rest = read_summary()
        resting_scroll = cdp.evaluate("Math.round(window.scrollY)")
        expect(
            f"Sticky header: no edge at rest (scrollY {resting_scroll})",
            at_rest_cleared and at_rest["stuck"] is False and at_rest["shadow"] == "none",
        )
        expect("Sticky header: no border of its own", at_rest["borderBottom"] == "0px")

        # One explicit jump: scrollIntoView followed by scrollBy cancels the first smooth
        # scroll under html{scroll-behavior:smooth}, and the row never reaches the header.
        cdp.evaluate(
            "(() => { const summary = document.querySelector('details.case-disclosure[open] > summary'); "
            "const top = summary.getBoundingClientRect().top + window.scrollY + 400; "
            "window.scrollTo({ top, behavior: 'instant' }); return true; })()"
        )
        pinned_ok = wait_for(
            cdp,
            "document.querySelector('details.case-disclosure[open] > summary').hasAttribute('data-stuck')",
            timeout=2,
        )
        pinned_state = cdp.evaluate(
            """
(() => {
  const summary = document.querySelector('details.case-disclosure[open] > summary');
  if (!summary) return { missing: true };
  const style = getComputedStyle(summary);
  return {
    top: Math.round(summary.getBoundingClientRect().top),
    offset: style.top,
    position: style.position,
    stuck: summary.hasAttribute('data-stuck'),
    shadow: style.boxShadow,
    scrollY: Math.round(window.scrollY),
    headerH: getComputedStyle(document.documentElement).getPropertyValue('--header-h').trim(),
  };
})()
"""
        )
        expect(f"Sticky header: the edge appears once pinned ({json.dumps(pinned_state)})", pinned_ok)
        pinned = read_summary()
        expect("Sticky header: the pinned edge is painted", pinned["shadow"] != "none")
        expect("Sticky header: the edge does not change the height", pinned["height"] == at_rest["height"])

        cdp.evaluate("(() => { window.scrollTo({ top: 0, behavior: 'instant' }); return true; })()")
        expect(
            "Sticky header: the edge goes away when released",
            wait_for(
                cdp,
                "!document.querySelector('details.case-disclosure[open] > summary').hasAttribute('data-stuck')",
                timeout=2,
            ),
        )

        after_close = cdp.evaluate(
            """
(async () => {
  const details = document.querySelector('details.case-disclosure');
  const summary = details.querySelector('summary');
  summary.click();
  window.scrollTo(0, summary.getBoundingClientRect().top + window.scrollY + 200);
  await new Promise((done) => setTimeout(done, 300));
  return {
    marks: document.querySelectorAll('.case-disclosure > summary[data-stuck]').length,
    closedTop: Math.round(summary.getBoundingClientRect().top),
  };
})()
"""
        )
        expect(
            f"Sticky header: no mark survives a closed row (header at {after_close['closedTop']}px)",
            after_close["marks"] == 0,
        )

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
            f"Nav: three links, examples first "
            f"({json.dumps([item["label"] for item in nav_state])})",
            [item["key"] for item in nav_state] == ["examples", "about", "contact"],
        )
        expect(
            "Nav: examples points at the home work section, about at the home about section",
            nav_state[0]["href"].endswith("#work") and nav_state[1]["href"].endswith("#about"),
        )
        expect(
            "Nav: the work item stays out of the menu (commented by decision)",
            cdp.evaluate("document.querySelectorAll('.site-nav a[data-nav-key=\"work\"]').length") == 0,
        )
        cdp.evaluate("(() => { document.querySelector('#work').scrollIntoView({ block: 'start' }); return true; })()")
        expect(
            "Nav: examples highlights at the home work section",
            wait_for(
                cdp,
                "document.querySelector('.site-nav a[data-nav-key=\"examples\"]')?.hasAttribute('aria-current')",
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
        f"Browser check OK: {ASSERTIONS} assertions over the no-JS fallback on both pages, print layout "
        "and PDF text, theme toggle, icons, the sticky row and fact headers, and the header midline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
