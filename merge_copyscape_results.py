#!/usr/bin/env python3
"""Merge Copyscape result CSV files into a cumulative output file.

By default, this script reads all files matching
`copyscape_results_*_date.csv` (i risultati arricchiti da check_date.py) in
the current folder, keeps only the rows whose `before_cutoff` field is `no`
(pagine non precedenti alla data limite), appends them, and writes
`output_cumulativo.csv`.

Per evitare duplicati quando lo script copyscape_check.py viene eseguito piu'
volte nello stesso giorno per lo stesso articolo, le righe con lo stesso
`id_hash`, la stessa data (giorno/mese/anno nel campo "data") e lo stesso
`URL` vengono scartate dopo la prima occorrenza incontrata (i file vengono
elaborati in ordine di nome, quindi in ordine cronologico).

It also normalizes legacy files that used `hashtag` instead of `id_hash`.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from copyscape_common import RESULT_COLUMNS

DAY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge all copyscape result files matching a pattern "
            "into a single cumulative CSV."
        )
    )
    parser.add_argument(
        "--input-glob",
        default="copyscape_results_*_date.csv",
        help="Glob pattern for input files (default: copyscape_results_*_date.csv)",
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
            # Solo le pagine non precedenti alla data limite (check_date.py).
            if (raw_row.get("before_cutoff") or "").strip().lower() != "no":
                continue
            normalized = normalize_row(raw_row)

            # Keep only rows that have at least URL or title-like payload.
            if not normalized[3] and not normalized[4]:
                continue
            rows.append(normalized)

    return rows


def extract_day_key(data_value: str) -> str:
    """Return the day/month/year portion (YYYY-MM-DD) of a "data" value."""
    match = DAY_RE.match(data_value.strip())
    return match.group(1) if match else data_value.strip()


def merge_files(input_glob: str, output_path: Path) -> tuple[int, int, int]:
    base_dir = Path.cwd()
    input_files = sorted(base_dir.glob(input_glob))

    if not input_files:
        raise FileNotFoundError(
            f"Nessun file trovato con pattern '{input_glob}' in {base_dir}"
        )

    merged_rows: list[list[str]] = []
    seen_id_hash_day_url: set[tuple[str, str, str]] = set()
    skipped_duplicates = 0
    for input_file in input_files:
        for row in read_rows_from_file(input_file):
            id_hash = row[2]
            url = row[3]
            day_key = extract_day_key(row[0])
            if id_hash and day_key and url:
                key = (id_hash, day_key, url)
                if key in seen_id_hash_day_url:
                    # Stesso articolo (id_hash) con lo stesso URL gia'
                    # presente nel cumulativo con la stessa data
                    # (giorno/mese/anno): non aggiungere.
                    skipped_duplicates += 1
                    continue
                seen_id_hash_day_url.add(key)
            merged_rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(RESULT_COLUMNS)
        for idx, row in enumerate(merged_rows, start=1):
            writer.writerow([idx, *row])

    return len(input_files), len(merged_rows), skipped_duplicates


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)

    try:
        files_count, rows_count, skipped_count = merge_files(args.input_glob, output_path)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    except OSError as exc:
        print(f"Errore I/O: {exc}")
        return 1

    print(f"File processati: {files_count}")
    print(f"Righe scritte: {rows_count}")
    print(
        "Righe duplicate scartate (stesso id_hash, stessa data e stesso URL): "
        f"{skipped_count}"
    )
    print(f"Output: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
