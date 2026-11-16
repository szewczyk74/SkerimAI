"""Tokenizacja i polski stemming (pystempel) z pamięcią podręczną rdzeni w bazie."""

from __future__ import annotations

import contextlib
import io
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

_SLOWO = re.compile(r"\w+", re.UNICODE)
_POLSKIE = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
# Tokenizer FTS5 (unicode61 remove_diacritics) zdejmuje ogonki tylko z liter rozkładalnych w Unicode – „ł” nie
# jest rozkładalne i zostaje, więc „lodz” nie trafiało w „Łódź”. Indeks i zapytania dostają „l” zamiast „ł”.
_BEZ_L_KRESKOWANEGO = str.maketrans("łŁ", "lL")
_PACZKA = 500


def tokenizuj(tekst: str) -> list[str]:
    """Słowa małymi literami; pomija pojedyncze litery (liczby zostają)."""
    tokeny = (m.group(0).lower() for m in _SLOWO.finditer(tekst))
    return [t for t in tokeny if len(t) >= 2 or t.isdigit()]


def liczba(n: int, jeden: str, kilka: str, wiele: str) -> str:
    """1 rozmowa, 3 rozmowy, 12 rozmów, 22 rozmowy."""
    if n == 1:
        return f"{n} {jeden}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {kilka}"
    return f"{n} {wiele}"


def bez_ogonkow(tekst: str) -> str:
    """Małe litery bez polskich znaków; długość tekstu się nie zmienia (indeksy się zgadzają)."""
    return tekst.lower().translate(_POLSKIE)


def do_indeksu(tekst: str) -> str:
    """Tekst do kolumn FTS5 i do zapytań: „ł” → „l” (resztę ogonków zdejmuje sam tokenizer)."""
    return tekst.translate(_BEZ_L_KRESKOWANEGO)


@lru_cache(maxsize=1)
def _stemmer():
    # pystempel 2.0 importuje aliasy typów usunięte z importlib.resources w Pythonie 3.13;
    # służą tylko do adnotacji, więc wystarczy je podstawić przed importem.
    import importlib.resources as zasoby

    for nazwa in ("Package", "Resource"):
        if not hasattr(zasoby, nazwa):
            setattr(zasoby, nazwa, str)

    import pystempel
    from pystempel import Stemmer

    tabela = Path(pystempel.__file__).parent / "data" / "polimorf" / "stemmer_polimorf.tbl.gz"
    with contextlib.redirect_stderr(io.StringIO()):  # pasek postępu tqdm przy wczytywaniu
        return Stemmer.from_file(tabela)


def rdzen_slowa(slowo: str) -> str:
    """Rdzeń słowa; liczby, kody i krótkie słowa bez zmian."""
    if len(slowo) < 3 or not slowo.isalpha():
        return slowo
    return _stemmer()(slowo) or slowo


class Rdzenie:
    """Rdzenie słów z pamięcią w tabeli `rdzenie` – stemmer wczytuje się tylko dla nowych słów."""

    def __init__(self, polaczenie: sqlite3.Connection):
        self._polaczenie = polaczenie

    def mapa(self, slowa: set[str]) -> dict[str, str]:
        wynik: dict[str, str] = {}
        lista = sorted(slowa)
        for i in range(0, len(lista), _PACZKA):
            paczka = lista[i : i + _PACZKA]
            znaczniki = ",".join("?" * len(paczka))
            for wiersz in self._polaczenie.execute(
                f"SELECT slowo, rdzen FROM rdzenie WHERE slowo IN ({znaczniki})", paczka
            ):
                wynik[wiersz["slowo"]] = wiersz["rdzen"]

        nowe = [(s, rdzen_slowa(s)) for s in lista if s not in wynik]
        if nowe:
            self._polaczenie.executemany("INSERT OR IGNORE INTO rdzenie (slowo, rdzen) VALUES (?, ?)", nowe)
            wynik.update(nowe)
        return wynik

    def tekst_rdzeni(self, tokeny: list[str], mapa: dict[str, str]) -> str:
        return do_indeksu(" ".join(mapa.get(t, t) for t in tokeny))
