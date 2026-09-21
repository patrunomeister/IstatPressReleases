"""Legge rss_latest_hash_output.csv, individua per ogni comunicato il link al PDF
"Testo integrale e nota metodologica" e ne estrae il testo con PyPDF2."""

import io
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader

INPUT_CSV = Path("rss_latest_hash_output.csv")
OUTPUT_CSV = Path("print_reviews_content.csv")
LINK_TEXT = "testo integrale e nota metodologica"
REQUEST_TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0"}


def find_pdf_link(page_url: str) -> str | None:
    """Cerca nella pagina il link il cui testo contiene LINK_TEXT e ne restituisce l'href."""
    response = requests.get(page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        if LINK_TEXT in anchor.get_text(strip=True).lower():
            return anchor["href"]
    return None


def extract_pdf_text(pdf_url: str) -> str:
    """Scarica il pdf e ne estrae il testo con PyPDF2."""
    response = requests.get(pdf_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    reader = PdfReader(io.BytesIO(response.content))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages_text)


def main() -> None:
    df = pd.read_csv(INPUT_CSV)

    pdf_links: list[str | None] = []
    pdf_texts: list[str | None] = []

    for link in df["link"]:
        pdf_link: str | None = None
        pdf_text: str | None = None
        try:
            pdf_link = find_pdf_link(link)
            if pdf_link:
                pdf_text = extract_pdf_text(pdf_link)
            else:
                print(f"Link PDF non trovato per: {link}")
        except requests.RequestException as exc:
            print(f"Errore nel recupero dei dati per {link}: {exc}")

        pdf_links.append(pdf_link)
        pdf_texts.append(pdf_text)

    df["pdf_link"] = pdf_links
    df["pdf_text"] = pdf_texts

    df.to_csv(OUTPUT_CSV, index=False)
    print(f"File salvato in: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
