import time

import pytest

from skerimai import okno_pamieci
from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.archiwizator import archiwizuj
from skerimai.edycja import UKRYTE, Wystapienia
from skerimai.okno_pamieci import UKRYTA_ZNAK, W_PAMIECI_ZNAK, OknoPamieci, opis_wystapien

tk = pytest.importorskip("tkinter")


@pytest.fixture
def okno(projekty, sciezki, polaczenie, monkeypatch):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    okno_pamieci.wskaz_biblioteki_tcl()
    try:
        korzen = tk.Tk()
    except tk.TclError as blad:
        pytest.skip(f"brak Tcl/Tk: {blad}")
    korzen.withdraw()
    monkeypatch.setattr(okno_pamieci.dialogi, "pytaj", lambda *a, **k: True)
    okno = OknoPamieci(korzen, sciezki)
    yield okno
    if okno.zajete:
        czekaj(okno)
    korzen.destroy()


def czekaj(okno: OknoPamieci) -> None:
    koniec = time.monotonic() + 30
    while okno.zajete and time.monotonic() < koniec:
        okno.okno.update()
        time.sleep(0.02)
    okno.okno.update()


def wiersze(tabela) -> list[tuple]:
    return [tuple(tabela.item(i, "values")) for i in tabela.get_children()]


def test_lista_rozmow_i_wiadomosci(okno):
    assert [w[4] for w in wiersze(okno.lista)] == ["Problem z NodeJsTools", "Grzałka utrzymuje cache budzika"]
    assert okno.licznik.get() == "2 rozmowy na liście, w tym 0 ukrytych"

    pierwsza = okno.lista.get_children()[0]
    okno.lista.selection_set(pierwsza)
    okno._wybrano_rozmowe()
    okno.lista_wiadomosci.selection_set("2")
    okno._wybrano_wiadomosc()

    assert len(okno.lista_wiadomosci.get_children()) == 4
    assert wiersze(okno.lista_wiadomosci)[2][4].endswith("📎")
    assert "Sprawdź moją pocztę w Outlook" in okno.tresc.get("1.0", "end")


def test_zalacznik_otwiera_sie_z_tresci_wiadomosci(okno, sciezki, monkeypatch):
    otwarte = []
    monkeypatch.setattr(okno_pamieci.os, "startfile", otwarte.append, raising=False)
    okno.lista.selection_set(okno.lista.get_children()[0])
    okno._wybrano_rozmowe()
    okno.lista_wiadomosci.selection_set("2")
    okno._wybrano_wiadomosc()

    assert len(okno.zalaczniki) == 2
    tresc = okno.tresc.get("1.0", "end")
    assert "kliknij" in tresc
    assert tresc.startswith("Ty   [2] · ")
    assert " B\n" in tresc or "KB\n" in tresc  # rozmiar przy kafelku załącznika
    okno.otworz_zalacznik(0)

    assert len(otwarte) == 1
    assert otwarte[0].is_file() and sciezki.zalaczniki in otwarte[0].parents

    okno.lista_wiadomosci.selection_set("1")
    okno._wybrano_wiadomosc()
    assert okno.zalaczniki == []


def test_przelaczanie_zapisuje_od_razu(okno, polaczenie):
    rozmowa = int(okno.lista.get_children()[0])

    okno.przelacz_rozmowy([rozmowa])

    assert wiersze(okno.lista)[0][0] == UKRYTA_ZNAK
    assert polaczenie.execute("SELECT ukryta FROM rozmowy WHERE id = ?", (rozmowa,)).fetchone()[0] == 1
    okno.lista.selection_set(str(rozmowa))
    okno._wybrano_rozmowe()
    okno.przelacz_wiadomosci([0, 1])
    assert polaczenie.execute("SELECT count(*) FROM ukryte_wiadomosci").fetchone()[0] == 2

    okno.przelacz_rozmowy([rozmowa])
    okno.przelacz_wiadomosci([0])

    assert wiersze(okno.lista)[0][0] == W_PAMIECI_ZNAK
    assert [w[0] for w in polaczenie.execute("SELECT lp FROM ukryte_wiadomosci")] == [1]


def test_filtry_i_szukanie(okno):
    okno.przelacz_rozmowy([int(okno.lista.get_children()[1])])
    okno.pokaz.set(UKRYTE)
    okno.odswiez()
    assert [w[4] for w in wiersze(okno.lista)] == ["Grzałka utrzymuje cache budzika"]

    okno.szukane.set("outlook")
    okno.odswiez()
    assert wiersze(okno.lista) == []

    okno.zaznacz([int(i) for i in okno.rozmowy] or [1])
    assert len(wiersze(okno.lista)) == 2


def test_zrodlo_pod_czytelna_nazwa(okno):
    assert {w[2] for w in wiersze(okno.lista)} == {"Claude Code"}

    okno.zrodlo.set("Codex")
    okno.odswiez()
    assert wiersze(okno.lista) == []
    okno.zrodlo.set("Claude Code")
    okno.odswiez()
    assert len(wiersze(okno.lista)) == 2


def test_naprzemienne_tlo_zostaje_po_ukryciu(okno):
    druga = okno.lista.get_children()[1]
    assert tuple(okno.lista.item(druga, "tags")) == ("parzysta",)

    okno.przelacz_rozmowy([int(druga)])
    assert tuple(okno.lista.item(druga, "tags")) == ("parzysta", "ukryta")


def test_uklad_okna_zapisany_przy_zamknieciu(okno, sciezki):
    okno.okno.deiconify()
    okno.okno.geometry("1000x700+20+30")
    okno.okno.update()
    okno._zapisz_uklad()

    uklad = okno_pamieci.wyglad.wczytaj_uklad(sciezki.uklad_okna)
    assert uklad["geometria"].startswith("1000x700")
    assert uklad["zmaksymalizowane"] is False


def test_usuniecie_w_tle(okno, polaczenie):
    okno.lista.selection_set(okno.lista.get_children()[0])

    okno.usun()
    assert okno.zajete
    czekaj(okno)

    assert [w[4] for w in wiersze(okno.lista)] == ["Grzałka utrzymuje cache budzika"]
    assert okno.stan.get() == "Usunięto na stałe: 1 rozmowa."
    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 1


def test_opis_wystapien():
    assert opis_wystapien(Wystapienia(wiadomosci=2, rozmowy=1, surowe=5, tytuly=1)) == \
        "2 wiadomości w 1 rozmowie, 1 tytuł, 5 surowych kopii sesji"
    assert opis_wystapien(Wystapienia()) == "nigdzie"
