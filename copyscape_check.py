#!/usr/bin/env python3
"""Copyscape text plagiarism check utility.

This script reads the article rows (with the extracted "testo" column,
plus "id_hash" and "codice") from a CSV file into a pandas DataFrame,
calls Copyscape for each row's text one at a time, writes an aggregated
results CSV, and writes a per-call log CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


COPYSCAPE_API_URL = "https://www.copyscape.com/api/"

# ============================================================================
# CONFIGURATION: Set your Copyscape Premium credentials here
# ============================================================================
COPYSCAPE_USERNAME = "vincpatruno2"
COPYSCAPE_API_KEY = "63dvir4atdjc8qt1"
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read article texts from a CSV file, query Copyscape one text at a "
            "time, and export aggregated results + call log CSV files."
        )
    )
    parser.add_argument(
        "--input",
        default="get_print_reviews.csv",
        help=(
            "Input CSV file with columns including id_hash, codice, link and "
            "testo (default: get_print_reviews.csv)"
        ),
    )
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
        default="facebook.com,instagram.com,threads.com,istat.it,x.com,linkedin.com",
        help=(
            "Comma-separated domains to ignore "
            "(default: facebook.com,instagram.com)"
        ),
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
        help="Results CSV output path (default: copyscape_results_<timestamp>.csv)",
    )
    parser.add_argument(
        "--log-output",
        default=None,
        help="Call log CSV output path (default: copyscape_log.csv)",
    )
    return parser.parse_args()


def build_base_params(args: argparse.Namespace) -> dict[str, str]:
    if not args.username or not args.api_key:
        raise ValueError(
            "Missing credentials. Provide --username/--api-key or set "
            "COPYSCAPE_USERNAME and COPYSCAPE_API_KEY."
        )

    if not 0 <= args.full_comparisons <= 10:
        raise ValueError("--full-comparisons must be between 0 and 10.")

    params: dict[str, str] = {
        "u": args.username,
        "k": args.api_key,
        "o": "csearch",
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


def call_copyscape(params: dict[str, str], method: str = "POST") -> dict[str, Any]:
    """Call the Copyscape API. Text searches require POST (the "t" text
    parameter can exceed the length allowed in a GET query string)."""
    try:
        if method == "GET":
            query = urlencode(params)
            request_url = f"{COPYSCAPE_API_URL}?{query}"
            with urlopen(request_url, timeout=60) as response:
                body = response.read().decode("utf-8")
        else:
            data = urlencode(params).encode("utf-8")
            request = Request(COPYSCAPE_API_URL, data=data, method="POST")
            with urlopen(request, timeout=60) as response:
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


def extract_result_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract detailed result data from Copyscape payload."""
    results = payload.get("result", [])
    if not isinstance(results, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        row: dict[str, Any] = {
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


def read_input_rows(input_file: Path) -> list[dict[str, str]]:
    if not input_file.exists():
        raise FileNotFoundError(f"Input file non trovato: {input_file}")

    df = pd.read_csv(input_file, dtype=str, keep_default_na=False)

    normalized_map = {name.strip().lower(): name for name in df.columns}
    link_col = normalized_map.get("link") or normalized_map.get("url")
    id_hash_col = normalized_map.get("id_hash")
    codice_col = normalized_map.get("codice")
    testo_col = normalized_map.get("testo")

    if not id_hash_col or not testo_col:
        raise ValueError(
            "Il file input deve contenere almeno le colonne 'id_hash' e 'testo'."
        )

    rows: list[dict[str, str]] = []
    for _, record in df.iterrows():
        testo = (record.get(testo_col, "") or "").strip()
        id_hash = (record.get(id_hash_col, "") or "").strip()
        link = (record.get(link_col, "") or "").strip() if link_col else ""
        codice = (record.get(codice_col, "") or "").strip() if codice_col else ""
        if testo and id_hash:
            rows.append({"testo": testo, "link": link, "id_hash": id_hash, "codice": codice})

    return rows


def save_aggregated_results(rows: list[dict[str, Any]], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Index",
                "data",
                "codice",
                "id_hash",
                "URL",
                "Title",
                "Min Words Matched",
                "Percent Matched",
                "Words Matched (Full)",
                "Text Snippet",
                "View URL",
            ]
        )

        for idx, row in enumerate(rows, start=1):
            writer.writerow(
                [
                    idx,
                    row["date"],
                    row["codice"],
                    row["id_hash"],
                    row["url"],
                    row["title"],
                    row["minwordsmatched"],
                    row["percentmatched"],
                    row["wordsmatch"],
                    row["textsnippet"],
                    row["viewurl"],
                ]
            )


def save_call_log(rows: list[dict[str, Any]], log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    write_header = not log_file.exists() or log_file.stat().st_size == 0
    with log_file.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["data", "query url", "totale risultati", "costo in USD", "query words"])
        for row in rows:
            writer.writerow(
                [
                    row["date"],
                    row["query_url"],
                    row["total_results"],
                    row["cost_usd"],
                    row["query_words"],
                ]
            )


def run_for_single_text(
    base_params: dict[str, str],
    text: str,
    link: str,
    id_hash: str,
    codice: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = dict(base_params)
    params["t"] = text
    params["e"] = "UTF-8"
    payload = call_copyscape(params, method="POST")
    call_date = datetime.now().isoformat()

    result_rows = extract_result_rows(payload)
    output_rows: list[dict[str, Any]] = []
    for row in result_rows:
        output_rows.append(
            {
                "url": row.get("url", ""),
                "query_url": link,
                "date": call_date,
                "id_hash": id_hash,
                "codice": codice,
                "title": row.get("title", ""),
                "minwordsmatched": row.get("minwordsmatched", ""),
                "percentmatched": row.get("percentmatched", ""),
                "wordsmatch": row.get("wordsmatch", ""),
                "textsnippet": row.get("textsnippet", ""),
                "viewurl": row.get("viewurl", ""),
            }
        )

    log_row = {
        "date": call_date,
        "query_url": link,
        "total_results": payload.get("count", len(result_rows)),
        "cost_usd": payload.get("cost", ""),
        "query_words": payload.get("querywords", ""),
    }
    return output_rows, log_row


def main() -> int:
    args = parse_args()
    input_file = Path(args.input)

    try:
        base_params = build_base_params(args)
        input_rows = read_input_rows(input_file)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not input_rows:
        print("Nessuna riga con testo valido trovata nel file input.", file=sys.stderr)
        return 1

    all_result_rows: list[dict[str, Any]] = []
    call_log_rows: list[dict[str, Any]] = []

    for item in input_rows:
        try:
            result_rows, log_row = run_for_single_text(
                base_params=base_params,
                text=item["testo"],
                link=item.get("link", ""),
                id_hash=item["id_hash"],
                codice=item.get("codice", ""),
            )
            all_result_rows.extend(result_rows)
            call_log_rows.append(log_row)
        except RuntimeError as exc:
            print(f"Errore su id_hash {item['id_hash']}: {exc}", file=sys.stderr)
            call_log_rows.append(
                {
                    "date": datetime.now().isoformat(),
                    "query_url": item.get("link", ""),
                    "total_results": "ERROR",
                    "cost_usd": "",
                    "query_words": "",
                }
            )

    # Generate output filenames if not provided
    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"copyscape_results_{timestamp}.csv"
    if args.log_output is None:
        args.log_output = "copyscape_log.csv"

    output_path = Path(args.output)
    log_path = Path(args.log_output)

    try:
        save_aggregated_results(all_result_rows, output_path)
        save_call_log(call_log_rows, log_path)
    except OSError as exc:
        print(f"Error saving CSV: {exc}", file=sys.stderr)
        return 1

    print(f"Input rows processed: {len(input_rows)}")
    print(f"Total result rows written: {len(all_result_rows)}")
    print(f"Results saved to: {output_path.resolve()}")
    print(f"Log saved to: {log_path.resolve()}")

    if args.raw_json:
        print("\n--raw-json e disponibile solo in modalita testo singolo (non usata qui).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
