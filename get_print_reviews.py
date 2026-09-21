"""Scarica il testo del comunicato stampa per ogni link presente in
rss_latest_hash_output.csv ed esporta il risultato in get_print_reviews.csv.

Per ogni riga del file di input viene effettuata una richiesta HTTP all'URL
indicato nella colonna "link" e viene estratto il testo contenuto nel tag
<div class="guid-article"> della pagina restituita. Il testo estratto viene
aggiunto al dataframe in una nuova colonna "testo".
"""

from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

INPUT_FILE = Path(__file__).with_name("rss_latest_hash_output.csv")
OUTPUT_FILE = Path(__file__).with_name("get_print_reviews.csv")
ARTICLE_CLASS = "guid-article"
REQUEST_TIMEOUT = 20
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IstatPressReleases/1.0)"}


def fetch_article_text(url: str) -> str:
    """Recupera il testo del div guid-article dalla pagina indicata da url."""
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Impossibile scaricare la pagina ({url}): {exc}")
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    article_div = soup.find("div", class_=ARTICLE_CLASS)
    if article_div is None:
        print(f"Div '{ARTICLE_CLASS}' non trovato nella pagina: {url}")
        return ""

    return article_div.get_text(separator="\n", strip=True)


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"File non trovato: {INPUT_FILE}")

    df = pd.read_csv(INPUT_FILE)

    df["testo"] = df["link"].apply(fetch_article_text)

    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
    print(f"File salvato in: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
