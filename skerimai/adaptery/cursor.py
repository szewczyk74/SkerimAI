"""Adapter Cursora: wszystkie rozmowy w jednej bazie SQLite %APPDATA%\\Cursor\\User\\globalStorage\\state.vscdb.

Tabela `cursorDiskKV`:
- `composerData:<rozmowa>` – nazwa, czasy, `fullConversationHeadersOnly` (kolejność wiadomości),
- `bubbleId:<rozmowa>:<wiadomość>` – `type` 1 użytkownik / 2 asystent, `text`, obrazy w `context.selectedImages`;
  wywołania narzędzi i myślenie modelu są w osobnych polach i do archiwum nie trafiają.

Folder projektu – pierwsze, co się znajdzie: przypisanie rozmowy do folderu w workspaceStorage\\<hash>\\state.vscdb,
`workspaceIdentifier` rozmowy, najczęstszy `lastTerminalCwd` jej wiadomości, a na końcu znany folder, który
najczęściej pada w ścieżkach plików rozmowy (Cursor gubi przypisanie, gdy folder zniknie z jego listy).

Jedna baza na wszystkie rozmowy, więc stan do wykrywania zmian i surową kopię rozmowy podaje adapter
(`stan_sesji`, `surowe`), a nie pliki. Format nie jest opisany – sprawdzony na wersjach `_v` 9–17 (2025–2026).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlparse

from ..model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK, Rozmowa, Wiadomosc, Zalacznik
from .wspolne import nazwa_katalogu, tytul_z_pierwszej

UZYTKOWNIK, ASYSTENT = 1, 2
_SEPARATORY = re.compile(r"[\\/]+")


def katalog_cursora() -> Path:
    appdata = os.environ.get("APPDATA")
    return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "Cursor" / "User"


class Cursor:
    nazwa = "cursor"

    def __init__(self, katalog: Path | None = None):
        self.katalog = katalog or katalog_cursora()
        self.baza = self.katalog / "globalStorage" / "state.vscdb"
        # Lista rozmów i foldery – odświeżane, gdy zmieni się baza (ikona i serwer MCP pytają co kilka minut).
        self._odcisk: tuple | None = None
        self._rozmowy: dict[str, tuple[int, int]] = {}  # rozmowa → (rozmiar composerData, lastUpdatedAt w ms)
        self._foldery_rozmow: dict[str, str] | None = None
        self._znane_foldery: list[str] | None = None
        self._ostatnia: tuple[str, str, list[str]] | None = None  # wczytaj i surowe czytają tę samą rozmowę

    def znajdz_sesje(self) -> dict[str, list[Path]]:
        """Rozmowy z co najmniej jedną wiadomością; rozmowy subagentów są w bazie osobno i są pomijane."""
        pliki = [p for p in (self.baza, self.baza.with_name(self.baza.name + "-wal")) if p.is_file()]
        if not pliki:
            return {}
        odcisk = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in pliki)
        if odcisk != self._odcisk:
            self._rozmowy = self._lista_rozmow()
            self._foldery_rozmow = self._znane_foldery = self._ostatnia = None
            self._odcisk = odcisk
        return {rozmowa: pliki for rozmowa in self._rozmowy}

    def stan_sesji(self, sesja: str) -> dict[str, tuple[int, int]]:
        """Zamiast stanu plików (rozmiar, czas zmiany) – baza jest jedna, zmienia się przy każdej rozmowie."""
        rozmiar, zmiana_ms = self._rozmowy[sesja]
        return {f"{self.baza}#{sesja}": (rozmiar, zmiana_ms * 1_000_000)}

    def surowe(self, sesja: str) -> bytes:
        """Rozmowa i jej wiadomości tak, jak są w bazie (JSONL) – także wywołania narzędzi i myślenie modelu."""
        _, rozmowa, babelki = self._rekordy(sesja)
        return "".join(f"{r}\n" for r in [rozmowa, *babelki]).encode("utf-8")

    def wczytaj(self, sesja: str, pliki: list[Path]) -> Rozmowa:
        _, surowa_rozmowa, surowe_babelki = self._rekordy(sesja)
        dane = _json(surowa_rozmowa) or {}
        babelki = [b for b in map(_json, surowe_babelki) if b]

        rozmowa = Rozmowa(zrodlo=self.nazwa, sesja=sesja, pliki=list(pliki))
        czas = _czas_ms(dane.get("createdAt"))
        biezacy_asystent: Wiadomosc | None = None
        for babelek in babelki:
            czas = _czas_babelka(babelek) or czas  # wiadomość bez czasu (starsze wersje) – czas poprzedniej
            tekst = (babelek.get("text") or "").strip()
            if babelek.get("type") == UZYTKOWNIK:
                obrazy = (babelek.get("context") or {}).get("selectedImages") or []
                zalaczniki = [Zalacznik(nazwa=PureWindowsPath(o["path"]).name, sciezka=Path(o["path"]))
                              for o in obrazy if isinstance(o, dict) and o.get("path")]
                if tekst or zalaczniki:
                    rozmowa.wiadomosci.append(Wiadomosc(ROLA_UZYTKOWNIK, czas, tekst, zalaczniki))
                    biezacy_asystent = None
            elif babelek.get("type") == ASYSTENT and tekst:
                if biezacy_asystent is None:  # tekst między wywołaniami narzędzi i odpowiedź końcowa – jedna tura
                    biezacy_asystent = Wiadomosc(ROLA_ASYSTENT, czas, tekst)
                    rozmowa.wiadomosci.append(biezacy_asystent)
                else:
                    biezacy_asystent.tekst += "\n\n" + tekst

        rozmowa.cwd = self._folder(sesja, dane, babelki, [surowa_rozmowa, *surowe_babelki])
        rozmowa.projekt = nazwa_katalogu(rozmowa.cwd)
        rozmowa.tytul = dane.get("name") or tytul_z_pierwszej(rozmowa)
        return rozmowa

    # ------------------------------------------------------------ odczyt bazy

    def _polacz(self, baza: Path) -> sqlite3.Connection:
        return sqlite3.connect(f"{baza.as_uri()}?mode=ro", uri=True)  # Cursor może w tym czasie działać

    def _lista_rozmow(self) -> dict[str, tuple[int, int]]:
        rozmowy = {}
        with closing(self._polacz(self.baza)) as baza:
            for klucz, wartosc in baza.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"):
                dane = _json(wartosc)
                if (not dane or not dane.get("fullConversationHeadersOnly")
                        or dane.get("subagentInfo") or dane.get("isBestOfNSubcomposer")):
                    continue
                zmiana = dane.get("lastUpdatedAt") or dane.get("createdAt") or 0
                rozmowy[klucz.split(":", 1)[1]] = (len(wartosc), int(zmiana))
        return rozmowy

    def _rekordy(self, sesja: str) -> tuple[str, str, list[str]]:
        """Surowy JSON rozmowy i jej wiadomości w kolejności rozmowy."""
        if self._ostatnia is None or self._ostatnia[0] != sesja:
            with closing(self._polacz(self.baza)) as baza:
                wiersz = baza.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (f"composerData:{sesja}",)).fetchone()
                rozmowa = _tekst(wiersz[0]) if wiersz else "{}"
                babelki = []
                for naglowek in (_json(rozmowa) or {}).get("fullConversationHeadersOnly") or []:
                    wiersz = baza.execute("SELECT value FROM cursorDiskKV WHERE key = ?",
                                          (f"bubbleId:{sesja}:{naglowek.get('bubbleId')}",)).fetchone()
                    if wiersz and wiersz[0]:
                        babelki.append(_tekst(wiersz[0]))
            self._ostatnia = (sesja, rozmowa, babelki)
        return self._ostatnia

    # ------------------------------------------------------------ folder projektu

    def _folder(self, sesja: str, dane: dict, babelki: list[dict], surowe: list[str]) -> str | None:
        folder = (self._przypisane_foldery().get(sesja)
                  or _sciezka_uri((dane.get("workspaceIdentifier") or {}).get("uri"))
                  or _najczestszy(b.get("lastTerminalCwd") for b in babelki))
        return folder or _folder_ze_sciezek(" ".join(surowe), self._znane())

    def _obszary(self) -> Iterable[tuple[Path, str | None]]:
        """Katalogi workspaceStorage i ich foldery (workspace.json)."""
        katalog = self.katalog / "workspaceStorage"
        if not katalog.is_dir():
            return
        for obszar in sorted(katalog.iterdir()):
            opis = _json(_czytaj(obszar / "workspace.json")) or {}
            yield obszar, _sciezka_uri(opis.get("folder"))

    def _przypisane_foldery(self) -> dict[str, str]:
        if self._foldery_rozmow is None:
            self._foldery_rozmow = {}
            for obszar, folder in self._obszary():
                if not folder or not (obszar / "state.vscdb").is_file():
                    continue
                try:
                    with closing(self._polacz(obszar / "state.vscdb")) as baza:
                        wiersz = baza.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerData'").fetchone()
                except sqlite3.Error:
                    continue
                for opis in ((_json(wiersz[0]) if wiersz else None) or {}).get("allComposers") or []:
                    if isinstance(opis, dict) and opis.get("composerId"):
                        self._foldery_rozmow[opis["composerId"]] = folder
        return self._foldery_rozmow

    def _znane(self) -> list[str]:
        """Foldery, w których Cursor pracował: z listy folderów i z terminali wszystkich rozmów."""
        if self._znane_foldery is None:
            foldery = {f for _, f in self._obszary() if f}
            with closing(self._polacz(self.baza)) as baza:
                for (wartosc,) in baza.execute("SELECT value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%' "
                                               "AND value LIKE '%lastTerminalCwd%'"):
                    folder = (_json(wartosc) or {}).get("lastTerminalCwd")
                    if folder:
                        foldery.add(folder)
            self._znane_foldery = sorted(foldery)
        return self._znane_foldery


def _folder_ze_sciezek(tekst: str, foldery: list[str]) -> str | None:
    """Znany folder, w którym leży najwięcej plików wymienionych w rozmowie; folder nadrzędny innego
    trafionego (np. C:\\Projekty przy C:\\Projekty\\sklep.pl) się nie liczy."""
    plaski = _SEPARATORY.sub("/", unquote(tekst).lower())
    trafienia = {}
    for folder in foldery:
        klucz = _SEPARATORY.sub("/", folder.lower()).rstrip("/")
        if (liczba := plaski.count(klucz + "/")) and klucz not in trafienia:
            trafienia[klucz] = (liczba, folder)
    najwezsze = [t for k, t in trafienia.items() if not any(inny.startswith(k + "/") for inny in trafienia)]
    return max(najwezsze)[1] if najwezsze else None


def _najczestszy(wartosci: Iterable[str | None]) -> str | None:
    licznik = Counter(w for w in wartosci if w)
    return licznik.most_common(1)[0][0] if licznik else None


def _sciezka_uri(uri) -> str | None:
    """file:///c%3A/Projekty/hr albo {"fsPath": "c:\\\\Projekty\\\\hr", …} → c:\\Projekty\\hr."""
    if isinstance(uri, dict):
        return uri.get("fsPath") or _sciezka_uri(uri.get("external"))
    if not isinstance(uri, str) or not uri.startswith("file://"):
        return None
    sciezka = unquote(urlparse(uri).path).lstrip("/")
    return str(PureWindowsPath(sciezka)) if sciezka else None


def _czas_babelka(babelek: dict) -> str | None:
    if isinstance(babelek.get("createdAt"), str):
        return babelek["createdAt"]
    return _czas_ms((babelek.get("timingInfo") or {}).get("clientRpcSendTime"))


def _czas_ms(ms) -> str | None:
    if not isinstance(ms, (int, float)) or ms < 1e12:  # clientStartTime itp. liczone od startu programu
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(wartosc) -> dict | None:
    if wartosc is None:
        return None
    try:
        dane = json.loads(wartosc)
    except (ValueError, UnicodeDecodeError):
        return None
    return dane if isinstance(dane, dict) else None


def _tekst(wartosc) -> str:
    return wartosc.decode("utf-8", "replace") if isinstance(wartosc, bytes) else str(wartosc)


def _czytaj(plik: Path) -> str | None:
    try:
        return plik.read_text(encoding="utf-8")
    except OSError:
        return None
