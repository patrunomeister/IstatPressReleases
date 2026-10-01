#!/usr/bin/env python3
"""Copyscape text plagiarism check utility.

This script reads the article rows (with the extracted "testo" column,
plus "id_hash" and "codice") from a CSV file into a pandas DataFrame,
calls Copyscape for each row's text one at a time, writes an aggregated
results CSV, and writes a per-call log CSV.

La logica di chiamata a Copyscape (parametri, richiesta HTTP, salvataggio
CSV) e la lettura del file di input sono condivise con copyscape_scheduler.py
tramite il modulo copyscape_common.py.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from copyscape_common import (
    add_credential_args,
    add_request_args,
    build_base_params,
    read_press_release_rows,
    run_for_single_text,
    save_aggregated_results,
    save_call_log,
)


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
    add_credential_args(parser)
    add_request_args(parser)
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


def main() -> int:
    args = parse_args()
    input_file = Path(args.input)

    try:
        base_params = build_base_params(args)
        input_rows = read_press_release_rows(input_file)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
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
