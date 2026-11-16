"""Blokada międzyprocesowa archiwum: naraz pisze jeden archiwizator (hook, serwer MCP, wiersz poleceń)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt

    def _zablokuj(plik) -> None:
        plik.seek(0)
        msvcrt.locking(plik.fileno(), msvcrt.LK_NBLCK, 1)

    def _odblokuj(plik) -> None:
        plik.seek(0)
        msvcrt.locking(plik.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _zablokuj(plik) -> None:
        fcntl.flock(plik.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _odblokuj(plik) -> None:
        fcntl.flock(plik.fileno(), fcntl.LOCK_UN)


# Tyle czekają procesy, które muszą zarchiwizować (hook, wiersz poleceń) – pełne wczytanie trwa do minuty.
CZEKAJ_NA_ARCHIWUM_S = 120.0
_ODSTEP_PROB_S = 0.2


@contextmanager
def blokada(sciezka: Path, czekaj_s: float) -> Iterator[bool]:
    """Daje True, gdy blokadę udało się uzyskać w ciągu `czekaj_s`, a False, gdy nie (wtedy bez blokady).

    System zwalnia blokadę także po zabiciu procesu, więc nie zostaje „martwa” blokada.
    """
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    with open(sciezka, "a+b") as plik:
        koniec = time.monotonic() + czekaj_s
        while True:
            try:
                _zablokuj(plik)
                break
            except OSError:
                if time.monotonic() >= koniec:
                    yield False
                    return
                time.sleep(_ODSTEP_PROB_S)
        try:
            yield True
        finally:
            _odblokuj(plik)
