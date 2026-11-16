import os
import subprocess
import sys
from datetime import datetime

import pytest

from skerimai import ikona
from skerimai.dziennik import Wpis, ostatni_wpis
from skerimai.instalacja import BLAD, INFO, NAZWA_DZIENNIKA, OK, UWAGA, Miejsca, Wynik

TERAZ = datetime(2026, 10, 2, 9, 30)
POPRAWNY = Wpis("2026-10-02 09:12:00", "INFO", "SessionEnd/other: Sesje: 36, zaktualizowane: 1")


def test_wszystko_w_porzadku():
    stan = ikona.oblicz_stan(POPRAWNY, [Wynik("Program", OK), Wynik("Sesje w źródłach", INFO, "codex 15")], 2, 116,
                             TERAZ)

    assert stan.kolor == ikona.ZIELONY
    assert stan.podpowiedz == "SkerimAI · 116 rozmów\nArchiwizacja 09:12: OK\nSerwer MCP: 2 sesje"
    assert stan.problemy == ()
    assert not stan.aktualizacja


def test_blad_ostatniej_archiwizacji_bierze_ze_swiezego_wpisu():
    starsze_sprawdzenie = [Wynik(NAZWA_DZIENNIKA, OK, "2026-10-01 20:00:00 – Sesje: 36")]
    wpis = Wpis("2026-10-01 21:11:53", "ERROR", "zasobnik: archiwizacja nie powiodła się")

    stan = ikona.oblicz_stan(wpis, starsze_sprawdzenie, 0, None, TERAZ)

    assert stan.kolor == ikona.CZERWONY
    assert [(p.nazwa, p.stan) for p in stan.problemy] == [(NAZWA_DZIENNIKA, BLAD)]
    assert stan.podpowiedz == (f"SkerimAI\nArchiwizacja 01.10 21:11: błąd\nSerwer MCP: brak otwartych sesji\n"
                               f"✗ {NAZWA_DZIENNIKA}")


def test_uwagi_z_diagnostyki_i_dostepna_aktualizacja():
    wyniki = [Wynik("Program", UWAGA, "w repozytorium jest nowszy commit – zaktualizuj"),
              Wynik("Ikona w zasobniku", UWAGA, "nie uruchamia się z Windows")]

    stan = ikona.oblicz_stan(POPRAWNY, wyniki, 1, 5, TERAZ)

    assert stan.kolor == ikona.ZOLTY
    assert stan.aktualizacja
    assert stan.podpowiedz.endswith("Serwer MCP: 1 sesja\n! Program (+1)")


def test_blad_wazniejszy_niz_uwaga():
    wyniki = [Wynik("Program", UWAGA, "x"), Wynik("Hook SessionEnd w Claude Code", BLAD, "brak")]

    assert ikona.oblicz_stan(Wpis("2026-10-02 09:00:00", "WARNING", "zajęte"), wyniki, 0, 1, TERAZ).kolor == ikona.CZERWONY
    assert ikona.oblicz_stan(Wpis("2026-10-02 09:00:00", "WARNING", "zajęte"), [], 0, 1, TERAZ).kolor == ikona.ZOLTY


def test_podpowiedz_miesci_sie_w_limicie_windows():
    wyniki = [Wynik("Bardzo długa nazwa sprawdzenia " * 5, BLAD, "opis")]

    podpowiedz = ikona.oblicz_stan(POPRAWNY, wyniki, 12, 123456, TERAZ).podpowiedz

    assert len(podpowiedz) <= 127
    assert podpowiedz.endswith("…")


def test_tekst_pozycji_menu():
    assert ikona.tekst_menu("✗ Hook: brak –\nuruchom & napraw") == "✗ Hook: brak – uruchom && napraw"
    assert len(ikona.tekst_menu("x" * 300)) == 90


def test_ostatni_wpis_dziennika(tmp_path):
    plik = tmp_path / "archiwizator.log"
    assert ostatni_wpis(plik) is None

    plik.write_text("2026-10-01 08:17:31 INFO a: Sesje: 2\n2026-10-01 09:00:00 ERROR b: nie powiodła się\n"
                    "Traceback (most recent call last):\n" + "  linia\n" * 20000 + "OSError: dysk\n", encoding="utf-8")

    assert ostatni_wpis(plik) == Wpis("2026-10-01 09:00:00", "ERROR", "b: nie powiodła się")


def test_polecenie_konsoli():
    polecenie = ikona.polecenie_konsoli([r"C:\a b\skerimai.exe", "kopia", r"D:\Kopie & archiwa"], "SkerimAI – kopia")

    assert polecenie == ('cmd.exe /d /s /c "title SkerimAI – kopia & "C:\\a b\\skerimai.exe" "kopia" '
                         '"D:\\Kopie & archiwa" & echo. & pause"')


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe tylko w Windows")
def test_polecenie_konsoli_przekazuje_argumenty_bez_zmian(tmp_path):
    wynik = tmp_path / "argumenty.txt"
    zapis = "import sys; open(sys.argv[1], 'w', encoding='utf-8').write(repr(sys.argv[2:]))"

    subprocess.run(ikona.polecenie_konsoli([sys.executable, "-c", zapis, str(wynik), r"D:\Kopie & archiwa", "żółw"],
                                           "test"),
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                   timeout=30)

    assert wynik.read_text(encoding="utf-8") == repr([r"D:\Kopie & archiwa", "żółw"])


def test_obraz_ikony_dla_kazdego_koloru():
    kropka = (256 - 62, 256 - 62)
    for kolor in (ikona.ZIELONY, ikona.ZOLTY, ikona.CZERWONY):
        obraz = ikona.obraz_ikony(kolor)
        assert (obraz.size, obraz.mode) == ((256, 256), "RGBA")
        assert obraz.getpixel((30, 128))[:3] == ikona._KAFEL
        assert obraz.getpixel(kropka)[:3] == (ikona._KOLORY[kolor] or ikona._KAFEL)  # w porządku – bez kropki
    assert obraz.getpixel((256 - 62 - 68, 256 - 62))[3] == 0  # przezroczysty pierścień wokół kropki


def test_ikony_okna_we_wszystkich_rozmiarach():
    assert [o.size[0] for o in ikona.ikony_okna()] == [16, 24, 32, 48, 256]


class Zrodlo:
    nazwa = "codex"

    def __init__(self, katalog):
        self.plik = katalog / "rollout-1.jsonl"
        self.plik.write_text("{}\n", encoding="utf-8")
        self.brakujacy = katalog / "skasowany.jsonl"

    def znajdz_sesje(self):
        return {"1": [self.plik], "2": [self.brakujacy]}


@pytest.fixture
def zrodlo(tmp_path):
    return Zrodlo(tmp_path)


def test_odcisk_zmienia_sie_razem_z_plikiem_sesji(zrodlo):
    przed = ikona.odcisk_zrodel([zrodlo])
    assert ikona.odcisk_zrodel([zrodlo]) == przed

    with open(zrodlo.plik, "a", encoding="utf-8") as plik:
        plik.write("{}\n")

    assert ikona.odcisk_zrodel([zrodlo]) != przed


def test_odcisk_zrodla_z_jedna_baza_bierze_stan_rozmow(tmp_path):
    """OpenCode i Cursor trzymają rozmowy w jednej bazie; nowe wiadomości leżą w pliku `-wal`, a plik bazy
    godzinami się nie zmienia – o zmianie mówi tylko stan rozmowy, który podaje adapter."""
    class Baza(Zrodlo):
        nazwa = "opencode"
        stany = {"1": (2, 100)}

        def znajdz_sesje(self):
            return {sesja: [self.plik] for sesja in self.stany}

        def stan_sesji(self, sesja):
            return {f"{self.plik}#{sesja}": self.stany[sesja]}

    baza = Baza(tmp_path)
    przed = ikona.odcisk_zrodel([baza])
    assert ikona.odcisk_zrodel([baza]) == przed

    baza.stany = {"1": (3, 200)}  # nowa wiadomość, plik bazy bez zmian
    po_wiadomosci = ikona.odcisk_zrodel([baza])
    assert po_wiadomosci != przed

    baza.stany = {"1": (3, 200), "2": (1, 300)}  # nowa rozmowa
    assert ikona.odcisk_zrodel([baza]) != po_wiadomosci


def zasobnik_testowy(sciezki, tmp_path, zrodlo, zlecenia, wyniki=(), serwery=0):
    return ikona.Zasobnik(sciezki, Miejsca(tmp_path / "dom"), adaptery=[zrodlo],
                          zlec_archiwizacje=lambda *a, **k: zlecenia.append((a, k)),
                          sprawdzaj=lambda: list(wyniki), policz_serwery=lambda: serwery)


def test_archiwizuje_zrodla_bez_hooka_tylko_po_zmianie(sciezki, tmp_path, zrodlo):
    zlecenia = []
    zasobnik = zasobnik_testowy(sciezki, tmp_path, zrodlo, zlecenia)

    zasobnik.archiwizuj_zmienione()  # pierwszy raz po starcie – dociąga zaległości
    zasobnik.archiwizuj_zmienione()
    with open(zrodlo.plik, "a", encoding="utf-8") as plik:
        plik.write("{}\n")
    zasobnik.archiwizuj_zmienione()

    assert zlecenia == [(("zasobnik", ["codex"]), {"tylko_bledy": True})] * 2


def test_krok_odswieza_stan_i_konczy_na_prosbe(sciezki, tmp_path, zrodlo):
    zlecenia = []
    zasobnik = zasobnik_testowy(sciezki, tmp_path, zrodlo, zlecenia, [Wynik("Program", UWAGA, "zaktualizuj")], 3)

    assert zasobnik.krok(0)
    assert (zasobnik.stan.kolor, zasobnik.stan.aktualizacja, zasobnik.stan.serwery) == (ikona.ZOLTY, True, 3)
    assert len(zlecenia) == 1

    sciezki.katalog.mkdir(parents=True, exist_ok=True)
    sciezki.koniec_zasobnika.touch()
    assert not zasobnik.krok(1)


def test_po_archiwizacji_sprawdza_stan_od_razu(sciezki, tmp_path, zrodlo):
    """Nowy komputer: pierwsze sprawdzenie widzi „brak bazy”, zanim pierwsza archiwizacja ją założy – czerwona
    kropka nie może trwać 10 minut do następnego sprawdzenia."""
    sprawdzenia = []

    def sprawdzaj():
        sprawdzenia.append(True)
        return [Wynik("Archiwum", BLAD, "brak bazy")] if len(sprawdzenia) == 1 else []

    zasobnik = ikona.Zasobnik(sciezki, Miejsca(tmp_path / "dom"), adaptery=[zrodlo], zlec_archiwizacje=lambda *a, **k: None,
                              sprawdzaj=sprawdzaj, policz_serwery=lambda: 0)

    assert zasobnik.krok(0) and zasobnik.stan.kolor == ikona.CZERWONY
    assert zasobnik.krok(ikona.ODSWIEZANIE_S) and len(sprawdzenia) == 1  # dziennik pusty – archiwizacja trwa
    sciezki.katalog.mkdir(parents=True, exist_ok=True)
    sciezki.dziennik.write_text("2026-11-16 23:41:08 INFO zasobnik: Sesje: 4, zaktualizowane: 4\n", encoding="utf-8")

    assert zasobnik.krok(2 * ikona.ODSWIEZANIE_S) and zasobnik.stan.kolor == ikona.ZIELONY
    assert zasobnik.krok(3 * ikona.ODSWIEZANIE_S) and len(sprawdzenia) == 2  # bez nowego wpisu – bez sprawdzenia


class UdawanaIkona:
    visible = True

    def __init__(self):
        self.dodania = 0

    def _show(self):
        self.dodania += 1


@pytest.mark.skipif(os.name != "nt", reason="zasobnik Windows")
def test_dodaje_ponownie_ikone_zgubiona_przez_windows(sciezki, tmp_path, zrodlo, monkeypatch):
    zasobnik = zasobnik_testowy(sciezki, tmp_path, zrodlo, [])
    zasobnik.ikona = UdawanaIkona()
    obecna = True
    monkeypatch.setattr(ikona, "ikona_w_zasobniku", lambda _: obecna)

    zasobnik.pilnuj_ikony()
    assert zasobnik.ikona.dodania == 0

    obecna = False
    zasobnik.pilnuj_ikony()
    assert zasobnik.ikona.dodania == 1

    zasobnik.ikona.visible = False  # schowana celowo – nie przywracamy
    zasobnik.pilnuj_ikony()
    assert zasobnik.ikona.dodania == 1


def test_krok_pilnuje_ikony_co_minute(sciezki, tmp_path, zrodlo, monkeypatch):
    zasobnik = zasobnik_testowy(sciezki, tmp_path, zrodlo, [])
    pilnowania = []
    monkeypatch.setattr(zasobnik, "pilnuj_ikony", lambda: pilnowania.append(True))

    for sekunda in range(1, ikona.PILNOWANIE_IKONY_S * 2 + 1):
        zasobnik.krok(sekunda)

    assert len(pilnowania) == 2


def test_sciaga_zwrotow_jest_w_pakiecie():
    tekst = ikona.JAK_ROZMAWIAC.read_text(encoding="utf-8")

    assert "CLAUDE CODE" in tekst and "KREIONAI" in tekst and "szukaj_w_historii" in tekst


def test_menu_pokazuje_problemy_i_aktualizacje(sciezki, tmp_path, zrodlo):
    def teksty(wyniki, serwery):
        zasobnik = zasobnik_testowy(sciezki, tmp_path, zrodlo, [], wyniki, serwery)
        zasobnik.sprawdz_pelne()
        zasobnik.odswiez()
        return [(p.text, p.enabled) for p in zasobnik.pozycje_menu() if p.text != "- - - -"]

    w_porzadku = teksty([], 0)
    assert w_porzadku[0] == ("SkerimAI – w porządku", False)  # bez archiwum – bez liczby rozmów
    assert [t for t, _ in w_porzadku[1:]] == ["Edytuj pamięć…", "Archiwizuj teraz", "Sprawdź stan", "Więcej", "Zakończ"]
    zasobnik = zasobnik_testowy(sciezki, tmp_path, zrodlo, [], [], 0)
    zasobnik.rozmowy = 5
    assert next(iter(zasobnik.pozycje_menu())).text == "SkerimAI · 5 rozmów – w porządku"
    [wiecej] = [p for p in zasobnik.pozycje_menu() if p.text == "Więcej"]
    assert [p.text for p in wiecej.submenu.items if p.text != "- - - -"] == [
        "Jak rozmawiać z pamięcią…", "Kopia zapasowa…", "Otwórz folder archiwum", "Otwórz dziennik"]

    aktualizacja = [Wynik("Program", UWAGA, "nowszy commit & zmiany")]
    assert ("! Program: nowszy commit && zmiany", True) in teksty(aktualizacja, 0)
    assert ("Zainstaluj aktualizację", True) in teksty(aktualizacja, 0)
    assert ("Zainstaluj aktualizację – najpierw zamknij 2 sesje Claude Code i Codex", False) in teksty(aktualizacja, 2)
    assert ("Aplikację Claude zakończ z jej ikony – zamknięte okno zostawia sesje", False) in teksty(aktualizacja, 2)


def test_aktualizacja_z_wydania_uruchamia_nowa_wersje_przez_uv(sciezki, tmp_path, zrodlo, monkeypatch):
    """Kopia z wydania: okno postępu i instalacja z nowej wersji w pamięci podręcznej uv, poza kopią używaną."""
    uruchomione, powiadomienia = [], []
    monkeypatch.setattr(ikona, "program_docelowy", lambda: ikona.Program(tmp_path / "Scripts", wersja="0.2.0"))
    monkeypatch.setattr(ikona.shutil, "which", lambda nazwa: rf"C:\uv\{nazwa}.exe")
    monkeypatch.setattr(ikona.subprocess, "Popen", lambda polecenie, **opcje: uruchomione.append(polecenie))
    monkeypatch.setattr(ikona.powiadomienie, "pokaz", lambda tytul, tresc: powiadomienia.append(tytul))
    zasobnik = zasobnik_testowy(sciezki, tmp_path, zrodlo, [])
    najnowsza = []
    monkeypatch.setattr(ikona, "najnowsza_wersja", lambda plik: najnowsza[0])

    najnowsza.append("0.2.0")
    zasobnik.aktualizuj()
    assert uruchomione == [] and powiadomienia == ["Brak aktualizacji"]

    najnowsza[0] = "0.3.0"
    zasobnik.aktualizuj()
    adres = "https://github.com/szewczyk74/SkerimAI/archive/refs/tags/v0.3.0.zip"
    z_wydania = [r"C:\uv\uv.exe", "tool", "run", "--python", "3.13", "--python-preference", "only-managed",
                 "--from", adres]
    assert uruchomione == [[*z_wydania, "skerimai-pamiec", "--polecenie", "SkerimAI – aktualizacja", "--",
                            *z_wydania, "skerimai", "instaluj", "--wersja", "0.3.0"]]
    assert powiadomienia[-1] == "Pobieranie aktualizacji"
