import hashlib
import csv
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
import xml.etree.ElementTree as ET


FEED_PREFIX_MAP: dict[str, str] = {
    "prezzi-al-consumo": "PRE",
    "produzione-industriale": "PRO",
    "occupati-e-disoccupati": "OCC",
}

TITLE_CODE_RULES: list[tuple[str, str]] = [
    ("prezzi al consumo", "CON"),
    ("produzione industriale", "IND"),
    ("prezzi alla produzione dell'industria", "IND"),
    ("prezzi alla produzione dell'industria e delle costruzioni", "IND"),
    ("occupati e disoccupati", "DIS"),
]

MONTH_MAP: dict[str, str] = {
    "gennaio": "GEN",
    "febbraio": "FEB",
    "marzo": "MAR",
    "aprile": "APR",
    "maggio": "MAG",
    "giugno": "GIU",
    "luglio": "LUG",
    "agosto": "AGO",
    "settembre": "SET",
    "ottobre": "OTT",
    "novembre": "NOV",
    "dicembre": "DIC",
}

MONTH_YEAR_RE = re.compile(rf"\b({'|'.join(MONTH_MAP.keys())})\s+(\d{{4}})\b")
HEADER_5_NEW = ["data", "titolo", "codice", "id_hash", "link"]
HEADER_4_NEW = ["titolo", "codice", "id_hash", "link"]
HEADER_4_OLD = ["titolo", "codice", "hashtag", "link"]
HEADER_3_OLD = ["titolo", "hashtag", "link"]
KNOWN_HEADERS = (HEADER_5_NEW, HEADER_4_NEW, HEADER_4_OLD, HEADER_3_OLD)


def read_feed_urls(file_path: Path) -> list[str]:
    if not file_path.exists():
        raise FileNotFoundError(f"File non trovato: {file_path}")

    urls: list[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls


def to_utc(date_value: str | None) -> datetime:
    if not date_value:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        dt = parsedate_to_datetime(date_value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def parse_feed(feed_url: str) -> list[dict[str, str | datetime]]:
    with urlopen(feed_url, timeout=20) as response:
        xml_content = response.read()

    root = ET.fromstring(xml_content)
    entries: list[dict[str, str | datetime]] = []

    # RSS 2.0 format
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = (item.findtext("pubDate") or "").strip()

        if title and link:
            entries.append(
                {
                    "title": title,
                    "link": link,
                    "published": to_utc(pub_date_raw),
                }
            )

    # Atom fallback
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("./atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        updated_raw = (
            entry.findtext("atom:updated", default="", namespaces=ns) or ""
        ).strip()
        published_raw = (
            entry.findtext("atom:published", default="", namespaces=ns) or ""
        ).strip()

        link_elem = entry.find("atom:link[@rel='alternate']", ns)
        if link_elem is None:
            link_elem = entry.find("atom:link", ns)
        link = (link_elem.get("href") if link_elem is not None else "") or ""
        link = link.strip()

        if title and link:
            entries.append(
                {
                    "title": title,
                    "link": link,
                    "published": to_utc(updated_raw or published_raw),
                }
            )

    return entries


def extract_feed_slug(feed_url: str) -> str:
    match = re.search(r"/tag/([^/]+)/feed/", feed_url)
    if not match:
        raise ValueError(f"URL feed non valido o non supportato: {feed_url}")
    return match.group(1).strip().lower()


def build_press_code(feed_url: str, title: str) -> str:
    feed_slug = extract_feed_slug(feed_url)
    feed_prefix = FEED_PREFIX_MAP.get(feed_slug)
    if not feed_prefix:
        raise ValueError(f"Feed non mappato: {feed_slug}")

    lowered_title = title.lower().replace("’", "'").strip()

    title_code = ""
    for needle, code in TITLE_CODE_RULES:
        if needle in lowered_title:
            title_code = code
            break
    if not title_code:
        raise ValueError(f"Titolo non classificabile: {title}")

    provisional_code = "PRO" if "(dati provvisori)" in lowered_title else ""

    month_match = MONTH_YEAR_RE.search(lowered_title)
    if not month_match:
        raise ValueError(f"Mese/anno non trovati nel titolo: {title}")

    month_abbr = MONTH_MAP[month_match.group(1)]
    year = month_match.group(2)

    return f"{feed_prefix}{title_code}{provisional_code}{month_abbr}{year}"


def main() -> None:
    feeds_file = Path(__file__).with_name("rss_feeds.txt")
    output_file = Path(__file__).with_name("rss_latest_hash_output.csv")
    feed_urls = read_feed_urls(feeds_file)

    if not feed_urls:
        raise ValueError("Nessun feed trovato nel file di input.")

    rows_to_add: list[list[str]] = []
    for feed_url in feed_urls:
        try:
            feed_entries = parse_feed(feed_url)
        except (URLError, ET.ParseError) as exc:
            print(f"Feed non leggibile ({feed_url}): {exc}")
            continue

        if not feed_entries:
            continue

        latest = max(feed_entries, key=lambda entry: entry["published"])
        title = str(latest["title"])
        link = str(latest["link"])
        published = latest["published"]
        assert isinstance(published, datetime)
        data_str = (
            "" if published == datetime.min.replace(tzinfo=timezone.utc)
            else published.strftime("%Y-%m-%d")
        )
        normalized_title_for_hash = "".join(title.strip().upper().split())
        title_hash = hashlib.sha256(normalized_title_for_hash.encode("utf-8")).hexdigest()
        try:
            code = build_press_code(feed_url, title)
        except ValueError as exc:
            print(f"Classificazione non riuscita ({feed_url}): {exc}")
            continue

        rows_to_add.append([data_str, title, code, title_hash, link])

    if not rows_to_add:
        raise RuntimeError("Nessun elemento valido trovato nei feed RSS.")

    # existing_rows: link -> (data, titolo, codice, id_hash)
    existing_rows: dict[str, tuple[str, str, str, str]] = {}

    if output_file.exists():
        with output_file.open("r", encoding="utf-8", newline="") as csvfile:
            reader = csv.reader(csvfile)
            rows = [row for row in reader if row]

        if rows:
            header_normalized = [col.strip().lower() for col in rows[0]]
            is_new_5_header = header_normalized == HEADER_5_NEW
            data_rows = rows[1:] if header_normalized in KNOWN_HEADERS else rows

            for row in data_rows:
                if is_new_5_header and len(row) >= 5:
                    # Formato aggiornato: data, titolo, codice, id_hash, link
                    date_value, title_value, code_value, id_hash_value, link_value = row[:5]
                    existing_rows[link_value] = (date_value, title_value, code_value, id_hash_value)
                elif len(row) >= 4:
                    # Formato legacy (senza colonna "data"): titolo, codice,
                    # id_hash, link. La data di rilascio non e' nota per
                    # queste righe: viene lasciata vuota.
                    title_value, code_value, id_hash_value, link_value = row[:4]
                    existing_rows[link_value] = ("", title_value, code_value, id_hash_value)

    for data_value, title_value, code_value, id_hash_value, link_value in rows_to_add:
        existing_rows[link_value] = (data_value, title_value, code_value, id_hash_value)

    with output_file.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(HEADER_5_NEW)
        for link_value, (date_value, title_value, code_value, id_hash_value) in sorted(existing_rows.items()):
            writer.writerow([date_value, title_value, code_value, id_hash_value, link_value])


if __name__ == "__main__":
    main()