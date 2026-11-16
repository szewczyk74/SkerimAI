"""Ikona SkerimAI w zasobniku systemowym (`skerimai-zasobnik`): stan archiwum na pierwszy rzut oka i polecenia w menu.

Znak SkerimAI z kropką stanu: bez kropki – w porządku, żółta – uwaga (np. nowszy kod niż zainstalowana kopia),
czerwona – błąd ostatniej archiwizacji albo konfiguracji. Co pół minuty ikona czyta koniec dziennika archiwizacji i liczy serwery MCP, co
10 minut wykonuje pełne `sprawdz`, co minutę sprawdza, czy Windows nie zgubił ikony (zdarza się po uśpieniu
lub zablokowaniu komputera – program działa dalej, tylko kółka nie widać) i w razie potrzeby dodaje ją ponownie. Codex i KreionAI nie mają hooka – co 5 minut ikona porównuje ich pliki
i po zmianie zleca archiwizację w tle (powiadomienie tylko o błędzie).

Program bez okna konsoli (punkt wejścia `zasobnik.start_ikony`). Błędy samej ikony trafiają do `zasobnik.log`,
żeby nie zmieniały koloru.
Sterowanie z zewnątrz (zamknięcie, autostart): `zasobnik.py`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import hook, powiadomienie, zasobnik
from .adaptery import ADAPTERY, Adapter
from .baza import polacz
from .blokada import blokada
from .dziennik import Wpis, ostatni_wpis
from .instalacja import (
    BLAD,
    NAZWA_DZIENNIKA,
    STANY_DZIENNIKA,
    UWAGA,
    Miejsca,
    Program,
    Wynik,
    biezacy_program,
    instalator,
    najnowsza_wersja,
    numer_wersji,
    polecenie_aktualizacji,
    polecenie_wydania,
    program_docelowy,
    sprawdz,
)
from .konfiguracja import Sciezki, domyslne_sciezki
from .tekst import liczba
from .wyglad import ostry_obraz_na_ekranach_hidpi
from .zapytania import statystyki

log = logging.getLogger("skerimai.ikona")

ZIELONY, ZOLTY, CZERWONY = "zielony", "żółty", "czerwony"
_KOLORY = {ZIELONY: None, ZOLTY: (240, 170, 0), CZERWONY: (220, 55, 45)}  # kropka stanu; w porządku – bez kropki
_KAFEL = (64, 83, 168)
_ROZMIARY_OKNA = (16, 24, 32, 48, 256)
_NAGLOWKI = {ZIELONY: "w porządku", ZOLTY: "uwaga", CZERWONY: "błąd"}
_OPISY_WPISU = {"INFO": "OK", "WARNING": "uwaga", "ERROR": "błąd"}
_ZNAKI = {BLAD: "✗", UWAGA: "!"}

ODSWIEZANIE_S = 30
SPRAWDZANIE_S = 600
ARCHIWIZACJA_S = 300
PILNOWANIE_IKONY_S = 60
ZRODLA_BEZ_HOOKA = ("codex", "kreion_kartridz", "kreion_silniki", "cursor", "opencode")
SERWER_MCP = "skerimai-mcp.exe"
# Ściąga ze zwrotami dla Claude Code i KreionAI – plik pakietu, więc jest w każdej zainstalowanej kopii.
JAK_ROZMAWIAC = Path(__file__).with_name("jak_rozmawiac.txt")
_MAKS_PODPOWIEDZI = 127  # NOTIFYICONDATA.szTip
_MAKS_POZYCJI_MENU = 90
_MAKS_PROBLEMOW_W_MENU = 5
_ROZMIAR_DZIENNIKA = 500_000


@dataclass(frozen=True)
class Stan:
    kolor: str
    podpowiedz: str
    problemy: tuple[Wynik, ...] = ()
    aktualizacja: bool = False
    serwery: int = 0


def oblicz_stan(wpis: Wpis | None, wyniki: list[Wynik], serwery: int, rozmowy: int | None, teraz: datetime) -> Stan:
    """Wynik ostatniej archiwizacji bierze ze świeżego wpisu dziennika, a nie z `sprawdz` sprzed kilku minut."""
    problemy = [w for w in wyniki if w.stan in (BLAD, UWAGA) and w.nazwa != NAZWA_DZIENNIKA]
    if wpis and wpis.poziom != "INFO":
        problemy.insert(0, Wynik(NAZWA_DZIENNIKA, STANY_DZIENNIKA[wpis.poziom], f"{wpis.czas} – {wpis.tresc}"))
    stany = {w.stan for w in problemy}
    kolor = CZERWONY if BLAD in stany else ZOLTY if UWAGA in stany else ZIELONY

    linie = ["SkerimAI" + (f" · {liczba(rozmowy, 'rozmowa', 'rozmowy', 'rozmów')}" if rozmowy is not None else "")]
    if wpis:
        linie.append(f"Archiwizacja {_kiedy(wpis.czas, teraz)}: {_OPISY_WPISU[wpis.poziom]}")
    linie.append(f"Serwer MCP: {liczba(serwery, 'sesja', 'sesje', 'sesji')}" if serwery
                 else "Serwer MCP: brak otwartych sesji")
    if problemy:
        linie.append(f"{_ZNAKI[problemy[0].stan]} {problemy[0].nazwa}" + (f" (+{len(problemy) - 1})" if len(problemy) > 1 else ""))
    return Stan(
        kolor=kolor,
        podpowiedz=_skroc("\n".join(linie), _MAKS_PODPOWIEDZI),
        problemy=tuple(problemy),
        aktualizacja=any(w.nazwa == "Program" and w.stan == UWAGA for w in wyniki),
        serwery=serwery,
    )


def _kiedy(czas: str, teraz: datetime) -> str:
    chwila = datetime.strptime(czas, "%Y-%m-%d %H:%M:%S")
    return chwila.strftime("%H:%M" if chwila.date() == teraz.date() else "%d.%m %H:%M")


def _skroc(tekst: str, dlugosc: int) -> str:
    return tekst if len(tekst) <= dlugosc else tekst[:dlugosc - 1] + "…"


def tekst_menu(tekst: str) -> str:
    """Jedna linia; „&” w menu Windows podkreśla następną literę, więc podwojony."""
    return _skroc(" ".join(tekst.split()), _MAKS_POZYCJI_MENU).replace("&", "&&")


def odcisk_zrodel(adaptery: list[Adapter]) -> frozenset[tuple[str, int, int]]:
    """Pliki sesji z czasem zmiany i rozmiarem – porównanie mówi, czy jest co archiwizować (kilka ms).
    Źródło z wieloma rozmowami w jednej bazie (OpenCode, Cursor) podaje stan każdej rozmowy samo, jak
    archiwizatorowi: nowe wiadomości leżą w pliku `-wal`, a plik bazy potrafi się nie zmieniać godzinami."""
    pliki = set()
    for adapter in adaptery:
        stan_sesji = getattr(adapter, "stan_sesji", None)
        for sesja, pliki_sesji in adapter.znajdz_sesje().items():
            if stan_sesji is not None:
                pliki.update((klucz, czas, rozmiar) for klucz, (rozmiar, czas) in stan_sesji(sesja).items())
                continue
            for plik in pliki_sesji:
                try:
                    dane = plik.stat()
                except OSError:
                    continue
                pliki.add((str(plik), dane.st_mtime_ns, dane.st_size))
    return frozenset(pliki)


def polecenie_konsoli(polecenie: list[str], tytul: str) -> str:
    """Okno konsoli, które po poleceniu czeka na klawisz – wynik da się przeczytać. `cmd /s /c "…"` zdejmuje tylko
    zewnętrzne cudzysłowy, a każdy argument jest w swoich – folder z „&” czy spacją nie rozbije polecenia."""
    argumenty = " ".join(f'"{a}"' for a in polecenie)
    return f'cmd.exe /d /s /c "title {tytul} & {argumenty} & echo. & pause"'


def obraz_ikony(kolor: str):
    """Znak 256 px: zaokrąglony kafelek z „S”, a przy uwadze lub błędzie kropka w prawym dolnym rogu, odcięta od
    kafelka przezroczystym pierścieniem – czytelna na jasnym i ciemnym pasku zadań. Mniejsze rozmiary powstają
    przez zmniejszenie (`ikona_w_rozmiarze`)."""
    from PIL import Image, ImageDraw, ImageFont

    rozmiar = 256
    obraz = Image.new("RGBA", (rozmiar, rozmiar), (0, 0, 0, 0))
    rysunek = ImageDraw.Draw(obraz)
    rysunek.rounded_rectangle((6, 6, rozmiar - 7, rozmiar - 7), radius=56, fill=_KAFEL)
    try:
        czcionka = ImageFont.truetype("segoeuib.ttf", 190)
    except OSError:
        czcionka = ImageFont.load_default(size=190)
    rysunek.text((rozmiar / 2, rozmiar / 2 - 6), "S", font=czcionka, fill="white", anchor="mm")
    if kropka := _KOLORY[kolor]:
        x = y = rozmiar - 62
        promien, pierscien = 58, 20
        zewnetrzny = promien + pierscien
        maska = Image.new("L", obraz.size, 0)
        ImageDraw.Draw(maska).ellipse((x - zewnetrzny, y - zewnetrzny, x + zewnetrzny, y + zewnetrzny), fill=255)
        obraz.paste((0, 0, 0, 0), mask=maska)
        rysunek.ellipse((x - promien, y - promien, x + promien, y + promien), fill=kropka)
    return obraz


def ikona_w_rozmiarze(obraz, rozmiar: int):
    from PIL import Image

    return obraz.resize((rozmiar, rozmiar), Image.LANCZOS)


def ikony_okna(kolor: str = ZIELONY) -> list:
    """Ten sam znak w rozmiarach dla paska tytułu, paska zadań i Alt+Tab (`iconphoto`)."""
    obraz = obraz_ikony(kolor)
    return [ikona_w_rozmiarze(obraz, r) for r in _ROZMIARY_OKNA]


def klasa_ikony():
    """pystray ładuje ikonę w rozmiarze dużej ikony (32 px), a zasobnik zmniejsza ją do małej – rozmyte „S”.
    Tu ikona powstaje od razu w rozmiarze małej ikony przy bieżącym DPI (SM_CXSMICON)."""
    import ctypes
    import sys

    import pystray

    if sys.platform != "win32":
        return pystray.Icon
    from pystray._util import serialized_image, win32

    class Ikona(pystray.Icon):
        def _assert_icon_handle(self):
            if self._icon_handle:
                return
            rozmiar = ctypes.windll.user32.GetSystemMetrics(49) or 16  # SM_CXSMICON
            with serialized_image(ikona_w_rozmiarze(self.icon, rozmiar), "ICO") as plik:
                self._icon_handle = win32.LoadImage(None, plik, win32.IMAGE_ICON, rozmiar, rozmiar,
                                                    win32.LR_LOADFROMFILE)

    return Ikona


# Same typy .NET, bez cmdletów – jak w powiadomienie.py. Wybrana ścieżka jako bajty UTF-8 (polskie litery).
_SKRYPT_FOLDERU = r"""$ErrorActionPreference = 'Stop'
$null = [System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
$wlasciciel = [System.Windows.Forms.Form]::new()
$wlasciciel.TopMost = $true
$wybor = [System.Windows.Forms.FolderBrowserDialog]::new()
$wybor.Description = $env:SKERIMAI_OPIS
if ($wybor.ShowDialog($wlasciciel) -eq [System.Windows.Forms.DialogResult]::OK) {
    $bajty = [System.Text.Encoding]::UTF8.GetBytes($wybor.SelectedPath)
    [Console]::OpenStandardOutput().Write($bajty, 0, $bajty.Length)
}
"""


def wybierz_folder(opis: str) -> str | None:
    wynik = powiadomienie.powershell(_SKRYPT_FOLDERU, {"SKERIMAI_OPIS": opis}, timeout=None)
    if wynik.returncode:
        log.warning("okno wyboru folderu: %s", wynik.stderr.decode("oem", "replace").strip()[:500])
        return None
    return wynik.stdout.decode("utf-8", "replace").strip() or None


def ikona_w_zasobniku(ikona) -> bool:
    """Czy Windows ma jeszcze tę ikonę: NIM_MODIFY (tu tylko podpowiedź, bez zmian) zwraca FALSE, gdy jej nie ma.
    pystray tego wyniku nie sprawdza, więc wołamy Shell_NotifyIcon sami – z tym samym oknem i identyfikatorem."""
    import ctypes

    from pystray._util import win32

    dane = win32.NOTIFYICONDATAW(cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW), hWnd=ikona._hwnd, hID=id(ikona),
                                 uFlags=win32.NIF_TIP, szTip=ikona.title)
    return bool(win32.Shell_NotifyIcon(win32.NIM_MODIFY, dane))


class Zasobnik:
    """Stan i polecenia ikony. Sama ikona (pystray) powstaje dopiero w `uruchom` – reszta działa bez okien."""

    def __init__(
        self,
        sciezki: Sciezki,
        miejsca: Miejsca,
        adaptery: list[Adapter] | None = None,
        zlec_archiwizacje: Callable[..., object] = hook.zlec_archiwizacje_zrodel,
        sprawdzaj: Callable[[], list[Wynik]] | None = None,
        policz_serwery: Callable[[], int] = lambda: zasobnik.liczba_procesow(SERWER_MCP),
    ):
        self.sciezki = sciezki
        self.program = biezacy_program()  # jego skerimai.exe uruchamia menu – leży obok skerimai-zasobnik.exe
        self.adaptery = adaptery if adaptery is not None else [ADAPTERY[nazwa]() for nazwa in ZRODLA_BEZ_HOOKA]
        self.zlec_archiwizacje = zlec_archiwizacje
        self.sprawdzaj = sprawdzaj or (lambda: sprawdz(miejsca, program_docelowy(), sciezki))
        self.policz_serwery = policz_serwery
        self.wyniki: list[Wynik] = []
        self.rozmowy: int | None = None
        self.odcisk: frozenset | None = None
        self.stan = Stan(ZIELONY, "SkerimAI – sprawdzam…")
        self.ikona = None
        self._koniec = threading.Event()

    # ------------------------------------------------------------ praca w tle

    def krok(self, sekunda: int) -> bool:
        """Jedna sekunda pracy w tle; False – ktoś poprosił o zamknięcie (`zasobnik.stop`)."""
        if self.sciezki.koniec_zasobnika.exists():
            return False
        if sekunda % SPRAWDZANIE_S == 0:
            self._bezpiecznie(self.odswiez)  # przy starcie: kolor i podpowiedź od razu, pełne sprawdzenie trwa dłużej
            self._bezpiecznie(self.sprawdz_pelne)
        if sekunda % ODSWIEZANIE_S == 0:
            self._bezpiecznie(self.odswiez)
        if sekunda % ARCHIWIZACJA_S == 0:
            self._bezpiecznie(self.archiwizuj_zmienione)
        if sekunda and sekunda % PILNOWANIE_IKONY_S == 0:
            self._bezpiecznie(self.pilnuj_ikony)
        return True

    def odswiez(self) -> None:
        stan = oblicz_stan(ostatni_wpis(self.sciezki.dziennik), self.wyniki, self.policz_serwery(), self.rozmowy,
                           datetime.now())
        poprzedni, self.stan = self.stan, stan
        if self.ikona is not None and stan != poprzedni:
            if stan.kolor != poprzedni.kolor:
                self.ikona.icon = obraz_ikony(stan.kolor)
            self.ikona.title = stan.podpowiedz
            self.ikona.update_menu()

    def sprawdz_pelne(self) -> None:
        self.wyniki = self.sprawdzaj()
        if self.sciezki.baza.is_file():
            polaczenie = polacz(self.sciezki.baza)
            try:
                self.rozmowy = statystyki(polaczenie)["rozmowy"]
            finally:
                polaczenie.close()

    def archiwizuj_zmienione(self) -> None:
        """Pierwszy raz zaraz po starcie – dociąga to, co zmieniło się, kiedy ikona nie działała."""
        odcisk = odcisk_zrodel(self.adaptery)
        if odcisk == self.odcisk:
            return
        self.odcisk = odcisk
        if odcisk:
            self.zlec_archiwizacje("zasobnik", [a.nazwa for a in self.adaptery], tylko_bledy=True)

    def pilnuj_ikony(self) -> None:
        if self.ikona is None or not self.ikona.visible or os.name != "nt" or ikona_w_zasobniku(self.ikona):
            return
        log.warning("ikona zniknęła z zasobnika – dodaję ją ponownie")
        self.ikona._show()  # NIM_ADD jak przy starcie; pystray robi to sam tylko po restarcie Explorera

    # ------------------------------------------------------------ polecenia z menu

    def sprawdz_w_oknie(self) -> None:
        if not self._w_oknie(self.program, "--stan"):
            self._w_konsoli([str(self.program.hook), "sprawdz"], "SkerimAI – stan")

    def archiwizuj_teraz(self) -> None:
        self.zlec_archiwizacje("zasobnik/na żądanie", list(ADAPTERY))

    def edytuj_pamiec(self) -> None:
        program = self.program.okno_pamieci
        if not program.is_file():
            powiadomienie.pokaz("Nie można otworzyć okna", f"Brak {program.name} w tej kopii – zainstaluj aktualizację.")
            return
        zasobnik.uruchom(program)

    def kopia(self) -> None:
        folder = wybierz_folder("Folder na kopię archiwum SkerimAI")
        if folder:
            polecenie = [str(self.program.hook), "kopia", folder]
            if not self._w_oknie(self.program, "--polecenie", "SkerimAI – kopia archiwum", "--", *polecenie):
                self._w_konsoli(polecenie, "SkerimAI – kopia archiwum")

    def otworz_dziennik(self) -> None:
        subprocess.Popen(["notepad.exe", str(self.sciezki.dziennik)])

    def otworz_folder(self) -> None:
        os.startfile(self.sciezki.katalog)

    def jak_rozmawiac(self) -> None:
        subprocess.Popen(["notepad.exe", str(JAK_ROZMAWIAC)])

    def aktualizuj(self) -> None:
        """Z kopii rozwojowej albo z wydania na GitHubie – zainstalowana nie podmieni samej siebie. `instaluj`
        zamknie tę ikonę i uruchomi nową."""
        program = program_docelowy()
        kod = program.kod
        if kod is None:
            self._aktualizuj_z_wydania(program)
            return
        if not instalator(kod).is_file():
            powiadomienie.pokaz("Nie można zaktualizować", "Nie znaleziono kopii rozwojowej SkerimAI.")
            return
        # Okno z kopii rozwojowej – okno z kopii używanej zajmowałoby jej pliki, które instalacja podmienia.
        rozwojowa = Program(instalator(kod).parent)
        polecenie = [str(instalator(kod)), "instaluj"]
        if not self._w_oknie(rozwojowa, "--polecenie", "SkerimAI – aktualizacja", "--", *polecenie):
            self._w_konsoli(polecenie, "SkerimAI – aktualizacja")

    def _aktualizuj_z_wydania(self, program: Program) -> None:
        """Okno postępu i instalacja z wydania uruchomione przez uv z jego pamięci podręcznej, czyli nowa wersja
        instaluje samą siebie. Pobranie i przygotowanie trwa chwilę, zanim okno się pokaże."""
        wersja = najnowsza_wersja(self.sciezki.wydanie)
        uv = shutil.which("uv")
        if wersja is None or numer_wersji(wersja) <= numer_wersji(program.wersja):
            powiadomienie.pokaz("Brak aktualizacji", f"SkerimAI {program.wersja} to najnowsza wersja.")
            return
        if uv is None:
            powiadomienie.pokaz("Nie można zaktualizować", "Nie znaleziono programu uv.")
            return
        aktualizacja = [uv, *polecenie_aktualizacji(wersja)[1:]]
        powiadomienie.pokaz("Pobieranie aktualizacji", f"SkerimAI {wersja} – okno instalacji pokaże się za chwilę.")
        subprocess.Popen([uv, *polecenie_wydania(wersja, "skerimai-pamiec")[1:], "--polecenie",
                          "SkerimAI – aktualizacja", "--", *aktualizacja],
                         stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

    def zakoncz(self) -> None:
        self._koniec.set()
        if self.ikona is not None:
            self.ikona.stop()

    def pozycje_menu(self) -> Iterator:
        import pystray

        pozycja, stan = pystray.MenuItem, self.stan
        rozmowy = f" · {liczba(self.rozmowy, 'rozmowa', 'rozmowy', 'rozmów')}" if self.rozmowy is not None else ""
        yield pozycja(f"SkerimAI{rozmowy} – {_NAGLOWKI[stan.kolor]}", None, enabled=False)
        for problem in stan.problemy[:_MAKS_PROBLEMOW_W_MENU]:
            yield pozycja(tekst_menu(f"{_ZNAKI[problem.stan]} {problem.nazwa}: {problem.opis}"),
                          self._w_tle(self.sprawdz_w_oknie))
        yield pystray.Menu.SEPARATOR
        yield pozycja("Edytuj pamięć…", self._w_tle(self.edytuj_pamiec))
        yield pozycja("Archiwizuj teraz", self._w_tle(self.archiwizuj_teraz))
        yield pozycja("Sprawdź stan", self._w_tle(self.sprawdz_w_oknie), default=True)
        yield pozycja("Więcej", pystray.Menu(
            pozycja("Jak rozmawiać z pamięcią…", self._w_tle(self.jak_rozmawiac)),
            pozycja("Kopia zapasowa…", self._w_tle(self.kopia)),
            pystray.Menu.SEPARATOR,
            pozycja("Otwórz folder archiwum", self._w_tle(self.otworz_folder)),
            pozycja("Otwórz dziennik", self._w_tle(self.otworz_dziennik)),
        ))
        if stan.aktualizacja:
            if stan.serwery:
                yield pozycja(f"Zainstaluj aktualizację – najpierw zamknij "
                              f"{liczba(stan.serwery, 'sesję', 'sesje', 'sesji')} Claude Code i Codex", None,
                              enabled=False)
                yield pozycja("Aplikację Claude zakończ z jej ikony – zamknięte okno zostawia sesje", None,
                              enabled=False)
            else:
                yield pozycja("Zainstaluj aktualizację", self._w_tle(self.aktualizuj))
        yield pystray.Menu.SEPARATOR
        yield pozycja("Zakończ", lambda: self.zakoncz())

    # ------------------------------------------------------------ ikona

    def uruchom(self) -> None:
        import pystray

        self.ikona = klasa_ikony()("SkerimAI", obraz_ikony(self.stan.kolor), self.stan.podpowiedz,
                                   pystray.Menu(self.pozycje_menu))
        self.ikona.run(setup=self._start)

    def _start(self, ikona) -> None:
        ikona.visible = True
        threading.Thread(target=self._praca, name="skerimai-zasobnik", daemon=True).start()

    def _praca(self) -> None:
        sekunda = 0
        while self.krok(sekunda) and not self._koniec.wait(1):
            sekunda += 1
        self.zakoncz()

    @staticmethod
    def _w_oknie(program: Program, *argumenty: str) -> bool:
        """Okno stanu albo postępu (`skerimai-pamiec` z argumentami); False – brak programu, zostaje konsola."""
        if not program.okno_pamieci.is_file():
            return False
        zasobnik.uruchom(program.okno_pamieci, *argumenty)
        return True

    def _w_konsoli(self, polecenie: list[str], tytul: str) -> None:
        subprocess.Popen(polecenie_konsoli(polecenie, tytul), creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _w_tle(self, polecenie: Callable[[], None]) -> Callable[[], None]:
        """Pozycje menu wywołuje pętla komunikatów ikony – okno wyboru folderu czy uruchamianie programów
        zablokowałyby ją, a z nią całe menu."""
        return lambda: threading.Thread(target=self._bezpiecznie, args=(polecenie,), daemon=True).start()

    @staticmethod
    def _bezpiecznie(funkcja: Callable[[], None]) -> None:
        try:
            funkcja()
        except Exception:
            log.exception("%s nie powiodło się", getattr(funkcja, "__name__", funkcja))


def main() -> None:
    sciezki = domyslne_sciezki()
    sciezki.katalog.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[RotatingFileHandler(sciezki.dziennik_zasobnika, maxBytes=_ROZMIAR_DZIENNIKA, backupCount=1,
                                      encoding="utf-8")],
    )
    with blokada(sciezki.blokada_zasobnika, 0) as jedyna:
        if not jedyna:
            return  # ikona już działa
        sciezki.koniec_zasobnika.unlink(missing_ok=True)  # po przerwanym zamykaniu
        log.info("start z %s", biezacy_program().skrypty)
        ostry_obraz_na_ekranach_hidpi()  # mała ikona w rozmiarze dla bieżącego DPI, nie rozciągnięta 16 px
        try:
            Zasobnik(sciezki, Miejsca(Path.home())).uruchom()
        except Exception:
            log.exception("ikona zatrzymana przez błąd")
            raise
        log.info("koniec")


if __name__ == "__main__":
    main()
