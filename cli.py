#!/usr/bin/env python3
"""
CLI interface for the ITU Web Crawler.
Allows indexing and search from the command line.
"""

import argparse
import sys
import time
import signal
from crawler import CrawlerEngine


def print_banner():
    print("""
╔═══════════════════════════════════════════════════╗
║        🕷️  ITU Web Crawler — CLI Mode              ║
╚═══════════════════════════════════════════════════╝
""")


def cmd_index(engine: CrawlerEngine, url: str, depth: int, wait: bool):
    """Start crawling and optionally wait for completion."""
    print(f"▶ Starting crawl: {url} (depth={depth})")
    engine.index_url(url, depth)

    if wait:
        print("  Waiting for crawl to complete (Ctrl+C to stop)...\n")
        try:
            last_count = -1
            stable_rounds = 0
            while True:
                time.sleep(2)
                m = engine.get_metrics()
                crawled = m["total_pages_crawled"]
                q = m["queue_depth"]
                bp = m["backpressure_hits"]
                print(
                    f"\r  Pages: {crawled} | Queue: {q} | "
                    f"Errors: {m['total_errors']} | BP hits: {bp}",
                    end="", flush=True,
                )
                if q == 0 and crawled == last_count:
                    stable_rounds += 1
                    if stable_rounds >= 3:
                        print("\n\n✓ Crawl complete!")
                        break
                else:
                    stable_rounds = 0
                last_count = crawled
        except KeyboardInterrupt:
            print("\n\n⏹ Stopping...")
            engine.stop_workers()
    else:
        print("  Crawl running in background. Use 'search' or 'metrics' commands.\n")


def cmd_search(engine: CrawlerEngine, query: str, limit: int):
    """Search indexed pages."""
    results = engine.search_query(query, limit)
    if not results:
        print(f"No results for '{query}'\n")
        return
    print(f"\nFound {len(results)} result(s) for '{query}':\n")
    print(f"{'#':<4} {'Score':<10} {'Depth':<6} {'Title / URL'}")
    print("─" * 80)
    for i, r in enumerate(results, 1):
        title = r["title"][:50] if r["title"] else "(no title)"
        print(f"{i:<4} {r['score']:<10.4f} {r['depth']:<6} {title}")
        print(f"     URL:    {r['relevant_url']}")
        print(f"     Origin: {r['origin_url']}")
        print()


def cmd_metrics(engine: CrawlerEngine):
    """Display current metrics."""
    m = engine.get_metrics()
    print(f"""
┌─ System Metrics ─────────────────────────────────┐
│ Pages crawled:    {m['total_pages_crawled']:<10}                    │
│ Pages indexed:    {m['total_pages_indexed']:<10}                    │
│ Queue depth:      {m['queue_depth']:<6} / {m['max_queue_depth']:<10}            │
│ Errors:           {m['total_errors']:<10}                    │
│ BP hits:          {m['backpressure_hits']:<10}                    │
│ Workers:          {m['workers']:<10}                    │
│ Running:          {str(m['is_running']):<10}                    │
│ Paused:           {str(m['is_paused']):<10}                    │
│ BP active:        {str(m['backpressure_active']):<10}                    │
└──────────────────────────────────────────────────┘""")
    if m["jobs"]:
        print("\n  Active Jobs:")
        for j in m["jobs"]:
            print(f"    • {j['origin_url']} — depth={j['max_depth']}, "
                  f"crawled={j['pages_crawled']}, status={j['status']}, "
                  f"elapsed={j['elapsed']}s")
    print()


def cmd_interactive(engine: CrawlerEngine):
    """Interactive REPL mode."""
    print_banner()
    print("Commands: index <url> [depth], search <query>, metrics, reset, quit\n")

    def handle_sigint(sig, frame):
        print("\nStopping workers...")
        engine.stop_workers()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    while True:
        try:
            line = input("crawler> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            engine.stop_workers()
            break

        if not line:
            continue

        parts = line.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "quit" or cmd == "exit":
            engine.stop_workers()
            break
        elif cmd == "index":
            if len(parts) < 2:
                print("Usage: index <url> [depth]")
                continue
            url = parts[1]
            depth = int(parts[2]) if len(parts) > 2 else 2
            cmd_index(engine, url, depth, wait=False)
        elif cmd == "search":
            if len(parts) < 2:
                print("Usage: search <query>")
                continue
            query = " ".join(parts[1:])
            cmd_search(engine, query, 20)
        elif cmd == "metrics":
            cmd_metrics(engine)
        elif cmd == "wait":
            print("Waiting for queue to drain...\n")
            try:
                while True:
                    m = engine.get_metrics()
                    print(f"\r  Queue: {m['queue_depth']} | Crawled: {m['total_pages_crawled']}", end="", flush=True)
                    if m["queue_depth"] == 0:
                        print("\n✓ Queue empty\n")
                        break
                    time.sleep(2)
            except KeyboardInterrupt:
                print()
        elif cmd == "pause":
            engine.pause()
            print("Paused\n")
        elif cmd == "resume":
            engine.resume()
            print("Resumed\n")
        elif cmd == "reset":
            engine.reset_all()
            print("System reset\n")
        else:
            print(f"Unknown command: {cmd}")
            print("Commands: index <url> [depth], search <query>, metrics, pause, resume, wait, reset, quit\n")


def main():
    parser = argparse.ArgumentParser(
        description="ITU Web Crawler & Search Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py interactive
  python cli.py index https://example.com --depth 2 --wait
  python cli.py search "python programming"
  python cli.py metrics
        """,
    )
    parser.add_argument("--db", default="crawler.db", help="SQLite DB path (default: crawler.db)")

    sub = parser.add_subparsers(dest="command")

    # interactive
    sub.add_parser("interactive", help="Interactive REPL mode")

    # index
    idx = sub.add_parser("index", help="Start crawling a URL")
    idx.add_argument("url", help="Origin URL to crawl")
    idx.add_argument("--depth", "-d", type=int, default=2, help="Max crawl depth (default: 2)")
    idx.add_argument("--wait", "-w", action="store_true", help="Wait for crawl to finish")

    # search
    srch = sub.add_parser("search", help="Search indexed pages")
    srch.add_argument("query", help="Search query")
    srch.add_argument("--limit", "-n", type=int, default=20, help="Max results (default: 20)")

    # metrics
    sub.add_parser("metrics", help="Show system metrics")

    # reset
    sub.add_parser("reset", help="Reset all data")

    args = parser.parse_args()

    engine = CrawlerEngine(db_path=args.db)

    if args.command is None or args.command == "interactive":
        cmd_interactive(engine)
    elif args.command == "index":
        cmd_index(engine, args.url, args.depth, args.wait)
        if not args.wait:
            time.sleep(1)
            engine.stop_workers()
    elif args.command == "search":
        cmd_search(engine, args.query, args.limit)
    elif args.command == "metrics":
        cmd_metrics(engine)
    elif args.command == "reset":
        engine.reset_all()
        print("System reset complete")


if __name__ == "__main__":
    main()
