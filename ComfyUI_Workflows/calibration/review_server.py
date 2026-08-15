from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import posixpath
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from lys_calibration import (
    ProjectPaths,
    read_json,
    review_weight,
    utc_now,
    validate_review,
    write_json,
    write_reviewer,
)


def discover_batches(paths: ProjectPaths) -> dict[str, Path]:
    batches: dict[str, Path] = {}
    if paths.generated_root.is_dir():
        for manifest_path in paths.generated_root.glob("*/manifest.json"):
            manifest = read_json(manifest_path)
            batches[str(manifest["batch_id"])] = manifest_path.parent.resolve()
    return batches


def draft_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "batch_id": manifest["batch_id"],
        "exported_at": utc_now(),
        "reviews": [
            {
                "candidate_id": item["candidate_id"],
                "identity": None,
                "expression": None,
                "cleanliness": None,
                "keep": False,
                "notes": "",
            }
            for item in manifest["candidates"]
        ],
    }


def normalize_draft(payload: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    review = validate_review(payload, manifest)
    review["saved_at"] = utc_now()
    return review


def review_csv(review: dict[str, Any]) -> str:
    stream = io.StringIO(newline="")
    fields = (
        "candidate_id",
        "identity",
        "expression",
        "cleanliness",
        "keep",
        "weighted_score",
        "passes",
        "notes",
    )
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows({field: item.get(field) for field in fields} for item in review["reviews"])
    return stream.getvalue()


def complete(review: dict[str, Any]) -> bool:
    return all(
        item.get(field) is not None
        for item in review["reviews"]
        for field in ("identity", "expression", "cleanliness")
    )


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "LysReview/1.0"

    @property
    def batches(self) -> dict[str, Path]:
        return self.server.batches  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_bytes(self, data: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        self.send_bytes(
            (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def parse_batch_route(self, prefix: str) -> tuple[str, str] | None:
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix) :].lstrip("/")
        parts = remainder.split("/", 1)
        batch_id = urllib.parse.unquote(parts[0])
        relative = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
        if batch_id not in self.batches:
            return None
        return batch_id, relative

    def dashboard(self) -> None:
        cards = []
        for batch_id, batch_dir in sorted(self.batches.items()):
            manifest = read_json(batch_dir / "manifest.json")
            final = (batch_dir / "review.json").is_file()
            draft = (batch_dir / "review_draft.json").is_file()
            state = "final" if final else "draft" if draft else "not started"
            cards.append(
                f'<li><a href="/batch/{urllib.parse.quote(batch_id)}/">{batch_id}</a>'
                f' — {len(manifest["candidates"])} candidates — {state}</li>'
            )
        body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Lys reviewer</title>
<style>body{{max-width:900px;margin:40px auto;padding:0 20px;background:#15171b;color:#eee;font:16px system-ui}}
a{{color:#ef9aa6}}li{{padding:8px 0}}</style></head><body><h1>Lys calibration reviewer</h1>
<p>The backend serves images and saves review drafts directly beside each batch.</p><ul>{''.join(cards)}</ul></body></html>"""
        self.send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

    def serve_batch_file(self, batch_id: str, relative: str) -> None:
        batch_dir = self.batches[batch_id]
        relative = relative or "review.html"
        clean = posixpath.normpath("/" + relative).lstrip("/")
        candidate = (batch_dir / Path(clean)).resolve()
        if candidate != batch_dir and batch_dir not in candidate.parents:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_bytes(candidate.read_bytes(), mime)

    def get_review(self, batch_id: str) -> None:
        batch_dir = self.batches[batch_id]
        manifest = read_json(batch_dir / "manifest.json")
        for name, kind in (("review_draft.json", "draft"), ("review.json", "final")):
            path = batch_dir / name
            if path.is_file():
                self.send_json({"kind": kind, "review": read_json(path)})
                return
        self.send_json({"kind": "empty", "review": draft_payload(manifest)})

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.dashboard()
            return
        if path == "/api/batches":
            self.send_json({"batches": sorted(self.batches)})
            return
        api = self.parse_batch_route("/api/batches/")
        if api and api[1] == "manifest":
            self.send_json(read_json(self.batches[api[0]] / "manifest.json"))
            return
        if api and api[1] == "review":
            self.get_review(api[0])
            return
        batch = self.parse_batch_route("/batch/")
        if batch:
            self.serve_batch_file(*batch)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc

    def save_review(self, batch_id: str, finalize: bool) -> None:
        batch_dir = self.batches[batch_id]
        manifest = read_json(batch_dir / "manifest.json")
        try:
            review = normalize_draft(self.read_payload(), manifest)
            if finalize and not complete(review):
                raise ValueError("All three scores are required for every candidate before finalizing")
            if finalize:
                write_json(batch_dir / "review.json", review)
                (batch_dir / "review.csv").write_text(review_csv(review), encoding="utf-8-sig")
            else:
                write_json(batch_dir / "review_draft.json", review)
            self.send_json(
                {
                    "ok": True,
                    "kind": "final" if finalize else "draft",
                    "complete": complete(review),
                    "accepted": sum(bool(item.get("passes")) for item in review["reviews"]),
                }
            )
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_PUT(self) -> None:
        route = self.parse_batch_route("/api/batches/")
        if route and route[1] == "review/draft":
            self.save_review(route[0], finalize=False)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        route = self.parse_batch_route("/api/batches/")
        if route and route[1] == "review/finalize":
            self.save_review(route[0], finalize=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def refresh_reviewers(paths: ProjectPaths) -> int:
    count = 0
    for batch_dir in discover_batches(paths).values():
        manifest = read_json(batch_dir / "manifest.json")
        write_reviewer(batch_dir, manifest)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Lys calibration reviews and persist human decisions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    paths = ProjectPaths.discover()
    count = refresh_reviewers(paths)
    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    server.batches = discover_batches(paths)  # type: ignore[attr-defined]
    print(f"Refreshed {count} reviewer pages")
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
