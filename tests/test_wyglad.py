import pytest

from skerimai import wyglad
from skerimai.wyglad import fragmenty, geometria_na_ekranie, rozmiar_pliku


def test_zwykly_tekst_bez_znacznikow():
    assert fragmenty("Ala ma kota.\nDruga linia") == [("Ala ma kota.", ()), ("\n", ()), ("Druga linia", ())]


def test_blok_kodu_bez_ogrodzen_z_tlem_do_konca_linii():
    wynik = fragmenty("Przed:\n```python\nx = 1\n\ny = 2\n```\nPo.")

    assert "".join(t for t, _ in wynik) == "Przed:\nx = 1\n\ny = 2\nPo."
    assert ("\n", ()) == wynik[1]  # koniec linii przed blokiem – bez tła
    assert [t for t, z in wynik if "kod" in z] == ["x = 1", "\n", "\n", "y = 2", "\n"]
    assert wynik[-1] == ("Po.", ())


def test_naglowki_listy_cytaty_i_wyroznienia_w_linii():
    wynik = fragmenty("## Plan\n- pierwszy `kod`\n  * zagnieżdżony\n2. **ważne** zdanie\n> cytat")

    assert ("Plan", ("naglowek",)) in wynik
    assert ("• ", ("lista",)) in wynik and ("kod", ("lista", "kod_w_linii")) in wynik
    assert ("• ", ("lista2",)) in wynik
    assert ("2. ", ("lista",)) in wynik and ("ważne", ("lista", "pogrubienie")) in wynik
    assert ("cytat", ("cytat",)) in wynik


def test_markdown_wewnatrz_bloku_kodu_zostaje_dosłownie():
    assert fragmenty("```\n# nie nagłówek\n- nie lista\n```") == [
        ("# nie nagłówek", ("kod",)), ("\n", ("kod",)), ("- nie lista", ("kod",))]


def test_rozmiar_pliku():
    assert rozmiar_pliku(None) == "brak pliku"
    assert rozmiar_pliku(512) == "512 B"
    assert rozmiar_pliku(1536) == "1,5 KB"
    assert rozmiar_pliku(1_717_954) == "1,6 MB"
    assert rozmiar_pliku(250 * 1024 * 1024) == "250 MB"


def test_geometria_zapisana():
    assert geometria_na_ekranie("1200x780+100+50") in ("1200x780+100+50", "1200x780")
    assert geometria_na_ekranie("1200x780+-30000+-30000") == "1200x780"  # monitor odłączony – sam rozmiar
    assert geometria_na_ekranie("zła") is None
    assert geometria_na_ekranie(None) is None


def test_uszkodzony_plik_ukladu(tmp_path):
    plik = tmp_path / "uklad.json"
    plik.write_text("[nie słownik", encoding="utf-8")
    assert wyglad.wczytaj_uklad(plik) == {}
    wyglad.zapisz_uklad(plik, {"geometria": "900x600+0+0"})
    assert wyglad.wczytaj_uklad(plik) == {"geometria": "900x600+0+0"}


@pytest.mark.parametrize("tryb", [wyglad.JASNY, wyglad.CIEMNY])
def test_motyw_i_paleta(tryb):
    tk = pytest.importorskip("tkinter")
    from skerimai import okno_pamieci

    okno_pamieci.wskaz_biblioteki_tcl()
    try:
        korzen = tk.Tk()
    except tk.TclError as blad:
        pytest.skip(f"brak Tcl/Tk: {blad}")
    try:
        assert wyglad.zastosuj_motyw(korzen, tryb) is wyglad.PALETY[tryb]
        assert tk.ttk.Style(korzen).theme_use() == f"sun-valley-{tryb}"
    finally:
        korzen.destroy()


def test_tryb_systemu_jest_jednym_z_dwoch():
    assert wyglad.tryb_systemu() in (wyglad.JASNY, wyglad.CIEMNY)
