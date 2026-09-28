
#!/usr/bin/env python3
"""Load output_cumulativo.csv into a local SQLite database.

Ad ogni esecuzione lo script legge l'intero file CSV cumulativo e confronta
il contenuto di ciascuna riga con quanto gia' presente nel database: solo le
righe mancanti vengono aggiunte (append incrementale), evitando di importare
piu' volte le stesse righe ad ogni nuova esecuzione.

Il database SQLite (e ogni file ad esso collegato) viene salvato nella
cartella "db" accanto a questo script.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
from pathlib import Path

DB_DIR_NAME = "db"
DB_FILE_NAME = "copyscape_results.db"
TABLE_NAME = "copyscape_results"

CSV_COLUMNS = [
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

# Colonne (in ordine) usate per calcolare l'hash di deduplicazione di una
# riga. "Index" viene escluso perche' e' solo una numerazione progressiva
# che puo' cambiare da un merge all'altro e non identifica il contenuto
# effettivo della riga.
HASH_COLUMNS = CSV_COLUMNS[1:]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Carica output_cumulativo.csv in un database SQLite, aggiungendo "
            "in append solo le righe non gia' presenti."
        )
    )
    parser.add_argument(
        "--input",
        default="output_cumulativo.csv",
        help="File CSV cumulativo da caricare (default: output_cumulativo.csv)",
    )
    parser.add_argument(
        "--db-dir",
        default=DB_DIR_NAME,
        help=f"Cartella dove salvare il database SQLite (default: {DB_DIR_NAME})",
    )
    parser.add_argument(
        "--db-name",
        default=DB_FILE_NAME,
        help=f"Nome del file del database SQLite (default: {DB_FILE_NAME})",
    )
    return parser.parse_args()


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_index TEXT,
            data TEXT,
            codice TEXT,
            id_hash TEXT,
            url TEXT,
            title TEXT,
            min_words_matched TEXT,
            percent_matched TEXT,
            words_matched_full TEXT,
            text_snippet TEXT,
            view_url TEXT,
            row_hash TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.commit()


def compute_row_hash(row: dict[str, str]) -> str:
    """Calcola un hash stabile del contenuto di una riga (esclude Index)."""
    normalized = "|".join((row.get(col) or "").strip() for col in HASH_COLUMNS)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def read_csv_rows(input_file: Path) -> list[dict[str, str]]:
    if not input_file.exists():
        raise FileNotFoundError(f"File non trovato: {input_file}")

    with input_file.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return []
        return list(reader)


def load_rows(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        row_hash = compute_row_hash(row)
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE_NAME} (
                source_index, data, codice, id_hash, url, title,
                min_words_matched, percent_matched, words_matched_full,
                text_snippet, view_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("Index", ""),
                row.get("data", ""),
                row.get("codice", ""),
                row.get("id_hash", ""),
                row.get("URL", ""),
                row.get("Title", ""),
                row.get("Min Words Matched", ""),
                row.get("Percent Matched", ""),
                row.get("Words Matched (Full)", ""),
                row.get("Text Snippet", ""),
                row.get("View URL", ""),
                row_hash,
            ),
        )
        if cursor.rowcount:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped


def main() -> int:
    args = parse_args()
    input_file = Path(args.input)
    db_dir = Path(args.db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / args.db_name

    try:
        rows = read_csv_rows(input_file)
    except FileNotFoundError as exc:
        print(f"Errore: {exc}")
        return 1

    if not rows:
        print(f"Nessuna riga trovata in {input_file}.")
        return 0

    conn = sqlite3.connect(db_path)
    try:
        ensure_table(conn)
        inserted, skipped = load_rows(conn, rows)
    finally:
        conn.close()

    print(f"Righe lette dal CSV: {len(rows)}")
    print(f"Righe inserite (nuove): {inserted}")
    print(f"Righe gia' presenti nel db (saltate): {skipped}")
    print(f"Database: {db_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
