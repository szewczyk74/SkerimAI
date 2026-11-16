"""Adapter OpenCode: wszystkie rozmowy w jednej bazie SQLite ~\\.local\\share\\opencode\\opencode.db.

Tabele wersji 2 (starszych `session` / `message` OpenCode już nie uzupełnia, a ich treść przeniósł do nowych):
- `session_v2` – katalog, tytuł, czasy; `parent_id` mają sesje subagentów i te są pomijane,
- `session_message` – wiersze w kolejności `seq`, typ w `type`, treść w JSON-ie `data`: `user` ma `text` i `files`
  (obrazy w base64), `assistant` ma listę `content` (`text`, `reasoning`, `tool`). Wywołania narzędzi, myślenie modelu
  i wiersze techniczne (`system`, `synthetic`, `compaction`, `idle`) do archiwum nie trafiają, są w surowej kopii.

Format nie jest opisany – sprawdzony na wersji 2.2.5 (listopad 2026).

Pomijane są też rozmowy z katalogów, w których inne programy tylko sprawdzają, czy model odpowiada (Herdr:
„Reply with exactly: OK” – 400 z 479 rozmów w listopadzie 2026). Inna lista: zmienna `SKERIMAI_OPENCODE_POMIN`
z nazwami katalogów po przecinku (pusta – nic nie pomija).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ..model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK, Rozmowa, Wiadomosc, Zalacznik
from .wspolne import nazwa_katalogu, tytul_z_pierwszej

log = logging.getLogger("skerimai")

_TYTUL_DOMYSLNY = "New session - "
_KATALOGI_SOND = ("herdr-oc-probe", "oc-models")


def pomijane_katalogi() -> frozenset[str]:
    """Nazwy katalogów roboczych (ostatni człon ścieżki), z których rozmowy nie trafiają do archiwum."""
    wlasne = os.environ.get("SKERIMAI_OPENCODE_POMIN")
    nazwy = _KATALOGI_SOND if wlasne is None else wlasne.split(",")
    return frozenset(n.strip().lower() for n in nazwy if n.strip())


class OpenCode:
    nazwa = "opencode"

    def __init__(self, katalog: Path | None = None):
        self.katalog = katalog or Path.home() / ".local" / "share" / "opencode"
        self.baza = self.katalog / "opencode.db"
        self._rozmowy: dict[str, tuple[int, int]] = {}  # rozmowa → (liczba wiadomości, czas ostatniej zmiany w ms)
        self._ostatnia: tuple[str, tuple[int, int], dict, list[tuple[str, int, str]]] | None = None

    def znajdz_sesje(self) -> dict[str, list[Path]]:
        """Rozmowy z co najmniej jedną wiadomością. Lista czytana za każdym razem: stan plików nic nie mówi,
        bo działający OpenCode trzyma bazę otwartą i czas zmiany pliku `-wal` potrafi stać godzinami."""
        if not self.baza.is_file():
            return {}
        try:
            self._rozmowy = self._lista_rozmow()
        except sqlite3.Error as blad:  # np. nowa wersja zmieniła tabele – pozostałe źródła mają działać dalej
            log.warning("OpenCode: nie udało się odczytać listy rozmów: %s", blad)
            return {}
        return {rozmowa: [self.baza] for rozmowa in self._rozmowy}

    def stan_sesji(self, sesja: str) -> dict[str, tuple[int, int]]:
        liczba, zmiana_ms = self._rozmowy[sesja]
        return {f"{self.baza}#{sesja}": (liczba, zmiana_ms * 1_000_000)}

    def surowe(self, sesja: str) -> bytes:
        """Rozmowa i jej wiersze tak, jak są w bazie (JSONL) – także wywołania narzędzi i myślenie modelu."""
        opis, wiersze = self._rekordy(sesja)
        linie = [opis, *({"type": typ, "seq": seq, "data": _json(dane)} for typ, seq, dane in wiersze)]
        return "".join(json.dumps(linia, ensure_ascii=False) + "\n" for linia in linie).encode("utf-8")

    def wczytaj(self, sesja: str, pliki: list[Path]) -> Rozmowa:
        opis, wiersze = self._rekordy(sesja)
        rozmowa = Rozmowa(zrodlo=self.nazwa, sesja=sesja, pliki=list(pliki))
        biezacy_asystent: Wiadomosc | None = None
        numer_obrazu = 0
        for typ, _, surowe in wiersze:
            dane = _json(surowe) or {}
            czas = _czas_ms((dane.get("time") or {}).get("created"))
            if typ == "user":
                zalaczniki = []
                for plik in dane.get("files") or []:
                    numer_obrazu += 1
                    if (zalacznik := _zalacznik(plik, numer_obrazu)) is not None:
                        zalaczniki.append(zalacznik)
                tekst = (dane.get("text") or "").strip()
                if tekst or zalaczniki:
                    rozmowa.wiadomosci.append(Wiadomosc(ROLA_UZYTKOWNIK, czas, tekst, zalaczniki))
                    biezacy_asystent = None
            elif typ == "assistant" and (tekst := _tekst_asystenta(dane)):
                if biezacy_asystent is None:  # kolejne kroki między wywołaniami narzędzi – jedna tura
                    biezacy_asystent = Wiadomosc(ROLA_ASYSTENT, czas, tekst)
                    rozmowa.wiadomosci.append(biezacy_asystent)
                else:
                    biezacy_asystent.tekst += "\n\n" + tekst

        rozmowa.cwd = opis.get("directory")
        rozmowa.projekt = nazwa_katalogu(rozmowa.cwd)
        tytul = opis.get("title") or ""
        rozmowa.tytul = (tytul if tytul and not tytul.startswith(_TYTUL_DOMYSLNY) else None) or tytul_z_pierwszej(rozmowa)
        return rozmowa

    # ------------------------------------------------------------ odczyt bazy

    def _polacz(self) -> sqlite3.Connection:
        return sqlite3.connect(f"{self.baza.as_uri()}?mode=ro", uri=True)  # OpenCode może w tym czasie działać

    def _lista_rozmow(self) -> dict[str, tuple[int, int]]:
        with closing(self._polacz()) as baza:
            wiersze = baza.execute(
                """SELECT s.id, COUNT(*), MAX(s.time_updated, COALESCE(MAX(m.time_updated), 0)), s.directory
                   FROM session_v2 s JOIN session_message m ON m.session_id = s.id
                   WHERE s.parent_id IS NULL AND m.type IN ('user', 'assistant')
                   GROUP BY s.id"""
            ).fetchall()
        pomijane = pomijane_katalogi()
        return {rozmowa: (liczba, int(zmiana or 0)) for rozmowa, liczba, zmiana, katalog in wiersze
                if (nazwa_katalogu(katalog) or "").lower() not in pomijane}

    def _rekordy(self, sesja: str) -> tuple[dict, list[tuple[str, int, str]]]:
        """Opis rozmowy i jej wiersze; wczytaj i surowe czytają tę samą rozmowę tuż po sobie."""
        stan = self._rozmowy.get(sesja, (0, 0))
        if self._ostatnia is None or self._ostatnia[:2] != (sesja, stan):
            with closing(self._polacz()) as baza:
                baza.row_factory = sqlite3.Row
                wiersz = baza.execute("SELECT * FROM session_v2 WHERE id = ?", (sesja,)).fetchone()
                wiersze = [tuple(w) for w in baza.execute(
                    "SELECT type, seq, data FROM session_message WHERE session_id = ? ORDER BY seq", (sesja,))]
            self._ostatnia = (sesja, stan, dict(wiersz) if wiersz else {}, wiersze)
        return self._ostatnia[2], self._ostatnia[3]


def _tekst_asystenta(dane: dict) -> str:
    fragmenty = (c.get("text") or "" for c in dane.get("content") or [] if isinstance(c, dict) and c.get("type") == "text")
    return "\n\n".join(t.strip() for t in fragmenty if t.strip())


def _zalacznik(plik, numer: int) -> Zalacznik | None:
    if not isinstance(plik, dict) or not isinstance(plik.get("data"), str):
        return None
    try:
        dane = base64.b64decode(plik["data"], validate=False)
    except (binascii.Error, ValueError):
        return None
    nazwa = plik.get("name")
    return Zalacznik(nazwa=nazwa if nazwa and nazwa != "clipboard" else f"obraz-{numer}", mime=plik.get("mime"), dane=dane)


def _czas_ms(ms) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(wartosc) -> dict | None:
    try:
        dane = json.loads(wartosc)
    except (TypeError, ValueError):
        return None
    return dane if isinstance(dane, dict) else None
