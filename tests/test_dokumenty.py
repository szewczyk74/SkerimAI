import sqlite3

import pytest

from skerimai import dokumenty
from skerimai.adaptery.kreion_kartridz import KreionKartridz
from skerimai.archiwizator import archiwizuj
from skerimai.baza import polacz
from skerimai.dokumenty import wyciagnij_tekst
from skerimai.zapytania import Filtry, statystyki, szukaj, znajdz_zalacznik

from .conftest import PNG, docx, pdf
from .test_serwer_mcp import tekst, wywolaj


def test_pdf(tmp_path):
    plik = tmp_path / "a.pdf"
    plik.write_bytes(pdf("Przekaznik K3", "Bezpiecznik F7"))

    assert wyciagnij_tekst(plik) == "Przekaznik K3\nBezpiecznik F7"


def test_skan_bez_warstwy_tekstu(tmp_path):
    plik = tmp_path / "skan.pdf"
    plik.write_bytes(pdf())

    assert wyciagnij_tekst(plik) is None


def test_docx_akapity_tabulatory_i_podzialy_wierszy(tmp_path):
    plik = tmp_path / "a.docx"
    plik.write_bytes(docx(xml_akapitow=(
        "<w:p><w:r><w:t>Zażółć</w:t><w:tab/><w:t>gęślą</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>jaźń</w:t><w:br/><w:t>koniec</w:t></w:r></w:p>"
    )))

    assert wyciagnij_tekst(plik) == "Zażółć\tgęślą\njaźń\nkoniec"


@pytest.mark.parametrize("kodowanie", ["utf-8", "utf-8-sig", "cp1250", "utf-16"])
def test_plik_tekstowy_w_roznych_kodowaniach(tmp_path, kodowanie):
    plik = tmp_path / "notatka.txt"
    plik.write_bytes("Zażółć gęślą jaźń".encode(kodowanie))

    assert wyciagnij_tekst(plik) == "Zażółć gęślą jaźń"


def test_obraz_i_nieznany_format_bez_tekstu(tmp_path):
    (tmp_path / "a.png").write_bytes(PNG)
    (tmp_path / "a.bin").write_bytes(b"\x00\x01")

    assert wyciagnij_tekst(tmp_path / "a.png") is None
    assert wyciagnij_tekst(tmp_path / "a.bin") is None


@pytest.fixture
def archiwum(kartridz_z_dokumentami, sciezki, polaczenie):
    raport = archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz_z_dokumentami)])
    return polaczenie, raport


def test_archiwizacja_wyciaga_tekst_raz_na_plik(archiwum):
    polaczenie, raport = archiwum
    teksty = {
        w["pierwsza_nazwa"]: (w["tekst"], w["tekst_wersja"])
        for w in polaczenie.execute("SELECT pierwsza_nazwa, tekst, tekst_wersja FROM zalaczniki")
    }

    assert raport.dokumenty == 2
    assert "Bezpiecznik F7" in teksty["schemat.pdf"][0]
    assert "Kowalski" in teksty["grafik.docx"][0]
    assert teksty["skan.pdf"][0] is None
    assert teksty["zepsuty.pdf"][0] is None  # uszkodzony plik nie zatrzymał archiwizacji
    assert {wersja for _, wersja in teksty.values()} == {dokumenty.WERSJA}
    assert polaczenie.execute("SELECT count(*) FROM fts_zalaczniki").fetchone()[0] == 2
    assert statystyki(polaczenie)["dokumenty"] == 2


def test_uszkodzony_dokument_trafia_do_dziennika(kartridz_z_dokumentami, sciezki, polaczenie, caplog):
    archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz_z_dokumentami)])

    ostrzezenia = [r for r in caplog.records if r.name == "skerimai" and r.levelname == "WARNING"]
    assert len(ostrzezenia) == 1
    assert "Nie udało się wyciągnąć tekstu" in ostrzezenia[0].getMessage()


def test_nie_przeglada_drugi_raz(archiwum, kartridz_z_dokumentami, sciezki):
    polaczenie, _ = archiwum

    raport = archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz_z_dokumentami)], pelne=True)

    assert raport.dokumenty == 0
    assert polaczenie.execute("SELECT count(*) FROM fts_zalaczniki").fetchone()[0] == 2


def test_uzupelnia_zalaczniki_zapisane_przed_wyciaganiem_tekstu(archiwum, kartridz_z_dokumentami, sciezki):
    polaczenie, _ = archiwum
    with polaczenie:
        polaczenie.execute("UPDATE zalaczniki SET tekst = NULL, tekst_wersja = NULL")
        polaczenie.execute("DELETE FROM fts_zalaczniki")

    raport = archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz_z_dokumentami)])

    assert raport.zaktualizowane == 0  # rozmowy bez zmian, a dokumenty i tak przejrzane
    assert raport.dokumenty == 2
    assert polaczenie.execute("SELECT count(*) FROM fts_zalaczniki").fetchone()[0] == 2


def test_szukanie_w_tekscie_dokumentu(archiwum):
    polaczenie, _ = archiwum

    trafienia = szukaj(polaczenie, "bezpiecznik F7")

    # schemat dołączony do dwóch wiadomości – jedno trafienie, przy pierwszej z nich
    assert len(trafienia) == 1
    t = trafienia[0]
    assert (t.zalacznik, t.lp, t.projekt) == ("schemat.pdf", 0, "Żuraw")
    assert "Bezpiecznik F7" in t.fragment
    assert znajdz_zalacznik(polaczenie, t.sha256).nazwa == "schemat.pdf"


def test_szukanie_w_dokumencie_odmiana_i_literowka(archiwum):
    polaczenie, _ = archiwum

    assert [t.zalacznik for t in szukaj(polaczenie, "dyżur")] == ["grafik.docx"]
    assert [t.zalacznik for t in szukaj(polaczenie, "bezpiecznk")] == ["schemat.pdf"]  # słowo tylko w dokumencie


def test_wiadomosci_i_dokumenty_w_jednych_wynikach(archiwum):
    polaczenie, _ = archiwum

    trafienia = szukaj(polaczenie, "dokumentację kowalski")  # pierwsze słowo w wiadomości, drugie w dokumencie

    assert {t.zalacznik for t in trafienia} == {None, "grafik.docx"}


def test_dokumenty_maja_zarezerwowane_miejsca_w_wynikach():
    from skerimai.zapytania import _polacz_wyniki

    wiadomosci = [{"wynik": -10.0 + i, "id": f"w{i}"} for i in range(10)]
    dokumenty = [{"wynik": -1.0 + i / 10, "id": f"d{i}"} for i in range(5)]  # słabszy BM25 niż każda wiadomość

    wyniki = [w["id"] for w in _polacz_wyniki(wiadomosci, dokumenty, 10)]

    assert wyniki == ["w0", "w1", "w2", "w3", "w4", "w5", "w6", "d0", "d1", "d2"]
    assert [w["id"] for w in _polacz_wyniki(wiadomosci, dokumenty, 2)] == ["w0", "w1"]


def test_filtry_dotycza_dokumentow(archiwum):
    polaczenie, _ = archiwum

    assert szukaj(polaczenie, "bezpiecznik", Filtry(projekt="inny")) == []
    assert szukaj(polaczenie, "bezpiecznik", Filtry(do="2026-08-01")) == []
    assert szukaj(polaczenie, "bezpiecznik", Filtry(zrodlo="claude_code")) == []
    assert szukaj(polaczenie, "bezpiecznik", Filtry(projekt="Żuraw"))


def test_stara_baza_dostaje_kolumny_tekstu(tmp_path):
    baza = tmp_path / "stara.db"
    with sqlite3.connect(baza) as stara:
        stara.execute(
            """CREATE TABLE zalaczniki (sha256 TEXT PRIMARY KEY, ext TEXT NOT NULL, rozmiar INTEGER NOT NULL,
               mime TEXT, sciezka TEXT NOT NULL, pierwsza_nazwa TEXT, dodano TEXT NOT NULL)"""
        )
        stara.execute("INSERT INTO zalaczniki VALUES ('ab', '.pdf', 1, NULL, 'ab.pdf', 'a.pdf', '2026-09-01')")
    stara.close()

    polaczenie = polacz(baza)
    try:
        wiersz = polaczenie.execute("SELECT tekst, tekst_wersja FROM zalaczniki").fetchone()
    finally:
        polaczenie.close()

    assert tuple(wiersz) == (None, None)


def test_serwer_mcp_szuka_w_dokumentach_i_oddaje_ich_tekst(kartridz_z_dokumentami, sciezki, monkeypatch):
    from skerimai import serwer_mcp

    monkeypatch.setattr(serwer_mcp, "_MAKS_ZNAKOW_WIADOMOSCI", 20)
    serwer = serwer_mcp.utworz_serwer(sciezki, [KreionKartridz(kartridz_z_dokumentami)])

    wynik = tekst(wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "przekaźnik K3"}))
    assert "w załączniku „schemat.pdf”" in wynik
    sha = wynik.split("(sha256 ")[1][:16]

    strona = tekst(wywolaj(serwer, "pobierz_zalacznik", {"sha256": sha}))
    assert "Tekst dokumentu „schemat.pdf” (znaki 0–20 z 53)" in strona
    assert "Przekaznik K3 zasila" in strona
    assert "[dalej: od=20]" in strona

    koniec = tekst(wywolaj(serwer, "pobierz_zalacznik", {"sha256": sha, "od": 40}))
    assert "F7 10A" in koniec
    assert "dalej" not in koniec

    za_koncem = tekst(wywolaj(serwer, "pobierz_zalacznik", {"sha256": sha, "od": 500}))
    assert "Koniec tekstu – dokument ma 53 znaków (od=500)" in za_koncem
    assert "znaki 500–53" not in za_koncem


def test_tekst_dokumentu_dla_kreion_ma_znacznik(kartridz_z_dokumentami, sciezki):
    from skerimai import serwer_mcp

    serwer = serwer_mcp.utworz_serwer(sciezki, [KreionKartridz(kartridz_z_dokumentami)], klient="kreion")
    wynik = tekst(wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "przekaźnik K3", "pomin_zrodla": []}))
    sha = wynik.split("(sha256 ")[1][:16]

    strona = tekst(wywolaj(serwer, "pobierz_zalacznik", {"sha256": sha}))

    assert f"[skerimai zalacznik={sha}]" in wynik
    assert f"[skerimai zalacznik={sha}]\nPrzekaznik K3 zasila" in strona
    assert strona.count("[/skerimai]") == 1
