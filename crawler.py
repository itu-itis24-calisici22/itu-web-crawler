"""
Web Crawler Engine
Uses only Python standard library for HTTP and HTML parsing.
Implements thread-safe concurrent crawling with back pressure.
"""

import threading
import time
import queue
import sqlite3
import os
import re
import json
import logging
from urllib.request import urlopen, Request
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crawler")


# ---------------------------------------------------------------------------
# HTML link & text extractor (stdlib only – no BeautifulSoup)
# ---------------------------------------------------------------------------
class LinkTextExtractor(HTMLParser):
    """Extract links and visible text from HTML using stdlib html.parser."""

    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.title: str = ""
        self._in_title = False
        self._text_parts: list[str] = []
        self._skip_tags = {"script", "style", "noscript", "svg", "meta", "link"}
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._skip_tags:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for attr_name, attr_val in attrs:
                if attr_name == "href" and attr_val:
                    self.links.append(attr_val)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip_depth == 0:
            cleaned = data.strip()
            if cleaned:
                self._text_parts.append(cleaned)

    @property
    def text(self) -> str:
        return " ".join(self._text_parts)


def parse_html(html: str, base_url: str):
    """Return (title, text, absolute_links) from raw HTML."""
    parser = LinkTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    abs_links = []
    for href in parser.links:
        href, _ = urldefrag(href)
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.scheme in ("http", "https"):
            abs_links.append(abs_url)
    return parser.title.strip(), parser.text, abs_links


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class CrawlTask:
    url: str
    origin_url: str
    depth: int
    max_depth: int


@dataclass
class PageRecord:
    url: str
    origin_url: str
    depth: int
    title: str
    text: str
    links: list[str]
    crawled_at: float


@dataclass
class CrawlJob:
    origin_url: str
    max_depth: int
    status: str = "running"  # running | paused | completed | error
    pages_crawled: int = 0
    pages_queued: int = 0
    started_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Persistence layer (SQLite)
# ---------------------------------------------------------------------------
class PersistenceStore:
    """SQLite-backed persistence for crawl state and page index."""

    def __init__(self, db_path: str = "crawler.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=10)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pages (
                url TEXT PRIMARY KEY,
                origin_url TEXT NOT NULL,
                depth INTEGER NOT NULL,
                title TEXT,
                body_text TEXT,
                links_json TEXT,
                crawled_at REAL
            );
            CREATE TABLE IF NOT EXISTS crawl_queue (
                url TEXT PRIMARY KEY,
                origin_url TEXT NOT NULL,
                depth INTEGER NOT NULL,
                max_depth INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS visited (
                url TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS jobs (
                origin_url TEXT PRIMARY KEY,
                max_depth INTEGER,
                status TEXT,
                pages_crawled INTEGER DEFAULT 0,
                pages_queued INTEGER DEFAULT 0,
                started_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_pages_origin ON pages(origin_url);
            CREATE INDEX IF NOT EXISTS idx_pages_depth ON pages(depth);
        """)
        conn.commit()

    # -- pages --
    def save_page(self, rec: PageRecord):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO pages (url, origin_url, depth, title, body_text, links_json, crawled_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rec.url, rec.origin_url, rec.depth, rec.title, rec.text, json.dumps(rec.links), rec.crawled_at),
        )
        conn.commit()

    def get_all_pages(self) -> list[dict]:
        conn = self._get_conn()
        cur = conn.execute("SELECT url, origin_url, depth, title, body_text FROM pages")
        rows = cur.fetchall()
        return [
            {"url": r[0], "origin_url": r[1], "depth": r[2], "title": r[3], "text": r[4]}
            for r in rows
        ]

    def page_count(self) -> int:
        conn = self._get_conn()
        cur = conn.execute("SELECT COUNT(*) FROM pages")
        return cur.fetchone()[0]

    # -- visited --
    def mark_visited(self, url: str):
        conn = self._get_conn()
        conn.execute("INSERT OR IGNORE INTO visited (url) VALUES (?)", (url,))
        conn.commit()

    def is_visited(self, url: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute("SELECT 1 FROM visited WHERE url = ?", (url,))
        return cur.fetchone() is not None

    def load_visited(self) -> set[str]:
        conn = self._get_conn()
        cur = conn.execute("SELECT url FROM visited")
        return {r[0] for r in cur.fetchall()}

    # -- queue (for resume) --
    def save_queue_items(self, tasks: list[CrawlTask]):
        conn = self._get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO crawl_queue (url, origin_url, depth, max_depth) VALUES (?, ?, ?, ?)",
            [(t.url, t.origin_url, t.depth, t.max_depth) for t in tasks],
        )
        conn.commit()

    def load_queue_items(self) -> list[CrawlTask]:
        conn = self._get_conn()
        cur = conn.execute("SELECT url, origin_url, depth, max_depth FROM crawl_queue")
        items = [CrawlTask(url=r[0], origin_url=r[1], depth=r[2], max_depth=r[3]) for r in cur.fetchall()]
        return items

    def remove_queue_item(self, url: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM crawl_queue WHERE url = ?", (url,))
        conn.commit()

    def clear_queue(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM crawl_queue")
        conn.commit()

    # -- jobs --
    def save_job(self, job: CrawlJob):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO jobs (origin_url, max_depth, status, pages_crawled, pages_queued, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job.origin_url, job.max_depth, job.status, job.pages_crawled, job.pages_queued, job.started_at),
        )
        conn.commit()

    def get_jobs(self) -> list[dict]:
        conn = self._get_conn()
        cur = conn.execute("SELECT origin_url, max_depth, status, pages_crawled, pages_queued, started_at FROM jobs")
        return [
            {
                "origin_url": r[0], "max_depth": r[1], "status": r[2],
                "pages_crawled": r[3], "pages_queued": r[4], "started_at": r[5],
            }
            for r in cur.fetchall()
        ]

    def reset(self):
        conn = self._get_conn()
        conn.executescript("DELETE FROM pages; DELETE FROM crawl_queue; DELETE FROM visited; DELETE FROM jobs;")
        conn.commit()


# ---------------------------------------------------------------------------
# In-memory inverted index (thread-safe) for search
# ---------------------------------------------------------------------------
class InvertedIndex:
    """Thread-safe in-memory inverted index for keyword search."""

    def __init__(self):
        self._lock = threading.RLock()
        # word -> { url: score }
        self._index: dict[str, dict[str, float]] = defaultdict(dict)
        # url -> (origin_url, depth, title)
        self._meta: dict[str, tuple[str, int, str]] = {}

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9\u00c0-\u024f\u0400-\u04ff]+", text.lower())

    def add_page(self, url: str, origin_url: str, depth: int, title: str, text: str):
        tokens = self._tokenize(title + " " + text)
        if not tokens:
            return
        tf: dict[str, int] = defaultdict(int)
        for t in tokens:
            tf[t] += 1
        title_tokens = set(self._tokenize(title))
        with self._lock:
            self._meta[url] = (origin_url, depth, title)
            for word, count in tf.items():
                score = count / len(tokens)
                # title match boost
                if word in title_tokens:
                    score *= 3.0
                self._index[word][url] = score

    def search(self, query: str, limit: int = 50) -> list[dict]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        url_scores: dict[str, float] = defaultdict(float)
        with self._lock:
            for token in query_tokens:
                if token in self._index:
                    for url, score in self._index[token].items():
                        url_scores[url] += score
        results = sorted(url_scores.items(), key=lambda x: -x[1])[:limit]
        out = []
        with self._lock:
            for url, score in results:
                origin_url, depth, title = self._meta.get(url, ("", 0, ""))
                out.append({
                    "relevant_url": url,
                    "origin_url": origin_url,
                    "depth": depth,
                    "title": title,
                    "score": round(score, 6),
                })
        return out

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._meta)


# ---------------------------------------------------------------------------
# Crawler engine
# ---------------------------------------------------------------------------
class CrawlerEngine:
    """Concurrent web crawler with back pressure, persistence, and live search."""

    # Back-pressure knobs
    MAX_QUEUE_DEPTH = 10_000
    MAX_CONCURRENT_WORKERS = 8
    RATE_LIMIT_DELAY = 0.15  # seconds between requests per worker
    REQUEST_TIMEOUT = 10  # seconds
    MAX_PAGE_SIZE = 2 * 1024 * 1024  # 2 MB

    def __init__(self, db_path: str = "crawler.db"):
        self.store = PersistenceStore(db_path)
        self.index = InvertedIndex()

        # Thread-safe structures
        self._task_queue: queue.Queue[CrawlTask] = queue.Queue(maxsize=self.MAX_QUEUE_DEPTH)
        self._visited: set[str] = set()
        self._visited_lock = threading.Lock()

        self._workers: list[threading.Thread] = []
        self._running = threading.Event()
        self._paused = threading.Event()
        self._paused.set()  # not paused

        # Metrics
        self._metrics_lock = threading.Lock()
        self._pages_crawled = 0
        self._pages_errors = 0
        self._backpressure_hits = 0
        self._current_jobs: dict[str, CrawlJob] = {}

        # Resume: load existing state
        self._load_persisted_state()

    def _load_persisted_state(self):
        """Load visited set and index from DB for resume support."""
        self._visited = self.store.load_visited()
        pages = self.store.get_all_pages()
        for p in pages:
            self.index.add_page(p["url"], p["origin_url"], p["depth"], p["title"] or "", p["text"] or "")
        self._pages_crawled = len(pages)
        logger.info(f"Loaded {len(self._visited)} visited URLs and {len(pages)} indexed pages from DB")

        # Restore queue
        queued = self.store.load_queue_items()
        for task in queued:
            try:
                self._task_queue.put_nowait(task)
            except queue.Full:
                break
        if queued:
            logger.info(f"Restored {min(len(queued), self.MAX_QUEUE_DEPTH)} tasks to queue")

    def _normalize_url(self, url: str) -> str:
        """Basic URL normalization."""
        url, _ = urldefrag(url)
        parsed = urlparse(url)
        # Remove trailing slash for consistency (but keep for root)
        path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"
        
    def _fetch_page(self, url: str) -> Optional[str]:
        """Fetch a page using urllib (stdlib). Returns HTML string or None."""
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "ITU-Crawler/1.0 (Educational Project; +https://github.com)",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
                },
            )
            with urlopen(req, timeout=self.REQUEST_TIMEOUT) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    return None
                data = resp.read(self.MAX_PAGE_SIZE)
                # Try to detect encoding
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                try:
                    return data.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    return data.decode("utf-8", errors="replace")
        except (HTTPError, URLError, OSError, Exception) as e:
            logger.debug(f"Fetch error for {url}: {e}")
            return None

    def _worker(self):
        """Worker thread: pull tasks, fetch, parse, enqueue children."""
        while self._running.is_set():
            self._paused.wait()  # block if paused
            try:
                task = self._task_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            url = self._normalize_url(task.url)

            # Double-check visited
            with self._visited_lock:
                if url in self._visited:
                    self._task_queue.task_done()
                    self.store.remove_queue_item(task.url)
                    continue
                self._visited.add(url)

            self.store.mark_visited(url)

            # Rate limiting
            time.sleep(self.RATE_LIMIT_DELAY)

            html = self._fetch_page(url)
            if html is None:
                with self._metrics_lock:
                    self._pages_errors += 1
                self._task_queue.task_done()
                self.store.remove_queue_item(task.url)
                continue

            title, text, links = parse_html(html, url)

            # Save to DB and in-memory index
            rec = PageRecord(
                url=url,
                origin_url=task.origin_url,
                depth=task.depth,
                title=title,
                text=text[:10000],  # truncate for storage
                links=links,
                crawled_at=time.time(),
            )
            self.store.save_page(rec)
            self.index.add_page(url, task.origin_url, task.depth, title, text[:10000])

            with self._metrics_lock:
                self._pages_crawled += 1
                if task.origin_url in self._current_jobs:
                    self._current_jobs[task.origin_url].pages_crawled += 1

            # Enqueue child links if within depth
            if task.depth < task.max_depth:
                children_to_save = []
                for link in links:
                    norm_link = self._normalize_url(link)
                    with self._visited_lock:
                        if norm_link in self._visited:
                            continue
                    child_task = CrawlTask(
                        url=norm_link,
                        origin_url=task.origin_url,
                        depth=task.depth + 1,
                        max_depth=task.max_depth,
                    )
                    try:
                        self._task_queue.put_nowait(child_task)
                        children_to_save.append(child_task)
                    except queue.Full:
                        with self._metrics_lock:
                            self._backpressure_hits += 1
                        logger.debug("Back pressure: queue full, dropping link")
                        break

                if children_to_save:
                    self.store.save_queue_items(children_to_save)
                    with self._metrics_lock:
                        if task.origin_url in self._current_jobs:
                            self._current_jobs[task.origin_url].pages_queued += len(children_to_save)

            self.store.remove_queue_item(task.url)
            self._task_queue.task_done()

    def start_workers(self):
        """Start background worker threads."""
        if self._running.is_set():
            return
        self._running.set()
        for i in range(self.MAX_CONCURRENT_WORKERS):
            t = threading.Thread(target=self._worker, name=f"crawler-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        logger.info(f"Started {self.MAX_CONCURRENT_WORKERS} crawler workers")

    def stop_workers(self):
        """Signal workers to stop."""
        self._running.clear()
        self._paused.set()  # unblock paused workers so they can exit
        for t in self._workers:
            t.join(timeout=5)
        self._workers.clear()
        # Persist remaining queue
        remaining = []
        while not self._task_queue.empty():
            try:
                remaining.append(self._task_queue.get_nowait())
            except queue.Empty:
                break
        if remaining:
            self.store.save_queue_items(remaining)
        logger.info("All workers stopped")

    def pause(self):
        self._paused.clear()
        for job in self._current_jobs.values():
            job.status = "paused"
            self.store.save_job(job)
        logger.info("Crawler paused")

    def resume(self):
        self._paused.set()
        for job in self._current_jobs.values():
            if job.status == "paused":
                job.status = "running"
                self.store.save_job(job)
        logger.info("Crawler resumed")

    # -- Public API --

    def index_url(self, origin: str, k: int):
        """Start crawling from `origin` to depth `k`."""
        self.start_workers()
        origin = self._normalize_url(origin)
        job = CrawlJob(origin_url=origin, max_depth=k)
        with self._metrics_lock:
            self._current_jobs[origin] = job
        self.store.save_job(job)

        task = CrawlTask(url=origin, origin_url=origin, depth=0, max_depth=k)
        with self._visited_lock:
            if origin not in self._visited:
                try:
                    self._task_queue.put_nowait(task)
                    self.store.save_queue_items([task])
                    with self._metrics_lock:
                        job.pages_queued += 1
                except queue.Full:
                    logger.warning("Queue full, cannot start crawl")
                    return False
        logger.info(f"Crawl started: origin={origin}, depth={k}")
        return True

    def search_query(self, query: str, limit: int = 50) -> list[dict]:
        """Search indexed pages. Safe to call while indexer is running."""
        return self.index.search(query, limit)

    def get_metrics(self) -> dict:
        """Return current system metrics for dashboard."""
        with self._metrics_lock:
            jobs = []
            for origin, job in self._current_jobs.items():
                jobs.append({
                    "origin_url": job.origin_url,
                    "max_depth": job.max_depth,
                    "status": job.status,
                    "pages_crawled": job.pages_crawled,
                    "pages_queued": job.pages_queued,
                    "elapsed": round(time.time() - job.started_at, 1),
                })
            return {
                "total_pages_crawled": self._pages_crawled,
                "total_pages_indexed": self.index.size,
                "total_errors": self._pages_errors,
                "queue_depth": self._task_queue.qsize(),
                "max_queue_depth": self.MAX_QUEUE_DEPTH,
                "backpressure_hits": self._backpressure_hits,
                "backpressure_active": self._task_queue.qsize() >= self.MAX_QUEUE_DEPTH * 0.9,
                "workers": self.MAX_CONCURRENT_WORKERS,
                "is_running": self._running.is_set(),
                "is_paused": not self._paused.is_set(),
                "jobs": jobs,
            }

    def reset_all(self):
        """Reset all state."""
        self.stop_workers()
        self.store.reset()
        self._visited.clear()
        self._pages_crawled = 0
        self._pages_errors = 0
        self._backpressure_hits = 0
        self._current_jobs.clear()
        self.index = InvertedIndex()
        # Clear the queue
        while not self._task_queue.empty():
            try:
                self._task_queue.get_nowait()
            except queue.Empty:
                break
        logger.info("System reset complete")
