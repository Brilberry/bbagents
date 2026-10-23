"""Safe, streaming HTML rendering and asynchronous postmortem export."""
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import webbrowser


def inline(text):
    # Only local anchors become links. All event/model text remains inert text.
    parts = []
    offset = 0
    for match in re.finditer(r"\[([^\]]+)\]\((#[a-z0-9-]+)\)", text):
        parts.append(escape(text[offset:match.start()]))
        parts.append(f'<a href="{escape(match[2], quote=True)}">{escape(match[1])}</a>')
        offset = match.end()
    parts.append(escape(text[offset:]))
    return "".join(parts)


def render_html(markdown: Path) -> Path:
    """Render the generated report's Markdown subset without loading its logs into RAM."""
    destination = markdown.with_suffix(".html")
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write('''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Survival Island — Postmortem</title>
<style>
:root{color-scheme:dark light}body{font:16px/1.65 system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 24px}
h1,h2,h3{line-height:1.25}h2{margin-top:2em;border-bottom:1px solid #8886;padding-bottom:.4em}
a{color:#529cd9}pre{overflow:auto;max-height:65vh;background:#8881;padding:16px;border-radius:8px;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}
details{border:1px solid #8886;border-radius:8px;margin:1em 0;padding:12px}summary{cursor:pointer;font-weight:600}
.note{color:#888}li{margin:.2em 0}@media print{pre{max-height:none}body{max-width:none}}
</style></head><body><main>
<p class="note">Generated from the accompanying survival-report.md. Model interpretations are not established facts. Event logs are expandable.</p>
''')
            code = False
            with markdown.open(encoding="utf-8") as source:
                for raw in source:
                    line = raw.rstrip("\n")
                    if line.startswith("```"):
                        output.write('</code></pre></details>\n' if code else '<details><summary>Event log (expand)</summary><pre><code>')
                        code = not code
                    elif code:
                        output.write(escape(raw))
                    elif re.fullmatch(r"<!-- /?model-postmortem:agent-\d+ -->", line):
                        continue
                    elif re.fullmatch(r'<a id="(?:agent-\d+|complete-event-history)"></a>', line):
                        output.write(line + "\n")
                    elif line.startswith("#") and (match := re.match(r"^(#{1,6}) (.*)$", line)):
                        level = len(match[1])
                        output.write(f"<h{level}>{inline(match[2])}</h{level}>\n")
                    elif line.startswith("- "):
                        output.write(f"<ul><li>{inline(line[2:])}</li></ul>\n")
                    elif line.strip():
                        output.write(f"<p>{inline(line)}</p>\n")
            if code:
                output.write('</code></pre></details>\n')
            output.write('</main></body></html>\n')
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


class PostmortemExport:
    """Run reporting/model waits off the UI thread; launch the browser on poll."""
    def __init__(self, reports, opener=None):
        self.reports = reports
        self.opener = opener or webbrowser.open
        self.pool = None
        self.future = None
        self.path = None
        self.error = None
        self.message = "Markdown is saved. Generate a browser-readable postmortem."

    @property
    def busy(self):
        return self.future is not None and not self.future.done()

    def start(self):
        if self.path:
            self.open_browser()
        elif not self.busy and (self.future is None or self.error):
            self.error = None
            self.message = "Generating postmortem; waiting for any model summaries…"
            if self.pool is None:
                self.pool = ThreadPoolExecutor(max_workers=1)
            self.future = self.pool.submit(self._generate)

    def _generate(self):
        from .reporting import REPORT_NAME
        self.reports.close()
        return render_html(self.reports.directory / REPORT_NAME)

    def open_browser(self):
        try:
            opened = self.opener(self.path.resolve().as_uri())
            self.message = "Postmortem opened in your browser." if opened else "HTML saved; browser unavailable. See terminal for its path."
        except Exception as exc:
            self.message = f"HTML saved; browser could not open: {exc}"
        print(f"HTML postmortem: {self.path.resolve()}", flush=True)

    def poll(self):
        if self.future is not None and self.future.done() and self.path is None and self.error is None:
            try:
                self.path = self.future.result()
            except Exception as exc:
                self.error = str(exc)
                self.message = f"Generation failed: {exc}. Markdown is preserved; retry available."
                print(self.message, flush=True)
            else:
                self.open_browser()

    def finish(self):
        if self.pool:
            self.pool.shutdown(wait=True)
            self.poll()
