#!/usr/bin/env python3
"""Copyscape URL plagiarism check utility.

This script queries the Copyscape Premium API and prints the list of URLs
where matching content was found for an input source URL.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen


COPYSCAPE_API_URL = "https://www.copyscape.com/api/"

# ============================================================================
# CONFIGURATION: Set your Copyscape Premium credentials here
# ============================================================================
COPYSCAPE_USERNAME = "your-copyscape-username"
COPYSCAPE_API_KEY = "your-copyscape-api-key"
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check a source URL with Copyscape and return URLs where "
            "matching content appears."
        )
    )
    parser.add_argument("url", help="Source web page URL to check")
    parser.add_argument(
        "--username",
        default=os.getenv("COPYSCAPE_USERNAME") or COPYSCAPE_USERNAME,
        help="Copyscape username (env: COPYSCAPE_USERNAME, or edit script constants)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("COPYSCAPE_API_KEY") or COPYSCAPE_API_KEY,
        help="Copyscape API key (env: COPYSCAPE_API_KEY, or edit script constants)",
    )
    parser.add_argument(
        "--full-comparisons",
        type=int,
        default=0,
        help="Number of full comparisons to request (0-10, default: 0)",
    )
    parser.add_argument(
        "--ignore-sites",
        default=None,
        help="Comma-separated domains to ignore (e.g. site1.com,site2.com)",
    )
    parser.add_argument(
        "--spend-limit",
        type=float,
        default=None,
        help="Optional max spend for this request in dollars (e.g. 0.50)",
    )
    parser.add_argument(
        "--example-test",
        action="store_true",
        help="Run Copyscape test search (x=1, not charged)",
    )
    parser.add_argument(
        "--raw-json",
        action="store_true",
        help="Print full JSON response after URL list",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="CSV output file path (default: copyscape_results_<timestamp>.csv)",
    )
    return parser.parse_args()


def build_params(args: argparse.Namespace) -> Dict[str, str]:
    if not args.username or not args.api_key:
        raise ValueError(
            "Missing credentials. Provide --username/--api-key or set "
            "COPYSCAPE_USERNAME and COPYSCAPE_API_KEY."
        )

    if not 0 <= args.full_comparisons <= 10:
        raise ValueError("--full-comparisons must be between 0 and 10.")

    params: Dict[str, str] = {
        "u": args.username,
        "k": args.api_key,
        "o": "csearch",
        "q": args.url,
        "f": "json",
        "c": str(args.full_comparisons),
    }

    if args.ignore_sites:
        params["i"] = args.ignore_sites
    if args.spend_limit is not None:
        params["l"] = f"{args.spend_limit:.2f}"
    if args.example_test:
        params["x"] = "1"

    return params


def call_copyscape(params: Dict[str, str]) -> Dict[str, Any]:
    query = urlencode(params)
    request_url = f"{COPYSCAPE_API_URL}?{query}"

    try:
        with urlopen(request_url, timeout=60) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP error from Copyscape: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while calling Copyscape: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from Copyscape: {exc}") from exc

    if "error" in payload:
        raise RuntimeError(f"Copyscape API error: {payload['error']}")

    return payload


def extract_result_urls(payload: Dict[str, Any]) -> List[str]:
    results = payload.get("result", [])
    if not isinstance(results, list):
        return []

    urls: List[str] = []
    for item in results:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            urls.append(item["url"])
    return urls


def extract_result_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract detailed result data for CSV export."""
    results = payload.get("result", [])
    if not isinstance(results, list):
        return []

    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue

        row: Dict[str, Any] = {
            "index": idx,
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "minwordsmatched": item.get("minwordsmatched", ""),
            "textsnippet": item.get("textsnippet", ""),
            "wordsmatch": item.get("wordsmatch", ""),
            "percentmatched": item.get("percentmatched", ""),
            "viewurl": item.get("viewurl", ""),
        }
        rows.append(row)
    return rows


def save_results_csv(
    rows: List[Dict[str, Any]], output_file: str, query_url: str, payload: Dict[str, Any]
) -> None:
    """Save result rows to CSV file."""
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Write metadata header
        writer.writerow(["Copyscape URL Search Results"])
        writer.writerow(["Query URL:", query_url])
        writer.writerow(["Date:", datetime.now().isoformat()])
        writer.writerow(["Total Results:", payload.get("count", len(rows))])
        writer.writerow(["Cost (USD):", payload.get("cost", "")])
        writer.writerow(["Query Words:", payload.get("querywords", "")])
        writer.writerow([])

        # Write results table
        if rows:
            fieldnames = [
                "Index",
                "URL",
                "Title",
                "Min Words Matched",
                "Percent Matched",
                "Words Matched (Full)",
                "Text Snippet",
                "View URL",
            ]
            writer.writerow(fieldnames)
            for row in rows:
                writer.writerow(
                    [
                        row["index"],
                        row["url"],
                        row["title"],
                        row["minwordsmatched"],
                        row["percentmatched"],
                        row["wordsmatch"],
                        row["textsnippet"],
                        row["viewurl"],
                    ]
                )


def main() -> int:
    args = parse_args()

    try:
        params = build_params(args)
        payload = call_copyscape(params)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    result_urls = extract_result_urls(payload)
    result_rows = extract_result_rows(payload)

    print(f"Query URL: {payload.get('query', args.url)}")
    print(f"Results found: {payload.get('count', len(result_urls))}")
    print("Matched URLs:")

    if result_urls:
        for idx, url in enumerate(result_urls, start=1):
            print(f"{idx}. {url}")
    else:
        print("No matching URLs found.")

    # Generate CSV filename if not provided
    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"copyscape_results_{timestamp}.csv"

    # Save to CSV
    try:
        save_results_csv(result_rows, args.output, payload.get("query", args.url), payload)
        print(f"\nResults saved to: {Path(args.output).resolve()}")
    except OSError as exc:
        print(f"Error saving CSV: {exc}", file=sys.stderr)
        return 1

    if args.raw_json:
        print("\nRaw JSON response:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
