#!/usr/bin/env python3
"""Estrae la data di pubblicazione delle pagine trovate da Copyscape.

Legge un CSV di risultati (es. copyscape_results_*.csv o output_cumulativo.csv,
con la colonna "URL"), scarica ogni pagina una sola volta e ne ricava la data
di pubblicazione, in quest'ordine:

1. metadati HTML (meta article:published_time, JSON-LD datePublished, <time>);
2. testo della pagina: data accanto a diciture come "pubblicato il".

L'header HTTP Last-Modified non viene usato: indica l'ultima modifica, non la
pubblicazione.

Il CSV di output (default: <input>_date.csv) contiene tutte le colonne di
input piu' `published_date` (YYYY-MM-DD, vuota se non trovata), `date_source`,
`date_status` (ok / not_found / fetch_error) e, se e' impostata una data
limite, `before_cutoff` (yes se la pagina e' precedente alla data limite).

La data limite puo' essere:
- per ogni comunicato, la sua data di rilascio (--release-dates, un CSV con le
  colonne id_hash e data, come get_print_reviews.csv): una pagina pubblicata
  prima del comunicato non puo' esserne la copia;
- una data fissa per tutte le righe (--before).
Con entrambe, --before ha la precedenza. Le pagine senza data trovata hanno
before_cutoff=no.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; IstatPressReleases-check-date/1.0)"
MIN_YEAR = 1990

META_DATE_KEYS = (
    "article:published_time",
    "og:article:published_time",
    "article:published",
    "datepublished",
    "date",
    "dc.date",
    "dc.date.issued",
    "dcterms.created",
    "dcterms.date",
    "pubdate",
    "publishdate",
    "publish_date",
    "sailthru.date",
    "parsely-pub-date",
    "citation_publication_date",
    "citation_date",
)

ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
SLASH_DATE_RE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
ITALIAN_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "novembre": 11, "dicembre": 12,
}
LABELED_DATE_RE = re.compile(
    r"(?:pubblicat[oa]|published|data di pubblicazione|posted)\W{0,3}(?:il|on)?\W{0,3}"
    r"(\d{1,2}\s+[a-zà-ú]+\s+\d{4}|\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
ITALIAN_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(ITALIAN_MONTHS) + r")\s+(\d{4})\b", re.IGNORECASE
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estrae la data di pubblicazione delle pagine elencate in un CSV Copyscape."
    )
    parser.add_argument("input", help="CSV di risultati Copyscape (colonna URL)")
    parser.add_argument(
        "--output",
        default=None,
        help="CSV di output (default: <input>_date.csv)",
    )
    parser.add_argument("--url-column", default="URL", help="Nome della colonna URL (default: URL)")
    parser.add_argument(
        "--before",
        default=None,
        help="Data limite fissa YYYY-MM-DD: before_cutoff=yes se la pagina e' precedente",
    )
    parser.add_argument(
        "--release-dates",
        default=None,
        help=(
            "CSV con colonne id_hash e data (es. get_print_reviews.csv): la data limite "
            "di ogni riga e' la data di rilascio del suo comunicato"
        ),
    )
    parser.add_argument(
        "--exclude-before",
        action="store_true",
        help="Scrive nell'output solo le righe NON precedenti alla data limite",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Timeout HTTP in secondi")
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Pausa tra una pagina e l'altra (secondi)"
    )
    return parser.parse_args()


def normalize_date(value: str) -> date | None:
    """Converte una stringa data (ISO, gg/mm/aaaa, '9 ottobre 2026') in date."""
    if match := ISO_DATE_RE.search(value):
        year, month, day = int(match[1]), int(match[2]), int(match[3])
    elif match := ITALIAN_DATE_RE.search(value):
        year, month, day = int(match[3]), ITALIAN_MONTHS[match[2].lower()], int(match[1])
    elif match := SLASH_DATE_RE.search(value):
        year, month, day = int(match[3]), int(match[2]), int(match[1])
    else:
        return None
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    return parsed if MIN_YEAR <= parsed.year <= date.today().year + 1 else None

def _iter_json_ld(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_json_ld(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_json_ld(item)


def date_from_metadata(soup: BeautifulSoup) -> tuple[date, str] | None:
    """Cerca la data nei metadati HTML. Restituisce (data, sorgente)."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _iter_json_ld(data):
            for key in ("datePublished", "dateCreated"):
                value = node.get(key)
                if isinstance(value, str) and (parsed := normalize_date(value)):
                    return parsed, f"json-ld:{key}"

    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
        content = meta.get("content") or ""
        if key in META_DATE_KEYS and (parsed := normalize_date(content)):
            return parsed, f"meta:{key}"

    for tag in soup.find_all("time"):
        value = tag.get("datetime") or tag.get_text(strip=True)
        if parsed := normalize_date(value):
            return parsed, "html:time"
    return None


def date_from_text(text: str) -> date | None:
    """Cerca una data accanto a diciture come 'pubblicato il'."""
    for match in LABELED_DATE_RE.finditer(text):
        if parsed := normalize_date(match[1]):
            return parsed
    return None


def extract_page_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def analyze_url(
    session: requests.Session, url: str, args: argparse.Namespace
) -> tuple[str, str, str]:
    """Restituisce (published_date, date_source, date_status) per un URL."""
    try:
        response = session.get(url, timeout=args.timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  Pagina non raggiungibile: {exc}", file=sys.stderr)
        return "", "", "fetch_error"

    if "html" not in response.headers.get("Content-Type", "").lower():
        return "", "", "not_found"

    soup = BeautifulSoup(response.text, "html.parser")
    if found := date_from_metadata(soup):
        return found[0].isoformat(), found[1], "ok"

    page_text = extract_page_text(soup)
    if parsed := date_from_text(page_text):
        return parsed.isoformat(), "text:label", "ok"

    return "", "", "not_found"


def load_release_dates(path: Path) -> dict[str, date]:
    """Legge {id_hash: data di rilascio} da un CSV con colonne id_hash e data."""
    with path.open("r", encoding="utf-8", newline="") as f:
        return {
            row["id_hash"]: parsed
            for row in csv.DictReader(f)
            if row.get("id_hash") and (parsed := normalize_date(row.get("data", "")))
        }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"File non trovato: {input_path}", file=sys.stderr)
        return 1

    fixed_cutoff: date | None = None
    if args.before:
        try:
            fixed_cutoff = date.fromisoformat(args.before)
        except ValueError:
            print("--before deve essere nel formato YYYY-MM-DD", file=sys.stderr)
            return 1

    release_dates: dict[str, date] = {}
    if args.release_dates:
        try:
            release_dates = load_release_dates(Path(args.release_dates))
        except OSError as exc:
            print(f"Impossibile leggere {args.release_dates}: {exc}", file=sys.stderr)
            return 1
    use_cutoff = fixed_cutoff is not None or bool(args.release_dates)
    if args.exclude_before and not use_cutoff:
        print("--exclude-before richiede --before o --release-dates", file=sys.stderr)
        return 1

    with input_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if args.url_column not in fieldnames:
        print(f"Colonna '{args.url_column}' non trovata in {input_path}", file=sys.stderr)
        return 1

    urls = list(dict.fromkeys(r[args.url_column].strip() for r in rows if r[args.url_column].strip()))
    print(f"{len(rows)} righe, {len(urls)} URL distinti da controllare.")

    results: dict[str, tuple[str, str, str]] = {}
    with requests.Session() as session:
        for index, url in enumerate(urls, start=1):
            if urlparse(url).scheme not in ("http", "https"):
                results[url] = ("", "", "fetch_error")
                continue
            results[url] = analyze_url(session, url, args)
            print(f"[{index}/{len(urls)}] {results[url][0] or '-':10} {results[url][1] or results[url][2]:22} {url}")
            if index < len(urls):
                time.sleep(args.delay)

    extra = ["published_date", "date_source", "date_status"] + (["before_cutoff"] if use_cutoff else [])
    output_rows: list[dict[str, str]] = []
    for row in rows:
        published, source, status = results.get(row[args.url_column].strip(), ("", "", "fetch_error"))
        row.update(published_date=published, date_source=source, date_status=status)
        if use_cutoff:
            cutoff = fixed_cutoff or release_dates.get(row.get("id_hash", ""))
            is_before = bool(published and cutoff and date.fromisoformat(published) < cutoff)
            row["before_cutoff"] = "yes" if is_before else "no"
            if args.exclude_before and is_before:
                continue
        output_rows.append(row)

    output_path = Path(args.output) if args.output else input_path.with_name(f"{input_path.stem}_date.csv")
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames + [c for c in extra if c not in fieldnames])
        writer.writeheader()
        writer.writerows(output_rows)

    found = sum(1 for v in results.values() if v[0])
    print(f"Date trovate: {found}/{len(urls)}. Righe scritte: {len(output_rows)}")
    print(f"Output: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
