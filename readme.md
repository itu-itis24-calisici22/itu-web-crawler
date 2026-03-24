# 🕷️ ITU Web Crawler & Real-Time Search Engine

A concurrent web crawler and search engine built for the **Istanbul Technical University — AI Aided Computer Engineering** course. The system crawls web pages from a seed URL to a configurable depth, builds a live full-text index, and serves search results in real time — all using Python's standard library for core HTTP and HTML parsing.

---

## Features

- **Recursive crawling** from any URL to depth *k* (BFS traversal)
- **Live search** — query the index while crawling is still running
- **Thread-safe concurrency** — 8 parallel workers with lock-protected shared state
- **Back-pressure controls** — bounded queue (10k), per-worker rate limiting, page size cap
- **Persistence & resume** — SQLite-backed state survives restarts
- **Real-time dashboard** — web UI with auto-refreshing metrics
- **CLI** — interactive REPL and one-shot commands
- **No heavyweight libraries** — HTTP via `urllib`, HTML parsing via `html.parser`

---

## Quick Start

### Prerequisites

- Python 3.10 or later
- pip

### Install & Run

```bash
# Clone the repository
git clone https://github.com/<your-username>/crawler.git
cd crawler

# Install dependencies (only Flask)
pip install -r requirements.txt

# Option 1: Web Dashboard
python app.py
# → Open http://localhost:5000

# Option 2: CLI
python cli.py interactive
```

### Web Dashboard

1. Open `http://localhost:5000` in your browser.
2. Enter a URL and depth, click **Crawl**.
3. Watch live metrics update every 1.5 seconds.
4. Use the **Search** box to query indexed pages at any time.
5. Use **Pause**, **Resume**, and **Reset** to control the crawler.

### CLI Usage

```bash
# Interactive mode
python cli.py interactive
> index https://en.wikipedia.org/wiki/Web_crawler 2
> metrics
> search "web crawling"
> quit

# One-shot commands
python cli.py index https://example.com --depth 3 --wait
python cli.py search "python programming"
python cli.py metrics
python cli.py reset
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/index` | Start crawl. Body: `{ "url": "...", "depth": 2 }` |
| `GET` | `/api/search?q=keyword&limit=50` | Search indexed pages |
| `GET` | `/api/metrics` | System metrics (JSON) |
| `POST` | `/api/pause` | Pause crawling |
| `POST` | `/api/resume` | Resume crawling |
| `POST` | `/api/reset` | Reset all data |

---

## Architecture

```
User ──► Flask API ──► CrawlerEngine
                          ├── TaskQueue (bounded, thread-safe)
                          ├── Worker Threads ×8
                          │     └── urllib fetch → HTMLParser → index
                          ├── InvertedIndex (RLock-protected)
                          └── PersistenceStore (SQLite WAL)
```

### Concurrency Model

- **8 worker threads** dequeue tasks from a bounded `queue.Queue`.
- The **visited set** is protected by `threading.Lock` for atomic check-and-add.
- The **inverted index** uses `threading.RLock` for concurrent read/write access.
- **SQLite WAL mode** allows concurrent reads during writes; each thread uses its own connection.

### Back-Pressure Mechanisms

1. **Bounded queue** (10,000 items) — excess links are dropped when full.
2. **Rate limiting** — 150ms delay per worker per request (~53 req/s total).
3. **Page size cap** — pages > 2 MB are skipped.
4. **Request timeout** — 10s max per HTTP request.

### Relevancy Scoring

Simple TF (term frequency) heuristic:
- `score = occurrences / total_tokens` for each query term.
- Title matches receive a **3× boost**.
- Multi-word queries sum per-token scores.

---

## File Structure

```
├── app.py              # Flask web server & API
├── cli.py              # Command-line interface
├── crawler.py          # Core engine (crawler, index, persistence)
├── requirements.txt    # Python dependencies
├── templates/
│   └── dashboard.html  # Web dashboard (vanilla HTML/CSS/JS)
├── product_prd.md      # Product Requirements Document
├── recommendation.md   # Production deployment roadmap
└── readme.md           # This file
```

## Technology Choices

| Concern | Choice | Rationale |
|---------|--------|-----------|
| HTTP | `urllib.request` | Stdlib — no external dependency |
| HTML parsing | `html.parser` | Stdlib — satisfies "native focus" constraint |
| Concurrency | `threading` + `queue` | Stdlib — works well for I/O-bound tasks |
| Persistence | `sqlite3` | Stdlib — zero-config, WAL mode for concurrency |
| Web framework | Flask | Minimal single dependency for serving the dashboard |
| Frontend | Vanilla JS | No build step; minimal footprint |

---

## Resuming After Interruption

The system persists crawl state to SQLite:

1. Every visited URL is recorded.
2. The task queue is flushed to a `crawl_queue` table on shutdown.
3. On restart, the engine reloads visited URLs, rebuilds the in-memory index from stored pages, and re-enqueues pending tasks.

Simply restart `python app.py` or `python cli.py interactive` to resume.

---

## License

Educational project — MIT License.
