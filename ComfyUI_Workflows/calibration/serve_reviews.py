from __future__ import annotations

import argparse
import urllib.parse
from http.server import ThreadingHTTPServer

from lys_calibration import ProjectPaths
from review_server import ReviewHandler, discover_batches, refresh_reviewers


class AppReviewHandler(ReviewHandler):
    def dashboard(self) -> None:
        cards = []
        for batch_id, batch_dir in sorted(self.batches.items()):
            manifest = __import__("json").loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
            final = (batch_dir / "review.json").is_file()
            draft = (batch_dir / "review_draft.json").is_file()
            state = "final" if final else "draft" if draft else "not started"
            cards.append(
                f'<li><a href="/review/{urllib.parse.quote(batch_id)}">{batch_id}</a>'
                f' — {len(manifest["candidates"])} candidates — {state}</li>'
            )
        body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Lys reviewer</title>
<style>body{{max-width:900px;margin:40px auto;padding:0 20px;background:#15171b;color:#eee;font:16px system-ui}}
a{{color:#ef9aa6}}li{{padding:8px 0}}</style></head><body><h1>Lys calibration reviewer</h1>
<p>Open a batch below. Images are served locally; draft and final decisions are stored beside that batch.</p><ul>{''.join(cards)}</ul></body></html>"""
        self.send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/review/"):
            batch_id = urllib.parse.unquote(path[len("/review/") :].strip("/"))
            if batch_id not in self.batches:
                self.send_error(404)
                return
            app = ProjectPaths.discover().calibration_dir / "review_app.html"
            self.send_bytes(app.read_bytes(), "text/html; charset=utf-8")
            return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Lys calibration reviewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    paths = ProjectPaths.discover()
    refreshed = refresh_reviewers(paths)
    server = ThreadingHTTPServer((args.host, args.port), AppReviewHandler)
    server.batches = discover_batches(paths)  # type: ignore[attr-defined]
    print(f"Refreshed {refreshed} offline reviewer pages")
    print(f"Lys reviewer: http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
