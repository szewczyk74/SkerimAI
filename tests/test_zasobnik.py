import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from skerimai import zasobnik
from skerimai.blokada import blokada

windows = pytest.mark.skipif(os.name != "nt", reason="autostart i lista procesów tylko w Windows")


def udawana_ikona(sciezki, gotowa: threading.Event) -> None:
    """Jak prawdziwa ikona: trzyma blokadę i co chwilę sprawdza prośbę o zamknięcie."""
    with blokada(sciezki.blokada_zasobnika, 0) as jedyna:
        assert jedyna
        gotowa.set()
        while not sciezki.koniec_zasobnika.exists():
            time.sleep(0.05)


def test_zamyka_dzialajaca_ikone(sciezki):
    gotowa = threading.Event()
    watek = threading.Thread(target=udawana_ikona, args=(sciezki, gotowa))
    watek.start()
    assert gotowa.wait(5)

    assert zasobnik.dziala(sciezki)
    assert zasobnik.zakoncz(sciezki)

    watek.join(5)
    assert not zasobnik.dziala(sciezki)
    assert not sciezki.koniec_zasobnika.exists()


def test_bez_dzialajacej_ikony(sciezki):
    assert not zasobnik.dziala(sciezki)
    assert zasobnik.zakoncz(sciezki)
    assert not sciezki.katalog.exists()  # samo sprawdzenie niczego nie zakłada


def test_ikona_ktora_sie_nie_zamyka(sciezki):
    with blokada(sciezki.blokada_zasobnika, 0):
        assert not zasobnik.zakoncz(sciezki, czekaj_s=0.3)
    assert not sciezki.koniec_zasobnika.exists()


@windows
def test_autostart_zmienia_tylko_swoj_wpis():
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, zasobnik.KLUCZ_AUTOSTARTU) as klucz:
        winreg.SetValueEx(klucz, "InnyProgram", 0, winreg.REG_SZ, "inny.exe")

    zasobnik.ustaw_autostart(zasobnik.polecenie_autostartu(Path(r"C:\uv\Scripts\skerimai-zasobnik.exe")))
    assert zasobnik.wpis_autostartu() == r'"C:\uv\Scripts\skerimai-zasobnik.exe"'
    zasobnik.ustaw_autostart(None)
    zasobnik.ustaw_autostart(None)  # drugi raz – bez błędu

    assert zasobnik.wpis_autostartu() is None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, zasobnik.KLUCZ_AUTOSTARTU) as klucz:
        assert winreg.QueryValueEx(klucz, "InnyProgram")[0] == "inny.exe"


@windows
@pytest.mark.parametrize(("dane", "wylaczony"), [
    (bytes([2]) + bytes(11), False),
    (bytes([3]) + bytes(11), True),
    (None, False),
])
def test_autostart_wylaczony_w_menedzerze_zadan(dane, wylaczony):
    import winreg

    if dane is not None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, zasobnik.KLUCZ_ZATWIERDZEN) as klucz:
            winreg.SetValueEx(klucz, zasobnik.NAZWA_AUTOSTARTU, 0, winreg.REG_BINARY, dane)

    assert zasobnik.autostart_wylaczony() is wylaczony


@windows
def test_chowa_tylko_wlasna_konsole(tmp_path):
    """Własna konsola (jak python.exe z launchera venv 3.13.0) – schowana; wspólna z terminalem – nietknięta."""
    sonda = ("import sys; from skerimai import zasobnik; wynik = zasobnik.schowaj_wlasna_konsole(); "
             "open(sys.argv[1], 'w').write(str(wynik))")
    ukryte = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=0)  # bez mignięcia w teście
    kod = Path(zasobnik.__file__).parents[1]

    # Bazowy interpreter, nie launcher venv – ten jest konsolowy i też siedziałby w nowej konsoli. W ikonie launcherem
    # jest pythonw.exe, który do konsoli się nie podłącza.
    for plik, opcje in ((tmp_path / "wlasna.txt", {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": ukryte}),
                        (tmp_path / "wspolna.txt", {})):
        subprocess.run([sys._base_executable, "-c", sonda, str(plik)], cwd=kod, timeout=30, stdin=subprocess.DEVNULL,
                       **opcje)

    assert (tmp_path / "wlasna.txt").read_text() == "True"
    assert (tmp_path / "wspolna.txt").read_text() == "False"


@windows
def test_liczy_procesy_po_nazwie_pliku(tmp_path):
    nazwa = f"skerimai-test-{os.getpid()}.exe"
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", tmp_path / nazwa)
    assert zasobnik.liczba_procesow(nazwa) == 0

    procesy = [subprocess.Popen([str(tmp_path / nazwa), "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL) for _ in range(2)]
    try:
        assert zasobnik.liczba_procesow(nazwa.upper()) == 2
    finally:
        for proces in procesy:
            proces.kill()
            proces.wait()
    assert zasobnik.liczba_procesow(nazwa) == 0
