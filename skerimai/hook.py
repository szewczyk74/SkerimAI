"""Hook Claude Code `SessionEnd`: archiwizacja zakończonej sesji bez opóźniania jej zamknięcia.

Claude Code daje hookom SessionEnd łącznie 1,5 s, a archiwizacja sesji z nowymi słowami trwa
kilka sekund (wczytanie stemmera). Dlatego hook (`skerimai hook`) tylko uruchamia odłączony
proces `python -m skerimai.hook` i od razu kończy. Proces w tle nie ma okna – wynik pokazuje
w powiadomieniu Windows, a szczegóły i błędy zapisuje w dzienniku `archiwizator.log`.
Ten sam proces w tle uruchamia ikona w zasobniku – dla źródeł bez hooka (Codex, KreionAI).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import BinaryIO, Callable

from . import powiadomienie
from .adaptery import ADAPTERY, Adapter
from .archiwizator import Raport, archiwizuj
from .baza import polacz
from .blokada import CZEKAJ_NA_ARCHIWUM_S, blokada
from .konfiguracja import Sciezki, domyslne_sciezki
from .tekst import liczba

log = logging.getLogger("skerimai.hook")

ZRODLO = "claude_code"
_ROZMIAR_DZIENNIKA = 1_000_000


def opis_zdarzenia(dane: str) -> str:
    """Np. „SessionEnd/clear, sesja 9e3b7d8c” z JSON-a, który Claude Code przekazuje hookowi na stdin."""
    try:
        zdarzenie = json.loads(dane)
    except ValueError:
        zdarzenie = None
    if not isinstance(zdarzenie, dict):
        return "hook"
    opis = zdarzenie.get("hook_event_name") or "hook"
    if zdarzenie.get("reason"):
        opis += f"/{zdarzenie['reason']}"
    if zdarzenie.get("session_id"):
        opis += f", sesja {str(zdarzenie['session_id'])[:8]}"
    return opis


def zlec_archiwizacje(wejscie: BinaryIO) -> None:
    """Uruchamia archiwizator w odłączonym procesie i wraca od razu.

    Wejście czytane jako bajty: Claude Code pisze UTF-8, a stdin w Windows dekodowałby je w cp1250.
    """
    dane = "" if wejscie.isatty() else wejscie.read().decode("utf-8", "replace")
    uruchom_w_tle([sys.executable, "-m", "skerimai.hook", opis_zdarzenia(dane)])


def zlec_archiwizacje_zrodel(opis: str, zrodla: list[str], tylko_bledy: bool = False) -> subprocess.Popen:
    """Dla ikony w zasobniku: archiwizacja wybranych źródeł w odłączonym procesie."""
    polecenie = [sys.executable, "-m", "skerimai.hook", opis]
    for zrodlo in zrodla:
        polecenie += ["--zrodlo", zrodlo]
    if tylko_bledy:
        polecenie.append("--tylko-bledy")
    return uruchom_w_tle(polecenie)


def interpreter_konsolowy(program: str) -> str:
    """`pythonw.exe` → sąsiedni `python.exe` (gdy istnieje). Launcher `pythonw.exe` w venv to program okienkowy:
    Windows ignoruje dla niego CREATE_NO_WINDOW, a uruchamiany przez niego konsolowy `python.exe` dostaje
    nową, widoczną konsolę – puste okno co 5 min z ikony w zasobniku, które zabiera fokus klawiatury."""
    sciezka = Path(program)
    if sciezka.name.lower() != "pythonw.exe":
        return program
    konsolowy = sciezka.with_name("python.exe")
    return str(konsolowy) if konsolowy.is_file() else program


def uruchom_w_tle(polecenie: list[str]) -> subprocess.Popen:
    """Bez okna konsoli, we własnej grupie procesów i bez dziedziczenia potoków hooka –
    inaczej Claude Code czekałby, aż proces w tle zamknie jego stdout.

    Nie DETACHED_PROCESS: `.venv\\Scripts\\python.exe` to launcher, który uruchamia właściwy
    interpreter jako proces potomny, a ten bez konsoli rodzica dostaje od Windows nową, widoczną –
    puste czarne okno przy zamykaniu Claude Code. Konsolę z CREATE_NO_WINDOW potomek dziedziczy ukrytą
    (tylko z konsolowego launchera – stąd `interpreter_konsolowy`)."""
    if os.name == "nt":
        polecenie = [interpreter_konsolowy(polecenie[0]), *polecenie[1:]]
        opcje = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        opcje = {"start_new_session": True}
    return subprocess.Popen(
        polecenie,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **opcje,
    )


def podsumowanie(raport: Raport) -> tuple[str, str]:
    """Nagłówek i treść powiadomienia po udanej archiwizacji.

    Archiwizator zapisuje zmienioną rozmowę w całości, więc liczba wiadomości to cała rozmowa, nie przyrost.
    """
    nieczytelne = (f"Nie udało się odczytać: {liczba(raport.nieczytelne, 'sesja', 'sesje', 'sesji')} – "
                   "szczegóły w dzienniku.") if raport.nieczytelne else ""
    if not raport.zaktualizowane:
        if nieczytelne:
            return "Archiwizacja niepełna", nieczytelne
        return "Archiwum jest aktualne", "Nic nowego do zapisania."
    if raport.zaktualizowane == 1:
        naglowek, czesci = "Rozmowa zarchiwizowana", []
    else:
        naglowek, czesci = "Rozmowy zarchiwizowane", [liczba(raport.zaktualizowane, "rozmowa", "rozmowy", "rozmów")]
    czesci.append(liczba(raport.wiadomosci, "wiadomość", "wiadomości", "wiadomości"))
    if raport.nowe_zalaczniki:
        czesci.append(liczba(raport.nowe_zalaczniki, "nowy załącznik", "nowe załączniki", "nowych załączników"))
    tresc = "Zapisano: " + ", ".join(czesci) + "."
    if raport.brakujace_zalaczniki:
        tresc += f" Nie znaleziono plików załączników: {raport.brakujace_zalaczniki}."
    if nieczytelne:
        tresc += " " + nieczytelne
    return naglowek, tresc


def _bez_powiadomienia(naglowek: str, tresc: str) -> None:
    pass


def archiwizuj_w_tle(
    sciezki: Sciezki,
    opis: str,
    adaptery: list[Adapter] | None = None,
    powiadom: Callable[[str, str], None] = _bez_powiadomienia,
    tylko_bledy: bool = False,
) -> bool:
    """Część wykonywana w tle. Najpierw czeka, aż inny archiwizator (np. serwer MCP) skończy.
    `tylko_bledy`: powiadomienie tylko o błędzie – dla archiwizacji, o którą nikt nie prosił (ikona co kilka minut)."""
    adaptery = adaptery if adaptery is not None else [ADAPTERY[ZRODLO]()]
    try:
        with blokada(sciezki.blokada, CZEKAJ_NA_ARCHIWUM_S) as uzyskana:
            if not uzyskana:
                log.warning("%s: archiwum zajęte dłużej niż %.0f s – pominięto", opis, CZEKAJ_NA_ARCHIWUM_S)
                if not tylko_bledy:
                    powiadom("Archiwum zajęte", "Rozmowa trafi do archiwum przy następnym starcie Claude Code.")
                return False
            polaczenie = polacz(sciezki.baza)
            try:
                raport = archiwizuj(polaczenie, sciezki, adaptery)
            finally:
                polaczenie.close()
    except Exception as blad:
        log.exception("%s: archiwizacja nie powiodła się", opis)
        powiadom("Archiwizacja nie powiodła się", f"{blad}\nSzczegóły: {sciezki.dziennik}")
        return False
    # Ikona i `sprawdz` patrzą na ostatni wpis – pominięta sesja nie może się schować za „INFO”.
    log.log(logging.WARNING if raport.nieczytelne else logging.INFO, "%s: %s", opis, raport)
    if not tylko_bledy:
        powiadom(*podsumowanie(raport))
    return True


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m skerimai.hook")
    parser.add_argument("opis", nargs="?", default="ręcznie", help="skąd zlecenie – do dziennika")
    parser.add_argument("--zrodlo", action="append", choices=list(ADAPTERY), help=f"domyślnie {ZRODLO}")
    parser.add_argument("--tylko-bledy", action="store_true", help="powiadomienie tylko o błędzie")
    args = parser.parse_args(argv)
    sciezki = domyslne_sciezki()
    sciezki.katalog.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[RotatingFileHandler(sciezki.dziennik, maxBytes=_ROZMIAR_DZIENNIKA, backupCount=1, encoding="utf-8")],
    )
    adaptery = [ADAPTERY[nazwa]() for nazwa in (args.zrodlo or [ZRODLO])]
    return 0 if archiwizuj_w_tle(sciezki, args.opis, adaptery, powiadom=powiadomienie.pokaz,
                                 tylko_bledy=args.tylko_bledy) else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
