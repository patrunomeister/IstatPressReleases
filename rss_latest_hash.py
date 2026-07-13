import hashlib
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
import xml.etree.ElementTree as ET


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
        title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
        rows_to_add.append([title, title_hash, link])

    if not rows_to_add:
        raise RuntimeError("Nessun elemento valido trovato nei feed RSS.")

    existing_rows: set[tuple[str, str, str]] = set()
    has_header = False

    if output_file.exists():
        with output_file.open("r", encoding="utf-8", newline="") as csvfile:
            reader = csv.reader(csvfile)
            for index, row in enumerate(reader):
                if not row:
                    continue

                if (
                    index == 0
                    and [col.strip().lower() for col in row[:3]]
                    == ["titolo", "hashtag", "link"]
                ):
                    has_header = True
                    continue

                if len(row) >= 3:
                    existing_rows.add((row[0], row[1], row[2]))

    should_write_header = (not output_file.exists()) or (
        output_file.exists() and output_file.stat().st_size == 0
    )

    with output_file.open("a", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        if should_write_header and not has_header:
            writer.writerow(["titolo", "hashtag", "link"])
        for row_to_add in rows_to_add:
            if tuple(row_to_add) in existing_rows:
                continue
            writer.writerow(row_to_add)


if __name__ == "__main__":
    main()