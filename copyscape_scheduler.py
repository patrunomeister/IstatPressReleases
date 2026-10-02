#!/usr/bin/env python3
"""Scheduler dei controlli Copyscape per i comunicati stampa.

Invece di analizzare su Copyscape tutti i testi presenti in
`get_print_reviews.csv` in un'unica volta, questo script permette di
pianificare per ciascun comunicato stampa un piano di controlli distanziati
nel tempo (ad esempio dopo 1 giorno, dopo 1 settimana e dopo 1 mese dalla
sua prima registrazione), cosi' da poter individuare eventuali plagi anche a
distanza di tempo dalla pubblicazione, senza dover interrogare Copyscape per
tutti i testi ad ogni esecuzione.

Componenti:

- File di configurazione (default: copyscape_schedule_config.json): definisce
  gli offset (in giorni) a cui eseguire i controlli, con la possibilita' di
  personalizzarli per singolo comunicato tramite override per uguaglianza
  esatta sull'intero campo "codice" oppure sull'"id_hash".
- File di stato (default: copyscape_schedule_state.json): tiene traccia, per
  ogni comunicato (chiave "id_hash"), della data di prima registrazione
  nello scheduler ("first_seen"), della data di rilascio usata come base
  per il calcolo delle scadenze ("release_date"), del testo da analizzare
  (congelato al momento della registrazione, cosi' da restare disponibile
  anche quando il comunicato non compare piu' nel feed RSS piu' recente) e
  dello stato di ciascun controllo pianificato (pending / done / error /
  failed).

Le date di scadenza dei controlli sono calcolate a partire dal campo "data"
del comunicato (la data di rilascio, presente in `get_print_reviews.csv`),
e non dalla data in cui lo scheduler lo incontra per la prima volta: ad
esempio se un comunicato ha "data" uguale a oggi e il piano prevede gli
offset [7, 30], i controlli scadranno tra 7 e 30 giorni da oggi; se invece
"data" e' di 2 giorni fa e il piano prevede l'offset 15, il controllo
scadra' tra 13 giorni (15 - 2). Se il campo "data" manca o non e'
interpretabile, si usa come base la data in cui il comunicato viene
registrato nel piano (comportamento precedente).

Ad ogni esecuzione lo script:
1. legge `get_print_reviews.csv` e registra i comunicati non ancora
   presenti nel file di stato, calcolando le date dei controlli pianificati
   in base alla configurazione e alla data di rilascio del comunicato;
2. individua i controlli "in scadenza" (data odierna >= data pianificata) e
   non ancora eseguiti con successo;
3. per ciascun controllo in scadenza chiama l'API Copyscape (riusando le
   funzioni di copyscape_check.py) e salva i risultati con lo stesso formato
   di copyscape_check.py (`copyscape_results_<timestamp>.csv` e
   `copyscape_log.csv`), cosi' da restare compatibile con
   merge_copyscape_results.py e upload_sqlite.py;
4. aggiorna e salva il file di stato.

Uso tipico (da eseguire una volta al giorno, ad es. tramite Task Scheduler
di Windows o cron):

    python copyscape_scheduler.py

Per una prova senza effettuare chiamate reali (mostra solo cosa verrebbe
eseguito):

    python copyscape_scheduler.py --dry-run

Per forzare l'esecuzione immediata di tutti i controlli pianificati,
indipendentemente dalla data prevista (utile per test):

    python copyscape_scheduler.py --ignore-schedule --example-test

Filtro dei comunicati da monitorare (--filter):

Non tutti i comunicati presenti in `get_print_reviews.csv` devono
necessariamente essere analizzati su Copyscape. Passando l'opzione
`--filter filter.txt` (un file di testo con una riga per comunicato
nel formato `link,codice_man`, con intestazione `link,codice_man`; e'
accettato anche il solo link), lo scheduler registra ed esegue i controlli
solo per i comunicati il cui link e' presente in quel file; gli altri
vengono ignorati. Se e' indicato un codice_man, questo sostituisce il
"codice" proveniente da get_print_reviews.csv per quel link (negli override
della config, nello stato e nei risultati). Se l'opzione non viene passata, vengono monitorati
tutti i comunicati presenti nell'input (comportamento di default).

    python copyscape_scheduler.py --filter filter.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
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

DEFAULT_INPUT_FILE = "get_print_reviews.csv"
DEFAULT_CONFIG_FILE = "copyscape_schedule_config.json"
DEFAULT_STATE_FILE = "copyscape_schedule_state.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "default_offsets_days": list(range(1, 31)),
    "overrides": {},
    "max_attempts": 3,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pianifica ed esegue i controlli Copyscape per i comunicati stampa "
            "presenti in get_print_reviews.csv, distribuendoli nel tempo secondo "
            "un file di configurazione (invece di analizzarli tutti insieme)."
        )
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_FILE,
        help=(
            "File CSV con i comunicati da monitorare, colonne id_hash, codice, "
            f"link e testo (default: {DEFAULT_INPUT_FILE})"
        ),
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help=f"File di configurazione del piano di controlli (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE_FILE,
        help=f"File di stato dello scheduler (default: {DEFAULT_STATE_FILE})",
    )
    add_credential_args(parser)
    add_request_args(parser)
    parser.add_argument(
        "--output",
        default=None,
        help="Percorso del CSV dei risultati (default: copyscape_results_<timestamp>.csv)",
    )
    parser.add_argument(
        "--log-output",
        default=None,
        help="Percorso del CSV di log delle chiamate (default: copyscape_log.csv)",
    )
    parser.add_argument(
        "--ignore-schedule",
        action="store_true",
        help=(
            "Considera 'in scadenza' tutti i controlli pianificati non ancora "
            "eseguiti, indipendentemente dalla data prevista (utile per test/debug)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Non chiama Copyscape e non modifica il file di stato: mostra solo "
            "quali comunicati verrebbero registrati e quali controlli sono in "
            "scadenza in questa esecuzione."
        ),
    )
    parser.add_argument(
        "--filter",
        default=None,
        help=(
            "Percorso di un file di testo (es. filter.txt) con una riga "
            "'link,codice_man' per comunicato, che restringe i comunicati da "
            "monitorare a quelli presenti nel file; il codice_man, se indicato, "
            "sostituisce il codice proveniente dall'input. Se omesso, vengono "
            "monitorati tutti i comunicati presenti nell'input."
        ),
    )
    return parser.parse_args()


# ============================================================================
# Configurazione del piano di controlli
# ============================================================================
def load_config(config_file: Path) -> dict[str, Any]:
    """Carica il file di configurazione, creandolo con i valori di default se
    non esiste ancora."""
    if not config_file.exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with config_file.open("w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"Creato file di configurazione con valori di default: {config_file}")
        return dict(DEFAULT_CONFIG)

    with config_file.open("r", encoding="utf-8") as f:
        config = json.load(f)

    # Applica i default per le chiavi eventualmente mancanti nel file esistente.
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    return merged


def load_filter_links(filter_file: Path) -> dict[str, str]:
    """Legge un file di filtro e restituisce un dizionario {link: codice_man}
    con i comunicati da monitorare. Ogni riga contiene il link e,
    opzionalmente, il codice_man separato da virgola, punto e virgola o
    tabulazione (codice_man vuoto se assente). L'intestazione ("link" oppure
    "link,codice_man") e le righe vuote vengono ignorate."""
    if not filter_file.exists():
        raise FileNotFoundError(f"File di filtro non trovato: {filter_file}")

    filter_map: dict[str, str] = {}
    with filter_file.open("r", encoding="utf-8-sig") as f:
        for line in f:
            parts = [p.strip() for p in re.split(r"[,;\t]", line.strip(), maxsplit=1)]
            link = parts[0]
            if not link or link.lower() == "link":
                continue
            filter_map[link] = parts[1] if len(parts) > 1 else ""
    return filter_map


def resolve_offsets(codice: str, id_hash: str, config: dict[str, Any]) -> list[int]:
    """Determina gli offset (in giorni) del piano di controlli per un dato
    comunicato, seguendo questa priorita': override per id_hash, override per
    l'intero campo codice (uguaglianza esatta, non per prefisso), infine il
    piano di default."""
    overrides = config.get("overrides", {}) or {}

    if id_hash and id_hash in overrides:
        return list(overrides[id_hash])
    if codice and codice in overrides:
        return list(overrides[codice])

    return list(config.get("default_offsets_days", DEFAULT_CONFIG["default_offsets_days"]))


# ============================================================================
# Stato dello scheduler
# ============================================================================
def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {}
    with state_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict[str, Any], state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def build_checks(offsets_days: list[int], base_date: date) -> list[dict[str, Any]]:
    """Calcola le date di scadenza dei controlli a partire da 'base_date'
    (tipicamente la data di rilascio del comunicato) sommando ciascun
    offset in giorni."""
    checks: list[dict[str, Any]] = []
    for offset in sorted(set(offsets_days)):
        due_date = base_date + timedelta(days=offset)
        checks.append(
            {
                "offset_days": offset,
                "due_date": due_date.isoformat(),
                "status": "pending",
                "attempts": 0,
                "executed_at": None,
            }
        )
    return checks


def parse_release_date(data_str: str, fallback: date) -> date:
    """Interpreta il campo 'data' (data di rilascio del comunicato, formato
    atteso AAAA-MM-GG) restituendo la data da usare come base per il calcolo
    delle scadenze dei controlli. Se il campo e' vuoto o non interpretabile,
    restituisce 'fallback' (tipicamente la data odierna di registrazione)."""
    if not data_str:
        return fallback
    try:
        return date.fromisoformat(data_str[:10])
    except ValueError:
        return fallback


# ============================================================================
# Registrazione di nuovi comunicati nel piano
# ============================================================================
def register_new_press_releases(
    press_releases: list[dict[str, str]],
    state: dict[str, Any],
    config: dict[str, Any],
    today: date,
    filter_map: dict[str, str] | None = None,
) -> int:
    """Aggiunge al file di stato i comunicati non ancora monitorati,
    calcolando il loro piano di controlli. Se 'filter_map' e' fornito
    (non None), vengono registrati solo i comunicati il cui link e' una
    chiave del dizionario (filtro da filter.txt); altrimenti vengono
    registrati tutti. Se per il link e' indicato un codice_man, questo
    sostituisce il "codice" proveniente dall'input (anche per i comunicati
    gia' registrati). Restituisce il numero di nuove registrazioni."""
    newly_registered = 0
    for item in press_releases:
        id_hash = item["id_hash"]
        codice_man = (filter_map or {}).get(item.get("link", ""), "")
        if codice_man:
            item = {**item, "codice": codice_man}
        if id_hash in state:
            # Comunicato gia' monitorato: aggiorna solo i metadati
            # descrittivi (non il testo, che resta congelato alla prima
            # registrazione) nel caso siano nel frattempo diventati disponibili.
            entry = state[id_hash]
            for field in ("codice", "link", "titolo", "data"):
                if not entry.get(field) and item.get(field):
                    entry[field] = item[field]
            if codice_man:
                entry["codice"] = codice_man
            continue

        if filter_map is not None and item.get("link", "") not in filter_map:
            # Comunicato escluso dal filtro: non viene monitorato.
            continue

        offsets = resolve_offsets(item["codice"], id_hash, config)
        release_date = parse_release_date(item.get("data", ""), fallback=today)
        state[id_hash] = {
            "id_hash": id_hash,
            "codice": item["codice"],
            "titolo": item["titolo"],
            "link": item["link"],
            "testo": item["testo"],
            "data": item.get("data", ""),
            "release_date": release_date.isoformat(),
            "first_seen": today.isoformat(),
            "offsets_days": offsets,
            "checks": build_checks(offsets, release_date),
        }
        newly_registered += 1

    return newly_registered


# ============================================================================
# Individuazione ed esecuzione dei controlli in scadenza
# ============================================================================
def find_due_checks(
    state: dict[str, Any],
    today: date,
    max_attempts: int,
    ignore_schedule: bool,
    filter_map: dict[str, str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Restituisce la lista di (id_hash, check) per i controlli pianificati
    che vanno eseguiti in questa esecuzione. Se 'filter_map' e' fornito
    (non None), vengono considerati solo i comunicati il cui link e'
    presente in tale dizionario (filtro da filter.txt)."""
    due: list[tuple[str, dict[str, Any]]] = []
    for id_hash, entry in state.items():
        if filter_map is not None and entry.get("link", "") not in filter_map:
            continue
        for check in entry.get("checks", []):
            if check["status"] not in ("pending", "error"):
                continue
            if check.get("attempts", 0) >= max_attempts:
                continue
            due_date = date.fromisoformat(check["due_date"])
            if ignore_schedule or due_date <= today:
                due.append((id_hash, check))
    return due



def execute_due_checks(
    state: dict[str, Any],
    due_checks: list[tuple[str, dict[str, Any]]],
    base_params: dict[str, str],
    max_attempts: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_result_rows: list[dict[str, Any]] = []
    call_log_rows: list[dict[str, Any]] = []

    for id_hash, check in due_checks:
        entry = state[id_hash]
        try:
            result_rows, log_row = run_for_single_text(
                base_params=base_params,
                text=entry["testo"],
                link=entry.get("link", ""),
                id_hash=id_hash,
                codice=entry.get("codice", ""),
            )
            all_result_rows.extend(result_rows)
            call_log_rows.append(log_row)
            check["status"] = "done"
            check["executed_at"] = datetime.now().isoformat()
        except RuntimeError as exc:
            check["attempts"] = check.get("attempts", 0) + 1
            check["status"] = "failed" if check["attempts"] >= max_attempts else "error"
            print(
                f"Errore nel controllo (id_hash={id_hash}, "
                f"offset={check['offset_days']} giorni): {exc}",
                file=sys.stderr,
            )
            call_log_rows.append(
                {
                    "date": datetime.now().isoformat(),
                    "query_url": entry.get("link", ""),
                    "total_results": "ERROR",
                    "cost_usd": "",
                    "query_words": "",
                }
            )

    return all_result_rows, call_log_rows


def main() -> int:
    args = parse_args()
    today = date.today()

    input_file = Path(args.input)
    config_file = Path(args.config)
    state_file = Path(args.state)

    try:
        config = load_config(config_file)
        press_releases = read_press_release_rows(input_file)
        filter_map: dict[str, str] | None = None
        if args.filter:
            filter_file = Path(args.filter)
            filter_map = load_filter_links(filter_file)
            print(f"Filtro attivo ({filter_file}): {len(filter_map)} link ammessi")
    except (ValueError, FileNotFoundError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1

    state = load_state(state_file)
    max_attempts = int(config.get("max_attempts", DEFAULT_CONFIG["max_attempts"]))

    newly_registered = register_new_press_releases(
        press_releases, state, config, today, filter_map=filter_map
    )

    due_checks = find_due_checks(
        state,
        today,
        max_attempts=max_attempts,
        ignore_schedule=args.ignore_schedule,
        filter_map=filter_map,
    )

    print(f"Comunicati letti da {input_file}: {len(press_releases)}")
    print(f"Nuovi comunicati registrati nel piano: {newly_registered}")
    print(f"Controlli Copyscape in scadenza oggi ({today.isoformat()}): {len(due_checks)}")

    if args.dry_run:
        for id_hash, check in due_checks:
            entry = state[id_hash]
            print(
                f"  - [DRY-RUN] {entry.get('codice') or id_hash}: "
                f"offset {check['offset_days']} giorni, "
                f"scadenza {check['due_date']}"
            )
        # In modalita' dry-run non si salva lo stato: le nuove registrazioni
        # calcolate in memoria non vengono persistite.
        return 0

    # Persistiamo comunque le nuove registrazioni anche se non ci sono
    # controlli da eseguire in questa esecuzione.
    if not due_checks:
        save_state(state, state_file)
        print("Nessun controllo in scadenza. Stato aggiornato e salvato.")
        return 0

    try:
        base_params = build_base_params(args)
    except ValueError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1

    all_result_rows, call_log_rows = execute_due_checks(
        state, due_checks, base_params, max_attempts=max_attempts
    )

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
        print(f"Errore durante il salvataggio dei CSV: {exc}", file=sys.stderr)
        return 1

    save_state(state, state_file)

    print(f"Controlli eseguiti: {len(due_checks)}")
    print(f"Righe di risultato scritte: {len(all_result_rows)}")
    print(f"Risultati salvati in: {output_path.resolve()}")
    print(f"Log salvato in: {log_path.resolve()}")
    print(f"Stato aggiornato in: {state_file.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
