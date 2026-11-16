"""Edycja pamięci (okno „SkerimAI – pamięć”): ukrywanie rozmów i wiadomości, własne tytuły i projekty, usuwanie
rozmów na stałe i wymazywanie sekretów.

Ukrycie oraz własny tytuł i projekt leżą w osobnych kolumnach i tabelach – archiwizacja ich nie nadpisuje.
Usunięcie i wymazanie są nieodwracalne: działają pod blokadą archiwizatora (inaczej mógłby w tym czasie zapisać
starą wersję), a na koniec baza jest przepisywana (VACUUM), żeby usunięta treść nie została w wolnych stronach
pliku. Pliki aplikacji (Claude Code, Codex, KreionAI) i wcześniejsze kopie zapasowe archiwum zostają bez zmian.
"""

from __future__ import annotations

import gzip
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import dokumenty, sekrety
from .archiwizator import Raport, indeksuj_dokumenty, skrot_sesji, surowa_kopia
from .blokada import CZEKAJ_NA_ARCHIWUM_S, blokada
from .konfiguracja import Sciezki
from .magazyn import Magazyn
from .sekrety import MIN_DLUGOSC, ZNACZNIK, Sekrety
from .tekst import Rdzenie, do_indeksu, tokenizuj
from .zapytania import PROJEKT, TYTUL, Filtry, szukaj

log = logging.getLogger("skerimai")

WSZYSTKIE, W_PAMIECI, UKRYTE = "wszystkie", "w pamięci", "ukryte"
_LIMIT_TRAFIEN = 300

Postep = Callable[[str], None]


class ArchiwumZajete(Exception):
    pass


@dataclass
class RozmowaEdycji:
    id: int
    zrodlo: str
    projekt: str | None
    tytul: str | None
    ostatnia_ts: str | None
    liczba_wiad: int
    ukryta: bool
    wlasny_tytul: bool
    wlasny_projekt: bool


def rozmowy(polaczenie: sqlite3.Connection, szukane: str = "", zrodlo: str | None = None, projekt: str | None = None,
            pokaz: str = WSZYSTKIE) -> list[RozmowaEdycji]:
    """Rozmowy do okna, od najnowszej. `szukane` – słowa w treści (jak szukaj_w_historii) albo fragment tytułu."""
    filtry = Filtry(zrodlo=zrodlo, projekt=projekt, ukryte=True)
    warunki, parametry = filtry.sql("r.ostatnia_ts")
    if pokaz == W_PAMIECI:
        warunki += " AND r.ukryta = 0"
    elif pokaz == UKRYTE:
        warunki += " AND r.ukryta = 1"
    if szukane.strip():
        trafione = sorted({t.rozmowa_id for t in szukaj(polaczenie, szukane, filtry, _LIMIT_TRAFIEN)})
        warunki += f" AND (r.id IN ({', '.join('?' * len(trafione)) or 'NULL'}) OR {TYTUL} LIKE ?)"
        parametry += [*trafione, f"%{szukane.strip()}%"]
    return _rozmowy_gdzie(polaczenie, warunki, parametry)


def _rozmowy_gdzie(polaczenie: sqlite3.Connection, warunki: str, parametry: list) -> list[RozmowaEdycji]:
    wiersze = polaczenie.execute(
        f"""SELECT r.id, r.zrodlo, {PROJEKT} AS projekt, {TYTUL} AS tytul, r.ostatnia_ts, r.liczba_wiad, r.ukryta,
                   r.tytul_wlasny IS NOT NULL AS wlasny_tytul, r.projekt_wlasny IS NOT NULL AS wlasny_projekt
            FROM rozmowy r WHERE 1 = 1{warunki} ORDER BY r.ostatnia_ts DESC""",
        parametry,
    ).fetchall()
    return [RozmowaEdycji(**{**dict(w), **{k: bool(w[k]) for k in ("ukryta", "wlasny_tytul", "wlasny_projekt")}})
            for w in wiersze]


def projekty(polaczenie: sqlite3.Connection) -> list[str]:
    return [w[0] for w in polaczenie.execute(
        f"SELECT DISTINCT {PROJEKT} FROM rozmowy r WHERE {PROJEKT} IS NOT NULL ORDER BY lower({PROJEKT})")]


# ---------------------------------------------------------------- zmiany odwracalne

def ukryj_rozmowy(polaczenie: sqlite3.Connection, ids: Iterable[int], ukryta: bool = True) -> None:
    with polaczenie:
        polaczenie.executemany("UPDATE rozmowy SET ukryta = ? WHERE id = ?", [(int(ukryta), i) for i in ids])


def ukryj_wiadomosci(polaczenie: sqlite3.Connection, rozmowa_id: int, lps: Iterable[int], ukryta: bool = True) -> None:
    with polaczenie:
        if ukryta:
            polaczenie.executemany("INSERT OR IGNORE INTO ukryte_wiadomosci (rozmowa_id, lp) VALUES (?, ?)",
                                   [(rozmowa_id, lp) for lp in lps])
        else:
            polaczenie.executemany("DELETE FROM ukryte_wiadomosci WHERE rozmowa_id = ? AND lp = ?",
                                   [(rozmowa_id, lp) for lp in lps])


def ustaw_tytul(polaczenie: sqlite3.Connection, rozmowa_id: int, tytul: str | None) -> None:
    """Pusty albo None – znów tytuł ze źródła."""
    with polaczenie:
        polaczenie.execute("UPDATE rozmowy SET tytul_wlasny = ? WHERE id = ?", ((tytul or "").strip() or None, rozmowa_id))


def ustaw_projekt(polaczenie: sqlite3.Connection, ids: Iterable[int], projekt: str | None) -> None:
    """Pusty albo None – znów projekt ze źródła."""
    wartosc = (projekt or "").strip() or None
    with polaczenie:
        polaczenie.executemany("UPDATE rozmowy SET projekt_wlasny = ? WHERE id = ?", [(wartosc, i) for i in ids])


# ---------------------------------------------------------------- zmiany nieodwracalne

@contextmanager
def _archiwum_na_wylacznosc(sciezki: Sciezki, czekaj_s: float) -> Iterator[None]:
    with blokada(sciezki.blokada, czekaj_s) as uzyskana:
        if not uzyskana:
            raise ArchiwumZajete(f"Archiwum zajęte przez archiwizację dłużej niż {czekaj_s:.0f} s – spróbuj za chwilę.")
        yield


def usun_rozmowy(polaczenie: sqlite3.Connection, sciezki: Sciezki, ids: Iterable[int],
                 czekaj_s: float = CZEKAJ_NA_ARCHIWUM_S) -> int:
    """Usuwa rozmowy z bazy, ich surowe kopie i załączniki, których nie ma już w żadnej innej rozmowie.
    Archiwizator zapamiętuje je jako usunięte i nie wczyta ich ponownie ze źródła. Zwraca liczbę usuniętych."""
    with _archiwum_na_wylacznosc(sciezki, czekaj_s):
        pliki: list[Path] = []
        usuniete = 0
        with polaczenie:
            for rozmowa_id in ids:
                rozmowa = polaczenie.execute("SELECT zrodlo, sesja FROM rozmowy WHERE id = ?", (rozmowa_id,)).fetchone()
                if rozmowa is None:
                    continue
                zrodlo, sesja = rozmowa["zrodlo"], rozmowa["sesja"]
                zalaczniki = [w[0] for w in polaczenie.execute(
                    """SELECT DISTINCT wz.sha256 FROM wiadomosc_zalacznik wz
                       JOIN wiadomosci w ON w.id = wz.wiadomosc_id WHERE w.rozmowa_id = ?""", (rozmowa_id,))]
                polaczenie.execute(
                    "INSERT OR REPLACE INTO usuniete_rozmowy (skrot, usunieto) VALUES (?, ?)",
                    (skrot_sesji(zrodlo, sesja), datetime.now(timezone.utc).isoformat()),
                )
                polaczenie.execute("DELETE FROM fts_wiadomosci WHERE rowid IN (SELECT id FROM wiadomosci WHERE rozmowa_id = ?)",
                                   (rozmowa_id,))
                polaczenie.execute("DELETE FROM rozmowy WHERE id = ?", (rozmowa_id,))  # kaskadą wiadomości i powiązania
                polaczenie.execute("DELETE FROM stan_zrodel WHERE zrodlo = ? AND sesja = ?", (zrodlo, sesja))
                pliki.append(surowa_kopia(sciezki, zrodlo, sesja))
                pliki += _usun_osierocone(polaczenie, sciezki, zalaczniki)
                usuniete += 1
        for plik in pliki:
            try:
                plik.unlink(missing_ok=True)
            except OSError:
                log.warning("Nie udało się usunąć %s", plik, exc_info=True)
        wyczysc_baze(polaczenie)
    return usuniete


def _usun_osierocone(polaczenie: sqlite3.Connection, sciezki: Sciezki, zalaczniki: list[str]) -> list[Path]:
    pliki = []
    for sha in zalaczniki:
        if polaczenie.execute("SELECT 1 FROM wiadomosc_zalacznik WHERE sha256 = ? LIMIT 1", (sha,)).fetchone():
            continue
        wiersz = polaczenie.execute("SELECT sciezka FROM zalaczniki WHERE sha256 = ?", (sha,)).fetchone()
        polaczenie.execute("DELETE FROM fts_zalaczniki WHERE sha256 = ?", (sha,))
        polaczenie.execute("DELETE FROM zalaczniki WHERE sha256 = ?", (sha,))
        if wiersz:
            pliki.append(sciezki.zalaczniki / wiersz["sciezka"])
    return pliki


@dataclass
class Wystapienia:
    wiadomosci: int = 0
    rozmowy: int = 0
    dokumenty: int = 0
    surowe: int = 0  # surowe kopie sesji (z wynikami narzędzi – np. odczytany plik .env)
    tytuly: int = 0
    niewymazywalne: list[str] = field(default_factory=list)  # PDF, DOCX…: tekst w bazie tak, sam plik nie
    # Rozmowy z sekretem w nazwie sesji (KreionAI nazywa plik rozmowy pierwszą wiadomością). Nazwa wiąże rozmowę
    # z plikiem aplikacji, więc zostaje – pozbyć się jej można tylko, usuwając rozmowę na stałe.
    w_nazwie_sesji: list[RozmowaEdycji] = field(default_factory=list)

    @property
    def razem(self) -> int:
        """Bez nazw sesji – tych wymazanie nie zmienia."""
        return self.wiadomosci + self.dokumenty + self.surowe + self.tytuly


def _sprawdz_sekret(sekret: str) -> str:
    sekret = sekret.strip()
    if len(sekret) < MIN_DLUGOSC:
        raise ValueError(f"Sekret musi mieć co najmniej {MIN_DLUGOSC} znaki.")
    if sekret in ZNACZNIK:
        raise ValueError(f"Nie można wymazać samego znacznika {ZNACZNIK}.")
    return sekret


def policz_wystapienia(polaczenie: sqlite3.Connection, sciezki: Sciezki, sekret: str,
                       postep: Postep | None = None) -> Wystapienia:
    """Gdzie w archiwum jest sekret – bez zmieniania czegokolwiek. Przegląda też surowe kopie (kilka sekund)."""
    sekret = _sprawdz_sekret(sekret)
    wynik = Wystapienia()
    wynik.wiadomosci, wynik.rozmowy = polaczenie.execute(
        "SELECT count(*), count(DISTINCT rozmowa_id) FROM wiadomosci WHERE instr(tekst, ?) > 0", (sekret,)).fetchone()
    wynik.tytuly = polaczenie.execute(
        "SELECT count(*) FROM rozmowy WHERE instr(coalesce(tytul, ''), ?) > 0 OR instr(coalesce(tytul_wlasny, ''), ?) > 0",
        (sekret, sekret)).fetchone()[0]
    for w in polaczenie.execute("SELECT pierwsza_nazwa, ext FROM zalaczniki WHERE instr(tekst, ?) > 0", (sekret,)):
        wynik.dokumenty += 1
        if w["ext"] not in dokumenty.ROZSZERZENIA_TEKSTOWE:
            wynik.niewymazywalne.append(w["pierwsza_nazwa"] or w["ext"])
    szukany = Sekrety([sekret])
    for plik, dane in _surowe_kopie(sciezki, postep):
        wynik.surowe += szukany.wystepuje_w(dane)
    wynik.w_nazwie_sesji = _w_nazwie_sesji(polaczenie, sekret)
    return wynik


def _w_nazwie_sesji(polaczenie: sqlite3.Connection, sekret: str) -> list[RozmowaEdycji]:
    return _rozmowy_gdzie(polaczenie, " AND instr(r.sesja, ?) > 0", [sekret])


def wymaz_sekret(polaczenie: sqlite3.Connection, sciezki: Sciezki, sekret: str, postep: Postep | None = None,
                 czekaj_s: float = CZEKAJ_NA_ARCHIWUM_S) -> Wystapienia:
    """Zamienia sekret na „[wymazano]” w wiadomościach, tytułach, indeksie, tekście dokumentów, plikach tekstowych
    w magazynie załączników i surowych kopiach. Zapamiętuje go (zaszyfrowany), żeby archiwizacja wymazywała go
    także przy kolejnych wczytaniach ze źródła. Zwraca, co zostało wymazane, i rozmowy, w których sekret został
    w nazwie sesji (do usunięcia na stałe)."""
    sekret = _sprawdz_sekret(sekret)
    wymazany = Sekrety([sekret])
    wynik = Wystapienia()
    with _archiwum_na_wylacznosc(sciezki, czekaj_s):
        _postep(postep, "Wymazuję z wiadomości…")
        rdzenie = Rdzenie(polaczenie)
        with polaczenie:
            sekrety.zapamietaj(polaczenie, sekret)
            wiersze = polaczenie.execute(
                "SELECT id, rozmowa_id, tekst FROM wiadomosci WHERE instr(tekst, ?) > 0", (sekret,)).fetchall()
            for w in wiersze:
                tekst = wymazany.tekst(w["tekst"])
                tokeny = tokenizuj(tekst)
                polaczenie.execute("UPDATE wiadomosci SET tekst = ? WHERE id = ?", (tekst, w["id"]))
                polaczenie.execute("DELETE FROM fts_wiadomosci WHERE rowid = ?", (w["id"],))
                polaczenie.execute("INSERT INTO fts_wiadomosci (rowid, rdzenie, tekst) VALUES (?, ?, ?)",
                                   (w["id"], rdzenie.tekst_rdzeni(tokeny, rdzenie.mapa(set(tokeny))), do_indeksu(tekst)))
            wynik.wiadomosci, wynik.rozmowy = len(wiersze), len({w["rozmowa_id"] for w in wiersze})
            for kolumna in ("tytul", "tytul_wlasny"):
                wynik.tytuly += polaczenie.execute(
                    f"UPDATE rozmowy SET {kolumna} = replace({kolumna}, ?, ?) WHERE instr({kolumna}, ?) > 0",
                    (sekret, ZNACZNIK, sekret)).rowcount
            for w in polaczenie.execute("SELECT pierwsza_nazwa, ext FROM zalaczniki WHERE instr(tekst, ?) > 0", (sekret,)):
                if w["ext"] not in dokumenty.ROZSZERZENIA_TEKSTOWE:
                    wynik.niewymazywalne.append(w["pierwsza_nazwa"] or w["ext"])
            # Tekst dokumentów wyciągnie od nowa archiwizator – z wymazaniem, a w plikach tekstowych także w pliku.
            wynik.dokumenty = polaczenie.execute(
                "UPDATE zalaczniki SET tekst_wersja = NULL WHERE instr(tekst, ?) > 0", (sekret,)).rowcount
            # Pamięć podręczna stemmera zna każde słowo z archiwum, także słowa sekretu.
            polaczenie.executemany("DELETE FROM rdzenie WHERE slowo = ?", [(t,) for t in set(tokenizuj(sekret))])
        _postep(postep, "Wymazuję z dokumentów…")
        indeksuj_dokumenty(polaczenie, Magazyn(sciezki.zalaczniki, polaczenie), rdzenie, Raport(), wymazany)
        for plik, dane in _surowe_kopie(sciezki, postep):
            if wymazany.wystepuje_w(dane):
                _zapisz_gzip(plik, wymazany.bajty(dane))
                wynik.surowe += 1
        _postep(postep, "Porządkuję bazę…")
        wyczysc_baze(polaczenie)
    wynik.w_nazwie_sesji = _w_nazwie_sesji(polaczenie, sekret)
    return wynik


def _surowe_kopie(sciezki: Sciezki, postep: Postep | None) -> Iterator[tuple[Path, bytes]]:
    pliki = sorted(sciezki.surowe.rglob("*.jsonl.gz")) if sciezki.surowe.is_dir() else []
    for nr, plik in enumerate(pliki, 1):
        _postep(postep, f"Surowe kopie: {nr} z {len(pliki)}…")
        try:
            yield plik, gzip.decompress(plik.read_bytes())
        except (OSError, EOFError):
            log.warning("Nie udało się odczytać %s", plik, exc_info=True)


def _zapisz_gzip(plik: Path, dane: bytes) -> None:
    tymczasowy = plik.with_suffix(".tmp")
    with gzip.open(tymczasowy, "wb") as wyjscie:
        wyjscie.write(dane)
    os.replace(tymczasowy, plik)


def _postep(postep: Postep | None, opis: str) -> None:
    if postep:
        postep(opis)


def wyczysc_baze(polaczenie: sqlite3.Connection) -> None:
    """Usunięta treść nie może zostać w pliku bazy: indeksy FTS5 trzymają skasowane słowa w starych segmentach
    do scalenia (optimize), a SQLite – w wolnych stronach pliku i w dzienniku WAL (VACUUM, checkpoint)."""
    polaczenie.commit()
    for tabela in ("fts_wiadomosci", "fts_zalaczniki"):
        polaczenie.execute(f"INSERT INTO {tabela} ({tabela}) VALUES ('optimize')")
        polaczenie.commit()
    polaczenie.execute("VACUUM")
    polaczenie.execute("PRAGMA wal_checkpoint(TRUNCATE)")
