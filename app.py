"""Flask web server for the literature harvester UI."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from flask import Flask, jsonify, request, send_from_directory

from literature_harvester.formatting import chicago_bibliography
from literature_harvester.search import SearchEngine

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

SEEN_DB_PATH = DATA_DIR / "seen.sqlite"
SOURCES_CONFIG = DATA_DIR / "sources.json"

engine = SearchEngine(seen_path=SEEN_DB_PATH, sources_config=SOURCES_CONFIG)

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.route("/api/sources", methods=["GET", "POST"])
def handle_sources():
    if request.method == "GET":
        return jsonify(engine.available_sources())
    payload = request.get_json(force=True)
    if not isinstance(payload, list):
        return jsonify({"error": "Invalid payload"}), 400
    updated = engine.update_sources(payload)
    return jsonify(updated)


@app.route("/api/search", methods=["POST"])
def search():
    payload: Dict = request.get_json(force=True) or {}
    results = engine.search(payload)
    return jsonify(results)


@app.route("/api/export", methods=["POST"])
def export_bibliography():
    payload: Dict = request.get_json(force=True) or {}
    records: List[Dict] = payload.get("records", [])
    bibliography = chicago_bibliography(records)
    export_path = DATA_DIR / "export.txt"
    export_path.write_text(bibliography, encoding="utf-8")
    return jsonify({"path": str(export_path.relative_to(BASE_DIR)), "content": bibliography})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(debug=True)
