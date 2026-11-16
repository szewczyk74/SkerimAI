import sys
import time

import pytest

from skerimai import dialogi, okno_pamieci
from skerimai.instalacja import BLAD, INFO, OK, UWAGA, Wynik
from skerimai.okno_stanu import OknoPolecenia, OknoStanu, tekst_raportu

tk = pytest.importorskip("tkinter")

WYNIKI = [Wynik("Program", OK, "SkerimAI 0.1.0"), Wynik("Hook SessionEnd w Claude Code", UWAGA, "stara ścieżka"),
          Wynik("Archiwum", BLAD, "brak bazy"), Wynik("Codex", INFO, "brak Codex")]


@pytest.fixture
def korzen():
    okno_pamieci.wskaz_biblioteki_tcl()
    try:
        korzen = tk.Tk()
    except tk.TclError as blad:
        pytest.skip(f"brak Tcl/Tk: {blad}")
    korzen.withdraw()
    yield korzen
    korzen.destroy()


def czekaj(korzen, warunek, limit=30.0):
    koniec = time.monotonic() + limit
    while not warunek() and time.monotonic() < koniec:
        korzen.update()
        time.sleep(0.02)
    assert warunek()


def przyciski(okno):
    for dziecko in okno.winfo_children():
        if isinstance(dziecko, tk.ttk.Button):
            yield dziecko
        yield from przyciski(dziecko)


def kliknij_w_dialogu(korzen, tekst, wpisz=None):
    """Po pokazaniu okienka: wpisuje tekst w pole i klika przycisk. Po 5 s zamyka okienko – test nie zawiśnie."""
    def teraz():
        [dialog] = [w for w in korzen.winfo_children() if isinstance(w, tk.Toplevel)]
        if wpisz is not None:
            [pole] = [w for w in dialog.winfo_children()[0].winfo_children()
                      if isinstance(w, (tk.ttk.Entry, tk.ttk.Combobox))]
            pole.delete(0, "end")
            pole.insert(0, wpisz)
        next(p for p in przyciski(dialog) if p.cget("text") == tekst).invoke()

    korzen.after(200, teraz)
    korzen.after(5000, lambda: [w.destroy() for w in korzen.winfo_children() if isinstance(w, tk.Toplevel)])


def test_raport_jak_w_konsoli():
    assert tekst_raportu(WYNIKI) == (
        "Stan SkerimAI\n  ✓ Program: SkerimAI 0.1.0\n  ! Hook SessionEnd w Claude Code: stara ścieżka\n"
        "  ✗ Archiwum: brak bazy\n  • Codex: brak Codex\nBłędy: 1, uwagi: 1. Wpisy w konfiguracji naprawia: "
        "skerimai konfiguruj")


def test_okno_stanu_pokazuje_wyniki_i_pierwszy_problem(korzen, sciezki):
    okno = OknoStanu(korzen, sciezki, sprawdzaj=lambda: WYNIKI)
    czekaj(korzen, lambda: not okno.zajete)

    assert okno.tytul.cget("text") == "Błędy: 1, Uwagi: 1"
    assert okno.znak.cget("text") == "✗"
    assert [okno.tabela.item(i, "values")[1] for i in okno.tabela.get_children()] == [w.nazwa for w in WYNIKI]
    assert okno.tabela.selection() == ("1",)
    assert "stara ścieżka" in okno.szczegoly.get("1.0", "end")

    okno.kopiuj()
    assert korzen.clipboard_get() == tekst_raportu(WYNIKI)


def test_okno_stanu_wszystko_w_porzadku_i_blad_sprawdzania(korzen, sciezki):
    wyniki = [[Wynik("Program", OK, "")], RuntimeError("brak git")]

    def sprawdzaj():
        wynik = wyniki.pop(0)
        if isinstance(wynik, Exception):
            raise wynik
        return wynik

    okno = OknoStanu(korzen, sciezki, sprawdzaj=sprawdzaj)
    czekaj(korzen, lambda: not okno.zajete)
    assert (okno.znak.cget("text"), okno.tytul.cget("text")) == ("✓", "Wszystko w porządku")

    okno.odswiez()
    czekaj(korzen, lambda: not okno.zajete)
    assert okno.tytul.cget("text") == "Nie udało się sprawdzić stanu"
    assert "brak git" in okno.podtytul.cget("text")


def test_okno_polecenia_pokazuje_wynik_na_zywo(korzen):
    koniec = []
    okno = OknoPolecenia(tk.Toplevel(korzen), "SkerimAI – kopia archiwum",
                         [sys.executable, "-c", "print('✓ Kopia gotowa'); print('rozmowy: 3')"], po=koniec.append)
    czekaj(korzen, lambda: koniec)

    assert koniec == [0]
    assert okno.stan.cget("text") == "Gotowe"
    assert okno.wyjscie.get("1.0", "end").strip() == "✓ Kopia gotowa\nrozmowy: 3"
    assert str(okno.przycisk_zamknij.cget("state")) == "normal"


def test_okno_polecenia_blad_i_brak_programu(korzen):
    koniec = []
    okno = OknoPolecenia(tk.Toplevel(korzen), "SkerimAI – aktualizacja",
                         [sys.executable, "-c", "import sys; print('✗ zajęte'); sys.exit(3)"], po=koniec.append)
    czekaj(korzen, lambda: koniec)
    assert okno.stan.cget("text").startswith("Nie udało się (kod 3)")
    assert "blad" in okno.wyjscie.tag_names("1.0")

    brak = OknoPolecenia(tk.Toplevel(korzen), "SkerimAI – kopia", [r"C:\nie\ma\takiego.exe"])
    assert brak.kod == -1 and "Nie udało się uruchomić" in brak.wyjscie.get("1.0", "end")


def test_okno_polecenia_nie_zamyka_sie_w_trakcie(korzen, monkeypatch):
    pokazane = []
    monkeypatch.setattr(dialogi, "pokaz", lambda *a, **k: pokazane.append(a[2]))
    okno = OknoPolecenia(tk.Toplevel(korzen), "SkerimAI – kopia",
                         [sys.executable, "-c", "import time; time.sleep(1)"])
    okno.zamknij()
    assert okno.okno.winfo_exists() and pokazane
    czekaj(korzen, lambda: okno.kod is not None)
    okno.zamknij()
    assert not okno.okno.winfo_exists()


def test_dialog_pytania_ostrzezenie_domyslnie_nie(korzen):
    kliknij_w_dialogu(korzen, "Usuń na stałe")
    assert dialogi.pytaj(korzen, "Usuń", "Na pewno?", tak="Usuń na stałe", ostrzezenie=True) is True

    korzen.after(200, lambda: next(w for w in korzen.winfo_children()
                                   if isinstance(w, tk.Toplevel)).event_generate("<Return>"))
    korzen.after(5000, lambda: [w.destroy() for w in korzen.winfo_children() if isinstance(w, tk.Toplevel)])
    assert dialogi.pytaj(korzen, "Usuń", "Na pewno?", tak="Usuń na stałe", ostrzezenie=True) is False


def test_dialog_tekstu_z_podpowiedziami(korzen):
    kliknij_w_dialogu(korzen, "Zapisz", wpisz="Pompa KSM")
    assert dialogi.zapytaj(korzen, "Projekt", "Projekt:", "stary", podpowiedzi=["Pompa KSM", "sklep"]) == "Pompa KSM"

    kliknij_w_dialogu(korzen, "Anuluj")
    assert dialogi.zapytaj(korzen, "Tytuł", "Nowy tytuł:", "x") is None


def test_dialog_komunikatu(korzen):
    kliknij_w_dialogu(korzen, "OK")
    dialogi.pokaz(korzen, "SkerimAI", "Zaznacz jedną rozmowę.")
