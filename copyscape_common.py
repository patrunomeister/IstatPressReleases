#!/usr/bin/env python3
"""Funzioni e costanti condivise tra copyscape_check.py e
copyscape_scheduler.py: chiamata all'API Copyscape, costruzione dei
parametri di richiesta, salvataggio dei CSV di output/log e lettura dei
comunicati stampa da un file CSV nel formato prodotto da get_print_reviews.py.

Centralizzare questa logica evita di mantenere due copie quasi identiche
del codice che parla con Copyscape nei due script che lo utilizzano.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

COPYSCAPE_API_URL = "https://www.copyscape.com/api/"

# ============================================================================
# CONFIGURATION: Copyscape Premium credentials di default
# ============================================================================
DEFAULT_COPYSCAPE_USERNAME = ""
DEFAULT_COPYSCAPE_API_KEY = ""
# ============================================================================

# Schema del CSV dei risultati: condiviso da copyscape_check/scheduler (scrittura),
# merge_copyscape_results.py e upload_sqlite.py (lettura).
RESULT_COLUMNS = [
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

DEFAULT_IGNORE_SITES = "facebook.com,instagram.com,threads.com,istat.it,x.com,linkedin.com"


def add_credential_args(parser: argparse.ArgumentParser) -> None:
    """Aggiunge al parser gli argomenti --username/--api-key, con priorita'
    CLI > variabili d'ambiente > costanti di default dello script."""
    parser.add_argument(
        "--username",
        default=os.getenv("COPYSCAPE_USERNAME") or DEFAULT_COPYSCAPE_USERNAME,
        help="Copyscape username (env: COPYSCAPE_USERNAME, o modifica le costanti nello script)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("COPYSCAPE_API_KEY") or DEFAULT_COPYSCAPE_API_KEY,
        help="Copyscape API key (env: COPYSCAPE_API_KEY, o modifica le costanti nello script)",
    )


def add_request_args(parser: argparse.ArgumentParser) -> None:
    """Aggiunge al parser gli argomenti comuni che controllano una
    richiesta Copyscape (confronti completi, siti da ignorare, limite di
    spesa, modalita' di test)."""
    parser.add_argument(
        "--full-comparisons",
        type=int,
        default=0,
        help="Numero di confronti completi da richiedere (0-10, default: 0)",
    )
    parser.add_argument(
        "--ignore-sites",
        default=DEFAULT_IGNORE_SITES,
        help="Domini da ignorare, separati da virgola",
    )
    parser.add_argument(
        "--spend-limit",
        type=float,
        default=None,
        help="Limite di spesa opzionale per questa richiesta (in dollari)",
    )
    parser.add_argument(
        "--example-test",
        action="store_true",
        help="Esegue una ricerca di test Copyscape (x=1, non addebitata)",
    )


def build_base_params(args: argparse.Namespace) -> dict[str, str]:
    """Costruisce i parametri comuni a tutte le chiamate Copyscape di
    un'esecuzione, a partire dagli argomenti CLI."""
    if not args.username or not args.api_key:
        raise ValueError(
            "Credenziali mancanti. Fornisci --username/--api-key oppure imposta "
            "COPYSCAPE_USERNAME e COPYSCAPE_API_KEY."
        )

    if not 0 <= args.full_comparisons <= 10:
        raise ValueError("--full-comparisons deve essere compreso tra 0 e 10.")

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
    """Chiama l'API Copyscape. Le ricerche testuali richiedono POST (il
    parametro testo "t" puo' superare la lunghezza consentita in una query
    GET)."""
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
        raise RuntimeError(f"Errore HTTP da Copyscape: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Errore di rete chiamando Copyscape: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Risposta JSON non valida da Copyscape: {exc}") from exc

    if "error" in payload:
        raise RuntimeError(f"Copyscape API error: {payload['error']}")

    return payload


def extract_result_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Estrae i dati di dettaglio dei risultati dal payload Copyscape."""
    results = payload.get("result", [])
    if not isinstance(results, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        rows.append(
            {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "minwordsmatched": item.get("minwordsmatched", ""),
                "textsnippet": item.get("textsnippet", ""),
                "wordsmatch": item.get("wordsmatch", ""),
                "percentmatched": item.get("percentmatched", ""),
                "viewurl": item.get("viewurl", ""),
            }
        )
    return rows


def run_for_single_text(
    base_params: dict[str, str],
    text: str,
    link: str,
    id_hash: str,
    codice: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Esegue una singola ricerca Copyscape sul testo indicato e restituisce
    (righe di risultato, riga di log) nello stesso formato usato sia da
    copyscape_check.py sia da copyscape_scheduler.py."""
    params = dict(base_params)
    params["t"] = text
    params["e"] = "UTF-8"
    payload = call_copyscape(params, method="POST")
    call_date = datetime.now().isoformat()

    result_rows = extract_result_rows(payload)
    output_rows: list[dict[str, Any]] = [
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
        for row in result_rows
    ]

    log_row = {
        "date": call_date,
        "query_url": link,
        "total_results": payload.get("count", len(result_rows)),
        "cost_usd": payload.get("cost", ""),
        "query_words": payload.get("querywords", ""),
    }
    return output_rows, log_row


def save_aggregated_results(rows: list[dict[str, Any]], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(RESULT_COLUMNS)

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


def read_press_release_rows(input_file: Path) -> list[dict[str, str]]:
    """Legge un CSV nel formato prodotto da get_print_reviews.py (colonne
    id_hash, testo, e opzionalmente codice, link/url, titolo/title, data) e
    restituisce solo le righe che hanno sia id_hash che testo non vuoti.

    Usata sia da copyscape_check.py (elaborazione immediata di tutte le
    righe) sia da copyscape_scheduler.py (registrazione nel piano di
    controlli pianificati)."""
    if not input_file.exists():
        raise FileNotFoundError(f"Input file non trovato: {input_file}")

    df = pd.read_csv(input_file, dtype=str, keep_default_na=False)
    normalized_map = {name.strip().lower(): name for name in df.columns}

    id_hash_col = normalized_map.get("id_hash")
    testo_col = normalized_map.get("testo")
    codice_col = normalized_map.get("codice")
    link_col = normalized_map.get("link") or normalized_map.get("url")
    titolo_col = normalized_map.get("titolo") or normalized_map.get("title")
    data_col = normalized_map.get("data")

    if not id_hash_col or not testo_col:
        raise ValueError(
            "Il file input deve contenere almeno le colonne 'id_hash' e 'testo'."
        )

    def field(record: pd.Series, col: str | None) -> str:
        return (record.get(col, "") or "").strip() if col else ""

    rows: list[dict[str, str]] = []
    for _, record in df.iterrows():
        id_hash = field(record, id_hash_col)
        testo = field(record, testo_col)
        if not id_hash or not testo:
            continue
        rows.append(
            {
                "id_hash": id_hash,
                "testo": testo,
                "codice": field(record, codice_col),
                "link": field(record, link_col),
                "titolo": field(record, titolo_col),
                "data": field(record, data_col),
            }
        )
    return rows
