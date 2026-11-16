"""Wymazane sekrety (hasła, klucze API): zamiana na znacznik w tekście i w bajtach plików.

Archiwizator wczytuje rozmowy ze źródła od nowa przy każdej ich zmianie, więc sam musi znać wymazane sekrety –
inaczej wróciłyby z pliku aplikacji. Baza przechowuje je zaszyfrowane przez Windows (DPAPI, klucz konta
użytkownika): jawny sekret nie leży w archiwum ani w jego kopii zapasowej.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger("skerimai")

ZNACZNIK = "[wymazano]"
MIN_DLUGOSC = 4
_ENTROPIA = b"SkerimAI.sekrety"
# Pliki tekstowe w magazynie załączników bywają w UTF-16 lub CP1250 (dokumenty.py czyta wszystkie trzy).
_KODOWANIA = ("utf-8", "utf-16-le", "cp1250")


class Sekrety:
    def __init__(self, sekrety: list[str] = ()):
        self._teksty = sorted(set(sekrety), key=len, reverse=True)
        zamiany: dict[bytes, bytes] = {}
        for sekret in self._teksty:
            for kodowanie in _KODOWANIA:
                try:
                    zamiany.setdefault(sekret.encode(kodowanie), ZNACZNIK.encode(kodowanie))
                except UnicodeEncodeError:
                    pass
            for wariant in _warianty_json(sekret):
                zamiany.setdefault(wariant.encode("utf-8"), ZNACZNIK.encode("utf-8"))
        # Najpierw dłuższe – wariant z ucieczkami zawiera czasem krótszy.
        self._bajty = sorted(zamiany.items(), key=lambda z: len(z[0]), reverse=True)

    def __bool__(self) -> bool:
        return bool(self._teksty)

    def __contains__(self, sekret: str) -> bool:
        return sekret in self._teksty

    def tekst(self, tekst: str | None) -> str | None:
        if not tekst:
            return tekst
        for sekret in self._teksty:
            tekst = tekst.replace(sekret, ZNACZNIK)
        return tekst

    def bajty(self, dane: bytes) -> bytes:
        """Surowe kopie (JSONL – sekret bywa w napisie JSON z ucieczkami, także zagnieżdżonym) i pliki tekstowe."""
        for szukane, zamiana in self._bajty:
            dane = dane.replace(szukane, zamiana)
        return dane

    def wystepuje_w(self, dane: bytes) -> bool:
        return any(szukane in dane for szukane, _ in self._bajty)


def _warianty_json(sekret: str) -> set[str]:
    """Postaci sekretu w napisie JSON: z ucieczkami \\uXXXX i bez, „/” jako „\\/”, napis JSON w napisie JSON."""
    warianty = {sekret}
    for _ in range(2):
        for w in list(warianty):
            warianty.add(json.dumps(w, ensure_ascii=False)[1:-1])
            warianty.add(json.dumps(w)[1:-1])
    warianty |= {w.replace("/", "\\/") for w in warianty}
    return warianty


def wczytaj(polaczenie: sqlite3.Connection) -> Sekrety:
    """Sekrety, których nie da się odszyfrować (baza z innego konta albo komputera), są pomijane z ostrzeżeniem."""
    teksty = []
    for wiersz in polaczenie.execute("SELECT id, szyfr FROM sekrety"):
        try:
            teksty.append(odszyfruj(wiersz["szyfr"]).decode("utf-8"))
        except (OSError, UnicodeDecodeError):
            log.warning("Nie udało się odszyfrować wymazanego sekretu #%s (baza z innego konta Windows?)", wiersz["id"])
    return Sekrety(teksty)


def zapamietaj(polaczenie: sqlite3.Connection, sekret: str) -> None:
    if sekret in wczytaj(polaczenie):
        return
    polaczenie.execute(
        "INSERT INTO sekrety (szyfr, dodano) VALUES (?, ?)",
        (zaszyfruj(sekret.encode("utf-8")), datetime.now(timezone.utc).isoformat()),
    )


# ---------------------------------------------------------------- DPAPI

def zaszyfruj(dane: bytes) -> bytes:
    return _dpapi("CryptProtectData", dane)


def odszyfruj(dane: bytes) -> bytes:
    return _dpapi("CryptUnprotectData", dane)


def _dpapi(nazwa: str, dane: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Szyfrowanie sekretów (DPAPI) działa tylko w Windows")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):  # DATA_BLOB
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    funkcja = getattr(crypt32, nazwa)
    funkcja.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    funkcja.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def blob(bajty: bytes) -> tuple[Blob, object]:
        bufor = ctypes.create_string_buffer(bajty, len(bajty))
        return Blob(len(bajty), ctypes.cast(bufor, ctypes.POINTER(ctypes.c_char))), bufor

    wejscie, _bufor = blob(dane)
    entropia, _bufor_entropii = blob(_ENTROPIA)
    wynik = Blob()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not funkcja(ctypes.byref(wejscie), None, ctypes.byref(entropia), None, None, CRYPTPROTECT_UI_FORBIDDEN,
                   ctypes.byref(wynik)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(wynik.pbData, wynik.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(wynik.pbData, ctypes.c_void_p))
