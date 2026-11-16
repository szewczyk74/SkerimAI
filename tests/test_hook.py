import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from skerimai import hook, powiadomienie
from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.archiwizator import Raport
from skerimai.baza import polacz
from skerimai.blokada import blokada


def tresc(plik) -> str:
    return plik.read_text(encoding="utf-8") if plik.exists() else ""


def test_opis_zdarzenia_z_wejscia_hooka():
    dane = json.dumps({"hook_event_name": "SessionEnd", "reason": "clear", "session_id": "9e3b7d8c-4534-4f61"})

    assert hook.opis_zdarzenia(dane) == "SessionEnd/clear, sesja 9e3b7d8c"


def test_opis_zdarzenia_przy_blednym_wejsciu():
    assert hook.opis_zdarzenia("") == "hook"
    assert hook.opis_zdarzenia("nie json") == "hook"
    assert hook.opis_zdarzenia("[1, 2]") == "hook"


def test_archiwizacja_w_tle_zapisuje_do_bazy_i_dziennika(projekty, sciezki, caplog):
    caplog.set_level("INFO", logger="skerimai.hook")
    powiadomienia = []

    assert hook.archiwizuj_w_tle(sciezki, "SessionEnd/other", [ClaudeCode(projekty)],
                                 powiadom=lambda *p: powiadomienia.append(p))

    polaczenie = polacz(sciezki.baza)
    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 2
    polaczenie.close()
    assert "SessionEnd/other: Sesje: 2, zaktualizowane: 2" in caplog.text
    assert powiadomienia == [("Rozmowy zarchiwizowane", "Zapisano: 2 rozmowy, 6 wiadomości, 2 nowe załączniki.")]


def test_archiwizacja_w_tle_rezygnuje_gdy_archiwum_zajete(projekty, sciezki, monkeypatch, caplog):
    monkeypatch.setattr(hook, "CZEKAJ_NA_ARCHIWUM_S", 0.2)
    powiadomienia = []

    with blokada(sciezki.blokada, 0):
        assert not hook.archiwizuj_w_tle(sciezki, "test", [ClaudeCode(projekty)],
                                         powiadom=lambda *p: powiadomienia.append(p))

    assert "archiwum zajęte" in caplog.text
    assert [n for n, _ in powiadomienia] == ["Archiwum zajęte"]


def test_archiwizacja_w_tle_zapisuje_blad(projekty, sciezki, caplog):
    class Zepsuty(ClaudeCode):
        def znajdz_sesje(self):
            raise OSError("dysk niedostępny")
    powiadomienia = []

    assert not hook.archiwizuj_w_tle(sciezki, "test", [Zepsuty(projekty)], powiadom=lambda *p: powiadomienia.append(p))

    assert "archiwizacja nie powiodła się" in caplog.text
    assert "dysk niedostępny" in caplog.text
    assert powiadomienia == [("Archiwizacja nie powiodła się", f"dysk niedostępny\nSzczegóły: {sciezki.dziennik}")]


def test_archiwizacja_z_zasobnika_powiadamia_tylko_o_bledzie(projekty, sciezki, monkeypatch):
    class Zepsuty(ClaudeCode):
        def znajdz_sesje(self):
            raise OSError("dysk niedostępny")
    monkeypatch.setattr(hook, "CZEKAJ_NA_ARCHIWUM_S", 0.2)
    powiadomienia = []

    def archiwizuj(adapter):
        return hook.archiwizuj_w_tle(sciezki, "zasobnik", [adapter], powiadom=lambda *p: powiadomienia.append(p),
                                     tylko_bledy=True)

    assert archiwizuj(ClaudeCode(projekty))
    with blokada(sciezki.blokada, 0):
        assert not archiwizuj(ClaudeCode(projekty))
    assert not archiwizuj(Zepsuty(projekty))

    assert [n for n, _ in powiadomienia] == ["Archiwizacja nie powiodła się"]


def test_zlecenie_archiwizacji_zrodel_z_zasobnika(monkeypatch):
    polecenia = []
    monkeypatch.setattr(hook, "uruchom_w_tle", polecenia.append)

    hook.zlec_archiwizacje_zrodel("zasobnik", ["codex", "kreion_kartridz"], tylko_bledy=True)

    assert polecenia == [[sys.executable, "-m", "skerimai.hook", "zasobnik", "--zrodlo", "codex",
                          "--zrodlo", "kreion_kartridz", "--tylko-bledy"]]


def test_proces_w_tle_archiwizuje_wskazane_zrodla(sciezki, monkeypatch):
    zlecenia = []
    monkeypatch.setenv("SKERIMAI_DIR", str(sciezki.katalog))
    monkeypatch.setattr(hook, "archiwizuj_w_tle",
                        lambda s, opis, adaptery, powiadom, tylko_bledy: zlecenia.append(
                            (opis, [a.nazwa for a in adaptery], tylko_bledy)) or True)

    assert hook._main(["zasobnik", "--zrodlo", "codex", "--zrodlo", "kreion_kartridz", "--tylko-bledy"]) == 0
    assert hook._main(["SessionEnd/other, sesja 12345678"]) == 0

    assert zlecenia == [("zasobnik", ["codex", "kreion_kartridz"], True),
                        ("SessionEnd/other, sesja 12345678", ["claude_code"], False)]


@pytest.mark.parametrize(("raport", "oczekiwane"), [
    (Raport(sesje=40, zaktualizowane=1, wiadomosci=1), ("Rozmowa zarchiwizowana", "Zapisano: 1 wiadomość.")),
    (Raport(zaktualizowane=1, wiadomosci=24, nowe_zalaczniki=3),
     ("Rozmowa zarchiwizowana", "Zapisano: 24 wiadomości, 3 nowe załączniki.")),
    (Raport(zaktualizowane=12, wiadomosci=212, nowe_zalaczniki=5),
     ("Rozmowy zarchiwizowane", "Zapisano: 12 rozmów, 212 wiadomości, 5 nowych załączników.")),
    (Raport(zaktualizowane=22, wiadomosci=300), ("Rozmowy zarchiwizowane", "Zapisano: 22 rozmowy, 300 wiadomości.")),
    (Raport(zaktualizowane=1, wiadomosci=6, brakujace_zalaczniki=2),
     ("Rozmowa zarchiwizowana", "Zapisano: 6 wiadomości. Nie znaleziono plików załączników: 2.")),
    (Raport(sesje=40), ("Archiwum jest aktualne", "Nic nowego do zapisania.")),
])
def test_podsumowanie_do_powiadomienia(raport, oczekiwane):
    assert hook.podsumowanie(raport) == oczekiwane


def test_toast_z_tekstem_z_rozmowy_jest_poprawnym_xml():
    import xml.etree.ElementTree as ET

    xml = powiadomienie.xml_toastu("Błąd <&>", "Plik „40 TYDZIEŃ.docx” – 'x' & \"y\"")

    assert [t.text for t in ET.fromstring(xml).iter("text")] == ["Błąd <&>", "Plik „40 TYDZIEŃ.docx” – 'x' & \"y\""]


def test_powiadomienia_mozna_wylaczyc(monkeypatch):
    monkeypatch.setattr(powiadomienie.subprocess, "run", lambda *a, **k: pytest.fail("toast mimo wyłączenia"))

    powiadomienie.pokaz("nagłówek", "treść")  # SKERIMAI_POWIADOMIENIA=0 z conftest

    assert not powiadomienie.wlaczone()


@pytest.mark.skipif(os.name != "nt", reason="okno konsoli dotyczy tylko Windows")
@pytest.mark.parametrize("launcher", ["python.exe", "pythonw.exe"])
def test_proces_w_tle_nie_otwiera_okna_konsoli(tmp_path, launcher):
    """Interpreter uruchomiony przez launcher venv nie może dostać widocznej konsoli (czarne okno) –
    także zlecony z ikony w zasobniku, która sama działa w `pythonw.exe`."""
    wynik = tmp_path / "okno.txt"
    sonda = ("import ctypes, sys; k = ctypes.windll.kernel32; u = ctypes.windll.user32; h = k.GetConsoleWindow(); "
             "open(sys.argv[1], 'w').write(str(bool(h and u.IsWindowVisible(h))))")
    program = os.path.join(os.path.dirname(sys.executable), launcher)

    hook.uruchom_w_tle([program, "-c", sonda, str(wynik)]).wait(timeout=20)

    assert tresc(wynik) == "False"


def test_interpreter_konsolowy_zamiast_pythonw(tmp_path):
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert hook.interpreter_konsolowy(str(tmp_path / "pythonw.exe")) == str(tmp_path / "pythonw.exe")  # brak python.exe
    (tmp_path / "python.exe").write_bytes(b"")
    assert hook.interpreter_konsolowy(str(tmp_path / "PythonW.exe")) == str(tmp_path / "python.exe")
    assert hook.interpreter_konsolowy(str(tmp_path / "python.exe")) == str(tmp_path / "python.exe")


def test_hook_wraca_od_razu_a_archiwizacja_konczy_sie_w_tle(projekty, sciezki, tmp_path):
    """Pełna ścieżka jak w Claude Code: `skerimai hook` z JSON-em na stdin, proces w tle, dziennik."""
    dom = tmp_path / "dom"
    shutil.copytree(projekty, dom / ".claude" / "projects")
    srodowisko = {**os.environ, "USERPROFILE": str(dom), "HOME": str(dom), "SKERIMAI_DIR": str(sciezki.katalog)}
    wejscie = json.dumps({"hook_event_name": "SessionEnd", "reason": "other", "session_id": "12345678-abcd",
                          "cwd": r"C:\Projekty\łódka"}, ensure_ascii=False).encode("utf-8")

    # Archiwum zajęte: proces w tle musi czekać, a hook i tak wraca – nie trzyma potoków Claude Code.
    with blokada(sciezki.blokada, 0):
        wynik = subprocess.run([sys.executable, "-m", "skerimai", "hook"], input=wejscie,
                               capture_output=True, env=srodowisko, timeout=20)
        assert wynik.returncode == 0, wynik.stderr.decode("utf-8", "replace")
        assert "zaktualizowane" not in tresc(sciezki.dziennik)

    koniec = time.monotonic() + 60
    while "zaktualizowane" not in tresc(sciezki.dziennik) and time.monotonic() < koniec:
        time.sleep(0.5)

    dziennik = tresc(sciezki.dziennik)
    assert "SessionEnd/other, sesja 12345678: Sesje: 2, zaktualizowane: 2" in dziennik
    polaczenie = polacz(sciezki.baza)
    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 2
    polaczenie.close()


def test_podsumowanie_mowi_o_nieczytelnych_sesjach():
    from skerimai.archiwizator import Raport

    assert hook.podsumowanie(Raport(nieczytelne=1)) == (
        "Archiwizacja niepełna", "Nie udało się odczytać: 1 sesja – szczegóły w dzienniku.")
    naglowek, tresc = hook.podsumowanie(Raport(zaktualizowane=1, wiadomosci=3, nieczytelne=2))
    assert naglowek == "Rozmowa zarchiwizowana"
    assert tresc == "Zapisano: 3 wiadomości. Nie udało się odczytać: 2 sesje – szczegóły w dzienniku."
