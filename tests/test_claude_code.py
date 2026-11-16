import json

from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK


def test_znajduje_tylko_sesje_glowne(projekty):
    sesje = ClaudeCode(projekty).znajdz_sesje()

    assert set(sesje) == {"sesja-1", "sesja-2"}


def test_wyciaga_tylko_rozmowe_bez_danych_technicznych(projekty):
    adapter = ClaudeCode(projekty)
    rozmowa = adapter.wczytaj("sesja-1", adapter.znajdz_sesje()["sesja-1"])

    assert [w.rola for w in rozmowa.wiadomosci] == [ROLA_UZYTKOWNIK, ROLA_ASYSTENT, ROLA_UZYTKOWNIK, ROLA_ASYSTENT]
    pierwsza = rozmowa.wiadomosci[0]
    assert pierwsza.tekst == "Nie działa instalacja NodeJsTools w KreionAI"
    assert "tajne instrukcje" not in pierwsza.tekst


def test_laczy_odpowiedz_asystenta_z_calej_tury(projekty):
    adapter = ClaudeCode(projekty)
    rozmowa = adapter.wczytaj("sesja-1", adapter.znajdz_sesje()["sesja-1"])

    odpowiedz = rozmowa.wiadomosci[1].tekst
    assert odpowiedz == "Sprawdzam log instalacji.\n\nWinny jest limit czasu pobierania paczki codex."
    assert "myślę" not in odpowiedz


def test_obrazy_z_base64_i_ze_sciezki(projekty, obraz_na_dysku):
    adapter = ClaudeCode(projekty)
    rozmowa = adapter.wczytaj("sesja-1", adapter.znajdz_sesje()["sesja-1"])

    wiadomosc = rozmowa.wiadomosci[2]
    assert wiadomosc.tekst == "Sprawdź moją pocztę w Outlook"
    assert {z.nazwa for z in wiadomosc.zalaczniki} == {"obraz-1", "1.png"}
    assert any(z.dane and z.mime == "image/png" for z in wiadomosc.zalaczniki)


def test_metadane_rozmowy(projekty):
    adapter = ClaudeCode(projekty)
    rozmowa = adapter.wczytaj("sesja-1", adapter.znajdz_sesje()["sesja-1"])

    assert rozmowa.tytul == "Problem z NodeJsTools"
    assert rozmowa.projekt == "sklep.pl"
    assert rozmowa.galaz == "main"
    assert rozmowa.pierwsza_ts == "2026-09-27T19:00:00.000Z"
    assert rozmowa.ostatnia_ts == "2026-09-27T19:02:05.000Z"


def test_tytul_z_pierwszej_wiadomosci_gdy_brak_wlasnego(projekty):
    adapter = ClaudeCode(projekty)
    rozmowa = adapter.wczytaj("sesja-2", adapter.znajdz_sesje()["sesja-2"])

    assert rozmowa.tytul == "Grzałka utrzymuje cache budzika"
    assert rozmowa.projekt == "budzik"


def test_pomija_uszkodzona_linie(tmp_path):
    plik = tmp_path / "p" / "s.jsonl"
    plik.parent.mkdir()
    plik.write_text('{"type":"user","message":{"content":"cześć"},"timestamp":"t"}\n{"type":"assis', encoding="utf-8")

    rozmowa = ClaudeCode(tmp_path).wczytaj("s", [plik])

    assert [w.tekst for w in rozmowa.wiadomosci] == ["cześć"]


def test_wklejony_tekst_bez_znacznikow(tmp_path):
    plik = tmp_path / "p" / "s.jsonl"
    wklejka = '<pasted_content id="82e3">\nPS C:\\> Get-Content log\nINFO hook\n</pasted_content id="82e3">'
    plik.parent.mkdir()
    plik.write_text(json.dumps({"type": "user", "message": {"content": wklejka}, "timestamp": "t"}) + "\n", encoding="utf-8")

    rozmowa = ClaudeCode(tmp_path).wczytaj("s", [plik])

    assert [w.tekst for w in rozmowa.wiadomosci] == ["PS C:\\> Get-Content log\nINFO hook"]
    assert rozmowa.tytul == "PS C:\\> Get-Content log INFO hook"


def test_wiadomosc_napisana_w_trakcie_pracy_agenta(tmp_path):
    """Wiadomość wysłana, gdy agent jeszcze pracuje, nie ma rekordu `user` – jest tylko w `attachment` typu
    `queued_command`. Komunikaty techniczne w tej samej kolejce (koniec zadania w tle, subagent) to nie rozmowa."""
    def kolejka(ts: str, **pola) -> dict:
        return {"type": "attachment", "timestamp": ts, "attachment": {"type": "queued_command", "timestamp": ts, **pola}}

    obraz = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    rekordy = [
        {"type": "user", "message": {"content": "zrób raport"}, "timestamp": "2026-10-01T10:00:00Z"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Zaczynam."}]},
         "timestamp": "2026-10-01T10:00:01Z"},
        kolejka("2026-10-01T10:00:02Z", prompt="dodaj też wykres", commandMode="prompt", origin={"kind": "human"}),
        kolejka("2026-10-01T10:00:03Z", prompt="<task-notification>koniec</task-notification>",
                commandMode="task-notification", origin={"kind": "task-notification"}),
        kolejka("2026-10-01T10:00:04Z", prompt="<agent-message>wynik</agent-message>", commandMode="prompt",
                origin={"kind": "peer"}, isMeta=True),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Dodaję wykres."}]},
         "timestamp": "2026-10-01T10:00:05Z"},
        kolejka("2026-10-01T10:00:06Z", prompt=[{"type": "text", "text": "[Image #1] taki jak tu"}, obraz],
                commandMode="prompt", origin={"kind": "human"}),
        {**kolejka("2026-10-01T10:00:07Z", prompt="z subagenta", commandMode="prompt", origin={"kind": "human"}),
         "isSidechain": True},
        kolejka("2026-10-01T10:00:08Z", prompt=None, commandMode="prompt", origin={"kind": "human"}),
    ]
    plik = tmp_path / "p" / "s.jsonl"
    plik.parent.mkdir()
    plik.write_text("\n".join(json.dumps(r) for r in rekordy), encoding="utf-8")

    rozmowa = ClaudeCode(tmp_path).wczytaj("s", [plik])

    assert [(w.rola, w.tekst) for w in rozmowa.wiadomosci] == [
        (ROLA_UZYTKOWNIK, "zrób raport"), (ROLA_ASYSTENT, "Zaczynam."), (ROLA_UZYTKOWNIK, "dodaj też wykres"),
        (ROLA_ASYSTENT, "Dodaję wykres."), (ROLA_UZYTKOWNIK, "[Image #1] taki jak tu"),
    ]
    assert rozmowa.wiadomosci[2].ts == "2026-10-01T10:00:02Z"
    assert [z.nazwa for z in rozmowa.wiadomosci[4].zalaczniki] == ["obraz-1"]


def test_pomija_rekordy_w_nietypowym_ksztalcie(tmp_path):
    """Jeden dziwny rekord nie może wyrzucić całej sesji – reszta rozmowy ma trafić do archiwum."""
    rekordy = [
        {"type": "user", "message": {"content": "pierwsza"}, "timestamp": "2026-10-01T10:00:00Z"},
        {"type": "user", "message": None, "timestamp": "2026-10-01T10:00:01Z"},
        {"type": "user", "message": "napis", "timestamp": "2026-10-01T10:00:02Z"},
        {"type": "user", "message": {"content": ["blok-napis", {"type": "text", "text": None},
                                                 {"type": "image", "source": "zly"}]}, "timestamp": 5},
        {"type": "assistant", "message": {"content": ["napis", {"type": "text", "text": 7},
                                                      {"type": "text", "text": "odpowiedź"}]},
         "timestamp": "2026-10-01T10:00:03Z"},
        {"type": "user", "message": {"content": "druga"}, "timestamp": "2026-10-01T10:00:04Z"},
    ]
    plik = tmp_path / "p" / "s.jsonl"
    plik.parent.mkdir()
    plik.write_text("\n".join(json.dumps(r) for r in rekordy), encoding="utf-8")

    rozmowa = ClaudeCode(tmp_path).wczytaj("s", [plik])

    assert [w.tekst for w in rozmowa.wiadomosci] == ["pierwsza", "odpowiedź", "druga"]
