import base64
import gzip
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from skerimai.adaptery.opencode import OpenCode
from skerimai.archiwizator import archiwizuj
from skerimai.model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK
from skerimai.zapytania import Filtry, lista_rozmow, szukaj

from .conftest import PNG

SESJA, BEZ_TYTULU, SUBAGENT, PUSTA = "ses_glowna", "ses_bez_tytulu", "ses_subagent", "ses_pusta"
START = 1794156361000


def pytanie(tekst, ms=0, pliki=()):
    return "user", {"time": {"created": START + ms}, "text": tekst, "files": list(pliki), "agents": []}


def odpowiedz(*tresc, ms=0):
    return "assistant", {"time": {"created": START + ms}, "content": list(tresc), "finish": "stop"}


def tekst(t):
    return {"type": "text", "text": t}


NARZEDZIE = {"type": "tool", "id": "c1", "name": "read", "state": {"status": "completed", "input": {"path": "tajne.txt"}}}
MYSLENIE = {"type": "reasoning", "text": "myślę o nakładce"}
OBRAZ = {"data": base64.b64encode(PNG).decode(), "mime": "image/png", "source": {"type": "inline"}, "name": "clipboard"}


def zapisz_rozmowe(baza: sqlite3.Connection, id_, wiersze, tytul="", katalog="C:/Projekty2/Kalkulator", rodzic=None,
                   zmiana=START):
    baza.execute("INSERT OR REPLACE INTO session_v2 (id, parent_id, directory, title, time_created, time_updated) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (id_, rodzic, katalog, tytul, START, zmiana))
    baza.execute("DELETE FROM session_message WHERE session_id = ?", (id_,))
    baza.executemany("INSERT INTO session_message (session_id, type, seq, time_created, time_updated, data) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     [(id_, typ, seq, START, zmiana, json.dumps(dane, ensure_ascii=False))
                      for seq, (typ, dane) in enumerate(wiersze)])


@pytest.fixture
def opencode(tmp_path: Path) -> Path:
    katalog = tmp_path / "opencode"
    katalog.mkdir()
    with closing(sqlite3.connect(katalog / "opencode.db")) as baza, baza:
        baza.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT, "
                     "time_created INTEGER, time_updated INTEGER)")
        baza.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, time_created INTEGER, "
                     "time_updated INTEGER, data TEXT)")
        zapisz_rozmowe(baza, SESJA, [
            pytanie("Nakładka rankingu bohatera", pliki=[OBRAZ]),
            ("system", {"text": "The available tools have changed."}),
            odpowiedz(MYSLENIE, NARZEDZIE, ms=1000),
            odpowiedz(tekst("Sprawdzam plik."), NARZEDZIE, ms=2000),
            ("synthetic", {"text": "<system-reminder>wstawka</system-reminder>"}),
            odpowiedz(tekst("Nakładka gotowa."), ms=3000),
            ("idle", {"outcome": "succeeded"}),
            pytanie("Dzięki", ms=4000),
        ], tytul="Nakładka Broken Ranks")
        zapisz_rozmowe(baza, BEZ_TYTULU, [pytanie("Wysyłka meldunków"), odpowiedz(tekst("Zrobione."))],
                       tytul="New session - 2026-11-02T20:20:44.669Z", katalog="C:/Projekty/budzik")
        zapisz_rozmowe(baza, SUBAGENT, [pytanie("Zadanie subagenta"), odpowiedz(tekst("Wynik"))], rodzic=SESJA)
        zapisz_rozmowe(baza, PUSTA, [("system", {"text": "tylko technika"})])
    return katalog


def test_rozmowy_bez_subagentow_i_pustych(opencode):
    assert set(OpenCode(opencode).znajdz_sesje()) == {SESJA, BEZ_TYTULU}


def test_pomija_sondy_innych_programow(opencode, monkeypatch):
    """Herdr sprawdza modele rozmową „Reply with exactly: OK” w swoich katalogach – to nie rozmowy użytkownika."""
    with closing(sqlite3.connect(opencode / "opencode.db")) as baza, baza:
        zapisz_rozmowe(baza, "ses_sonda", [pytanie("Reply with exactly: OK"), odpowiedz(tekst("OK"))],
                       katalog="C:/Users/ja/.local/state/herdr-oc-probe")
        zapisz_rozmowe(baza, "ses_modele", [pytanie("Reply with exactly: OK"), odpowiedz(tekst("OK"))],
                       katalog=r"C:\Users\ja\.config\herdr-app\mods\OC-models")

    assert set(OpenCode(opencode).znajdz_sesje()) == {SESJA, BEZ_TYTULU}

    monkeypatch.setenv("SKERIMAI_OPENCODE_POMIN", "budzik, herdr-oc-probe")
    assert set(OpenCode(opencode).znajdz_sesje()) == {SESJA, "ses_modele"}

    monkeypatch.setenv("SKERIMAI_OPENCODE_POMIN", "")
    assert set(OpenCode(opencode).znajdz_sesje()) == {SESJA, BEZ_TYTULU, "ses_sonda", "ses_modele"}


def test_brak_opencode(tmp_path):
    assert OpenCode(tmp_path / "nie-ma").znajdz_sesje() == {}


def test_zmieniony_format_bazy_nie_przerywa_archiwizacji(tmp_path):
    with closing(sqlite3.connect(tmp_path / "opencode.db")) as baza, baza:
        baza.execute("CREATE TABLE inna (id TEXT)")

    assert OpenCode(tmp_path).znajdz_sesje() == {}


def test_wiadomosci_i_tury_asystenta(opencode):
    adapter = OpenCode(opencode)
    sesje = adapter.znajdz_sesje()

    r = adapter.wczytaj(SESJA, sesje[SESJA])

    assert r.tytul == "Nakładka Broken Ranks"
    assert (r.projekt, r.cwd) == ("Kalkulator", "C:/Projekty2/Kalkulator")
    assert [w.rola for w in r.wiadomosci] == [ROLA_UZYTKOWNIK, ROLA_ASYSTENT, ROLA_UZYTKOWNIK]
    assert r.wiadomosci[1].tekst == "Sprawdzam plik.\n\nNakładka gotowa."
    assert r.wiadomosci[1].ts.startswith("2026-11-")
    assert r.pierwsza_ts < r.ostatnia_ts
    assert not any("myślę" in w.tekst or "tajne.txt" in w.tekst or "wstawka" in w.tekst for w in r.wiadomosci)
    z = r.wiadomosci[0].zalaczniki[0]
    assert (z.nazwa, z.mime, z.dane) == ("obraz-1", "image/png", PNG)


def test_tytul_domyslny_zastapiony_pierwsza_wiadomoscia(opencode):
    adapter = OpenCode(opencode)
    sesje = adapter.znajdz_sesje()

    assert adapter.wczytaj(BEZ_TYTULU, sesje[BEZ_TYTULU]).tytul == "Wysyłka meldunków"


def test_archiwizacja(opencode, sciezki, polaczenie):
    raport = archiwizuj(polaczenie, sciezki, [OpenCode(opencode)])

    assert (raport.zaktualizowane, raport.nowe_zalaczniki) == (2, 1)
    assert {r.projekt for r in lista_rozmow(polaczenie)} == {"Kalkulator", "budzik"}
    trafienia = szukaj(polaczenie, "nakładka", Filtry(zrodlo="opencode"))
    assert trafienia and trafienia[0].tytul == "Nakładka Broken Ranks"
    surowa = gzip.decompress((sciezki.surowe / "opencode" / f"{SESJA}.jsonl.gz").read_bytes()).decode("utf-8")
    assert "tajne.txt" in surowa and "myślę o nakładce" in surowa  # pełna kopia, także narzędzia

    assert archiwizuj(polaczenie, sciezki, [OpenCode(opencode)]).zaktualizowane == 0


def test_archiwizuje_tylko_zmieniona_rozmowe(opencode, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [OpenCode(opencode)])
    with closing(sqlite3.connect(opencode / "opencode.db")) as baza, baza:
        zapisz_rozmowe(baza, BEZ_TYTULU, [pytanie("Wysyłka meldunków"), odpowiedz(tekst("Zrobione.")),
                                          pytanie("Dodaj stopkę"), odpowiedz(tekst("Stopka dodana."))],
                       katalog="C:/Projekty/budzik", zmiana=START + 60_000)

    raport = archiwizuj(polaczenie, sciezki, [OpenCode(opencode)])

    assert raport.zaktualizowane == 1
    assert szukaj(polaczenie, "stopka")
