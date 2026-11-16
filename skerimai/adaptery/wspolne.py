"""Funkcje wspólne dla adapterów."""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath

import zstandard

from ..model import ROLA_UZYTKOWNIK, Rozmowa

_DLUGOSC_TYTULU = 80


def czytaj_bajty(plik: Path) -> bytes:
    """Zawartość pliku źródła; `.zst` rozpakowany (Codex kompresuje tak zamknięte sesje)."""
    if plik.suffix == ".zst":
        return _rozpakuj_zstd(plik.read_bytes())
    return plik.read_bytes()


def _rozpakuj_zstd(dane: bytes) -> bytes:
    """Wszystkie ramki po kolei. Niepełna ramka (plik obcięty albo jeszcze zapisywany) to błąd – `stream_reader`
    oddawał wtedy po cichu pusty wynik, a archiwizator zastępował nim całą zapisaną wcześniej rozmowę."""
    czesci = []
    while dane:
        rozpakowanie = zstandard.ZstdDecompressor().decompressobj()
        czesci.append(rozpakowanie.decompress(dane))
        if not rozpakowanie.eof:
            raise zstandard.ZstdError("niepełny plik .zst")
        dane = rozpakowanie.unused_data
    return b"".join(czesci)


def czytaj_jsonl(plik: Path) -> list[dict]:
    """Rekordy pliku JSONL. `utf-8-sig`: KreionAI zapisuje pliki z BOM."""
    rekordy = []
    # split("\n"), nie splitlines(): ten tnie też na U+2028 i podobnych, które JSON może mieć w napisach.
    for linia in czytaj_bajty(plik).decode("utf-8-sig", errors="replace").split("\n"):
        if not linia.strip():
            continue
        try:
            rekord = json.loads(linia)
        except json.JSONDecodeError:
            continue  # ostatnia linia bywa niedopisana, gdy sesja trwa
        if isinstance(rekord, dict):
            rekordy.append(rekord)
    return rekordy


def nazwa_katalogu(cwd: str | None) -> str | None:
    """Projekt z katalogu roboczego, np. C:\\Projekty\\sklep.pl → sklep.pl."""
    if not cwd:
        return None
    return PureWindowsPath(cwd).name or cwd


def tytul_z_pierwszej(rozmowa: Rozmowa) -> str | None:
    pierwsza = next((w for w in rozmowa.wiadomosci if w.rola == ROLA_UZYTKOWNIK and w.tekst), None)
    if pierwsza is None:
        return None
    jedna_linia = " ".join(pierwsza.tekst.split())
    return jedna_linia if len(jedna_linia) <= _DLUGOSC_TYTULU else jedna_linia[: _DLUGOSC_TYTULU - 1] + "…"
