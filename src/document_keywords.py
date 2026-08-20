import os
import re
from urllib.parse import urljoin

from unidecode import unidecode


DOCUMENT_KEYWORDS = [
    "prescripciones tecnicas", "pliego", "ppt", "pcap", "clausulas",
    "memoria justificativa", "cuadro de caracteristicas",
    "tender specification", "technical conditions", "specifications",
    "specification", "tender document",
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


def find_document_links(soup, base_url):
    """Devuelve candidatos documentales ordenados, incluyendo tablas de pliegos."""
    candidates = []
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        container = link.find_parent(["tr", "table", "section", "div"])
        row = link.find_parent("tr")
        table = link.find_parent("table")
        ancestors = link.find_parents(["tr", "table", "section", "div"], limit=5)
        container_id = " ".join(
            " ".join((ancestor.get("id", ""), " ".join(ancestor.get("class", []))))
            for ancestor in ancestors
        )
        container_text = " ".join((
            "" if container is None else container.get_text(" ", strip=True),
            "" if row is None else row.get_text(" ", strip=True),
            "" if table is None else table.get("id", ""),
        ))
        searchable = " ".join((
            link.get_text(" ", strip=True), link.get("title", ""), href,
            " ".join(
                image.get("alt", "") + " " + image.get("title", "")
                for image in link.find_all("img")
            ),
            container_id, container_text,
        ))
        normalized = normalize_document_text(searchable)
        priority = None
        for priority, keyword in enumerate(DOCUMENT_KEYWORDS):
            if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", normalized):
                break
        else:
            priority = None
        # En portales agregados el enlace carece de texto, pero su tabla se
        # identifica de forma estable como tabla de pliegos.
        if priority is None and not any(
            term in normalize_document_text(container_id)
            for term in ("pliego", "document", "prescrip")
        ):
            continue
        image_text = normalize_document_text(" ".join(
            image.get("alt", "") + " " + image.get("title", "")
            for image in link.find_all("img")
        ))
        score = (priority if priority is not None else len(DOCUMENT_KEYWORDS)) * 10
        if "pdf" not in image_text and not href.lower().endswith(".pdf"):
            score += 5
        candidates.append((score, urljoin(base_url, href)))
    ordered = []
    for _, url in sorted(candidates, key=lambda item: item[0]):
        if url not in ordered:
            ordered.append(url)
    return ordered


def find_document_link(soup, base_url):
    """Compatibilidad: devuelve el mejor candidato documental."""
    links = find_document_links(soup, base_url)
    return links[0] if links else None


def download_document(session, url, output_dir, filename, timeout=30):
    """Descarga atómicamente y rechaza HTML/Office con extensión PDF falsa."""
    os.makedirs(output_dir, exist_ok=True)
    response = session.get(url, stream=True, timeout=timeout)
    response.raise_for_status()
    path = os.path.join(output_dir, filename)
    temporary = f"{path}.part"
    iterator = response.iter_content(8192)
    first = next((chunk for chunk in iterator if chunk), b"")
    if not first.startswith(b"%PDF"):
        content_type = response.headers.get("Content-Type", "desconocido")
        raise ValueError(
            f"El enlace no devolvió un PDF real (Content-Type: {content_type})"
        )
    try:
        with open(temporary, "wb") as file:
            file.write(first)
            for chunk in iterator:
                if chunk:
                    file.write(chunk)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return filename
