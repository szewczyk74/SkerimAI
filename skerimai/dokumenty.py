"""Wyciąganie tekstu z załączonych dokumentów (PDF, DOCX, pliki tekstowe) do indeksu wyszukiwania."""

from __future__ import annotations

import codecs
import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

# Podnieść po dodaniu formatu lub zmianie wyciągania – archiwizator przejrzy wtedy załączniki jeszcze raz.
WERSJA = 1

ROZSZERZENIA_TEKSTOWE = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".xml", ".ini", ".yaml", ".yml"}
_MAKS_BAJTOW = 20 * 1024 * 1024
_MAKS_ZNAKOW = 2_000_000
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Ostrzeżenia pypdf o nietypowej budowie pliku zaśmiecałyby dziennik archiwizatora.
logging.getLogger("pypdf").setLevel(logging.ERROR)


def wyciagnij_tekst(plik: Path) -> str | None:
    """Tekst dokumentu; None dla obrazów, nieznanych formatów i skanów bez warstwy tekstu."""
    ext = plik.suffix.lower()
    if ext == ".pdf":
        tekst = _pdf(plik)
    elif ext == ".docx":
        tekst = _docx(plik)
    elif ext in ROZSZERZENIA_TEKSTOWE:
        tekst = _tekstowy(plik)
    else:
        return None
    tekst = tekst.replace("\x00", "").strip()
    return tekst[:_MAKS_ZNAKOW] or None


def _pdf(plik: Path) -> str:
    from pypdf import PdfReader  # tu, nie na górze: import trwa ~0,25 s i opóźniałby start serwera MCP

    czytnik = PdfReader(plik)
    if czytnik.is_encrypted and not czytnik.decrypt(""):  # PDF-y bez hasła do otwarcia bywają zaszyfrowane
        return ""
    return "\n\n".join(strona.extract_text() or "" for strona in czytnik.pages)


def _docx(plik: Path) -> str:
    with zipfile.ZipFile(plik) as archiwum:
        if archiwum.getinfo("word/document.xml").file_size > _MAKS_BAJTOW:
            return ""
        korzen = ElementTree.fromstring(archiwum.read("word/document.xml"))
    akapity = []
    for akapit in korzen.iter(f"{_WORD}p"):
        czesci = []
        for element in akapit.iter():
            if element.tag == f"{_WORD}t":
                czesci.append(element.text or "")
            elif element.tag == f"{_WORD}tab":
                czesci.append("\t")
            elif element.tag in (f"{_WORD}br", f"{_WORD}cr"):
                czesci.append("\n")
        akapity.append("".join(czesci))
    return "\n".join(akapity)


def _tekstowy(plik: Path) -> str:
    with plik.open("rb") as f:
        dane = f.read(_MAKS_BAJTOW)
    if dane.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):  # „Unicode” z Notatnika
        return dane.decode("utf-16", errors="replace")
    for kodowanie in ("utf-8-sig", "cp1250"):
        try:
            return dane.decode(kodowanie)
        except UnicodeDecodeError:
            pass
    return dane.decode("utf-8", errors="replace")
