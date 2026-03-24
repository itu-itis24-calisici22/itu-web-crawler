# Product Requirements Document (PRD)
## ITU Web Crawler & Real-Time Search Engine

**Author:** [Burak Çalışıcı - 820220329]  
**Date:** March 2026  
**Course:** AI Aided Computer Engineering – Istanbul Technical University  
**Version:** 1.0

---

## 1. Overview

This project implements a web crawler and real-time search engine built primarily with Python's standard library. The system crawls web pages starting from a seed URL up to a configurable depth, indexes their content for full-text search, and exposes both a web-based dashboard and a CLI for interaction. The architecture prioritizes concurrency safety, back-pressure management, and resumability.

## 2. Goals & Non-Goals

### Goals
- Crawl the web recursively from a given origin URL to a maximum depth *k*.
- Maintain a thread-safe inverted index that supports live search while crawling is active.
- Implement back-pressure mechanisms to prevent resource exhaustion under large-scale crawls.
- Provide a real-time dashboard (web UI) and CLI for monitoring and controlling the system.
- Persist crawl state to disk (SQLite) so that interrupted crawls can be resumed.
- Use only language-native or standard-library facilities for HTTP fetching and HTML parsing (no Scrapy, no BeautifulSoup).

### Non-Goals
- Distributed / multi-machine crawling (single-machine only).
- JavaScript rendering (static HTML only).
- Production-grade relevancy ranking (TF-based heuristic is sufficient).
- Authentication-protected page crawling.

## 3. System Architecture

```
┌──────────────┐    ┌──────────────────────────────────────────┐
│  Web UI /    │◄──►│               Flask API                  │
│  Dashboard   │    │  POST /api/index   GET /api/search       │
└──────────────┘    │  GET  /api/metrics POST /api/pause       │
                    └────────────┬─────────────────────────────┘
                                 │
                    ┌────────────▼─────────────────────────────┐
                    │           CrawlerEngine                   │
                    │                                           │
                    │  ┌──────────┐   ┌────────────────────┐   │
                    │  │ TaskQueue│   │  InvertedIndex      │   │
                    │  │ (bounded)│   │  (RLock-protected)  │   │
                    │  └────┬─────┘   └────────────────────┘   │
                    │       │                                   │
                    │  ┌────▼──────────────────────────────┐   │
                    │  │  Worker Threads (8 concurrent)     │   │
                    │  │  ┌──────┐┌──────┐┌──────┐  ...    │   │
                    │  │  │  W1  ││  W2  ││  W3  │         │   │
                    │  │  └──────┘└──────┘└──────┘         │   │
                    │  └───────────────────────────────────┘   │
                    │                                           │
                    │  ┌───────────────────────────────────┐   │
                    │  │   PersistenceStore (SQLite/WAL)    │   │
                    │  └───────────────────────────────────┘   │
                    └──────────────────────────────────────────┘
```

## 4. Technical Requirements

### 4.1 Indexer (`index`)

| Requirement | Implementation |
|---|---|
| **Recursive Crawling** | BFS from origin URL. Each discovered link increments depth by 1. Crawl stops when depth > *k*. |
| **Uniqueness** | Thread-safe `visited` set (Python `set` guarded by `threading.Lock`). URLs are normalized (fragment removal, trailing-slash normalization) before comparison. |
| **Back Pressure** | Bounded `queue.Queue(maxsize=10,000)`. When the queue is full, new links are dropped and a `backpressure_hits` counter increments. Each worker sleeps 150ms between requests to rate-limit outbound traffic. |
| **Native Focus** | HTTP via `urllib.request.urlopen`; HTML parsing via `html.parser.HTMLParser`. No third-party crawling or parsing libraries. |

### 4.2 Searcher (`search`)

| Requirement | Implementation |
|---|---|
| **Query Engine** | Returns list of triples `(relevant_url, origin_url, depth)` plus title and relevancy score. |
| **Live Indexing** | The `InvertedIndex` is updated in real-time by crawler workers. Search reads are protected by `threading.RLock`, allowing concurrent reads and writes. |
| **Concurrency** | `RLock` on the inverted index; `Lock` on the visited set; SQLite in WAL mode with per-thread connections. |
| **Relevancy** | TF-based scoring: `score = count(token) / total_tokens`. Title matches receive a 3× boost. Multi-keyword queries sum per-token scores. |

### 4.3 UI / Dashboard

| Feature | Detail |
|---|---|
| Start crawl | Input URL + depth, click "Crawl" |
| Search | Full-text keyword search with ranked results |
| Live metrics | Auto-refreshing (1.5s) display of: pages crawled, pages indexed, queue depth, errors, back-pressure hits, worker count |
| Controls | Pause / Resume / Reset |
| Job tracking | Per-origin-URL stats (crawled count, queued count, elapsed time, status) |

### 4.4 CLI

Interactive REPL and direct command modes:
- `python cli.py interactive` — REPL with `index`, `search`, `metrics`, `pause`, `resume`, `reset`, `wait`, `quit`.
- `python cli.py index <url> --depth 3 --wait`
- `python cli.py search "keyword"`
- `python cli.py metrics`

### 4.5 Persistence & Resume

- All crawled pages and the visited set are stored in SQLite.
- The task queue is periodically flushed to a `crawl_queue` table.
- On restart, the engine reloads visited URLs, rebuilds the in-memory inverted index, and restores queued tasks.
- SQLite WAL mode enables concurrent reads from the Flask API while workers write.

## 5. Concurrency Design

| Structure | Protection | Notes |
|---|---|---|
| `_visited` (set) | `threading.Lock` | Check-and-add is atomic within lock scope |
| `InvertedIndex._index` | `threading.RLock` | Reentrant; allows recursive locking within single thread |
| `_task_queue` | `queue.Queue` (intrinsically thread-safe) | Bounded to 10k items |
| `_metrics_lock` | `threading.Lock` | Guards counter increments |
| SQLite | Per-thread connections + WAL mode | Avoids "database is locked" under concurrency |

## 6. Back-Pressure Mechanisms

1. **Queue Bound**: `maxsize=10,000` on the task queue. When full, child URLs are silently dropped and `backpressure_hits` is incremented.
2. **Rate Limiting**: Each worker thread sleeps 150ms between requests, giving a system-wide effective rate of ~53 requests/second with 8 workers.
3. **Page Size Cap**: Pages exceeding 2 MB are discarded.
4. **Timeout**: HTTP requests time out after 10 seconds.
5. **Depth Limit**: Hard cap at depth *k* prevents runaway breadth explosion.

## 7. Data Flow

1. User calls `POST /api/index` with `{ url, depth }`.
2. Engine normalizes URL, creates a `CrawlTask(depth=0)`, enqueues it.
3. Worker dequeues task, checks visited set, fetches HTML via `urllib`.
4. `LinkTextExtractor` (stdlib `HTMLParser`) extracts title, body text, and links.
5. Page record is saved to SQLite; text is added to `InvertedIndex`.
6. Child links (depth + 1 ≤ k) are normalized, de-duplicated, and enqueued.
7. User calls `GET /api/search?q=keyword` at any time; index serves live results.

## 8. Success Criteria

- **Functionality (40%)**: The crawler fetches pages recursively, respects depth limits, and never visits the same page twice. Search returns relevant results with correct `(url, origin, depth)` triples while indexing is active.
- **Architectural Sensibility (40%)**: Back-pressure and concurrency mechanisms are clearly designed. Thread-safety is achieved with minimal locking granularity. The system degrades gracefully under load.
- **AI Stewardship (20%)**: All AI-generated code is reviewed, understood, and can be explained. Design choices (stdlib-only parsing, BFS over DFS, TF scoring with title boost) are justified.

## 9. Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| HTTP client | `urllib.request` (stdlib) |
| HTML parser | `html.parser.HTMLParser` (stdlib) |
| Concurrency | `threading`, `queue` (stdlib) |
| Persistence | `sqlite3` (stdlib) |
| Web server | Flask (single external dependency) |
| Frontend | Vanilla HTML/CSS/JS (no framework) |

## 10. File Structure

```
crawler/
├── app.py              # Flask web server & API routes
├── cli.py              # Command-line interface
├── crawler.py          # Core engine: crawler, index, persistence
├── requirements.txt    # Python dependencies
├── templates/
│   └── dashboard.html  # Web dashboard
├── readme.md           # Project documentation
├── product_prd.md      # This PRD
└── recommendation.md   # Production deployment roadmap
```
