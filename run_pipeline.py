#!/usr/bin/env python3
"""Orchestratore dell'intera pipeline di monitoraggio comunicati stampa.

Esegue in sequenza, nella stessa cartella dello script, tutti gli step
della pipeline:

    1. rss_latest_hash.py        -> aggiorna rss_latest_hash_output.csv
    2. get_print_reviews.py      -> aggiorna get_print_reviews.csv (+ testo)
    3. copyscape_scheduler.py    -> esegue i controlli Copyscape in scadenza
    4. merge_copyscape_results.py -> aggiorna output_cumulativo.csv
    5. upload_sqlite.py          -> aggiorna db/copyscape_results.db

Ogni step viene eseguito con lo stesso interprete Python in uso
(sys.executable), cosi' da usare automaticamente il venv/ambiente corrente.
Se uno step fallisce (return code diverso da zero), l'esecuzione si ferma
subito e viene riportato l'errore, per evitare di propagare dati
incompleti/incoerenti agli step successivi.

Uso tipico (una sola pianificazione, ad es. una volta al giorno tramite
Task Scheduler di Windows o cron):

    python run_pipeline.py

Per passare argomenti extra a un singolo step (ad es. forzare i controlli
Copyscape ignorando la pianificazione), usare i relativi flag CLI:

    python run_pipeline.py --scheduler-args "--ignore-schedule --example-test"

Per limitare i controlli Copyscape (step 3) solo ai comunicati elencati in
un file di filtro (un link per riga, con eventuale riga di intestazione
"link"), usare l'opzione --filter. Se omessa, vengono monitorati tutti i
comunicati presenti in get_print_reviews.csv:

    python run_pipeline.py --filter filter.txt
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Ogni step e' una tupla (nome, file .py, argomenti extra di default).
STEPS: list[tuple[str, str]] = [
    ("Aggiornamento feed RSS", "rss_latest_hash.py"),
    ("Estrazione testo comunicati", "get_print_reviews.py"),
    ("Controlli Copyscape pianificati", "copyscape_scheduler.py"),
    ("Merge risultati cumulativi", "merge_copyscape_results.py"),
    ("Caricamento su SQLite", "upload_sqlite.py"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Esegue in sequenza tutti gli step della pipeline di monitoraggio."
    )
    parser.add_argument(
        "--skip",
        default="",
        help=(
            "Elenco separato da virgole degli step da saltare, per nome file "
            "(es. --skip rss_latest_hash.py,get_print_reviews.py)"
        ),
    )
    parser.add_argument(
        "--scheduler-args",
        default="",
        help="Argomenti extra da passare a copyscape_scheduler.py (tra virgolette)",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help=(
            "Percorso di un file di testo con un link per riga (es. "
            "filter.txt) da passare a copyscape_scheduler.py per limitare i "
            "controlli Copyscape solo a quei comunicati. Se omesso, vengono "
            "processati tutti i comunicati presenti in get_print_reviews.csv."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "Prosegue con gli step successivi anche se uno step fallisce "
            "(di default l'esecuzione si interrompe al primo errore)."
        ),
    )
    return parser.parse_args()


def run_step(name: str, script: str, extra_args: list[str]) -> int:
    script_path = SCRIPT_DIR / script
    cmd = [sys.executable, str(script_path), *extra_args]
    print(f"\n{'=' * 70}")
    print(f"[{datetime.now().isoformat(timespec='seconds')}] STEP: {name} ({script})")
    print(f"Comando: {' '.join(cmd)}")
    print("=" * 70)

    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    return result.returncode


def main() -> int:
    args = parse_args()
    skip_set = {s.strip() for s in args.skip.split(",") if s.strip()}
    scheduler_extra_args = shlex.split(args.scheduler_args) if args.scheduler_args else []
    if args.filter:
        scheduler_extra_args = [*scheduler_extra_args, "--filter", args.filter]

    failures: list[str] = []
    for name, script in STEPS:
        if script in skip_set:
            print(f"\n[SKIP] {name} ({script})")
            continue

        extra_args = scheduler_extra_args if script == "copyscape_scheduler.py" else []
        return_code = run_step(name, script, extra_args)

        if return_code != 0:
            print(
                f"\n[ERRORE] Lo step '{name}' ({script}) e' terminato con "
                f"codice {return_code}.",
                file=sys.stderr,
            )
            failures.append(script)
            if not args.continue_on_error:
                print("Esecuzione interrotta (usa --continue-on-error per proseguire comunque).")
                return return_code

    print(f"\n{'=' * 70}")
    if failures:
        print(f"Pipeline completata CON ERRORI negli step: {', '.join(failures)}")
        return 1

    print("Pipeline completata con successo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
