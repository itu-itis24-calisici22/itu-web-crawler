"""
Web UI & API for the crawler.
Provides a real-time dashboard and REST endpoints for indexing and search.
"""

import os
import sys
import json
import signal
import threading
from flask import Flask, render_template, request, jsonify

from crawler import CrawlerEngine

app = Flask(__name__)
engine: CrawlerEngine = None  # initialized in main


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/index", methods=["POST"])
def api_index():
    """Start crawling. Body: { "url": "...", "depth": 2 }"""
    data = request.get_json(force=True)
    url = data.get("url", "").strip()
    depth = int(data.get("depth", 2))

    if not url:
        return jsonify({"error": "url is required"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if depth < 0 or depth > 10:
        return jsonify({"error": "depth must be between 0 and 10"}), 400

    success = engine.index_url(url, depth)
    if success:
        return jsonify({"status": "started", "url": url, "depth": depth})
    return jsonify({"error": "Queue full – back pressure active"}), 503


@app.route("/api/search", methods=["GET"])
def api_search():
    """Search indexed pages. Query param: ?q=keyword&limit=50"""
    query = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", 50))
    if not query:
        return jsonify({"error": "q is required", "results": []}), 400
    results = engine.search_query(query, limit)
    return jsonify({"query": query, "count": len(results), "results": results})


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    """Return live system metrics."""
    return jsonify(engine.get_metrics())


@app.route("/api/pause", methods=["POST"])
def api_pause():
    engine.pause()
    return jsonify({"status": "paused"})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    engine.resume()
    return jsonify({"status": "resumed"})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    engine.reset_all()
    return jsonify({"status": "reset"})


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def create_app(db_path: str = "crawler.db") -> Flask:
    global engine
    engine = CrawlerEngine(db_path=db_path)
    return app


def main():
    global engine
    db_path = os.environ.get("CRAWLER_DB", "crawler.db")
    port = int(os.environ.get("PORT", 5000))

    engine = CrawlerEngine(db_path=db_path)

    def shutdown_handler(signum, frame):
        print("\nShutting down gracefully...")
        engine.stop_workers()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print(f"""
╔═══════════════════════════════════════════════════════╗
║           🕷️  ITU Web Crawler & Search Engine          ║
║                                                       ║
║   Dashboard:  http://localhost:{port}                  ║
║   API Index:  POST /api/index                         ║
║   API Search: GET  /api/search?q=keyword              ║
║   Metrics:    GET  /api/metrics                       ║
╚═══════════════════════════════════════════════════════╝
    """)

    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
