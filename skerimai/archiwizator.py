"""Przyrostowe przenoszenie rozmów ze źródeł do archiwum SkerimAI."""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from . import dokumenty, sekrety
from .adaptery import Adapter
from .adaptery.wspolne import czytaj_bajty
from .konfiguracja import Sciezki
from .magazyn import Magazyn
from .model import Rozmowa
from .sekrety import Sekrety
from .tekst import Rdzenie, do_indeksu, tokenizuj

log = logging.getLogger("skerimai")


@dataclass
class Raport:
    sesje: int = 0
    zaktualizowane: int = 0
    wiadomosci: int = 0
    nowe_zalaczniki: int = 0
    brakujace_zalaczniki: int = 0
    dokumenty: int = 0
    nieczytelne: int = 0  # sesje, których źródła nie udało się odczytać – wrócą w następnym przebiegu

    def __str__(self) -> str:
        return (
            f"Sesje: {self.sesje}, zaktualizowane: {self.zaktualizowane}, wiadomości: {self.wiadomosci}, "
            f"nowe załączniki: {self.nowe_zalaczniki}, brakujące załączniki: {self.brakujace_zalaczniki}, "
            f"zindeksowane dokumenty: {self.dokumenty}"
            + (f", nieczytelne sesje: {self.nieczytelne}" if self.nieczytelne else "")
        )


def archiwizuj(
    polaczenie: sqlite3.Connection,
    sciezki: Sciezki,
    adaptery: list[Adapter],
    pelne: bool = False,
) -> Raport:
    """Wczytuje sesje nowe lub zmienione od ostatniego razu (`pelne` = wszystkie).

    Rozmowy, których pliki źródłowe zniknęły, zostają w archiwum – po to ono jest. Rozmowy usunięte na stałe
    w oknie „pamięć” są pomijane, a wymazane sekrety – wymazywane także z nowych wczytań.
    """
    raport = Raport()
    magazyn = Magazyn(sciezki.zalaczniki, polaczenie)
    rdzenie = Rdzenie(polaczenie)
    wymazane = sekrety.wczytaj(polaczenie)
    usuniete = {w[0] for w in polaczenie.execute("SELECT skrot FROM usuniete_rozmowy")}
    # Cały stan jednym zapytaniem: narzędzie MCP dociąga co 15 s, a osobne zapytanie na każdy plik źródła
    # to przy tysiącach sesji większość czasu przebiegu, w którym nic się nie zmieniło.
    znane = {} if pelne else {
        w["sciezka"]: (w["rozmiar"], w["mtime_ns"])
        for w in polaczenie.execute("SELECT sciezka, rozmiar, mtime_ns FROM stan_zrodel")
    }

    for adapter in adaptery:
        for sesja, pliki in adapter.znajdz_sesje().items():
            raport.sesje += 1
            if usuniete and skrot_sesji(adapter.nazwa, sesja) in usuniete:
                continue
            # Błąd odczytu jednej sesji (uszkodzony .zst, nietypowy rekord, plik zniknął lub jest zajęty) pomija
            # tylko ją – inaczej zatrzymywałby przy każdym przebiegu wszystkie dalsze sesje i źródła. Sesja nie
            # trafia do stan_zrodel, więc wróci w następnym przebiegu. Błędy zapisu archiwum idą wyżej, jak dotąd.
            try:
                stany = _stany(adapter, sesja, pliki)
            except Exception:
                _nieczytelna(adapter, sesja, raport)
                continue
            if not pelne and all(znane.get(klucz) == stan for klucz, stan in stany.items()):
                continue
            try:
                rozmowa = adapter.wczytaj(sesja, pliki)
                surowe = list(_surowe(adapter, sesja, pliki))
            except Exception:
                _nieczytelna(adapter, sesja, raport)
                continue
            # Kopia przed stanem źródła: gdy jej zapis się nie uda, sesja zostaje „zmieniona” i wróci w następnym
            # przebiegu – inaczej kopia zostałaby nieaktualna, aż do kolejnej zmiany źródła.
            _zapisz_surowe(surowa_kopia(sciezki, adapter.nazwa, sesja), surowe, wymazane)
            with polaczenie:
                _zapisz_rozmowe(polaczenie, rozmowa, magazyn, rdzenie, raport, wymazane)
                polaczenie.executemany(
                    """INSERT INTO stan_zrodel (sciezka, zrodlo, sesja, rozmiar, mtime_ns) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT (sciezka) DO UPDATE SET rozmiar = excluded.rozmiar, mtime_ns = excluded.mtime_ns""",
                    [(klucz, adapter.nazwa, sesja, r, m) for klucz, (r, m) in stany.items()],
                )
            raport.zaktualizowane += 1

    indeksuj_dokumenty(polaczenie, magazyn, rdzenie, raport, wymazane)
    return raport


def _nieczytelna(adapter: Adapter, sesja: str, raport: Raport) -> None:
    log.warning("Nie udało się odczytać sesji %s %s – pominięta", adapter.nazwa, sesja, exc_info=True)
    raport.nieczytelne += 1


def surowa_kopia(sciezki: Sciezki, zrodlo: str, sesja: str) -> Path:
    return sciezki.surowe / zrodlo / f"{sesja}.jsonl.gz"


def skrot_sesji(zrodlo: str, sesja: str) -> str:
    """Rozmowa usunięta na stałe – bez jawnej nazwy sesji (w KreionAI to początek pierwszej wiadomości)."""
    return hashlib.sha256(f"{zrodlo}\n{sesja}".encode("utf-8")).hexdigest()


def _stany(adapter: Adapter, sesja: str, pliki: list[Path]) -> dict[str, tuple[int, int]]:
    """Klucz w stan_zrodel → (rozmiar, czas zmiany). Źródło z wieloma rozmowami w jednej bazie (Cursor) podaje
    stan każdej rozmowy samo – stan pliku bazy zmienia się przy każdej z nich."""
    stan_sesji = getattr(adapter, "stan_sesji", None)
    if stan_sesji is not None:
        return stan_sesji(sesja)
    return {str(p): _stan_pliku(p) for p in pliki}


def _surowe(adapter: Adapter, sesja: str, pliki: list[Path]) -> Iterator[bytes]:
    surowe = getattr(adapter, "surowe", None)
    if surowe is not None:
        yield surowe(sesja)
        return
    for plik in pliki:
        yield czytaj_bajty(plik)  # rozpakowany: kopia ma być czytelnym JSONL, z którego da się wymazać sekret


def _stan_pliku(plik: Path) -> tuple[int, int]:
    st = plik.stat()
    return st.st_size, st.st_mtime_ns


def _zapisz_rozmowe(
    polaczenie: sqlite3.Connection, rozmowa: Rozmowa, magazyn: Magazyn, rdzenie: Rdzenie, raport: Raport,
    wymazane: Sekrety,
) -> None:
    """Zastępuje całą rozmowę w bazie – prościej i pewniej niż dopisywanie od offsetu. Kolumn edycji
    (ukryta, tytul_wlasny, projekt_wlasny) nie rusza – z wyjątkiem rozmowy nowej w projekcie, któremu w oknie
    „pamięć” zmieniono nazwę: dostaje tę nazwę od razu."""
    poprzednia = polaczenie.execute(
        "SELECT projekt, projekt_wlasny FROM rozmowy WHERE zrodlo = ? AND sesja = ?", (rozmowa.zrodlo, rozmowa.sesja)
    ).fetchone()
    # Nowa w projekcie: nowa rozmowa albo taka, której źródło dopiero teraz przypisało projekt (KreionAI).
    nowa_w_projekcie = poprzednia is None or (
        poprzednia["projekt_wlasny"] is None and poprzednia["projekt"] != rozmowa.projekt
    )
    nadana_nazwa = _nadana_nazwa_projektu(polaczenie, rozmowa.projekt) if nowa_w_projekcie else None
    rozmowa_id = polaczenie.execute(
        """INSERT INTO rozmowy (zrodlo, sesja, projekt, cwd, galaz, tytul, pierwsza_ts, ostatnia_ts, liczba_wiad)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (zrodlo, sesja) DO UPDATE SET
               projekt = excluded.projekt, cwd = excluded.cwd, galaz = excluded.galaz, tytul = excluded.tytul,
               pierwsza_ts = excluded.pierwsza_ts, ostatnia_ts = excluded.ostatnia_ts,
               liczba_wiad = excluded.liczba_wiad
           RETURNING id""",
        (
            rozmowa.zrodlo, rozmowa.sesja, rozmowa.projekt, rozmowa.cwd, rozmowa.galaz, wymazane.tekst(rozmowa.tytul),
            rozmowa.pierwsza_ts, rozmowa.ostatnia_ts, len(rozmowa.wiadomosci),
        ),
    ).fetchone()["id"]
    if nadana_nazwa:
        polaczenie.execute("UPDATE rozmowy SET projekt_wlasny = ? WHERE id = ?", (nadana_nazwa, rozmowa_id))

    # Pliki z %TEMP% znikają; przy ponownej indeksacji podpinamy kopię, która już jest w magazynie.
    znane_zalaczniki = {
        w["nazwa"]: w["sha256"]
        for w in polaczenie.execute(
            """SELECT wz.nazwa, wz.sha256 FROM wiadomosc_zalacznik wz
               JOIN wiadomosci w ON w.id = wz.wiadomosc_id WHERE w.rozmowa_id = ?""",
            (rozmowa_id,),
        )
    }
    ukryte = {w[0] for w in polaczenie.execute("SELECT lp FROM ukryte_wiadomosci WHERE rozmowa_id = ?", (rozmowa_id,))}
    dawne = [(w["rola"], w["tekst"]) for w in polaczenie.execute(
        "SELECT rola, tekst FROM wiadomosci WHERE rozmowa_id = ? ORDER BY lp", (rozmowa_id,))] if ukryte else []
    polaczenie.execute(
        "DELETE FROM fts_wiadomosci WHERE rowid IN (SELECT id FROM wiadomosci WHERE rozmowa_id = ?)", (rozmowa_id,)
    )
    polaczenie.execute("DELETE FROM wiadomosci WHERE rozmowa_id = ?", (rozmowa_id,))

    teksty = [wymazane.tekst(w.tekst) for w in rozmowa.wiadomosci]
    tokeny_wiadomosci = [tokenizuj(t) for t in teksty]
    mapa = rdzenie.mapa({t for tokeny in tokeny_wiadomosci for t in tokeny})

    for lp, (wiadomosc, tekst, tokeny) in enumerate(zip(rozmowa.wiadomosci, teksty, tokeny_wiadomosci)):
        wiadomosc_id = polaczenie.execute(
            "INSERT INTO wiadomosci (rozmowa_id, lp, rola, ts, tekst) VALUES (?, ?, ?, ?, ?)",
            (rozmowa_id, lp, wiadomosc.rola, wiadomosc.ts, tekst),
        ).lastrowid
        polaczenie.execute(
            "INSERT INTO fts_wiadomosci (rowid, rdzenie, tekst) VALUES (?, ?, ?)",
            (wiadomosc_id, rdzenie.tekst_rdzeni(tokeny, mapa), do_indeksu(tekst)),
        )
        for zalacznik in wiadomosc.zalaczniki:
            zapisany = magazyn.zapisz(zalacznik)
            if zapisany is not None:
                sha, nowy = zapisany
                raport.nowe_zalaczniki += int(nowy)
            elif zalacznik.nazwa in znane_zalaczniki:
                sha = znane_zalaczniki[zalacznik.nazwa]
            else:
                raport.brakujace_zalaczniki += 1
                continue
            polaczenie.execute(
                "INSERT OR IGNORE INTO wiadomosc_zalacznik (wiadomosc_id, sha256, nazwa) VALUES (?, ?, ?)",
                (wiadomosc_id, sha, zalacznik.nazwa),
            )
        raport.wiadomosci += 1
    if ukryte:
        nowe = [(w.rola, t) for w, t in zip(rozmowa.wiadomosci, teksty)]
        polaczenie.execute("DELETE FROM ukryte_wiadomosci WHERE rozmowa_id = ?", (rozmowa_id,))
        polaczenie.executemany("INSERT INTO ukryte_wiadomosci (rozmowa_id, lp) VALUES (?, ?)",
                               [(rozmowa_id, lp) for lp in sorted(przeniesione_ukrycia(ukryte, dawne, nowe))])


def przeniesione_ukrycia(ukryte: set[int], dawne: list[tuple[str, str]], nowe: list[tuple[str, str]]) -> set[int]:
    """Numery ukrytych wiadomości po ponownym wczytaniu rozmowy (wiadomość = rola i treść).

    Ukrycie idzie za treścią, nie za numerem – źródło może zmienić podział rozmowy (Cursor scala tury, Claude Code
    sortuje po czasie) i pod dawnym numerem byłaby inna wiadomość. Dopasowanie jak w diff: wiadomości wspólne
    przechodzą na swoje nowe miejsca; zmieniony fragment (rosnąca odpowiedź, scalone tury) – na odpowiadające
    miejsce w nowym fragmencie, a gdy nowy jest krótszy, na jego ostatnią wiadomość, żeby nic nie wyszło na jaw.
    Ukrycie wiadomości usuniętej ze źródła przepada razem z nią."""
    wynik = set()
    for operacja, d1, d2, n1, n2 in SequenceMatcher(None, dawne, nowe, autojunk=False).get_opcodes():
        if operacja in ("equal", "replace"):
            wynik.update(min(n1 + lp - d1, n2 - 1) for lp in range(d1, d2) if lp in ukryte)
    return wynik


def _nadana_nazwa_projektu(polaczenie: sqlite3.Connection, projekt: str | None) -> str | None:
    """Nazwa nadana w oknie „pamięć” wszystkim dotychczasowym rozmowom projektu (np. „pszczola” → „Pszczoła”).
    Gdy choć jedna została przy nazwie ze źródła albo trafiła gdzie indziej (przeniesiona pojedyncza rozmowa),
    nie ma jednej nazwy i nowa rozmowa zostaje przy nazwie ze źródła."""
    if not projekt:
        return None
    nazwy = {w[0] for w in polaczenie.execute("SELECT DISTINCT projekt_wlasny FROM rozmowy WHERE projekt = ?",
                                              (projekt,))}
    return nazwy.pop() if len(nazwy) == 1 else None


def indeksuj_dokumenty(
    polaczenie: sqlite3.Connection, magazyn: Magazyn, rdzenie: Rdzenie, raport: Raport, wymazane: Sekrety
) -> int:
    """Tekst dokumentów do indeksu – raz na plik, także dla załączników zapisanych przed wyciąganiem tekstu
    (i tych, którym edycja wyzerowała `tekst_wersja`). Z plików tekstowych wymazuje sekrety także w samym pliku –
    PDF czy DOCX zostają bez zmian, wymazany jest tylko ich tekst w bazie. Zwraca liczbę zmienionych plików."""
    zmienione = 0
    zalegle = polaczenie.execute(
        "SELECT sha256, sciezka FROM zalaczniki WHERE tekst_wersja IS NULL OR tekst_wersja < ?", (dokumenty.WERSJA,)
    ).fetchall()
    for wiersz in zalegle:
        plik = magazyn.sciezka(wiersz["sciezka"])
        if wymazane and plik.suffix.lower() in dokumenty.ROZSZERZENIA_TEKSTOWE:
            zmienione += _wymaz_w_pliku(plik, wymazane)
        try:
            tekst = wymazane.tekst(dokumenty.wyciagnij_tekst(plik))
        except Exception:  # uszkodzony lub nietypowy plik nie może zatrzymać archiwizacji
            log.warning("Nie udało się wyciągnąć tekstu z %s", plik, exc_info=True)
            tekst = None
        with polaczenie:
            polaczenie.execute("DELETE FROM fts_zalaczniki WHERE sha256 = ?", (wiersz["sha256"],))
            if tekst:
                tokeny = tokenizuj(tekst)
                polaczenie.execute(
                    "INSERT INTO fts_zalaczniki (sha256, rdzenie, tekst) VALUES (?, ?, ?)",
                    (wiersz["sha256"], rdzenie.tekst_rdzeni(tokeny, rdzenie.mapa(set(tokeny))), do_indeksu(tekst)),
                )
                raport.dokumenty += 1
            polaczenie.execute(
                "UPDATE zalaczniki SET tekst = ?, tekst_wersja = ? WHERE sha256 = ?",
                (tekst, dokumenty.WERSJA, wiersz["sha256"]),
            )
    return zmienione


def _wymaz_w_pliku(plik: Path, wymazane: Sekrety) -> bool:
    try:
        dane = plik.read_bytes()
    except OSError:
        return False
    if not wymazane.wystepuje_w(dane):
        return False
    tymczasowy = plik.with_suffix(plik.suffix + ".tmp")
    tymczasowy.write_bytes(wymazane.bajty(dane))
    os.replace(tymczasowy, plik)
    return True


def _zapisz_surowe(cel: Path, czesci: Iterable[bytes], wymazane: Sekrety) -> None:
    """Pełna kopia transkrypcji (z wynikami narzędzi) – na wypadek skasowania źródła lub zmiany formatu."""
    cel.parent.mkdir(parents=True, exist_ok=True)
    tymczasowy = cel.with_suffix(".tmp")
    with gzip.open(tymczasowy, "wb") as wyjscie:
        for dane in czesci:
            wyjscie.write(wymazane.bajty(dane))
    os.replace(tymczasowy, cel)
