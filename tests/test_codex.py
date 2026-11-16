import base64
from pathlib import Path

import pytest
import zstandard

from skerimai.adaptery.codex import Codex
from skerimai.model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK

from .conftest import PNG, zapisz_jsonl

ID = "019ee18e-9461-7843-bba5-9d683f6f609e"


def wiadomosc(rola, bloki, ts):
    return {"timestamp": ts, "type": "response_item", "payload": {"type": "message", "role": rola, "content": bloki}}


def tekst_uzytkownika(tekst, ts):
    return wiadomosc("user", [{"type": "input_text", "text": tekst}], ts)


def tekst_asystenta(tekst, ts, faza="final_answer"):
    rekord = wiadomosc("assistant", [{"type": "output_text", "text": tekst}], ts)
    rekord["payload"]["phase"] = faza
    return rekord


@pytest.fixture
def sesje_codex(tmp_path: Path) -> Path:
    katalog = tmp_path / "sessions"
    png_b64 = base64.b64encode(PNG).decode()
    zapisz_jsonl(
        katalog / "2026" / "06" / "19" / f"rollout-2026-06-19T22-24-38-{ID}.jsonl",
        [
            {"timestamp": "2026-06-19T20:24:38.000Z", "type": "session_meta",
             "payload": {"id": ID, "cwd": r"C:\Projekty\hr", "git": {"branch": "refactor/rbac"}}},
            wiadomosc("developer", [{"type": "input_text", "text": "<permissions instructions>sandbox</permissions instructions>"}],
                      "2026-06-19T20:24:39.000Z"),
            tekst_uzytkownika("# AGENTS.md instructions for C:\\Projekty\\hr\n\n<INSTRUCTIONS>zasady</INSTRUCTIONS>",
                              "2026-06-19T20:24:39.100Z"),
            tekst_uzytkownika("<environment_context>\n  <cwd>C:\\Projekty\\hr</cwd>\n</environment_context>",
                              "2026-06-19T20:24:39.200Z"),
            {"timestamp": "2026-06-19T20:26:00.000Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "Czy możesz sprawdzić gałąź rbac?"}},
            wiadomosc("user", [{"type": "input_text", "text": "Czy możesz sprawdzić gałąź rbac?"},
                               {"type": "input_image", "image_url": f"data:image/png;base64,{png_b64}"}],
                      "2026-06-19T20:26:00.100Z"),
            tekst_asystenta("Sprawdzam historię gałęzi.", "2026-06-19T20:26:05.000Z", "commentary"),
            {"timestamp": "2026-06-19T20:26:06.000Z", "type": "response_item",
             "payload": {"type": "function_call", "name": "shell_command", "arguments": "{}"}},
            {"timestamp": "2026-06-19T20:26:07.000Z", "type": "response_item",
             "payload": {"type": "function_call_output", "output": "wynik narzędzia"}},
            tekst_asystenta("Gałąź rbac ujednolica przechowywanie uprawnień.", "2026-06-19T20:27:00.000Z"),
            tekst_uzytkownika("<turn_aborted>\nThe user interrupted the previous turn.\n</turn_aborted>",
                              "2026-06-19T20:28:00.000Z"),
            tekst_uzytkownika("<div>wklejony HTML</div>", "2026-06-19T20:29:00.000Z"),
        ],
    )
    return katalog


def test_id_sesji_z_nazwy_pliku(sesje_codex):
    assert set(Codex(sesje_codex).znajdz_sesje()) == {ID}


def test_wyciaga_tylko_rozmowe_bez_wstawek_codex(sesje_codex):
    adapter = Codex(sesje_codex)
    rozmowa = adapter.wczytaj(ID, adapter.znajdz_sesje()[ID])

    assert [(w.rola, w.tekst) for w in rozmowa.wiadomosci] == [
        (ROLA_UZYTKOWNIK, "Czy możesz sprawdzić gałąź rbac?"),
        (ROLA_ASYSTENT, "Sprawdzam historię gałęzi.\n\nGałąź rbac ujednolica przechowywanie uprawnień."),
        (ROLA_UZYTKOWNIK, "<div>wklejony HTML</div>"),
    ]


def test_obraz_z_data_url(sesje_codex):
    adapter = Codex(sesje_codex)
    rozmowa = adapter.wczytaj(ID, adapter.znajdz_sesje()[ID])

    [obraz] = rozmowa.wiadomosci[0].zalaczniki
    assert (obraz.mime, obraz.dane) == ("image/png", PNG)


def test_metadane_rozmowy(sesje_codex):
    adapter = Codex(sesje_codex)
    rozmowa = adapter.wczytaj(ID, adapter.znajdz_sesje()[ID])

    assert rozmowa.zrodlo == "codex"
    assert rozmowa.projekt == "hr"
    assert rozmowa.galaz == "refactor/rbac"
    assert rozmowa.tytul == "Czy możesz sprawdzić gałąź rbac?"
    assert rozmowa.pierwsza_ts == "2026-06-19T20:26:00.100Z"


def test_brak_katalogu(tmp_path):
    assert Codex(tmp_path / "nie-ma").znajdz_sesje() == {}


def skompresuj(plik: Path) -> Path:
    """Jak Codex od wersji z października 2026: zamyka sesję jako `rollout-….jsonl.zst`."""
    spakowany = plik.with_name(plik.name + ".zst")
    spakowany.write_bytes(zstandard.ZstdCompressor().compress(plik.read_bytes()))
    plik.unlink()
    return spakowany


def test_sesja_skompresowana_zstd(sesje_codex):
    [plik] = sesje_codex.rglob("*.jsonl")
    skompresuj(plik)
    adapter = Codex(sesje_codex)

    sesje = adapter.znajdz_sesje()
    assert set(sesje) == {ID}
    rozmowa = adapter.wczytaj(ID, sesje[ID])
    assert rozmowa.tytul == "Czy możesz sprawdzić gałąź rbac?"
    assert len(rozmowa.wiadomosci) == 3


def test_sesja_w_trakcie_kompresji_tylko_raz(sesje_codex):
    """Przez chwilę są oba pliki – wtedy czytamy niespakowany, żeby nie było każdej wiadomości dwa razy."""
    [plik] = sesje_codex.rglob("*.jsonl")
    plik.with_name(plik.name + ".zst").write_bytes(zstandard.ZstdCompressor().compress(plik.read_bytes()))

    assert Codex(sesje_codex).znajdz_sesje() == {ID: [plik]}


def test_naglowek_agents_bez_katalogu(tmp_path):
    """Nowszy Codex pisze „# AGENTS.md instructions” bez „for <katalog>”."""
    zapisz_jsonl(
        tmp_path / "2026" / "10" / "01" / f"rollout-2026-10-01T21-53-14-{ID}.jsonl",
        [
            tekst_uzytkownika("# AGENTS.md instructions\n\n<INSTRUCTIONS>zasady</INSTRUCTIONS>",
                              "2026-10-01T19:53:45.000Z"),
            tekst_uzytkownika("co wiesz na temat karty Pompa KSM", "2026-10-01T19:53:46.000Z"),
        ],
    )
    adapter = Codex(tmp_path)
    rozmowa = adapter.wczytaj(ID, adapter.znajdz_sesje()[ID])

    assert rozmowa.tytul == "co wiesz na temat karty Pompa KSM"


def test_pomija_bloki_w_nietypowym_ksztalcie(tmp_path):
    def wiadomosc(rola, bloki, ts):
        return {"timestamp": ts, "type": "response_item", "payload": {"type": "message", "role": rola, "content": bloki}}

    plik = zapisz_jsonl(tmp_path / "rollout-x.jsonl", [
        wiadomosc("user", [{"type": "input_text", "text": "pytanie"}, {"type": "input_text", "text": None}],
                  "2026-10-01T10:00:00Z"),
        wiadomosc("assistant", ["napis", {"type": "output_text", "text": 3}, {"type": "output_text", "text": "jest"}],
                  "2026-10-01T10:00:01Z"),
        {"timestamp": 17, "type": "event_msg", "payload": {}},
    ])

    rozmowa = Codex(tmp_path).wczytaj("x", [plik])

    assert [w.tekst for w in rozmowa.wiadomosci] == ["pytanie", "jest"]
