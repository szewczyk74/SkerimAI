"""Instalacja (`skerimai instaluj`), konfigurator (`skerimai konfiguruj`) i diagnostyka (`skerimai sprawdz`).

Kopia używana na co dzień to instalacja przez `uv tool install`: u programisty z bieżącego commita repozytorium,
u pozostałych z wydania w publicznym repozytorium na GitHubie (archiwum ZIP tagu – bez gita). Kopia rozwojowa
(`.venv` w repozytorium, instalacja edytowalna) służy do zmian i testów. Gdy kopia z uv istnieje, konfigurator
i diagnostyka wskazują na nią, także uruchomione z kopii rozwojowej; bez niej – na tę, która działa.

Konfigurator włącza SkerimAI w Claude Code i Codex bez ręcznej edycji plików. Wpisy wskazują na programy
z katalogu Scripts tej kopii:
- hook SessionEnd w `~\\.claude\\settings.json` → `skerimai.exe hook`,
- w tym samym pliku `env.MCP_TIMEOUT` – dłuższy czas, przez jaki Claude Code czeka na start serwerów MCP,
- serwer MCP „skerimai” w Claude Code (zakres user), w Codex i w OpenCode,
- wskazówka o archiwum w `~\\.claude\\CLAUDE.md` i `~\\.codex\\AGENTS.md` (OpenCode bez własnego globalnego
  `AGENTS.md` czyta `CLAUDE.md`; dopisanie takiego pliku odcięłoby go od reszty zasad),
- AppUserModelID powiadomień Windows,
- autostart ikony w zasobniku (`skerimai-zasobnik.exe`) w HKCU\\…\\Run.
Serwery MCP zmieniane są przez `claude mcp` / `codex mcp`, nie edycją plików – `~\\.claude.json` na bieżąco
zapisuje każda otwarta sesja Claude Code. Wyjątkiem jest OpenCode: `opencode mcp` nie umie usuwać wpisów, a jego
pliku konfiguracji sesje nie nadpisują. Gdy wszystko jest na miejscu, ponowne uruchomienie niczego nie zmienia.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from importlib.metadata import Distribution, PackageNotFoundError, distribution, distributions
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, url2pathname, urlopen

from . import powiadomienie, zasobnik
from .adaptery import ADAPTERY, Adapter
from .baza import polacz
from .dziennik import ostatni_wpis
from .konfiguracja import Sciezki
from .tekst import liczba
from .zapytania import statystyki

SERWER = "skerimai"
NAGLOWEK_WSKAZOWKI = "# SkerimAI – archiwum wcześniejszych rozmów"
_TIMEOUT_HOOKA_S = 5
# Claude Code domyślnie czeka na serwer MCP 30 s, a potem uruchamia sesję bez niego i bez komunikatu.
# Serwer startuje zwykle w 3–8 s, ale tuż po włączeniu komputera zdarzało się ponad 30 s.
MCP_TIMEOUT_MS = 60_000
DNI_POLACZEN = 7

# Stany wyników. Konfigurator: dodano, zaktualizowano, bez zmian, usunięto, brak, pominięto, błąd.
OK, BLAD, UWAGA, INFO = "ok", "błąd", "uwaga", "info"
ZMIANY = frozenset({"dodano", "zaktualizowano", "usunięto"})


@dataclass(frozen=True)
class Wynik:
    nazwa: str
    stan: str
    opis: str = ""


@dataclass(frozen=True)
class Miejsca:
    """Pliki konfiguracji Claude Code i Codex w katalogu domowym."""

    dom: Path

    @property
    def claude(self) -> Path:
        return self.dom / ".claude"

    @property
    def ustawienia_claude(self) -> Path:
        return self.claude / "settings.json"

    @property
    def claude_json(self) -> Path:
        """Rejestr serwerów MCP zakresu user – zapisuje go Claude Code, my tylko czytamy."""
        return self.dom / ".claude.json"

    @property
    def dzienniki_mcp(self) -> Path:
        """Dzienniki połączeń Claude Code z serwerami: `<projekt>\\mcp-logs-skerimai\\<czas startu>.jsonl`."""
        return self.dom / "AppData" / "Local" / "claude-cli-nodejs" / "Cache"

    @property
    def claude_md(self) -> Path:
        return self.claude / "CLAUDE.md"

    @property
    def codex(self) -> Path:
        return self.dom / ".codex"

    @property
    def config_codex(self) -> Path:
        return self.codex / "config.toml"

    @property
    def agents_md(self) -> Path:
        return self.codex / "AGENTS.md"

    @property
    def opencode(self) -> Path:
        return self.dom / ".config" / "opencode"

    @property
    def config_opencode(self) -> Path:
        jsonc = self.opencode / "opencode.jsonc"
        return jsonc if jsonc.is_file() else self.opencode / "opencode.json"

    def krotko(self, plik: Path) -> str:
        return "~\\" + str(plik.relative_to(self.dom))


@dataclass(frozen=True)
class Program:
    """Kopia SkerimAI: katalog Scripts jej środowiska, repozytorium z kodem i – przy instalacji z gita – commit.
    Bez commita to kopia rozwojowa (`edytowalny`) albo instalacja ze zwykłego katalogu."""

    skrypty: Path
    kod: Path | None = None
    commit: str | None = None
    edytowalny: bool = False
    wersja: str = "?"

    @property
    def hook(self) -> Path:
        return self.skrypty / _exe("skerimai")

    @property
    def serwer_mcp(self) -> Path:
        return self.skrypty / _exe("skerimai-mcp")

    @property
    def zasobnik(self) -> Path:
        return self.skrypty / _exe("skerimai-zasobnik")

    @property
    def okno_pamieci(self) -> Path:
        return self.skrypty / _exe("skerimai-pamiec")


def biezacy_program() -> Program:
    """Kopia, z której SkerimAI właśnie działa."""
    try:
        program = _program_z(Path(sysconfig.get_path("scripts")), distribution("skerimai"))
    except PackageNotFoundError:
        program = Program(Path(sysconfig.get_path("scripts")))
    kod = Path(__file__).resolve().parents[1]
    if program.kod is None and (kod / "pyproject.toml").is_file():  # uruchomiony wprost z katalogu z kodem
        program = replace(program, kod=kod, edytowalny=True)
    return program


def program_srodowiska(srodowisko: Path) -> Program | None:
    skrypty = srodowisko / ("Scripts" if os.name == "nt" else "bin")
    if not (skrypty / _exe("skerimai")).is_file():
        return None
    pakiety = [srodowisko / "Lib" / "site-packages"] if os.name == "nt" else list(srodowisko.glob("lib/python*/site-packages"))
    dystrybucja = next(iter(distributions(name="skerimai", path=[str(p) for p in pakiety])), None)
    return _program_z(skrypty, dystrybucja) if dystrybucja else Program(skrypty)


def _program_z(skrypty: Path, dystrybucja: Distribution) -> Program:
    """Źródło instalacji z `direct_url.json` (PEP 610) – zapisują go pip i uv przy instalacji z katalogu lub gita."""
    try:
        zrodlo = json.loads(dystrybucja.read_text("direct_url.json") or "{}")
    except ValueError:
        zrodlo = {}
    url = str(zrodlo.get("url", "")) if isinstance(zrodlo, dict) else ""
    # Z archiwum (wydanie z GitHuba, plik ZIP) nie ma repozytorium – tylko z katalogu albo gita.
    if not url.startswith("file:") or not ({"dir_info", "vcs_info"} & zrodlo.keys()):
        return Program(skrypty, wersja=dystrybucja.version)
    return Program(
        skrypty,
        kod=Path(url2pathname(urlsplit(url).path)),
        commit=(zrodlo.get("vcs_info") or {}).get("commit_id"),
        edytowalny=bool((zrodlo.get("dir_info") or {}).get("editable")),
        wersja=dystrybucja.version,
    )


def tekst_wskazowki(program: Program) -> list[str]:
    zdania = [
        "Gdy użytkownik nawiązuje do wcześniejszych rozmów, ustaleń, decyzji lub plików („jak ustaliliśmy”, "
        "„pamiętasz…”, „co robiliśmy z…”), najpierw użyj narzędzia `szukaj_w_historii` z serwera MCP "
        "`skerimai`, a dopiero potem odpowiadaj.",
        # Bez tego „przypomnij sobie projekt” kończyło się czytaniem samych plików projektu.
        "Gdy wraca do projektu („przypomnij sobie projekt”, „wróćmy do projektu”, „na czym skończyliśmy”, "
        "„co jest do zrobienia”), zanim zajrzysz do plików, wywołaj `lista_rozmow` z nazwą projektu (katalogu "
        "roboczego) i doczytaj przez `czytaj_rozmowe` końcówkę ostatniej rozmowy z pracą nad projektem – "
        "bieżącą i krótkie rozmowy na boczny temat pomiń.",
    ]
    if program.kod:
        zdania[-1] += f" Kod SkerimAI: `{program.kod}`."
    return [NAGLOWEK_WSKAZOWKI, *zdania]


Wykonaj = Callable[[list[str]], subprocess.CompletedProcess]


def wykonaj(polecenie: list[str]) -> subprocess.CompletedProcess:
    """Uruchamia CLI aplikacji; shutil.which, bo `claude` to skrypt npm `claude.cmd`. Bez okna konsoli – ikona
    w zasobniku nie ma konsoli, a uv czy git uruchomione z niej dostawałyby własne, migające okno."""
    program = shutil.which(polecenie[0])
    if program is None:
        raise FileNotFoundError(polecenie[0])
    opcje = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run([program, *polecenie[1:]], stdin=subprocess.DEVNULL, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=120, **opcje)


def zainstalowany_program(wykonaj: Wykonaj = wykonaj) -> Program | None:
    """Kopia z `uv tool install`, o ile istnieje."""
    try:
        wynik = wykonaj(["uv", "tool", "dir"])
    except (OSError, subprocess.SubprocessError):
        return None
    if wynik.returncode or not wynik.stdout.strip():
        return None
    return program_srodowiska(Path(wynik.stdout.strip()) / "skerimai")


def program_docelowy(wykonaj: Wykonaj = wykonaj) -> Program:
    """Kopia, na którą mają wskazywać wpisy: zainstalowana przez uv, a bez niej ta, która działa."""
    return zainstalowany_program(wykonaj) or biezacy_program()


def instalator(kod: Path) -> Path:
    """`skerimai` z kopii rozwojowej – tylko ona instaluje i aktualizuje kopię używaną."""
    return kod / ".venv" / "Scripts" / _exe("skerimai")


def polecenie_instalacji(kod: Path) -> str:
    return f"{instalator(kod)} instaluj"


def _git(kod: Path, argumenty: list[str], wykonaj: Wykonaj) -> str | None:
    try:
        wynik = wykonaj(["git", "-C", str(kod), *argumenty])
    except (OSError, subprocess.SubprocessError):
        return None
    return None if wynik.returncode else wynik.stdout.strip()


# ---------------------------------------------------------------- wydania

# Publiczne repozytorium: na każde wydanie commit z tagiem vX.Y.Z, na gałęzi main zawsze najnowsze.
REPOZYTORIUM = "szewczyk74/SkerimAI"
_SUROWE = f"https://raw.githubusercontent.com/{REPOZYTORIUM}/main"
SKRYPT_INSTALACJI = f"{_SUROWE}/instaluj.ps1"
SPRAWDZANIE_WERSJI_S = 24 * 3600
_CZEKAJ_NA_GITHUB_S = 10


def adres_wydania(wersja: str) -> str:
    """Archiwum ZIP tagu – uv buduje z niego pakiet bez gita na komputerze."""
    return f"https://github.com/{REPOZYTORIUM}/archive/refs/tags/v{wersja}.zip"


def polecenie_wydania(wersja: str, *program: str) -> list[str]:
    """Program z wydania uruchomiony przez uv z jego pamięci podręcznej, poza zainstalowaną kopią – tylko tak
    zainstalowana kopia da się podmienić (Windows nie pozwala zastąpić działającego programu)."""
    return ["uv", "tool", "run", "--python-preference", "only-managed", "--from", adres_wydania(wersja), *program]


def polecenie_aktualizacji(wersja: str) -> list[str]:
    return polecenie_wydania(wersja, "skerimai", "instaluj", "--wersja", wersja)


def numer_wersji(wersja: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", wersja)[:3])


def _pobierz(adres: str) -> str:
    with urlopen(Request(adres, headers={"User-Agent": "SkerimAI"}), timeout=_CZEKAJ_NA_GITHUB_S) as odpowiedz:
        return odpowiedz.read().decode("utf-8")


def pobierz_najnowsza_wersje(pobierz: Callable[[str], str] = _pobierz) -> str:
    """Wersja z pyproject.toml na gałęzi main publicznego repozytorium. OSError (w tym brak sieci) albo ValueError."""
    try:
        return str(tomllib.loads(pobierz(f"{_SUROWE}/pyproject.toml"))["project"]["version"])
    except (KeyError, TypeError) as blad:
        raise ValueError(f"brak wersji w pyproject.toml ({blad})") from blad


def najnowsza_wersja(plik: Path, pobierz: Callable[[str], str] = _pobierz,
                     teraz: Callable[[], float] = time.time) -> str | None:
    """Najnowsza wersja, sprawdzana na GitHubie raz na dobę; wynik zapamiętany w `plik`. Gdy GitHub nie
    odpowiada – ostatnia zapamiętana, a następna próba też dopiero po dobie (ikona sprawdza stan co 10 minut)."""
    try:
        zapis = _wczytaj_json(plik)
    except (OSError, ValueError):
        zapis = {}
    sprawdzono, wersja = zapis.get("sprawdzono"), zapis.get("wersja")
    if not isinstance(wersja, str):
        wersja = None
    if isinstance(sprawdzono, (int, float)) and 0 <= teraz() - sprawdzono < SPRAWDZANIE_WERSJI_S:
        return wersja
    try:
        wersja = pobierz_najnowsza_wersje(pobierz)
    except (OSError, ValueError):
        pass
    try:
        _zapisz(plik, json.dumps({"sprawdzono": teraz(), "wersja": wersja}))
    except OSError:
        pass
    return wersja


# ---------------------------------------------------------------- instalacja

# 0.11.11 przerywa instalację z adresu git+file w Windows (panic AmbiguousAuthority); 0.12.21 sprawdzone.
UV_MINIMUM = (0, 12, 21)


class BladInstalacji(Exception):
    pass


def _uv_na_konsoli(polecenie: list[str]) -> int:
    """uv z wyjściem wprost na konsolę – instalacja trwa do kilku minut i pokazuje postęp."""
    program = shutil.which(polecenie[0])
    if program is None:
        raise FileNotFoundError(polecenie[0])
    sys.stdout.flush()  # nasze komunikaty przed wyjściem uv, także gdy wyjście idzie do potoku
    return subprocess.run([program, *polecenie[1:]], stdin=subprocess.DEVNULL).returncode


def zajete_programy(skrypty: Path) -> list[str]:
    """Programy kopii, które właśnie działają (serwer MCP, archiwizacja w tle). Windows nie pozwala ich usunąć,
    a uv odkrywa to w połowie podmiany – po usunięciu pakietów – i zostawia środowisko bez nich."""
    zajete = []
    for plik in sorted(skrypty.glob("*.exe")):
        try:
            with open(plik, "r+b"):
                pass
        except PermissionError:
            zajete.append(plik.name)
        except OSError:
            pass
    return zajete


# Programy ikony w zasobniku i archiwizacji, którą ona zleca: ikonę da się zamknąć, serwerów MCP – nie.
_PROGRAMY_ZASOBNIKA = frozenset({"skerimai-zasobnik.exe", "pythonw.exe"})
_OKNO_PAMIECI = "skerimai-pamiec.exe"  # okno edycji pamięci – zamknąć może je tylko użytkownik (trwa edycja)
CZEKAJ_NA_ZWOLNIENIE_S = 60.0


def instaluj(biezacy: Program, wykonaj: Wykonaj = wykonaj, uv: Callable[[list[str]], int] = _uv_na_konsoli,
             komunikat: Callable[[str], None] = print, zamknij_zasobnik: Callable[[], bool] | None = None,
             wersja: str | None = None, pobierz: Callable[[str], str] = _pobierz) -> Program:
    """Instaluje albo aktualizuje kopię używaną (`uv tool install`): z kopii rozwojowej – bieżący commit jej
    repozytorium, w pozostałych przypadkach i przy `wersja` – wydanie z GitHuba (bez `wersja` najnowsze).
    Zwraca zainstalowaną kopię; wpisów w konfiguracji nie zmienia – to robi `konfiguruj`.
    `zamknij_zasobnik`: zamyka ikonę w zasobniku, gdy działa z zainstalowanej kopii (True – zamknięta)."""
    kod = biezacy.kod if biezacy.kod is not None and (biezacy.kod / ".git").exists() else None
    if wersja is None and kod is None:
        try:
            wersja = pobierz_najnowsza_wersje(pobierz)
        except (OSError, ValueError) as blad:
            raise BladInstalacji(f"Nie udało się sprawdzić najnowszej wersji na GitHubie ({REPOZYTORIUM}): {blad}")
    if wersja is not None:
        kod = None
    try:
        wynik = wykonaj(["uv", "--version"])
    except FileNotFoundError:
        raise BladInstalacji("Brak programu uv – instrukcja: https://docs.astral.sh/uv/getting-started/installation/")
    znalezione = re.search(r"(\d+)\.(\d+)\.(\d+)", wynik.stdout)
    wersja_uv = tuple(int(x) for x in znalezione.groups()) if znalezione else (0, 0, 0)
    if wersja_uv < UV_MINIMUM:
        raise BladInstalacji(f"uv {'.'.join(map(str, wersja_uv))} jest za stare – instalacja z repozytorium git "
                             f"wymaga co najmniej {'.'.join(map(str, UV_MINIMUM))}; zaktualizuj: uv self update")

    obecny = zainstalowany_program(wykonaj)
    if obecny and _ta_sama(obecny.skrypty.resolve(), biezacy.skrypty.resolve()):  # resolve: krótkie nazwy 8.3
        if kod is None:
            raise BladInstalacji("To jest zainstalowana kopia – nie podmieni samej siebie. Zaktualizuj z menu ikony "
                                 "(Zainstaluj aktualizację) albo poleceniem: "
                                 f"{subprocess.list2cmdline(polecenie_aktualizacji(wersja))}")
        raise BladInstalacji(f"To jest zainstalowana kopia – nie podmieni samej siebie. Uruchom z kopii rozwojowej: "
                             f"{polecenie_instalacji(kod)}")
    zajete = zajete_programy(obecny.skrypty) if obecny else []
    inne = [p for p in zajete if p.lower() not in _PROGRAMY_ZASOBNIKA]
    if inne:
        raise BladInstalacji(_opis_zajetych(inne))
    if zajete:
        if zamknij_zasobnik is None or not zamknij_zasobnik():
            raise BladInstalacji("Ikona SkerimAI w zasobniku nie zamknęła się – zamknij ją (prawy klik → Zakończ) "
                                 "i spróbuj ponownie.")
        komunikat("Zamknięto ikonę w zasobniku – wróci po instalacji.")
        zajete = _czekaj_na_zwolnienie(obecny.skrypty)
        if zajete:
            raise BladInstalacji(_opis_zajetych(zajete))

    if kod is None:
        commit, zrodlo = None, adres_wydania(wersja)
        komunikat(f"Instaluję SkerimAI {wersja} z {zrodlo}")
        ponownie = subprocess.list2cmdline(polecenie_aktualizacji(wersja))
    else:
        commit = _git(kod, ["rev-parse", "HEAD"], wykonaj)
        if not commit:
            raise BladInstalacji(f"Nie udało się odczytać bieżącego commita w {kod} (git rev-parse HEAD).")
        if _git(kod, ["status", "--porcelain"], wykonaj):
            komunikat(f"Uwaga: niezacommitowane zmiany w {kod} nie trafią do instalacji – instaluję commit {commit[:7]}.")
        zrodlo, ponownie = f"git+{kod.as_uri()}", polecenie_instalacji(kod)
    # --compile-bytecode: bez tego pierwszy start serwera MCP po aktualizacji kompiluje wszystkie moduły
    # (kilka sekund więcej, a Claude Code czeka na serwer ograniczony czas).
    # only-managed: Python z katalogu uv (brak – uv go pobierze), nie pierwszy znaleziony w systemie. Środowisko
    # wskazuje na Pythona, z którego powstało – z Laragona czy innego programu zniknąłby przy jego aktualizacji.
    polecenie = ["uv", "tool", "install", "--reinstall", "--compile-bytecode", "--python", f"{sys.version_info[0]}.{sys.version_info[1]}",
                 "--python-preference", "only-managed", zrodlo]
    komunikat(subprocess.list2cmdline(polecenie))
    if uv(polecenie):
        raise BladInstalacji("uv zakończył się błędem (komunikat powyżej). Gdy przerwał w trakcie podmiany, "
                             f"zamknij sesje Claude Code i Codex i uruchom ponownie: {ponownie}")
    nowy = zainstalowany_program(wykonaj)
    if commit is None:
        if nowy is None or nowy.wersja != wersja:
            raise BladInstalacji(f"Po instalacji nie widać wersji {wersja} w kopii z uv "
                                 f"({nowy.wersja if nowy else 'brak'}).")
    elif nowy is None or nowy.commit != commit:
        raise BladInstalacji(f"Po instalacji nie widać commita {commit[:7]} w kopii z uv "
                             f"({nowy.commit[:7] if nowy and nowy.commit else 'brak'}).")
    return nowy


def _opis_zajetych(zajete: list[str]) -> str:
    okno = [p for p in zajete if p.lower() == _OKNO_PAMIECI]
    reszta = [p for p in zajete if p.lower() != _OKNO_PAMIECI]
    zamknij_okno = "zamknij okna SkerimAI (pamięć, stan, kopia)"
    if not reszta:
        return f"Okno SkerimAI jest otwarte – {zamknij_okno} i spróbuj ponownie."
    return (f"Zainstalowana kopia działa ({', '.join(reszta)}) – zamknij sesje Claude Code i Codex (serwer MCP; "
            "usługę OpenCode zatrzymaj: opencode service stop; aplikację Claude zakończ z jej ikony w zasobniku – zamknięte okno zostawia sesje)"
            f"{', ' + zamknij_okno if okno else ''} i poczekaj na koniec archiwizacji po sesji, potem spróbuj ponownie. "
            "Windows nie pozwala podmienić działającego programu, a przerwana instalacja zostawiłaby kopię bez części "
            "pakietów.")


def _czekaj_na_zwolnienie(skrypty: Path, czekaj_s: float = CZEKAJ_NA_ZWOLNIENIE_S) -> list[str]:
    """Po zamknięciu ikony jej proces kończy się chwilę później, a archiwizacja, którą zleciła, może jeszcze trwać."""
    koniec = time.monotonic() + czekaj_s
    while (zajete := zajete_programy(skrypty)) and time.monotonic() < koniec:
        time.sleep(0.5)
    return zajete


# ---------------------------------------------------------------- konfigurator

def konfiguruj(miejsca: Miejsca, program: Program, usun: bool = False, wykonaj: Wykonaj = wykonaj) -> list[Wynik]:
    """Dodaje albo (`usun`) usuwa wszystkie wpisy SkerimAI. Archiwum zostaje nietknięte."""
    if not usun:
        brakujace = [str(p) for p in (program.hook, program.serwer_mcp) if not p.is_file()]
        if brakujace:
            raise FileNotFoundError(f"Brak {', '.join(brakujace)} – najpierw zainstaluj pakiet (skerimai instaluj).")
    blok = None if usun else tekst_wskazowki(program)
    claude, codex = miejsca.claude.is_dir(), miejsca.codex.is_dir()
    return [
        _hook_claude(miejsca, program, usun) if claude else _brak("Hook SessionEnd w Claude Code", "Claude Code"),
        _czas_mcp(miejsca, usun) if claude else _brak(NAZWA_CZASU_MCP, "Claude Code"),
        _serwer_mcp(
            "Serwer MCP w Claude Code", lambda: _serwer_claude(miejsca), program, usun, wykonaj,
            dodaj=["claude", "mcp", "add", "--scope", "user", SERWER, str(program.serwer_mcp)],
            usun_polecenie=["claude", "mcp", "remove", "--scope", "user", SERWER],
        ) if claude else _brak("Serwer MCP w Claude Code", "Claude Code"),
        _serwer_mcp(
            "Serwer MCP w Codex", lambda: _serwer_codex(miejsca), program, usun, wykonaj,
            dodaj=["codex", "mcp", "add", SERWER, "--", str(program.serwer_mcp)],
            usun_polecenie=["codex", "mcp", "remove", SERWER],
        ) if codex else _brak("Serwer MCP w Codex", "Codex"),
        _mcp_opencode(miejsca, program, usun) if miejsca.opencode.is_dir() else _brak(NAZWA_MCP_OPENCODE, "OpenCode"),
        _wskazowka(miejsca, miejsca.claude_md, blok) if claude else _brak("Wskazówka w CLAUDE.md", "Claude Code"),
        _wskazowka(miejsca, miejsca.agents_md, blok) if codex else _brak("Wskazówka w AGENTS.md", "Codex"),
        _powiadomienia(usun),
        _autostart(program, usun),
    ]


def _brak(nazwa: str, aplikacja: str) -> Wynik:
    return Wynik(nazwa, "pominięto", f"brak {aplikacja} na tym komputerze")


def _hook_claude(miejsca: Miejsca, program: Program, usun: bool) -> Wynik:
    nazwa = "Hook SessionEnd w Claude Code"
    try:
        ustawienia = _wczytaj_json(miejsca.ustawienia_claude)
    except ValueError as blad:
        return Wynik(nazwa, BLAD, f"nieczytelny {miejsca.krotko(miejsca.ustawienia_claude)} ({blad}) – nic nie zmieniono")
    stan = _ustaw_hook(ustawienia, None if usun else _hook(program))
    if stan in ZMIANY:
        _zapisz(miejsca.ustawienia_claude, json.dumps(ustawienia, indent=2, ensure_ascii=False) + "\n")
    return Wynik(nazwa, stan)


NAZWA_CZASU_MCP = "Czas na start serwera MCP w Claude Code"


def _czas_mcp(miejsca: Miejsca, usun: bool) -> Wynik:
    """`env.MCP_TIMEOUT` w settings.json. Większej wartości ustawionej przez użytkownika nie zmniejsza,
    a przy usuwaniu zostawia każdą inną niż nasza."""
    try:
        ustawienia = _wczytaj_json(miejsca.ustawienia_claude)
    except ValueError as blad:
        return Wynik(NAZWA_CZASU_MCP, BLAD,
                     f"nieczytelny {miejsca.krotko(miejsca.ustawienia_claude)} ({blad}) – nic nie zmieniono")
    env = ustawienia.get("env") if isinstance(ustawienia.get("env"), dict) else {}
    obecny = env.get("MCP_TIMEOUT")
    if usun:
        if obecny != str(MCP_TIMEOUT_MS):
            return Wynik(NAZWA_CZASU_MCP, "brak")
        del env["MCP_TIMEOUT"]
        if not env:
            del ustawienia["env"]
        stan = "usunięto"
    else:
        if (_ms(obecny) or 0) >= MCP_TIMEOUT_MS:
            return Wynik(NAZWA_CZASU_MCP, "bez zmian")
        ustawienia["env"] = {**env, "MCP_TIMEOUT": str(MCP_TIMEOUT_MS)}
        stan = "zaktualizowano" if obecny is not None else "dodano"
    _zapisz(miejsca.ustawienia_claude, json.dumps(ustawienia, indent=2, ensure_ascii=False) + "\n")
    return Wynik(NAZWA_CZASU_MCP, stan, "" if usun else f"{MCP_TIMEOUT_MS // 1000} s – działa w nowych sesjach")


def _ms(wartosc: object) -> int | None:
    try:
        return int(str(wartosc).strip())
    except ValueError:
        return None


def _hook(program: Program) -> dict:
    return {"type": "command", "command": program.hook.as_posix(), "args": ["hook"], "timeout": _TIMEOUT_HOOKA_S}


def _nasz_hook(hook: object) -> bool:
    return isinstance(hook, dict) and Path(str(hook.get("command", ""))).name.lower() in ("skerimai.exe", "skerimai")


def _nasze_hooki(ustawienia: dict) -> list[dict]:
    grupy = ustawienia.get("hooks", {}).get("SessionEnd", [])
    return [h for g in grupy for h in g.get("hooks", []) if _nasz_hook(h)]


def _ustaw_hook(ustawienia: dict, nowy: dict | None) -> str:
    """Zostawia w SessionEnd dokładnie jeden hook SkerimAI (`nowy`) albo żaden; cudze hooki zostają."""
    nasze = _nasze_hooki(ustawienia)
    if nasze == ([nowy] if nowy else []):
        return "bez zmian" if nowy else "brak"
    hooki = ustawienia.setdefault("hooks", {})
    grupy = []
    for grupa in hooki.get("SessionEnd", []):
        grupa["hooks"] = [h for h in grupa.get("hooks", []) if not _nasz_hook(h)]
        if grupa["hooks"]:
            grupy.append(grupa)
    if nowy:
        grupy.append({"hooks": [nowy]})
    if grupy:
        hooki["SessionEnd"] = grupy
    else:
        hooki.pop("SessionEnd", None)
    if not hooki:
        del ustawienia["hooks"]
    if not nowy:
        return "usunięto"
    return "zaktualizowano" if nasze else "dodano"


def _serwer_claude(miejsca: Miejsca) -> dict | None:
    serwer = _wczytaj_json(miejsca.claude_json).get("mcpServers", {}).get(SERWER)
    return serwer if isinstance(serwer, dict) else None


def _serwer_codex(miejsca: Miejsca) -> dict | None:
    if not miejsca.config_codex.is_file():
        return None
    with open(miejsca.config_codex, "rb") as plik:
        serwer = tomllib.load(plik).get("mcp_servers", {}).get(SERWER)
    return serwer if isinstance(serwer, dict) else None


NAZWA_MCP_OPENCODE = "Serwer MCP w OpenCode"


def _wpis_opencode(ustawienia: dict) -> dict | None:
    """Wpis z `mcp.servers`, z poleceniem rozbitym na `command` i `args` jak w pozostałych aplikacjach."""
    mcp = ustawienia.get("mcp")
    serwery = mcp.get("servers") if isinstance(mcp, dict) else None
    serwer = serwery.get(SERWER) if isinstance(serwery, dict) else None
    polecenie = serwer.get("command") if isinstance(serwer, dict) else None
    if not isinstance(polecenie, list) or not polecenie:
        return None
    return {"command": polecenie[0], "args": polecenie[1:]}


def _mcp_opencode(miejsca: Miejsca, program: Program, usun: bool) -> Wynik:
    plik = miejsca.config_opencode
    try:
        ustawienia = _wczytaj_json(plik)
    except ValueError as blad:  # także JSONC z komentarzami – przepisanie pliku by je skasowało
        return Wynik(NAZWA_MCP_OPENCODE, BLAD, f"nieczytelny {miejsca.krotko(plik)} ({blad}) – nic nie zmieniono")
    obecny = _wpis_opencode(ustawienia)
    if usun:
        if obecny is None:
            return Wynik(NAZWA_MCP_OPENCODE, "brak")
        mcp = ustawienia["mcp"]
        del mcp["servers"][SERWER]
        if not mcp["servers"]:
            del mcp["servers"]
        if not mcp:
            del ustawienia["mcp"]
        stan = "usunięto"
    else:
        if _zgodny(obecny, program):
            return Wynik(NAZWA_MCP_OPENCODE, "bez zmian")
        ustawienia.setdefault("mcp", {}).setdefault("servers", {})[SERWER] = {
            "type": "local", "command": [str(program.serwer_mcp)]}
        stan = "zaktualizowano" if obecny is not None else "dodano"
    _zapisz(plik, json.dumps(ustawienia, indent=2, ensure_ascii=False) + "\n")
    return Wynik(NAZWA_MCP_OPENCODE, stan)


def _zgodny(serwer: dict | None, program: Program) -> bool:
    return serwer is not None and _ta_sama(serwer.get("command", ""), program.serwer_mcp) and not serwer.get("args")


def _serwer_mcp(nazwa: str, odczytaj: Callable[[], dict | None], program: Program, usun: bool, wykonaj: Wykonaj,
                dodaj: list[str], usun_polecenie: list[str]) -> Wynik:
    try:
        obecny = odczytaj()
    except ValueError as blad:
        return Wynik(nazwa, BLAD, f"nieczytelna konfiguracja ({blad}) – nic nie zmieniono")
    if usun:
        if obecny is None:
            return Wynik(nazwa, "brak")
        polecenia, stan = [usun_polecenie], "usunięto"
    else:
        if _zgodny(obecny, program):
            return Wynik(nazwa, "bez zmian")
        polecenia = ([usun_polecenie] if obecny is not None else []) + [dodaj]
        stan = "zaktualizowano" if obecny is not None else "dodano"
    for polecenie in polecenia:
        try:
            wynik = wykonaj(polecenie)
        except FileNotFoundError:
            return Wynik(nazwa, BLAD, f"nie znaleziono polecenia {polecenie[0]} – wykonaj ręcznie: "
                                      + subprocess.list2cmdline(polecenia[-1]))
        except (OSError, subprocess.SubprocessError) as blad:
            return Wynik(nazwa, BLAD, f"{subprocess.list2cmdline(polecenie[:3])}: {blad}")
        if wynik.returncode:
            komunikat = (wynik.stderr or wynik.stdout or "").strip()[:300]
            return Wynik(nazwa, BLAD, f"{subprocess.list2cmdline(polecenie[:3])} (kod {wynik.returncode}): {komunikat}")
    po = odczytaj()
    if (po is not None) if usun else not _zgodny(po, program):
        return Wynik(nazwa, BLAD, "polecenie się wykonało, ale konfiguracja się nie zmieniła")
    return Wynik(nazwa, stan)


def _wskazowka(miejsca: Miejsca, plik: Path, blok: list[str] | None) -> Wynik:
    nazwa = f"Wskazówka w {miejsca.krotko(plik)}"
    tekst = plik.read_bytes().decode("utf-8-sig") if plik.is_file() else ""
    nowy = wstaw_wskazowke(tekst, blok)
    if nowy == tekst:
        return Wynik(nazwa, "bez zmian" if blok else "brak")
    _zapisz(plik, nowy)
    if not blok:
        return Wynik(nazwa, "usunięto")
    return Wynik(nazwa, "zaktualizowano" if _ma_wskazowke(tekst) else "dodano")


def _ma_wskazowke(tekst: str) -> bool:
    return any(linia.rstrip() == NAGLOWEK_WSKAZOWKI for linia in tekst.splitlines())


def wstaw_wskazowke(tekst: str, blok: list[str] | None) -> str:
    """Podmienia, dopisuje na końcu albo (blok None) usuwa sekcję wskazówki – od jej nagłówka do następnego
    nagłówka pierwszego stopnia. Reszta pliku zostaje, łącznie z końcami linii (CRLF/LF)."""
    linie = tekst.splitlines()
    start = next((i for i, linia in enumerate(linie) if linia.rstrip() == NAGLOWEK_WSKAZOWKI), None)
    if start is None and blok is None:
        return tekst
    if start is None:
        przed, po = linie, []
    else:
        koniec = next((i for i in range(start + 1, len(linie)) if linie[i].startswith("# ")), len(linie))
        przed, po = linie[:start], linie[koniec:]
    while przed and not przed[-1].strip():
        przed = przed[:-1]
    wynik: list[str] = []
    for czesc in (przed, blok or [], po):
        if czesc:
            wynik += ([""] if wynik else []) + czesc
    nl = "\r\n" if "\r\n" in tekst else "\n"
    return nl.join(wynik) + nl if wynik else ""


def _powiadomienia(usun: bool) -> Wynik:
    nazwa = "Powiadomienia Windows"
    if os.name != "nt":
        return Wynik(nazwa, "pominięto", "tylko w Windows")
    if not powiadomienie.wlaczone():
        return Wynik(nazwa, "pominięto", "wyłączone zmienną SKERIMAI_POWIADOMIENIA=0")
    try:
        if usun:
            return Wynik(nazwa, "usunięto" if powiadomienie.wyrejestruj_aplikacje() else "brak")
        return Wynik(nazwa, "dodano" if powiadomienie.zarejestruj_aplikacje() else "bez zmian")
    except OSError as blad:
        return Wynik(nazwa, BLAD, str(blad))


def _autostart(program: Program, usun: bool) -> Wynik:
    nazwa = "Autostart ikony w zasobniku"
    if os.name != "nt":
        return Wynik(nazwa, "pominięto", "tylko w Windows")
    try:
        obecny = zasobnik.wpis_autostartu()
        if usun:
            if obecny is None:
                return Wynik(nazwa, "brak")
            zasobnik.ustaw_autostart(None)
            return Wynik(nazwa, "usunięto")
        if not program.zasobnik.is_file():
            return Wynik(nazwa, "pominięto", f"brak {program.zasobnik.name} w tej kopii – zaktualizuj ją")
        nowy = zasobnik.polecenie_autostartu(program.zasobnik)
        if obecny == nowy:
            return Wynik(nazwa, "bez zmian")
        zasobnik.ustaw_autostart(nowy)
    except OSError as blad:
        return Wynik(nazwa, BLAD, str(blad))
    return Wynik(nazwa, "zaktualizowano" if obecny else "dodano")


# ---------------------------------------------------------------- diagnostyka

def sprawdz(miejsca: Miejsca, program: Program, sciezki: Sciezki, adaptery: list[Adapter] | None = None,
            wykonaj: Wykonaj = wykonaj, najnowsza: Callable[[], str | None] | None = None) -> list[Wynik]:
    """`najnowsza`: najnowsza wersja z GitHuba – domyślnie sprawdzana raz na dobę (`najnowsza_wersja`)."""
    adaptery = adaptery if adaptery is not None else [klasa() for klasa in ADAPTERY.values()]
    claude, codex = miejsca.claude.is_dir(), miejsca.codex.is_dir()
    blok = tekst_wskazowki(program)
    return [
        _sprawdz_program(program, wykonaj, najnowsza or (lambda: najnowsza_wersja(sciezki.wydanie))),
        _sprawdz_hook(miejsca, program) if claude else Wynik("Hook SessionEnd w Claude Code", INFO, "brak Claude Code"),
        _sprawdz_czas_mcp(miejsca) if claude else Wynik(NAZWA_CZASU_MCP, INFO, "brak Claude Code"),
        _sprawdz_polaczenia(miejsca) if claude else Wynik(NAZWA_POLACZEN, INFO, "brak Claude Code"),
        _sprawdz_serwer("Serwer MCP w Claude Code", lambda: _serwer_claude(miejsca), program)
        if claude else Wynik("Serwer MCP w Claude Code", INFO, "brak Claude Code"),
        _sprawdz_serwer("Serwer MCP w Codex", lambda: _serwer_codex(miejsca), program)
        if codex else Wynik("Serwer MCP w Codex", INFO, "brak Codex"),
        _sprawdz_serwer(NAZWA_MCP_OPENCODE, lambda: _wpis_opencode(_wczytaj_json(miejsca.config_opencode)), program)
        if miejsca.opencode.is_dir() else Wynik(NAZWA_MCP_OPENCODE, INFO, "brak OpenCode"),
        _sprawdz_kreion(program, sciezki),
        _sprawdz_wskazowke(miejsca, miejsca.claude_md, blok) if claude else Wynik("Wskazówka w CLAUDE.md", INFO, "brak Claude Code"),
        _sprawdz_wskazowke(miejsca, miejsca.agents_md, blok) if codex else Wynik("Wskazówka w AGENTS.md", INFO, "brak Codex"),
        _sprawdz_powiadomienia(),
        _sprawdz_zasobnik(program, sciezki),
        _sprawdz_archiwum(sciezki),
        _sprawdz_dziennik(sciezki),
        _sprawdz_zrodla(adaptery),
    ]


_NAPRAW = "uruchom: skerimai konfiguruj"


_PLIKI_PROGRAMU = ["skerimai", "pyproject.toml"]  # to trafia do instalacji; reszta repozytorium – nie


def _sprawdz_program(program: Program, wykonaj: Wykonaj, najnowsza: Callable[[], str | None] = lambda: None) -> Wynik:
    brakujace = [p.name for p in (program.hook, program.serwer_mcp) if not p.is_file()]
    if brakujace:
        return Wynik("Program", BLAD, f"brak {', '.join(brakujace)} w {program.skrypty} – zainstaluj pakiet")
    if program.commit:
        opis = f"SkerimAI {program.wersja}, commit {program.commit[:7]} – {program.skrypty}"
        w_repozytorium = _git(program.kod, ["rev-parse", "HEAD"], wykonaj) if program.kod else None
        if w_repozytorium and w_repozytorium != program.commit:
            zmiany = _git(program.kod, ["diff", "--name-only", program.commit, w_repozytorium, "--", *_PLIKI_PROGRAMU],
                          wykonaj)
            if zmiany == "":  # np. same zmiany w PLAN.md albo testach – nie ma czego instalować
                return Wynik("Program", OK, f"{opis} (w repozytorium {w_repozytorium[:7]}, bez zmian w programie)")
            return Wynik("Program", UWAGA, f"{opis}; w repozytorium jest commit {w_repozytorium[:7]} ze zmianami "
                                           f"w programie – zaktualizuj: zamknij sesje Claude Code i Codex, potem "
                                           f"{polecenie_instalacji(program.kod)}")
    elif program.edytowalny:
        opis = f"SkerimAI {program.wersja}, kopia rozwojowa (zmiany w kodzie działają od razu) – {program.skrypty}"
    else:
        opis = f"SkerimAI {program.wersja} – {program.skrypty}"
        nowa = najnowsza() if program.kod is None else None  # kopia z wydania; z katalogu – bez sprawdzania
        if nowa and numer_wersji(nowa) > numer_wersji(program.wersja):
            return Wynik("Program", UWAGA, f"{opis}; jest nowsza wersja {nowa} – zaktualizuj z menu ikony "
                                           "(Zainstaluj aktualizację) przy zamkniętych sesjach Claude Code i Codex")
    return Wynik("Program", OK, opis)


def _sprawdz_hook(miejsca: Miejsca, program: Program) -> Wynik:
    nazwa = "Hook SessionEnd w Claude Code"
    try:
        nasze = _nasze_hooki(_wczytaj_json(miejsca.ustawienia_claude))
    except ValueError as blad:
        return Wynik(nazwa, BLAD, f"nieczytelny {miejsca.krotko(miejsca.ustawienia_claude)}: {blad}")
    if not nasze:
        return Wynik(nazwa, BLAD, f"brak – {_NAPRAW}")
    if len(nasze) > 1:
        return Wynik(nazwa, BLAD, f"{len(nasze)} wpisy – archiwizacja ruszałaby kilka razy; {_NAPRAW}")
    komenda = str(nasze[0].get("command", ""))
    if not _ta_sama(komenda, program.hook):
        return Wynik(nazwa, BLAD, f"wskazuje na {komenda}, a SkerimAI działa z {program.hook}; {_NAPRAW}")
    if nasze[0] != _hook(program):
        return Wynik(nazwa, BLAD, f"inne ustawienia niż oczekiwane ({json.dumps(nasze[0], ensure_ascii=False)}); {_NAPRAW}")
    return Wynik(nazwa, OK)


def _sprawdz_czas_mcp(miejsca: Miejsca) -> Wynik:
    try:
        env = _wczytaj_json(miejsca.ustawienia_claude).get("env")
    except ValueError as blad:
        return Wynik(NAZWA_CZASU_MCP, BLAD, f"nieczytelny {miejsca.krotko(miejsca.ustawienia_claude)}: {blad}")
    ms = _ms(env.get("MCP_TIMEOUT")) if isinstance(env, dict) and "MCP_TIMEOUT" in env else None
    if ms is None:
        return Wynik(NAZWA_CZASU_MCP, UWAGA, f"domyślne 30 s – po włączeniu komputera serwer może nie zdążyć; {_NAPRAW}")
    if ms < MCP_TIMEOUT_MS:
        return Wynik(NAZWA_CZASU_MCP, UWAGA, f"{ms / 1000:g} s – za mało, potrzeba {MCP_TIMEOUT_MS // 1000} s; {_NAPRAW}")
    return Wynik(NAZWA_CZASU_MCP, OK, f"{ms / 1000:g} s")


NAZWA_POLACZEN = "Połączenia Claude Code z serwerem MCP"
_LIMIT = re.compile(r"Starting connection with timeout of (\d+)ms")
_POLACZONO = re.compile(r"Successfully connected .* in (\d+)ms")
_PORZUCONO = "Connection abandoned"  # sesję zamknięto, zanim serwer zdążył – to nie jego wina


@dataclass(frozen=True)
class Polaczenie:
    projekt: str  # katalog projektu w zapisie Claude Code, np. C--Projekty-budzik
    czas: str  # nazwa dziennika – czas startu w UTC
    limit_ms: int | None
    trwalo_ms: int | None  # None – nie połączył się
    blad: str = ""


def polaczenia_mcp(miejsca: Miejsca, od: float) -> list[Polaczenie]:
    """Starty serwera SkerimAI w sesjach Claude Code z dzienników zmienionych po `od` (czas uniksowy)."""
    wynik = []
    for plik in miejsca.dzienniki_mcp.glob(f"*/mcp-logs-{SERWER}/*.jsonl"):
        try:
            if plik.stat().st_mtime < od:
                continue
            linie = plik.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        limit = trwalo = None
        blad = ""
        porzucone = False
        for linia in linie:
            try:
                wpis = json.loads(linia)
            except ValueError:
                continue
            if not isinstance(wpis, dict):
                continue
            tekst = str(wpis.get("debug") or wpis.get("error") or "")
            if m := _LIMIT.search(tekst):
                limit = int(m.group(1))
            elif m := _POLACZONO.search(tekst):
                trwalo = int(m.group(1))
            elif _PORZUCONO in tekst:
                porzucone = True
            elif "error" in wpis and trwalo is None and not blad:
                blad = tekst
        if porzucone and trwalo is None:
            continue
        if limit is not None or trwalo is not None:
            wynik.append(Polaczenie(plik.parent.parent.name, plik.stem, limit, trwalo, blad))
    return sorted(wynik, key=lambda p: p.czas)


def _sprawdz_polaczenia(miejsca: Miejsca, teraz: float | None = None) -> Wynik:
    teraz = time.time() if teraz is None else teraz
    polaczenia = polaczenia_mcp(miejsca, teraz - DNI_POLACZEN * 86400)
    if not polaczenia:
        return Wynik(NAZWA_POLACZEN, INFO, f"brak startów w ostatnich {DNI_POLACZEN} dniach")
    udane = [p.trwalo_ms for p in polaczenia if p.trwalo_ms is not None]
    nieudane = [p for p in polaczenia if p.trwalo_ms is None]
    opis = f"{liczba(len(polaczenia), 'start', 'starty', 'startów')} w ostatnich {DNI_POLACZEN} dniach"
    if udane:
        opis += f", najdłuższy {max(udane) / 1000:.1f} s"
    if not nieudane:
        return Wynik(NAZWA_POLACZEN, OK, opis)
    ostatni = nieudane[-1]
    opis += f"; nieudane: {len(nieudane)}, ostatnie {ostatni.czas[:10]} w {ostatni.projekt}"
    if ostatni.limit_ms:
        opis += f" przy limicie {ostatni.limit_ms / 1000:g} s"
    # Nieudane przy krótszym limicie niż nasz to ślad sprzed konfiguracji – nie powód do alarmu.
    if all(p.limit_ms is not None and p.limit_ms < MCP_TIMEOUT_MS for p in nieudane):
        return Wynik(NAZWA_POLACZEN, OK, opis + f" (przed ustawieniem {MCP_TIMEOUT_MS // 1000} s)")
    return Wynik(NAZWA_POLACZEN, UWAGA, opis + " – te sesje nie miały narzędzi SkerimAI")


def _sprawdz_serwer(nazwa: str, odczytaj: Callable[[], dict | None], program: Program) -> Wynik:
    try:
        serwer = odczytaj()
    except ValueError as blad:
        return Wynik(nazwa, BLAD, f"nieczytelna konfiguracja: {blad}")
    if serwer is None:
        return Wynik(nazwa, BLAD, f"brak – {_NAPRAW}")
    if not _zgodny(serwer, program):
        return Wynik(nazwa, BLAD, f"wskazuje na {serwer.get('command')}, a SkerimAI działa z {program.serwer_mcp}; {_NAPRAW}")
    return Wynik(nazwa, OK)


def _sprawdz_wskazowke(miejsca: Miejsca, plik: Path, blok: list[str]) -> Wynik:
    nazwa = f"Wskazówka w {miejsca.krotko(plik)}"
    tekst = plik.read_bytes().decode("utf-8-sig") if plik.is_file() else ""
    if wstaw_wskazowke(tekst, blok) == tekst:
        return Wynik(nazwa, OK)
    if _ma_wskazowke(tekst):
        return Wynik(nazwa, UWAGA, f"nieaktualna treść – {_NAPRAW}")
    return Wynik(nazwa, BLAD, f"brak – agent może nie sięgać do archiwum; {_NAPRAW}")


_USTAWIENIA_POWIADOMIEN = {
    "DisabledForApplication": "wyłączone dla SkerimAI w Ustawienia → System → Powiadomienia",
    "DisabledForUser": "wszystkie powiadomienia wyłączone w Ustawienia → System → Powiadomienia",
    "DisabledByGroupPolicy": "zablokowane zasadami grupy (administrator)",
    "DisabledByManifest": "zablokowane przez system",
}


def _sprawdz_powiadomienia() -> Wynik:
    nazwa = "Powiadomienia Windows"
    if os.name != "nt":
        return Wynik(nazwa, INFO, "tylko w Windows")
    if not powiadomienie.wlaczone():
        return Wynik(nazwa, INFO, "wyłączone zmienną SKERIMAI_POWIADOMIENIA=0")
    if not powiadomienie.zarejestrowana():
        return Wynik(nazwa, UWAGA, f"aplikacja niezarejestrowana – {_NAPRAW}")
    try:
        ustawienie = powiadomienie.ustawienie_windows()
    except (OSError, subprocess.SubprocessError) as blad:
        return Wynik(nazwa, UWAGA, f"nie udało się odczytać ustawień Windows: {blad}")
    if ustawienie == "Enabled":
        return Wynik(nazwa, OK, "włączone")
    return Wynik(nazwa, UWAGA, _USTAWIENIA_POWIADOMIEN.get(ustawienie, ustawienie))


def _sprawdz_zasobnik(program: Program, sciezki: Sciezki) -> Wynik:
    nazwa = "Ikona w zasobniku"
    if os.name != "nt":
        return Wynik(nazwa, INFO, "tylko w Windows")
    if not program.zasobnik.is_file():
        jak = f": zamknij sesje Claude Code i Codex, potem {polecenie_instalacji(program.kod)}" if program.kod else ""
        return Wynik(nazwa, UWAGA, f"brak {program.zasobnik.name} w tej kopii – zaktualizuj ją{jak}")
    try:
        wpis = zasobnik.wpis_autostartu()
        wylaczony = zasobnik.autostart_wylaczony()
    except OSError as blad:
        return Wynik(nazwa, UWAGA, f"nie udało się odczytać autostartu: {blad}")
    if wpis is None:
        return Wynik(nazwa, UWAGA, f"nie uruchamia się z Windows – {_NAPRAW}")
    if not _ta_sama(wpis.strip().strip('"'), program.zasobnik):
        return Wynik(nazwa, UWAGA, f"autostart wskazuje na {wpis}, a SkerimAI działa z {program.zasobnik}; {_NAPRAW}")
    if wylaczony:
        return Wynik(nazwa, UWAGA, "autostart wyłączony w Menedżerze zadań → Uruchamianie")
    if not zasobnik.dziala(sciezki):
        return Wynik(nazwa, UWAGA, f"nie działa – uruchom {program.zasobnik.name} albo: skerimai konfiguruj")
    return Wynik(nazwa, OK, "działa, uruchamia się z Windows")


def rodzaj_dysku(katalog: Path) -> str:
    """„dysk lokalny” albo powód, dla którego SQLite w trybie WAL nie powinien tam pracować."""
    if str(katalog).startswith("\\\\"):
        return "udział sieciowy"
    pelna = katalog.resolve()
    if str(pelna).startswith("\\\\"):  # dysk zmapowany – resolve() daje ścieżkę UNC
        return "udział sieciowy"
    for zmienna in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        folder = os.environ.get(zmienna)
        if folder and _wewnatrz(pelna, Path(folder).resolve()):
            return "folder OneDrive"
    if os.name == "nt":
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(pelna.anchor) == 4:  # DRIVE_REMOTE
            return "dysk sieciowy"
    return "dysk lokalny"


def _sprawdz_kreion(program: Program, sciezki: Sciezki) -> Wynik:
    """Zawsze tylko informacja: przerwa w używaniu KreionAI to nie awaria. Wpis dodaje użytkownik w KreionAI,
    więc nie da się tu odróżnić usuniętego wpisu od przerwy – po aktualizacji KreionAI sprawdza się stan_archiwum."""
    nazwa = "Serwer MCP w KreionAI"
    try:
        czas = datetime.strptime(sciezki.polaczenie_kreion.read_text(encoding="utf-8").strip(), "%Y-%m-%dT%H:%M:%S%z")
    except (OSError, ValueError):
        return Wynik(nazwa, INFO, f"jeszcze się nie łączył; wpis do dodania w KreionAI: {program.serwer_mcp} --klient kreion")
    return Wynik(nazwa, INFO, f"ostatnie połączenie {czas.astimezone():%Y-%m-%d %H:%M}")


def _sprawdz_archiwum(sciezki: Sciezki) -> Wynik:
    nazwa = "Archiwum"
    skad = "z SKERIMAI_DIR" if os.environ.get("SKERIMAI_DIR") else "domyślne"
    if not sciezki.baza.is_file():
        return Wynik(nazwa, BLAD, f"brak bazy w {sciezki.katalog} ({skad}) – uruchom: skerimai archiwizuj")
    rodzaj = rodzaj_dysku(sciezki.katalog)
    polaczenie = polacz(sciezki.baza)
    try:
        dane = statystyki(polaczenie)
    finally:
        polaczenie.close()
    opis = (f"{sciezki.katalog} ({skad}, {rodzaj}): {liczba(dane['rozmowy'], 'rozmowa', 'rozmowy', 'rozmów')}, "
            f"{liczba(dane['wiadomosci'], 'wiadomość', 'wiadomości', 'wiadomości')}")
    if rodzaj != "dysk lokalny":
        return Wynik(nazwa, BLAD, f"{opis} – SQLite w trybie WAL wymaga dysku lokalnego; przenieś archiwum "
                                  "(skerimai kopia + SKERIMAI_DIR)")
    return Wynik(nazwa, OK, opis)


STANY_DZIENNIKA = {"INFO": OK, "WARNING": UWAGA, "ERROR": BLAD}
NAZWA_DZIENNIKA = "Ostatnia archiwizacja w tle"


def _sprawdz_dziennik(sciezki: Sciezki) -> Wynik:
    wpis = ostatni_wpis(sciezki.dziennik)
    if wpis is None:
        return Wynik(NAZWA_DZIENNIKA, INFO, "jeszcze nie było – pojawi się po zamknięciu sesji Claude Code")
    return Wynik(NAZWA_DZIENNIKA, STANY_DZIENNIKA[wpis.poziom], f"{wpis.czas} – {wpis.tresc}")


def _sprawdz_zrodla(adaptery: list[Adapter]) -> Wynik:
    czesci = []
    for adapter in adaptery:
        try:
            czesci.append(f"{adapter.nazwa} {len(adapter.znajdz_sesje())}")
        except OSError as blad:
            czesci.append(f"{adapter.nazwa} – błąd odczytu ({blad})")
    return Wynik("Sesje w źródłach", INFO, ", ".join(czesci) or "brak źródeł")


# ---------------------------------------------------------------- pomocnicze

def _exe(nazwa: str) -> str:
    return f"{nazwa}.exe" if os.name == "nt" else nazwa


def _ta_sama(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _wewnatrz(sciezka: Path, folder: Path) -> bool:
    s, f = os.path.normcase(str(sciezka)), os.path.normcase(str(folder))
    return s == f or s.startswith(f.rstrip("\\/") + os.sep)


def _wczytaj_json(plik: Path) -> dict:
    if not plik.is_file():
        return {}
    dane = json.loads(plik.read_bytes().decode("utf-8-sig"))
    if not isinstance(dane, dict):
        raise ValueError("oczekiwano obiektu JSON")
    return dane


def _zapisz(plik: Path, tekst: str) -> None:
    """Przez plik tymczasowy – przerwany zapis nie zostawi uciętego pliku konfiguracji."""
    plik.parent.mkdir(parents=True, exist_ok=True)
    tymczasowy = plik.with_name(plik.name + ".skerimai.tmp")
    with open(tymczasowy, "w", encoding="utf-8", newline="") as wyjscie:
        wyjscie.write(tekst)
    os.replace(tymczasowy, plik)
