"""Odczyt archiwum: wyszukiwanie, lista rozmów, czytanie rozmowy, załączniki."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from rapidfuzz import fuzz, process

from .adaptery import ZRODLA_POMOCNICZE
from .tekst import Rdzenie, bez_ogonkow, do_indeksu, rdzen_slowa, tokenizuj

_MIN_PREFIKS = 4
_PROG_LITEROWKI = 80
_DLUGOSC_FRAGMENTU = 240
_SLOWNIKI = ("fts_slownik", "fts_slownik_zalacznikow")
_SZESNASTKOWY = re.compile(r"[0-9a-fA-F]{1,64}")

# Tytuł i projekt nadane w oknie „pamięć” mają pierwszeństwo przed tymi ze źródła.
TYTUL = "coalesce(r.tytul_wlasny, r.tytul)"
PROJEKT = "coalesce(r.projekt_wlasny, r.projekt)"
_WIDOCZNA_WIADOMOSC = (
    " AND NOT EXISTS (SELECT 1 FROM ukryte_wiadomosci u WHERE u.rozmowa_id = w.rozmowa_id AND u.lp = w.lp)"
)


def czas_lokalny(ts: str | None) -> str:
    """Znacznik czasu ISO (UTC) jako lokalne „RRRR-MM-DD GG:MM”."""
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts[:16]


@dataclass
class Trafienie:
    rozmowa_id: int
    wiadomosc_id: int
    lp: int
    rola: str
    ts: str | None
    fragment: str
    zrodlo: str
    projekt: str | None
    tytul: str | None
    zalacznik: str | None = None  # nazwa dokumentu, gdy słowa trafiły w jego tekst, a nie w wiadomość
    sha256: str | None = None
    sesja: str = ""


@dataclass
class Filtry:
    zrodlo: str | None = None  # bez źródła: wszystkie poza pomocniczymi (ZRODLA_POMOCNICZE)
    projekt: str | None = None
    od: str | None = None  # RRRR-MM-DD, włącznie
    do: str | None = None  # RRRR-MM-DD, włącznie
    ukryte: bool = False  # także rozmowy i wiadomości ukryte w oknie „pamięć”
    pomin_zrodla: tuple[str, ...] = ()  # bez źródła: także tych nie pokazuje

    def sql(self, kolumna_ts: str) -> tuple[str, list]:
        warunki, parametry = [], []
        if not self.ukryte:
            warunki.append("r.ukryta = 0")
        if self.zrodlo:
            warunki.append("r.zrodlo = ?")
            parametry.append(self.zrodlo)
        elif pominiete := sorted(ZRODLA_POMOCNICZE | set(self.pomin_zrodla)):
            warunki.append(f"r.zrodlo NOT IN ({', '.join('?' * len(pominiete))})")
            parametry.extend(pominiete)
        if self.projekt:  # bez względu na wielkość liter i polskie znaki
            warunki.append(f"bez_ogonkow({PROJEKT}) LIKE ?")
            parametry.append(f"%{bez_ogonkow(self.projekt)}%")
        if self.od:
            warunki.append(f"{kolumna_ts} >= ?")
            parametry.append(_poczatek_dnia(date.fromisoformat(self.od)))
        if self.do:
            warunki.append(f"{kolumna_ts} < ?")
            parametry.append(_poczatek_dnia(date.fromisoformat(self.do) + timedelta(days=1)))
        return "".join(f" AND {w}" for w in warunki), parametry


def _poczatek_dnia(dzien: date) -> str:
    """Lokalna północ jako znacznik UTC w kształcie z bazy – dzień w filtrach to dzień w czasie lokalnym.

    Strefę (także czas letni/zimowy) system ustala dla tej konkretnej daty."""
    return datetime.combine(dzien, time()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def szukaj(polaczenie: sqlite3.Connection, zapytanie: str, filtry: Filtry | None = None, limit: int = 10) -> list[Trafienie]:
    """Wszystkie słowa naraz (AND); gdy brak wyników – dowolne z nich (OR).

    Każde słowo pasuje przez rdzeń (odmiana), przez początek słowa albo przez poprawkę literówki.
    Przeszukuje wiadomości i tekst załączonych dokumentów.
    """
    tokeny = list(dict.fromkeys(tokenizuj(zapytanie)))
    if not tokeny:
        return []
    filtry = filtry or Filtry()
    try:
        mapa = Rdzenie(polaczenie).mapa(set(tokeny))
        polaczenie.commit()  # nowe rdzenie zapamiętane na następny raz
    except sqlite3.OperationalError:
        # Archiwizator trzyma zapis dłużej niż busy_timeout (np. wczytuje stemmer w środku transakcji) – rdzenie
        # są tylko pamięcią podręczną, a odczyt w trybie WAL działa, więc szukamy bez ich zapamiętania.
        polaczenie.rollback()
        mapa = {t: rdzen_slowa(t) for t in tokeny}
    warunki, poprawki = [], []
    for t in tokeny:
        warunek, poprawka = _warunek_slowa(polaczenie, t, mapa[t])
        warunki.append(warunek)
        if poprawka:
            poprawki.append(poprawka)

    trafienia = _wykonaj(polaczenie, " AND ".join(warunki), filtry, limit)
    if not trafienia and len(warunki) > 1:
        trafienia = _wykonaj(polaczenie, " OR ".join(warunki), filtry, limit)

    szukane_we_fragmencie = tokeny + poprawki
    rdzenie = [mapa[x] for x in tokeny]
    for t in trafienia:
        t.fragment = _fragment(t.fragment, szukane_we_fragmencie, rdzenie)
    return trafienia


def _warunek_slowa(polaczenie: sqlite3.Connection, slowo: str, rdzen: str) -> tuple[str, str | None]:
    """Warunek FTS5 dla jednego słowa i ewentualna poprawka literówki."""
    w_indeksie, rdzen_w_indeksie = do_indeksu(slowo), do_indeksu(rdzen)
    warianty = [
        f'rdzenie:"{rdzen_w_indeksie}"',
        f'tekst:"{w_indeksie}"*' if len(slowo) >= _MIN_PREFIKS else f'tekst:"{w_indeksie}"',
    ]
    poprawka = None
    if len(slowo) >= _MIN_PREFIKS and not _jest_w_slowniku(polaczenie, slowo):
        poprawka = _popraw_literowke(polaczenie, slowo)
        if poprawka:
            warianty.append(f'tekst:"{poprawka}"')
    return "(" + " OR ".join(warianty) + ")", poprawka


def _jest_w_slowniku(polaczenie: sqlite3.Connection, slowo: str) -> bool:
    poczatek = bez_ogonkow(slowo)
    return any(
        polaczenie.execute(
            f"SELECT 1 FROM {slownik} WHERE col = 'tekst' AND term >= ? AND term < ? LIMIT 1",
            (poczatek, poczatek + "￿"),
        ).fetchone()
        for slownik in _SLOWNIKI
    )


# Słowa obu indeksów do poprawiania literówek: plik bazy → (stan archiwum, słowa, ich długości). Pełny przegląd
# słownika FTS5 przy każdym nieznanym słowie rośnie z archiwum (0,3 s przy tysiącu rozmów, 2 s przy dziesięciu
# tysiącach), a serwer MCP żyje tyle co sesja – wystarczy wczytać raz i odświeżać, gdy coś przybędzie.
_SLOWA_SLOWNIKOW: dict[str, tuple[tuple, list[str], list[int]]] = {}


def _slowa_slownikow(polaczenie: sqlite3.Connection) -> tuple[list[str], list[int]]:
    """Stan archiwum zmienia każda zapisana rozmowa (wiadomości dostają nowe numery) i każdy nowy dokument.
    Nie zmienia go usunięcie ani wymazanie bez nowej wiadomości – słowo, którego już nie ma, może wtedy posłużyć
    za poprawkę, która nic nie znajdzie, jak przy braku poprawki."""
    plik = polaczenie.execute("PRAGMA database_list").fetchone()["file"]
    stan = tuple(polaczenie.execute(
        """SELECT (SELECT max(id) FROM wiadomosci), (SELECT count(*) FROM wiadomosci),
                  (SELECT count(*) FROM zalaczniki WHERE tekst IS NOT NULL)""").fetchone())
    zapisane = _SLOWA_SLOWNIKOW.get(plik)
    if zapisane is None or zapisane[0] != stan:
        slowa = [w[0] for slownik in _SLOWNIKI
                 for w in polaczenie.execute(f"SELECT DISTINCT term FROM {slownik} WHERE col = 'tekst'")]
        zapisane = _SLOWA_SLOWNIKOW[plik] = (stan, slowa, [len(s) for s in slowa])
    return zapisane[1], zapisane[2]


def _popraw_literowke(polaczenie: sqlite3.Connection, slowo: str) -> str | None:
    szukane = bez_ogonkow(slowo)
    slowa, dlugosci = _slowa_slownikow(polaczenie)
    od, do = len(szukane) - 2, len(szukane) + 2
    kandydaci = [s for s, n in zip(slowa, dlugosci) if od <= n <= do]
    najlepszy = process.extractOne(szukane, kandydaci, scorer=fuzz.ratio, score_cutoff=_PROG_LITEROWKI)
    return najlepszy[0] if najlepszy else None


def _wykonaj(polaczenie: sqlite3.Connection, dopasowanie: str, filtry: Filtry, limit: int) -> list[Trafienie]:
    """Trafienia w wiadomościach i w dokumentach (BM25: mniej = lepiej)."""
    warunki, parametry = filtry.sql("w.ts")
    if not filtry.ukryte:
        warunki += _WIDOCZNA_WIADOMOSC
    wiadomosci = polaczenie.execute(
        f"""SELECT bm25(fts_wiadomosci, 1.0, 0.7) AS wynik, w.id, w.rozmowa_id, w.lp, w.rola, w.ts, w.tekst,
                   r.zrodlo, {PROJEKT} AS projekt, {TYTUL} AS tytul, NULL AS zalacznik, NULL AS sha256,
                   r.sesja
            FROM fts_wiadomosci
            JOIN wiadomosci w ON w.id = fts_wiadomosci.rowid
            JOIN rozmowy r ON r.id = w.rozmowa_id
            WHERE fts_wiadomosci MATCH ?{warunki}
            ORDER BY wynik
            LIMIT ?""",
        [dopasowanie, *parametry, limit],
    ).fetchall()
    # Dokument wskazuje pierwszą wiadomość, do której go dołączono – raz na rozmowę.
    # Jedna funkcja agregująca (min), więc pozostałe kolumny pochodzą z tego samego wiersza.
    dokumenty = polaczenie.execute(
        f"""WITH dopasowane AS MATERIALIZED (
                SELECT sha256, bm25(fts_zalaczniki, 0.0, 1.0, 0.7) AS wynik
                FROM fts_zalaczniki WHERE fts_zalaczniki MATCH ?
            )
            SELECT d.wynik, w.id, w.rozmowa_id, min(w.lp) AS lp, w.rola, w.ts, z.tekst,
                   r.zrodlo, {PROJEKT} AS projekt, {TYTUL} AS tytul, wz.nazwa AS zalacznik, d.sha256,
                   r.sesja
            FROM dopasowane d
            JOIN zalaczniki z ON z.sha256 = d.sha256  -- oryginał tekstu: indeks ma „l” zamiast „ł”
            JOIN wiadomosc_zalacznik wz ON wz.sha256 = d.sha256
            JOIN wiadomosci w ON w.id = wz.wiadomosc_id
            JOIN rozmowy r ON r.id = w.rozmowa_id
            WHERE 1 = 1{warunki}
            GROUP BY d.sha256, w.rozmowa_id
            ORDER BY d.wynik
            LIMIT ?""",
        [dopasowanie, *parametry, limit],
    ).fetchall()
    wiersze = _polacz_wyniki(wiadomosci, dokumenty, limit)
    return [
        Trafienie(
            w["rozmowa_id"], w["id"], w["lp"], w["rola"], w["ts"], w["tekst"], w["zrodlo"], w["projekt"], w["tytul"],
            w["zalacznik"], w["sha256"], w["sesja"],
        )
        for w in wiersze
    ]


def _polacz_wyniki(wiadomosci: list, dokumenty: list, limit: int) -> list:
    """BM25 z dwóch indeksów nie jest porównywalny: dokumentów jest kilka, więc ich słowa mają niską wagę
    i przegrywałyby z każdą wiadomością. Najlepsze dokumenty dostają zarezerwowaną część miejsc."""
    pewne = dokumenty[: limit // 3]
    reszta = sorted([*wiadomosci, *dokumenty[len(pewne):]], key=lambda w: w["wynik"])[: limit - len(pewne)]
    return sorted([*pewne, *reszta], key=lambda w: w["wynik"])


def _fragment(tekst: str, tokeny: list[str], rdzenie: list[str]) -> str:
    """Wycinek tekstu wokół pierwszego trafionego słowa."""
    plaski = bez_ogonkow(tekst)
    szukane = [bez_ogonkow(t) for t in tokeny] + [bez_ogonkow(r)[: max(_MIN_PREFIKS, len(r) - 2)] for r in rdzenie]
    pozycje = [p for p in (plaski.find(s) for s in szukane if s) if p >= 0]
    srodek = min(pozycje) if pozycje else 0
    start = max(0, srodek - _DLUGOSC_FRAGMENTU // 3)
    koniec = min(len(tekst), start + _DLUGOSC_FRAGMENTU)
    wycinek = " ".join(tekst[start:koniec].split())
    return ("…" if start > 0 else "") + wycinek + ("…" if koniec < len(tekst) else "")


@dataclass
class OpisRozmowy:
    id: int
    zrodlo: str
    projekt: str | None
    tytul: str | None
    pierwsza_ts: str | None
    ostatnia_ts: str | None
    liczba_wiad: int
    sesja: str
    ukryta: bool = False


_KOLUMNY_OPISU = (f"r.id, r.zrodlo, {PROJEKT} AS projekt, {TYTUL} AS tytul, r.pierwsza_ts, r.ostatnia_ts, "
                  "r.liczba_wiad, r.sesja, r.ukryta")


def lista_rozmow(polaczenie: sqlite3.Connection, filtry: Filtry | None = None, limit: int = 30) -> list[OpisRozmowy]:
    warunki, parametry = (filtry or Filtry()).sql("r.ostatnia_ts")
    wiersze = polaczenie.execute(
        f"""SELECT {_KOLUMNY_OPISU} FROM rozmowy r
            WHERE 1 = 1{warunki} ORDER BY r.ostatnia_ts DESC LIMIT ?""",
        [*parametry, limit],
    ).fetchall()
    return [_opis(w) for w in wiersze]


@dataclass
class ZalacznikWiadomosci:
    sha256: str
    nazwa: str | None
    sciezka: str


@dataclass
class WiadomoscArchiwum:
    lp: int
    rola: str
    ts: str | None
    tekst: str
    zalaczniki: list[ZalacznikWiadomosci] = field(default_factory=list)
    ukryta: bool = False


def czytaj_rozmowe(
    polaczenie: sqlite3.Connection, rozmowa_id: int, od: int = 0, ile: int = 20
) -> tuple[OpisRozmowy | None, list[WiadomoscArchiwum]]:
    """Także rozmowy i wiadomości ukryte – z zaznaczeniem; co pokazać, decyduje wywołujący.
    Ujemne `od` liczy od końca: -10 to dziesięć ostatnich wiadomości (numer pierwszej jest w jej `lp`)."""
    opis = polaczenie.execute(f"SELECT {_KOLUMNY_OPISU} FROM rozmowy r WHERE r.id = ?", (rozmowa_id,)).fetchone()
    if opis is None:
        return None, []
    if od < 0:
        od = max(0, opis["liczba_wiad"] + od)
    wiadomosci = []
    for w in polaczenie.execute(
        """SELECT w.id, w.lp, w.rola, w.ts, w.tekst, u.lp IS NOT NULL AS ukryta FROM wiadomosci w
           LEFT JOIN ukryte_wiadomosci u ON u.rozmowa_id = w.rozmowa_id AND u.lp = w.lp
           WHERE w.rozmowa_id = ? AND w.lp >= ? ORDER BY w.lp LIMIT ?""",
        (rozmowa_id, od, ile),
    ):
        zalaczniki = [
            ZalacznikWiadomosci(z["sha256"], z["nazwa"], z["sciezka"])
            for z in polaczenie.execute(
                """SELECT wz.sha256, wz.nazwa, z.sciezka FROM wiadomosc_zalacznik wz
                   JOIN zalaczniki z ON z.sha256 = wz.sha256 WHERE wz.wiadomosc_id = ?""",
                (w["id"],),
            )
        ]
        wiadomosci.append(WiadomoscArchiwum(w["lp"], w["rola"], w["ts"], w["tekst"], zalaczniki, bool(w["ukryta"])))
    return _opis(opis), wiadomosci


def _opis(wiersz: sqlite3.Row) -> OpisRozmowy:
    return OpisRozmowy(**{**dict(wiersz), "ukryta": bool(wiersz["ukryta"])})


@dataclass
class ZalacznikArchiwum:
    sha256: str
    nazwa: str | None
    sciezka: str  # względna, w magazynie
    tekst: str | None  # wyciągnięty z dokumentu


def znajdz_zalacznik(
    polaczenie: sqlite3.Connection, sha_lub_poczatek: str, ukryte: bool = True
) -> ZalacznikArchiwum | None:
    """Wystarczy początek skrótu SHA-256, jeśli jest jednoznaczny. Tylko cyfry szesnastkowe – „%” i „_” w LIKE
    pasowałyby do czegokolwiek. `ukryte=False` (agent): tylko załącznik, który jest przy choć jednej wiadomości
    w pamięci – ten z samych ukrytych rozmów i wiadomości nie istnieje, jak w wyszukiwaniu."""
    if not _SZESNASTKOWY.fullmatch(sha_lub_poczatek):
        return None
    widoczny = "" if ukryte else f"""
        AND EXISTS (SELECT 1 FROM wiadomosc_zalacznik wz
                    JOIN wiadomosci w ON w.id = wz.wiadomosc_id JOIN rozmowy r ON r.id = w.rozmowa_id
                    WHERE wz.sha256 = zalaczniki.sha256 AND r.ukryta = 0{_WIDOCZNA_WIADOMOSC})"""
    wiersze = polaczenie.execute(
        f"""SELECT sha256, pierwsza_nazwa AS nazwa, sciezka, tekst FROM zalaczniki
            WHERE sha256 LIKE ?{widoczny} LIMIT 2""",
        (sha_lub_poczatek.lower() + "%",),
    ).fetchall()
    return ZalacznikArchiwum(**dict(wiersze[0])) if len(wiersze) == 1 else None


def statystyki(polaczenie: sqlite3.Connection) -> dict[str, object]:
    jeden = lambda sql: polaczenie.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "rozmowy": jeden("SELECT count(*) FROM rozmowy"),
        "ukryte": jeden("SELECT count(*) FROM rozmowy WHERE ukryta = 1"),
        "wiadomosci": jeden("SELECT count(*) FROM wiadomosci"),
        "zalaczniki": jeden("SELECT count(*) FROM zalaczniki"),
        "zalaczniki_mb": round((jeden("SELECT coalesce(sum(rozmiar), 0) FROM zalaczniki")) / 1024 / 1024, 1),
        "dokumenty": jeden("SELECT count(*) FROM zalaczniki WHERE tekst IS NOT NULL"),
        "najstarsza": jeden("SELECT min(pierwsza_ts) FROM rozmowy"),
        "najnowsza": jeden("SELECT max(ostatnia_ts) FROM rozmowy"),
        "zrodla": {w[0]: w[1] for w in polaczenie.execute("SELECT zrodlo, count(*) FROM rozmowy GROUP BY zrodlo")},
    }
