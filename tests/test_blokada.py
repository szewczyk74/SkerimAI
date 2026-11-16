import subprocess
import sys
import time

from skerimai.blokada import blokada

TRZYMAJ_BLOKADE = """
import sys
from pathlib import Path
from skerimai.blokada import blokada
with blokada(Path(sys.argv[1]), 5) as uzyskana:
    print("mam" if uzyskana else "brak", flush=True)
    sys.stdin.read()
"""


def test_druga_blokada_czeka_i_rezygnuje(tmp_path):
    plik = tmp_path / "a.lock"
    with blokada(plik, 0) as pierwsza:
        start = time.monotonic()
        with blokada(plik, 0.5) as druga:
            assert pierwsza
            assert not druga
        assert time.monotonic() - start >= 0.5


def test_blokada_wolna_po_zwolnieniu(tmp_path):
    plik = tmp_path / "a.lock"
    with blokada(plik, 0):
        pass

    with blokada(plik, 0) as uzyskana:
        assert uzyskana


def test_blokada_miedzy_procesami(tmp_path):
    plik = tmp_path / "a.lock"
    inny = subprocess.Popen(
        [sys.executable, "-c", TRZYMAJ_BLOKADE, str(plik)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert inny.stdout.readline().strip() == "mam"
        with blokada(plik, 0) as uzyskana:
            assert not uzyskana

        inny.stdin.close()  # inny proces kończy pracę i zwalnia blokadę
        with blokada(plik, 5) as uzyskana:
            assert uzyskana
    finally:
        inny.kill()
        inny.wait()
