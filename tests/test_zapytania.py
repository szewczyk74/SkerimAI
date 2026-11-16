from datetime import datetime, timedelta, timezone

import pytest

from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.archiwizator import archiwizuj
from skerimai.baza import WERSJA_INDEKSU, polacz
from skerimai.edycja import ustaw_projekt
from skerimai.zapytania import Filtry, czytaj_rozmowe, lista_rozmow, statystyki, szukaj, znajdz_zalacznik

from .conftest import asystent, uzytkownik, zapisz_jsonl


@pytest.fixture
def archiwum(projekty, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    return polaczenie


def test_znajduje_slowo(archiwum):
    trafienia = szukaj(archiwum, "NodeJsTools")

    assert trafienia
    assert trafienia[0].tytul == "Problem z NodeJsTools"
    assert "NodeJsTools" in trafienia[0].fragment


def test_znajduje_inna_odmiane(archiwum):
    # w rozmowie jest „pocztę”, szukamy „poczty”
    trafienia = szukaj(archiwum, "poczty")

    assert [t.lp for t in trafienia] == [2]


def test_znajduje_mimo_literowki(archiwum):
    trafienia = szukaj(archiwum, "Outloock")

    assert [t.lp for t in trafienia] == [2]
    assert "Outlook" in trafienia[0].fragment


def test_slownik_literowek_czytany_raz_i_odswiezany_po_nowej_rozmowie(archiwum, sciezki, tmp_path):
    """Pełny przegląd słownika FTS5 tylko raz – i ponownie dopiero, gdy w archiwum coś przybędzie."""
    przeglady = []
    archiwum.set_trace_callback(lambda sql: przeglady.append(sql) if "SELECT DISTINCT term" in sql else None)

    assert [t.lp for t in szukaj(archiwum, "Outloock")] == [2]
    pierwsze = len(przeglady)
    assert pierwsze > 0
    assert [t.lp for t in szukaj(archiwum, "Outloock")] == [2]
    assert szukaj(archiwum, "instalcja")
    assert len(przeglady) == pierwsze

    assert not szukaj(archiwum, "harmonogrm")
    katalog = tmp_path / "nowe"
    zapisz_jsonl(katalog / "C--Projekty-plan" / "sesja-n.jsonl", [
        uzytkownik("Harmonogram dostaw na listopad", "2026-10-05T10:00:00.000Z"),
    ])
    archiwizuj(archiwum, sciezki, [ClaudeCode(katalog)])

    assert [t.tytul for t in szukaj(archiwum, "harmonogrm")] == ["Harmonogram dostaw na listopad"]
    assert len(przeglady) > pierwsze


def test_fragment_wokol_trafionego_slowa():
    from skerimai.zapytania import _fragment

    tekst = "wstęp " * 100 + "tutaj jest kartridż KreionAI" + " koniec" * 100

    fragment = _fragment(tekst, ["kartrydż", "kartridz"], ["kartrydż"])

    assert "kartridż" in fragment
    assert fragment.startswith("…") and fragment.endswith("…")


def test_znajduje_po_poczatku_slowa(archiwum):
    assert szukaj(archiwum, "instal")


def test_wszystkie_slowa_a_potem_dowolne(archiwum):
    wszystkie = szukaj(archiwum, "grzałki cache")
    dowolne = szukaj(archiwum, "grzałki nieistniejącesłowoxyz")

    assert {t.projekt for t in wszystkie} == {"budzik"}
    assert {t.projekt for t in dowolne} == {"budzik"}


def test_filtr_projektu(archiwum):
    assert szukaj(archiwum, "instalacji", Filtry(projekt="budzik")) == []
    assert szukaj(archiwum, "instalacji", Filtry(projekt="sklep"))


def test_filtr_dat(archiwum):
    assert szukaj(archiwum, "grzałka", Filtry(od="2026-09-21")) == []
    assert szukaj(archiwum, "grzałka", Filtry(od="2026-09-20", do="2026-09-20"))


def _utc(lokalny: datetime) -> str:
    return lokalny.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def archiwum_wokol_polnocy(tmp_path, sciezki, polaczenie):
    """Rozmowy tuż przed i tuż po lokalnej północy 25.10.2026 – w tym dniu kończy się czas letni."""
    katalog = tmp_path / "projekty_polnoc"
    for slowo, lokalny in (
        ("wczorajsza", datetime(2026, 10, 24, 23, 30)),
        ("poranna", datetime(2026, 10, 25, 0, 30)),
        ("wieczorna", datetime(2026, 10, 25, 23, 30)),
        ("jutrzejsza", datetime(2026, 10, 26, 0, 30)),
    ):
        zapisz_jsonl(katalog / "C--Projekty-zegar" / f"{slowo}.jsonl", [
            uzytkownik(f"Rozmowa {slowo}", _utc(lokalny)),
            asystent([{"type": "text", "text": "Dobrze."}], _utc(lokalny + timedelta(minutes=1))),
        ])
    archiwizuj(polaczenie, sciezki, [ClaudeCode(katalog)])
    return polaczenie


def test_filtr_dat_liczy_dzien_lokalny(archiwum_wokol_polnocy):
    dzien = Filtry(od="2026-10-25", do="2026-10-25")

    assert sorted(r.tytul for r in lista_rozmow(archiwum_wokol_polnocy, dzien)) == [
        "Rozmowa poranna", "Rozmowa wieczorna"]
    for slowo, w_dniu in (("wczorajsza", False), ("poranna", True), ("wieczorna", True), ("jutrzejsza", False)):
        assert bool(szukaj(archiwum_wokol_polnocy, slowo, dzien)) == w_dniu, slowo


def test_nie_ujawnia_danych_technicznych(archiwum):
    for slowo in ("tajne", "myślę", "wynik narzędzia", "subagenta"):
        assert szukaj(archiwum, slowo) == [], slowo


def test_puste_zapytanie(archiwum):
    assert szukaj(archiwum, "  ?! ") == []


def test_lista_rozmow_od_najnowszej(archiwum):
    rozmowy = lista_rozmow(archiwum)

    assert [r.projekt for r in rozmowy] == ["sklep.pl", "budzik"]
    assert lista_rozmow(archiwum, Filtry(projekt="budzik"))[0].liczba_wiad == 2


def test_czytanie_rozmowy_stronami(archiwum, sciezki):
    rozmowa_id = lista_rozmow(archiwum, Filtry(projekt="sklep"))[0].id

    opis, wiadomosci = czytaj_rozmowe(archiwum, rozmowa_id, od=2, ile=1)

    assert opis.liczba_wiad == 4
    assert [w.lp for w in wiadomosci] == [2]
    assert len(wiadomosci[0].zalaczniki) == 2
    sha = wiadomosci[0].zalaczniki[0].sha256
    assert (sciezki.zalaczniki / znajdz_zalacznik(archiwum, sha[:10]).sciezka).is_file()


def test_statystyki(archiwum):
    stat = statystyki(archiwum)

    assert stat["rozmowy"] == 2
    assert stat["wiadomosci"] == 6
    assert stat["zalaczniki"] == 2
    assert stat["zrodla"] == {"claude_code": 2}


def test_filtr_projektu_bez_polskich_znakow(archiwum):
    ustaw_projekt(archiwum, [lista_rozmow(archiwum, Filtry(projekt="budzik"))[0].id], "Pszczoła")

    for filtr in ("pszczola", "PSZCZOŁA", "Pszczoł"):
        assert [r.projekt for r in lista_rozmow(archiwum, Filtry(projekt=filtr))] == ["Pszczoła"], filtr
    assert szukaj(archiwum, "grzałka", Filtry(projekt="pszczola"))


@pytest.fixture
def archiwum_z_l(tmp_path, sciezki, polaczenie):
    """Rozmowa ze słowami z „ł” – tokenizer FTS5 nie zdejmuje z niej kreski."""
    katalog = tmp_path / "projekty_l"
    zapisz_jsonl(katalog / "C--Projekty-mapa" / "sesja-l.jsonl", [
        uzytkownik("Dzielnica Białołęka, potem dostawa do Łodzi.", "2026-10-01T10:00:00.000Z"),
        asystent([{"type": "text", "text": "Trasa przez Łódź jest krótsza."}], "2026-10-01T10:00:05.000Z"),
    ])
    archiwizuj(polaczenie, sciezki, [ClaudeCode(katalog)])
    return polaczenie


def test_slowa_z_l_kreskowanym_bez_polskich_znakow(archiwum_z_l):
    for zapytanie in ("bialoleka", "Białołęka", "lodz", "Łódź"):
        assert szukaj(archiwum_z_l, zapytanie), zapytanie


def test_fragment_z_l_kreskowanym_w_oryginale(archiwum_z_l):
    trafienia = szukaj(archiwum_z_l, "bialoleka")

    assert "Białołęka" in trafienia[0].fragment


def test_stary_indeks_z_l_kreskowanym_przeliczony_przy_otwarciu(archiwum_z_l, sciezki):
    # indeks sprzed WERSJA_INDEKSU 1: kolumna tekst z oryginalnym „ł”
    archiwum_z_l.execute("UPDATE fts_wiadomosci SET tekst = (SELECT tekst FROM wiadomosci w WHERE w.id = fts_wiadomosci.rowid)")
    archiwum_z_l.execute("PRAGMA user_version = 0")
    archiwum_z_l.commit()
    assert not szukaj(archiwum_z_l, "lodz")

    ponownie = polacz(sciezki.baza)
    try:
        assert ponownie.execute("PRAGMA user_version").fetchone()[0] == WERSJA_INDEKSU
        assert szukaj(ponownie, "lodz")
    finally:
        ponownie.close()


def test_zalacznik_tylko_po_poczatku_skrotu_bez_symboli_wieloznacznych(archiwum):
    sha = archiwum.execute("SELECT sha256 FROM zalaczniki ORDER BY sha256 LIMIT 1").fetchone()[0]

    assert znajdz_zalacznik(archiwum, sha[:10].upper()).sha256 == sha
    assert znajdz_zalacznik(archiwum, "_" + sha[1:10]) is None  # „_” w LIKE pasuje do dowolnego znaku
    assert znajdz_zalacznik(archiwum, sha[:4] + "%") is None
    assert znajdz_zalacznik(archiwum, "") is None


def test_szukanie_nowego_slowa_gdy_archiwizator_trzyma_zapis(archiwum, sciezki):
    """Zapis rdzeni to tylko pamięć podręczna – zajęta baza nie może zepsuć wyszukiwania (odczyt w WAL działa)."""
    import sqlite3

    pisarz = sqlite3.connect(sciezki.baza)
    pisarz.execute("BEGIN IMMEDIATE")
    pisarz.execute("INSERT INTO rdzenie (slowo, rdzen) VALUES ('blokada', 'blokada')")
    archiwum.execute("PRAGMA busy_timeout = 100")
    try:
        trafienia = szukaj(archiwum, "NodeJsTools wichajstrem")
    finally:
        pisarz.rollback()
        pisarz.close()

    assert trafienia
    assert archiwum.execute("SELECT count(*) FROM rdzenie WHERE slowo = 'wichajstrem'").fetchone()[0] == 0


def test_wiersz_polecen_odrzuca_bledna_date(sciezki, monkeypatch, capsys):
    from skerimai import cli

    monkeypatch.setenv("SKERIMAI_DIR", str(sciezki.katalog))
    with pytest.raises(SystemExit) as wyjscie:
        cli.main(["rozmowy", "--od", "1.10.2026"])

    assert wyjscie.value.code == 2
    assert "RRRR-MM-DD" in capsys.readouterr().err
