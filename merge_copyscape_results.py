#!/usr/bin/env python3
"""Merge Copyscape result CSV files into a cumulative output file.

By default, this script reads all files matching `copyscape_results_*.csv`
in the current folder, appends their rows, and writes `output_cumulativo.csv`.

It also normalizes legacy files that used `hashtag` instead of `id_hash`.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


OUTPUT_HEADER = [
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge all copyscape result files matching a pattern "
            "into a single cumulative CSV."
        )
    )
    parser.add_argument(
        "--input-glob",
        default="copyscape_results_*.csv",
        help="Glob pattern for input files (default: copyscape_results_*.csv)",
    )
    parser.add_argument(
        "--output",
        default="output_cumulativo.csv",
        help="Output CSV path (default: output_cumulativo.csv)",
    )
    return parser.parse_args()


def _pick_field(row: dict[str, str], names: list[str]) -> str:
    for name in names:
        value = (row.get(name) or "").strip()
        if value:
            return value
    return ""


def normalize_row(row: dict[str, str]) -> list[str]:
    data = _pick_field(row, ["data"])
    codice = _pick_field(row, ["codice"])

    # Backward compatibility: old files used `hashtag`.
    id_hash = _pick_field(row, ["id_hash", "hashtag"])

    url = _pick_field(row, ["URL", "url", "query_url"])
    title = _pick_field(row, ["Title", "title"])
    min_words = _pick_field(row, ["Min Words Matched", "minwordsmatched"])
    percent = _pick_field(row, ["Percent Matched", "percentmatched"])
    words_full = _pick_field(row, ["Words Matched (Full)", "wordsmatch"])
    snippet = _pick_field(row, ["Text Snippet", "textsnippet"])
    view_url = _pick_field(row, ["View URL", "viewurl"])

    return [
        data,
        codice,
        id_hash,
        url,
        title,
        min_words,
        percent,
        words_full,
        snippet,
        view_url,
    ]


def read_rows_from_file(file_path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return rows

        for raw_row in reader:
            normalized = normalize_row(raw_row)

            # Keep only rows that have at least URL or title-like payload.
            if not normalized[3] and not normalized[4]:
                continue
            rows.append(normalized)

    return rows


def merge_files(input_glob: str, output_path: Path) -> tuple[int, int]:
    base_dir = Path.cwd()
    input_files = sorted(base_dir.glob(input_glob))

    if not input_files:
        raise FileNotFoundError(
            f"Nessun file trovato con pattern '{input_glob}' in {base_dir}"
        )

    merged_rows: list[list[str]] = []
    for input_file in input_files:
        merged_rows.extend(read_rows_from_file(input_file))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_HEADER)
        for idx, row in enumerate(merged_rows, start=1):
            writer.writerow([idx, *row])

    return len(input_files), len(merged_rows)


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)

    try:
        files_count, rows_count = merge_files(args.input_glob, output_path)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    except OSError as exc:
        print(f"Errore I/O: {exc}")
        return 1

    print(f"File processati: {files_count}")
    print(f"Righe scritte: {rows_count}")
    print(f"Output: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
