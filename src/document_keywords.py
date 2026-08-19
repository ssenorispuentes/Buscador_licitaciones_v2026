import os
import re
from urllib.parse import urljoin

from unidecode import unidecode


DOCUMENT_KEYWORDS = [
    "prescripciones tecnicas", "pliego", "ppt", "pcap", "clausulas",
    "memoria justificativa", "cuadro de caracteristicas",
    "tender specification", "technical conditions", "specifications",
    "specification", "tender document", "tender notice", "notice",
    "prescripcions tecniques", "veure documents", "clausules", "plec", "memoria",
    "baldintza teknikoak", "plegua", "agiria",
]


def normalize_document_text(value):
    return re.sub(r"\s+", " ", unidecode(str(value or "")).lower()).strip()


def matches_document_text(value):
    text = normalize_document_text(value)
    return any(
        re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text)
        for keyword in DOCUMENT_KEYWORDS
    )


def find_document_link(soup, base_url):
    """Devuelve el enlace con el término documental más específico."""
    best = None
    for link in soup.find_all("a", href=True):
        searchable = " ".join((link.get_text(" ", strip=True), link.get("title", ""), link["href"]))
        normalized = normalize_document_text(searchable)
        for priority, keyword in enumerate(DOCUMENT_KEYWORDS):
            if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", normalized):
                candidate = (priority, urljoin(base_url, link["href"]))
                if best is None or candidate[0] < best[0]:
                    best = candidate
                break
    return best[1] if best else None


def download_document(session, url, output_dir, filename, timeout=30):
    os.makedirs(output_dir, exist_ok=True)
    response = session.get(url, stream=True, timeout=timeout)
    response.raise_for_status()
    path = os.path.join(output_dir, filename)
    with open(path, "wb") as file:
        for chunk in response.iter_content(8192):
            if chunk:
                file.write(chunk)
    return filename
