import sqlite3

import pytest

from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.archiwizator import archiwizuj
from skerimai.baza import polacz
from skerimai.blokada import blokada
from skerimai.konfiguracja import Sciezki
from skerimai.kopia import utworz_kopie
from skerimai.zapytania import szukaj


@pytest.fixture
def archiwum(projekty, sciezki, polaczenie):
    """Archiwum z otwartym połączeniem – świeże zapisy leżą jeszcze w pliku -wal."""
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    return sciezki


def test_kopia_jest_pelnym_archiwum(archiwum, tmp_path):
    wynik = utworz_kopie(archiwum, tmp_path / "kopie")

    assert wynik.katalog.parent == tmp_path / "kopie"
    assert wynik.katalog.name.startswith("skerimai-")
    assert (wynik.rozmowy, wynik.wiadomosci) == (2, 6)
    kopia = Sciezki(wynik.katalog)
    polaczenie = polacz(kopia.baza)
    try:
        for wiersz in polaczenie.execute("SELECT sciezka FROM zalaczniki"):
            assert (kopia.zalaczniki / wiersz["sciezka"]).read_bytes() == (archiwum.zalaczniki / wiersz["sciezka"]).read_bytes()
        assert szukaj(polaczenie, "NodeJsTools")
    finally:
        polaczenie.close()
    assert (kopia.surowe / "claude_code" / "sesja-1.jsonl.gz").is_file()
    assert wynik.pliki == len([p for p in wynik.katalog.rglob("*") if p.is_file()])


def test_kopia_bazy_to_jeden_plik_bez_wal(archiwum, tmp_path):
    wynik = utworz_kopie(archiwum, tmp_path)

    assert sorted(p.name for p in wynik.katalog.iterdir()) == ["skerimai.db", "surowe", "zalaczniki"]
    polaczenie = sqlite3.connect(wynik.katalog / "skerimai.db")
    assert polaczenie.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    polaczenie.close()


def test_dwie_kopie_w_tej_samej_sekundzie(archiwum, tmp_path):
    pierwsza = utworz_kopie(archiwum, tmp_path)
    druga = utworz_kopie(archiwum, tmp_path)

    assert pierwsza.katalog != druga.katalog
    assert druga.rozmowy == 2


def test_nie_kopiuje_archiwum_do_niego_samego(archiwum):
    with pytest.raises(ValueError, match="w samym archiwum"):
        utworz_kopie(archiwum, archiwum.katalog / "kopie")


def test_czeka_na_archiwizator_i_nie_zostawia_smieci(archiwum, tmp_path):
    with blokada(archiwum.blokada, 0):
        with pytest.raises(TimeoutError, match="Archiwum zajęte"):
            utworz_kopie(archiwum, tmp_path / "kopie", czekaj_s=0.2)

    assert not (tmp_path / "kopie").exists() or not any((tmp_path / "kopie").iterdir())


def test_nieudana_kopia_nie_zostawia_niepelnego_katalogu(archiwum, tmp_path, monkeypatch):
    def awaria(*a, **k):
        raise OSError("dysk pełny")
    monkeypatch.setattr("skerimai.kopia.shutil.copytree", awaria)

    with pytest.raises(OSError, match="dysk pełny"):
        utworz_kopie(archiwum, tmp_path / "kopie")

    assert not any((tmp_path / "kopie").iterdir())
