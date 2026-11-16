"""Dziennik archiwizacji w tle (`archiwizator.log`): ostatni wpis – dla `skerimai sprawdz` i ikony w zasobniku."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_WPIS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (INFO|WARNING|ERROR) (.*)$")
# Ikona czyta dziennik co pół minuty, więc tylko jego koniec – z zapasem na długi traceback po ostatnim wpisie.
_KONIEC_PLIKU = 64_000


@dataclass(frozen=True)
class Wpis:
    czas: str  # RRRR-MM-DD GG:MM:SS, czas lokalny
    poziom: str  # INFO, WARNING, ERROR
    tresc: str


def ostatni_wpis(plik: Path) -> Wpis | None:
    try:
        with open(plik, "rb") as wejscie:
            wejscie.seek(0, 2)
            poczatek = max(0, wejscie.tell() - _KONIEC_PLIKU)
            for od in dict.fromkeys((poczatek, 0)):  # w końcówce sam traceback – wtedy cały plik (rotowany, ≤ 1 MB)
                wejscie.seek(od)
                if wpis := _ostatni_w(wejscie.read().decode("utf-8", "replace")):
                    return wpis
    except OSError:
        pass
    return None


def _ostatni_w(tekst: str) -> Wpis | None:
    for linia in reversed(tekst.splitlines()):
        if wpis := _WPIS.match(linia):
            return Wpis(*wpis.groups())
    return None
