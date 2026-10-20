# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=13,<18", "pypdf>=5"]
# ///
"""Behavioural check for what the static checks cannot see.

Drives the built dist/ in headless Chromium over CDP and asserts the no-JS fallback,
print media, the real printed PDF, the theme toggle, icons, and the sticky case-study
header.

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
    """The case-study rows are static markup, so their count is the render signal."""
    return wait_for(cdp, "document.querySelectorAll('.project-row').length === 6")


def goto(cdp: CDP, url: str, javascript: bool = True) -> None:
    cdp.call("Page.navigate", {"url": url})
    wait_for(cdp, "document.readyState === 'complete'")
    expect(f"{url} finished loading and rendered the capstone track", settle_ready(cdp, javascript), counted=False)


def reload(cdp: CDP, javascript: bool = True) -> None:
    cdp.call("Page.reload", {"ignoreCache": True})
    wait_for(cdp, "document.readyState === 'complete'")
    expect("reloaded page rendered the capstone track", settle_ready(cdp, javascript), counted=False)


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
            "No-JS: all six case studies are present",
            cdp.evaluate("document.querySelectorAll('.case-disclosure').length") == 6,
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
        scripts_enabled(cdp, True)
        reload(cdp)

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
        # must start under the site header rather than mid-body.
        cdp.call("Page.navigate", {"url": origin})
        wait_for(cdp, "document.readyState === 'complete'")
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
        f"Browser check OK: {ASSERTIONS} assertions over the no-JS fallback, print layout "
        "and PDF text, theme toggle, icons, and the sticky row header."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
