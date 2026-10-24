"""The inference runner, exercised against a stand-in server.

The real pass is hours of local inference, so the things that actually break a
build -- a malformed response, a dropped connection, being killed half way and
resumed -- are tested here against a tiny HTTP server instead, and the request
the runner sends is inspected to make sure it really is schema-constrained.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from pipeline import colour_model
from pipeline.cowen import EMOTIONS


class Recorder:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.mode = "ok"
        self.calls = 0


RECORDER = Recorder()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        RECORDER.requests.append(body)
        RECORDER.calls += 1
        if RECORDER.mode == "broken-json":
            payload = "this is not json"
        elif RECORDER.mode == "out-of-range":
            payload = json.dumps(
                {**{e: 0.5 for e in EMOTIONS}, "joyful": 7.0, "era": 1990,
                 "confidence": 0.5, "note": ""}
            )
        elif RECORDER.mode == "flaky-then-ok" and RECORDER.calls == 1:
            self.send_response(503)
            self.end_headers()
            return
        else:
            payload = json.dumps(
                {
                    **{e: 0.1 for e in EMOTIONS},
                    "joyful": 0.9,
                    "energizing": 0.7,
                    "era": 1995,
                    "confidence": 0.8,
                    "note": "bright and quick",
                }
            )
        out = json.dumps({"choices": [{"message": {"content": payload}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture()
def server():
    RECORDER.requests.clear()
    RECORDER.mode = "ok"
    RECORDER.calls = 0
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture()
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "track_colour.jsonl"
    monkeypatch.setattr(colour_model, "CACHE", path)
    return path


def tracks(n: int = 3) -> list[colour_model.Track]:
    return [
        colour_model.Track(f"key{i:03d}", f"artist {i}", f"title {i}", 100 - i)
        for i in range(n)
    ]


def run(host: str, items, monkeypatch, concurrency: int = 2) -> None:
    import asyncio

    monkeypatch.setenv("COLOUR_BACKEND", "llamacpp")
    monkeypatch.setenv("LLAMA_HOST", host)
    asyncio.run(colour_model.run(items, concurrency))


def test_request_is_schema_constrained(server, cache, monkeypatch):
    run(server, tracks(1), monkeypatch)
    body = RECORDER.requests[0]
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    props = fmt["json_schema"]["schema"]["properties"]
    for emotion in EMOTIONS:
        assert emotion in props, f"{emotion} missing from the decoding schema"
    # Decoding options are pinned so the table is stable across rebuilds.
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert body["seed"] == colour_model.SEED
    # The model is shown the track and nothing else: no audio features, no
    # genre tags, no play counts.
    user = [m for m in body["messages"] if m["role"] == "user"][0]["content"]
    assert user == "artist 0 — title 0"


def test_writes_one_record_per_track_and_is_resumable(server, cache, monkeypatch):
    run(server, tracks(3), monkeypatch)
    cached = colour_model.read_cache()
    assert set(cached) == {"key000", "key001", "key002"}
    assert cached["key000"]["judgement"]["joyful"] == 0.9
    assert cached["key000"]["L"] > 0 and cached["key000"]["C"] > 0

    # A resumed run only calls the model for what is missing.
    before = RECORDER.calls
    run(server, tracks(4)[3:], monkeypatch)
    assert RECORDER.calls == before + 1
    assert len(colour_model.read_cache()) == 4


def test_cache_survives_a_truncated_final_line(server, cache, monkeypatch):
    run(server, tracks(2), monkeypatch)
    with cache.open("a") as fh:
        fh.write('{"track_key": "key999", "judg')  # killed mid-write
    cached = colour_model.read_cache()
    assert set(cached) == {"key000", "key001"}


def test_malformed_response_is_not_written(server, cache, monkeypatch):
    RECORDER.mode = "broken-json"
    run(server, tracks(1), monkeypatch)
    assert colour_model.read_cache() == {}


def test_out_of_range_judgement_is_rejected(server, cache, monkeypatch):
    RECORDER.mode = "out-of-range"
    run(server, tracks(1), monkeypatch)
    assert colour_model.read_cache() == {}, "a rating outside 0..1 must not be stored"


def test_transient_failure_is_retried(server, cache, monkeypatch):
    RECORDER.mode = "flaky-then-ok"
    run(server, tracks(1), monkeypatch, concurrency=1)
    assert set(colour_model.read_cache()) == {"key000"}


def test_ollama_backend_sends_the_spec_shape(server, cache, monkeypatch):
    """The specification's runtime is still implemented and kept in step."""
    import asyncio

    class OllamaHandler(Handler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            RECORDER.requests.append(body)
            payload = json.dumps(
                {**{e: 0.2 for e in EMOTIONS}, "sad": 0.9, "era": 1971,
                 "confidence": 0.6, "note": ""}
            )
            out = json.dumps({"message": {"content": payload}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    httpd = HTTPServer(("127.0.0.1", 0), OllamaHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    RECORDER.requests.clear()
    monkeypatch.setenv("COLOUR_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{httpd.server_port}")
    asyncio.run(colour_model.run(tracks(1), 1))
    httpd.shutdown()

    body = RECORDER.requests[0]
    assert body["stream"] is False
    assert body["format"]["properties"].keys() >= set(EMOTIONS)
    assert body["options"] == colour_model.OPTIONS
    assert set(colour_model.read_cache()) == {"key000"}
