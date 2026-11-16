"""Schemat i połączenie z bazą SQLite (FTS5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .tekst import bez_ogonkow, do_indeksu

# Wersja zawartości indeksów FTS5; przy zmianie sposobu indeksowania podnieść i dopisać przeliczenie
# w _przelicz_indeks. 1: „ł” zapisane jako „l”.
WERSJA_INDEKSU = 1

SCHEMAT = """
CREATE TABLE IF NOT EXISTS rozmowy (
    id             INTEGER PRIMARY KEY,
    zrodlo         TEXT NOT NULL,
    sesja          TEXT NOT NULL,
    projekt        TEXT,
    cwd            TEXT,
    galaz          TEXT,
    tytul          TEXT,
    pierwsza_ts    TEXT,
    ostatnia_ts    TEXT,
    liczba_wiad    INTEGER NOT NULL DEFAULT 0,
    -- edycja w oknie „pamięć” (edycja.py); archiwizacja tych kolumn nie nadpisuje
    ukryta         INTEGER NOT NULL DEFAULT 0,  -- 1: poza wyszukiwaniem i listą rozmów
    tytul_wlasny   TEXT,                        -- NULL: tytuł ze źródła
    projekt_wlasny TEXT,                        -- NULL: projekt ze źródła
    UNIQUE (zrodlo, sesja)
);

CREATE TABLE IF NOT EXISTS wiadomosci (
    id          INTEGER PRIMARY KEY,
    rozmowa_id  INTEGER NOT NULL REFERENCES rozmowy(id) ON DELETE CASCADE,
    lp          INTEGER NOT NULL,
    rola        TEXT NOT NULL,
    ts          TEXT,
    tekst       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wiadomosci_rozmowa ON wiadomosci (rozmowa_id, lp);

CREATE TABLE IF NOT EXISTS zalaczniki (
    sha256          TEXT PRIMARY KEY,
    ext             TEXT NOT NULL,
    rozmiar         INTEGER NOT NULL,
    mime            TEXT,
    sciezka         TEXT NOT NULL,
    pierwsza_nazwa  TEXT,
    dodano          TEXT NOT NULL,
    tekst           TEXT,     -- tekst wyciągnięty z dokumentu (PDF, DOCX, TXT)
    tekst_wersja    INTEGER   -- NULL: jeszcze nie przejrzany; inaczej dokumenty.WERSJA z chwili przejrzenia
);

CREATE TABLE IF NOT EXISTS wiadomosc_zalacznik (
    wiadomosc_id  INTEGER NOT NULL REFERENCES wiadomosci(id) ON DELETE CASCADE,
    sha256        TEXT NOT NULL REFERENCES zalaczniki(sha256),
    nazwa         TEXT,
    PRIMARY KEY (wiadomosc_id, sha256)
);
CREATE INDEX IF NOT EXISTS wiadomosc_zalacznik_sha ON wiadomosc_zalacznik (sha256);

-- rowid = wiadomosci.id; kolumna rdzenie: tekst po polskim stemmingu, tekst: oryginał
CREATE VIRTUAL TABLE IF NOT EXISTS fts_wiadomosci USING fts5 (
    rdzenie, tekst, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_slownik USING fts5vocab (fts_wiadomosci, 'col');

-- tekst dokumentów: jeden wiersz na plik, kolumny jak w fts_wiadomosci
CREATE VIRTUAL TABLE IF NOT EXISTS fts_zalaczniki USING fts5 (
    sha256 UNINDEXED, rdzenie, tekst, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_slownik_zalacznikow USING fts5vocab (fts_zalaczniki, 'col');

-- wiadomości ukryte w oknie „pamięć”; archiwizacja zapisuje wiadomości rozmowy od nowa i przenosi numery
-- za treścią (archiwizator.przeniesione_ukrycia)
CREATE TABLE IF NOT EXISTS ukryte_wiadomosci (
    rozmowa_id  INTEGER NOT NULL REFERENCES rozmowy(id) ON DELETE CASCADE,
    lp          INTEGER NOT NULL,
    PRIMARY KEY (rozmowa_id, lp)
) WITHOUT ROWID;

-- rozmowy usunięte na stałe – archiwizator nie wczytuje ich ponownie ze źródła; sama sesja tylko jako skrót
-- SHA-256 (archiwizator.skrot_sesji), bo w KreionAI nazwą sesji jest początek pierwszej wiadomości
CREATE TABLE IF NOT EXISTS usuniete_rozmowy (
    skrot     TEXT PRIMARY KEY,
    usunieto  TEXT NOT NULL
) WITHOUT ROWID;

-- wymazane sekrety, zaszyfrowane przez Windows (DPAPI) – archiwizator wymazuje je też z nowych wczytań
CREATE TABLE IF NOT EXISTS sekrety (
    id      INTEGER PRIMARY KEY,
    szyfr   BLOB NOT NULL,
    dodano  TEXT NOT NULL
);

-- pamięć podręczna stemmera: jego wczytanie trwa kilka sekund
CREATE TABLE IF NOT EXISTS rdzenie (
    slowo  TEXT PRIMARY KEY,
    rdzen  TEXT NOT NULL
) WITHOUT ROWID;

-- postęp archiwizatora: pliki źródłowe już wczytane
CREATE TABLE IF NOT EXISTS stan_zrodel (
    sciezka   TEXT PRIMARY KEY,
    zrodlo    TEXT NOT NULL,
    sesja     TEXT NOT NULL,
    rozmiar   INTEGER NOT NULL,
    mtime_ns  INTEGER NOT NULL
);
"""

# Kolumny dodane po założeniu pierwszych baz – starsze bazy dostają je przy otwarciu.
_DODANE_KOLUMNY = [
    ("zalaczniki", "tekst", "TEXT"),
    ("zalaczniki", "tekst_wersja", "INTEGER"),
    ("rozmowy", "ukryta", "INTEGER NOT NULL DEFAULT 0"),
    ("rozmowy", "tytul_wlasny", "TEXT"),
    ("rozmowy", "projekt_wlasny", "TEXT"),
]


def polacz(sciezka: Path) -> sqlite3.Connection:
    """Otwiera bazę (tworzy ją, jeśli trzeba) w trybie WAL – hook i serwer MCP mogą pisać równolegle."""
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    polaczenie = sqlite3.connect(sciezka)
    polaczenie.row_factory = sqlite3.Row
    polaczenie.execute("PRAGMA journal_mode = WAL")
    polaczenie.execute("PRAGMA foreign_keys = ON")
    polaczenie.execute("PRAGMA busy_timeout = 10000")
    # Filtr projektu: „pszczola” znajduje „Pszczoła”.
    polaczenie.create_function("bez_ogonkow", 1, lambda t: None if t is None else bez_ogonkow(t), deterministic=True)
    polaczenie.create_function("do_indeksu", 1, lambda t: None if t is None else do_indeksu(t), deterministic=True)
    polaczenie.executescript(SCHEMAT)
    _uzupelnij_kolumny(polaczenie)
    _przelicz_indeks(polaczenie)
    return polaczenie


def _wersja(polaczenie: sqlite3.Connection) -> int:
    return polaczenie.execute("PRAGMA user_version").fetchone()[0]


def _przelicz_indeks(polaczenie: sqlite3.Connection) -> None:
    """Indeksy FTS5 zapisane przed WERSJA_INDEKSU – jednorazowo przy otwarciu (wersja w PRAGMA user_version).
    Pod BEGIN IMMEDIATE i z ponownym sprawdzeniem wersji: dwa procesy otwierające bazę naraz nie przeliczą jej
    dwa razy."""
    if _wersja(polaczenie) >= WERSJA_INDEKSU:
        return
    polaczenie.execute("BEGIN IMMEDIATE")
    try:
        if _wersja(polaczenie) < 1:  # „ł” → „l” (tekst.do_indeksu)
            for tabela in ("fts_wiadomosci", "fts_zalaczniki"):
                polaczenie.execute(
                    f"""UPDATE {tabela} SET rdzenie = do_indeksu(rdzenie), tekst = do_indeksu(tekst)
                        WHERE instr(rdzenie, 'ł') OR instr(tekst, 'ł') OR instr(tekst, 'Ł')"""
                )
        polaczenie.execute(f"PRAGMA user_version = {WERSJA_INDEKSU}")
        polaczenie.commit()
    except BaseException:
        polaczenie.rollback()
        raise


def _uzupelnij_kolumny(polaczenie: sqlite3.Connection) -> None:
    for tabela, kolumna, typ in _DODANE_KOLUMNY:
        if any(w["name"] == kolumna for w in polaczenie.execute(f"PRAGMA table_info({tabela})")):
            continue
        try:
            polaczenie.execute(f"ALTER TABLE {tabela} ADD COLUMN {kolumna} {typ}")
        except sqlite3.OperationalError as blad:  # inny proces dodał ją przed chwilą
            if "duplicate column" not in str(blad):
                raise
