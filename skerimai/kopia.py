"""Kopia zapasowa archiwum – spójna także wtedy, gdy Claude Code, Codex i KreionAI są otwarte.

Bazę kopiuje API kopii SQLite: obejmuje zapisy wciąż leżące w pliku `-wal`, które zwykłe skopiowanie
`skerimai.db` by pominęło. Na czas kopii trzymana jest blokada archiwizatora – baza, załączniki i surowe
kopie są ze sobą zgodne, a archiwizator nie podmienia pliku, który właśnie się kopiuje (w Windows by się
to nie udało). Wynik to gotowy katalog archiwum: można na niego wskazać SKERIMAI_DIR.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .blokada import CZEKAJ_NA_ARCHIWUM_S, blokada
from .konfiguracja import Sciezki

KATALOGI = ("zalaczniki", "surowe")


@dataclass(frozen=True)
class WynikKopii:
    katalog: Path
    rozmowy: int
    wiadomosci: int
    pliki: int
    bajty: int


def utworz_kopie(sciezki: Sciezki, folder: Path, czekaj_s: float = CZEKAJ_NA_ARCHIWUM_S) -> WynikKopii:
    """Kopiuje archiwum do nowego podkatalogu `folder/skerimai-RRRR-MM-DD_GGMMSS`."""
    zrodlo = sciezki.katalog.resolve()
    folder = folder.resolve()
    if folder == zrodlo or zrodlo in folder.parents:
        raise ValueError(f"Kopia nie może leżeć w samym archiwum ({zrodlo}).")
    cel = _wolna_nazwa(folder / f"skerimai-{datetime.now():%Y-%m-%d_%H%M%S}")
    roboczy = cel.with_name(cel.name + ".niepelna")

    with blokada(sciezki.blokada, czekaj_s) as uzyskana:
        if not uzyskana:
            raise TimeoutError(f"Archiwum zajęte przez archiwizator dłużej niż {czekaj_s:.0f} s – spróbuj później.")
        try:
            roboczy.mkdir(parents=True)
            _kopiuj_baze(sciezki.baza, roboczy / sciezki.baza.name)
            for nazwa in KATALOGI:
                if (sciezki.katalog / nazwa).is_dir():
                    shutil.copytree(sciezki.katalog / nazwa, roboczy / nazwa, ignore=shutil.ignore_patterns("*.tmp"))
            rozmowy, wiadomosci = _sprawdz(roboczy / sciezki.baza.name)
        except BaseException:
            shutil.rmtree(roboczy, ignore_errors=True)
            raise
    roboczy.rename(cel)

    pliki = [p for p in cel.rglob("*") if p.is_file()]
    return WynikKopii(cel, rozmowy, wiadomosci, len(pliki), sum(p.stat().st_size for p in pliki))


def _wolna_nazwa(cel: Path) -> Path:
    """Dwie kopie w tej samej sekundzie dostają przyrostek -2, -3…"""
    kandydat, nr = cel, 1
    while kandydat.exists() or kandydat.with_name(kandydat.name + ".niepelna").exists():
        nr += 1
        kandydat = cel.with_name(f"{cel.name}-{nr}")
    return kandydat


def _kopiuj_baze(zrodlo: Path, cel: Path) -> None:
    """Kopia w trybie DELETE – jeden samodzielny plik, bez -wal i -shm (archiwizator i tak włączy WAL)."""
    zrodlowa = sqlite3.connect(zrodlo)
    docelowa = sqlite3.connect(cel)
    try:
        zrodlowa.backup(docelowa)
        docelowa.execute("PRAGMA journal_mode = DELETE")
    finally:
        docelowa.close()
        zrodlowa.close()


def _sprawdz(baza: Path) -> tuple[int, int]:
    polaczenie = sqlite3.connect(baza)
    try:
        wynik = polaczenie.execute("PRAGMA quick_check").fetchone()[0]
        if wynik != "ok":
            raise RuntimeError(f"Kopia bazy jest uszkodzona: {wynik}")
        return (polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0],
                polaczenie.execute("SELECT count(*) FROM wiadomosci").fetchone()[0])
    finally:
        polaczenie.close()
