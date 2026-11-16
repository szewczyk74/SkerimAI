import gzip
import json
import os

import pytest

from skerimai import archiwizator, edycja
from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.archiwizator import archiwizuj


def test_archiwizuje_rozmowy_i_zalaczniki(projekty, sciezki, polaczenie):
    raport = archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert raport.zaktualizowane == 2
    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 2
    assert polaczenie.execute("SELECT count(*) FROM wiadomosci").fetchone()[0] == 6
    assert raport.nowe_zalaczniki == 2
    for wiersz in polaczenie.execute("SELECT sciezka FROM zalaczniki"):
        assert (sciezki.zalaczniki / wiersz["sciezka"]).is_file()


def test_zachowuje_surowa_kopie(projekty, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    kopia = sciezki.surowe / "claude_code" / "sesja-1.jsonl.gz"
    zrodlo = projekty / "C--Projekty-sklep-pl" / "sesja-1.jsonl"
    assert gzip.decompress(kopia.read_bytes()) == zrodlo.read_bytes()


def test_surowa_kopia_skompresowanej_sesji_codex_jest_rozpakowana(sciezki, polaczenie, tmp_path):
    import zstandard

    from skerimai.adaptery.codex import Codex

    jsonl = b'{"timestamp":"t1","type":"response_item","payload":{"type":"message","role":"user",' \
            b'"content":[{"type":"input_text","text":"pytanie"}]}}\n'
    katalog = tmp_path / "codex"
    plik = katalog / "2026" / "10" / "01" / "rollout-2026-10-01T21-53-14-01a0f907-360d-71f3-b417-a54ac45251b5.jsonl.zst"
    plik.parent.mkdir(parents=True)
    plik.write_bytes(zstandard.ZstdCompressor().compress(jsonl))

    archiwizuj(polaczenie, sciezki, [Codex(katalog)])

    kopia = sciezki.surowe / "codex" / "01a0f907-360d-71f3-b417-a54ac45251b5.jsonl.gz"
    assert gzip.decompress(kopia.read_bytes()) == jsonl


def test_nieudana_surowa_kopia_wraca_w_nastepnym_przebiegu(projekty, sciezki, polaczenie, monkeypatch):
    zapisz = archiwizator._zapisz_surowe
    zajety = True

    def zablokowana(cel, czesci, wymazane):
        if zajety:
            raise PermissionError("plik zajęty")
        zapisz(cel, czesci, wymazane)

    monkeypatch.setattr(archiwizator, "_zapisz_surowe", zablokowana)
    with pytest.raises(PermissionError):
        archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    zajety = False

    raport = archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert raport.zaktualizowane == 2
    for sesja in ("sesja-1", "sesja-2"):
        assert (sciezki.surowe / "claude_code" / f"{sesja}.jsonl.gz").is_file(), sesja


def test_pomija_niezmienione_sesje(projekty, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    raport = archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert raport.sesje == 2
    assert raport.zaktualizowane == 0


def test_zmieniona_sesja_bez_duplikatow(projekty, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    plik = projekty / "C--Projekty-budzik" / "sesja-2.jsonl"
    with plik.open("a", encoding="utf-8") as f:
        f.write('{"type":"user","timestamp":"2026-09-20T11:00:00.000Z","message":{"content":"Kolejne pytanie"}}\n')

    raport = archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert raport.zaktualizowane == 1
    assert polaczenie.execute("SELECT count(*) FROM wiadomosci").fetchone()[0] == 7
    assert polaczenie.execute("SELECT count(*) FROM fts_wiadomosci").fetchone()[0] == 7
    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 2


def test_zalacznik_zostaje_gdy_plik_zrodlowy_zniknal(projekty, sciezki, polaczenie, obraz_na_dysku):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    os.remove(obraz_na_dysku)  # Windows sprząta %TEMP%

    raport = archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)], pelne=True)

    assert raport.brakujace_zalaczniki == 0
    assert polaczenie.execute("SELECT count(*) FROM wiadomosc_zalacznik").fetchone()[0] == 2


def test_rozmowa_zostaje_po_skasowaniu_zrodla(projekty, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    os.remove(projekty / "C--Projekty-budzik" / "sesja-2.jsonl")

    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 2


def test_ten_sam_plik_zapisany_raz(tmp_path, sciezki, polaczenie):
    from tests.conftest import PNG, zapisz_jsonl, uzytkownik
    import base64

    blok = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode()}}
    katalog = tmp_path / "p2"
    zapisz_jsonl(katalog / "x" / "s.jsonl", [
        uzytkownik([blok, {"type": "text", "text": "pierwszy"}], "2026-09-01T10:00:00Z"),
        uzytkownik([blok, {"type": "text", "text": "drugi"}], "2026-09-01T10:01:00Z"),
    ])

    raport = archiwizuj(polaczenie, sciezki, [ClaudeCode(katalog)])

    assert raport.nowe_zalaczniki == 1
    assert polaczenie.execute("SELECT count(*) FROM zalaczniki").fetchone()[0] == 1
    assert polaczenie.execute("SELECT count(*) FROM wiadomosc_zalacznik").fetchone()[0] == 2


# ---------------------------------------------------------------- nazwa projektu nadana w oknie „pamięć”

def _id(polaczenie, sesja):
    return polaczenie.execute("SELECT id FROM rozmowy WHERE sesja = ?", (sesja,)).fetchone()[0]


def _projekt(polaczenie, sesja):
    return polaczenie.execute("SELECT coalesce(projekt_wlasny, projekt) FROM rozmowy WHERE sesja = ?",
                              (sesja,)).fetchone()[0]


def _sesja_sklep(projekty, sesja, tekst="Nowe pytanie", ts="2026-09-21T10:00:00.000Z"):
    from tests.conftest import uzytkownik

    plik = projekty / "C--Projekty-sklep-pl" / f"{sesja}.jsonl"
    with plik.open("a", encoding="utf-8") as f:
        f.write(json.dumps(uzytkownik(tekst, ts), ensure_ascii=False) + "\n")


def test_nowa_rozmowa_dostaje_nazwe_nadana_projektowi(projekty, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    edycja.ustaw_projekt(polaczenie, [_id(polaczenie, "sesja-1")], "eUTB")
    _sesja_sklep(projekty, "sesja-3")

    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert _projekt(polaczenie, "sesja-3") == "eUTB"
    assert _projekt(polaczenie, "sesja-2") == "budzik"


def test_nowa_rozmowa_bez_nazwy_gdy_przeniesiono_tylko_czesc(projekty, sciezki, polaczenie):
    _sesja_sklep(projekty, "sesja-3")
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    edycja.ustaw_projekt(polaczenie, [_id(polaczenie, "sesja-1")], "SkerimAI")  # jedna rozmowa w innym projekcie
    _sesja_sklep(projekty, "sesja-4")

    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert _projekt(polaczenie, "sesja-4") == "sklep.pl"


def test_nazwa_przywrocona_ze_zrodla_zostaje(projekty, sciezki, polaczenie):
    _sesja_sklep(projekty, "sesja-3")
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    edycja.ustaw_projekt(polaczenie, [_id(polaczenie, "sesja-1"), _id(polaczenie, "sesja-3")], "eUTB")
    edycja.ustaw_projekt(polaczenie, [_id(polaczenie, "sesja-3")], None)
    _sesja_sklep(projekty, "sesja-3", "Kolejne pytanie", "2026-09-21T11:00:00.000Z")

    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert _projekt(polaczenie, "sesja-3") == "sklep.pl"


# ---------------------------------------------------------------- sesje, których nie da się odczytać

def test_uszkodzona_sesja_nie_zatrzymuje_pozostalych_zrodel(projekty, sciezki, polaczenie, tmp_path):
    from skerimai.adaptery.codex import Codex

    katalog = tmp_path / "codex"
    plik = katalog / "2026" / "10" / "01" / "rollout-2026-10-01T21-53-14-01a0f907-360d-71f3-b417-a54ac45251b5.jsonl.zst"
    plik.parent.mkdir(parents=True)
    plik.write_bytes(b"\x28\xb5\x2f\xfd to nie jest poprawna ramka zstd")  # np. obcięty przy pakowaniu

    raport = archiwizuj(polaczenie, sciezki, [Codex(katalog), ClaudeCode(projekty)])

    assert raport.nieczytelne == 1
    assert raport.zaktualizowane == 2  # Claude Code – źródło po uszkodzonym – zarchiwizowany
    assert "nieczytelne sesje: 1" in str(raport)
    # nie trafia do stanu źródeł, więc wróci w następnym przebiegu
    assert polaczenie.execute("SELECT count(*) FROM stan_zrodel WHERE zrodlo = 'codex'").fetchone()[0] == 0


def test_nietypowy_rekord_pomija_tylko_jego_sesje(projekty, sciezki, polaczenie):
    from tests.conftest import zapisz_jsonl

    zapisz_jsonl(projekty / "C--Projekty-inny" / "sesja-0.jsonl",
                 [{"type": "user", "timestamp": "2026-09-01T10:00:00Z", "message": "nie słownik"}])

    raport = archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])

    assert raport.zaktualizowane >= 2
    assert {w[0] for w in polaczenie.execute("SELECT sesja FROM rozmowy")} >= {"sesja-1", "sesja-2"}


def test_hook_zglasza_nieczytelna_sesje_ostrzezeniem(sciezki, tmp_path, caplog):
    import logging

    from skerimai.adaptery.codex import Codex
    from skerimai.hook import archiwizuj_w_tle

    plik = tmp_path / "codex" / "rollout-2026-10-01T21-53-14-01a0f907-360d-71f3-b417-a54ac45251b5.jsonl.zst"
    plik.parent.mkdir(parents=True)
    plik.write_bytes(b"uszkodzony")

    with caplog.at_level(logging.INFO, logger="skerimai.hook"):
        assert archiwizuj_w_tle(sciezki, "test", [Codex(tmp_path / "codex")])

    ostatni = [r for r in caplog.records if r.name == "skerimai.hook"][-1]
    assert ostatni.levelname == "WARNING"


def test_obciety_plik_zst_nie_zastepuje_rozmowy_pusta(sciezki, polaczenie, tmp_path):
    import zstandard

    from skerimai.adaptery.codex import Codex

    rekordy = b"".join(
        b'{"timestamp":"2026-10-01T10:00:%02dZ","type":"response_item","payload":{"type":"message","role":"user",'
        b'"content":[{"type":"input_text","text":"pytanie numer %d"}]}}\n' % (i, i) for i in range(40)
    )
    katalog = tmp_path / "codex"
    plik = katalog / "rollout-2026-10-01T21-53-14-01a0f907-360d-71f3-b417-a54ac45251b5.jsonl.zst"
    plik.parent.mkdir(parents=True)
    spakowane = zstandard.ZstdCompressor().compress(rekordy)
    plik.write_bytes(spakowane)
    archiwizuj(polaczenie, sciezki, [Codex(katalog)])
    assert polaczenie.execute("SELECT count(*) FROM wiadomosci").fetchone()[0] == 40

    plik.write_bytes(spakowane[: len(spakowane) // 2])  # obcięty
    raport = archiwizuj(polaczenie, sciezki, [Codex(katalog)])

    assert raport.nieczytelne == 1
    assert polaczenie.execute("SELECT count(*) FROM wiadomosci").fetchone()[0] == 40
    kopia = sciezki.surowe / "codex" / "01a0f907-360d-71f3-b417-a54ac45251b5.jsonl.gz"
    assert gzip.decompress(kopia.read_bytes()) == rekordy


def test_plik_zst_z_kilkoma_ramkami(tmp_path):
    import zstandard

    from skerimai.adaptery.wspolne import czytaj_bajty

    kompresor = zstandard.ZstdCompressor()
    plik = tmp_path / "s.jsonl.zst"
    plik.write_bytes(kompresor.compress(b'{"a": 1}\n') + kompresor.compress(b'{"b": 2}\n'))

    assert czytaj_bajty(plik) == b'{"a": 1}\n{"b": 2}\n'
