import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skerimai import edycja, serwer_mcp
from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.archiwizator import skrot_sesji
from skerimai.baza import polacz
from skerimai.blokada import blokada
from skerimai.serwer_mcp import dogon_zaleglosci, utworz_serwer


@pytest.fixture
def serwer(projekty, sciezki):
    return utworz_serwer(sciezki, [ClaudeCode(projekty)])


def zadanie(serwer, metoda: str, parametry: dict | None = None) -> dict:
    return serwer.obsluz({"jsonrpc": "2.0", "id": 1, "method": metoda, "params": parametry or {}})


def wywolaj(serwer, narzedzie: str, argumenty: dict | None = None) -> dict:
    return zadanie(serwer, "tools/call", {"name": narzedzie, "arguments": argumenty or {}})["result"]


def tekst(wynik: dict) -> str:
    return "\n".join(c["text"] for c in wynik["content"] if c["type"] == "text")


def instrukcje(serwer) -> str:
    return zadanie(serwer, "initialize", {"protocolVersion": "2025-06-18"})["result"]["instructions"]


def test_udostepnia_narzedzia_tylko_do_odczytu(serwer):
    narzedzia = {n["name"]: n for n in zadanie(serwer, "tools/list")["result"]["tools"]}

    assert set(narzedzia) == {"szukaj_w_historii", "lista_rozmow", "czytaj_rozmowe", "pobierz_zalacznik", "stan_archiwum"}
    assert all(n["annotations"]["readOnlyHint"] for n in narzedzia.values())
    czytaj = narzedzia["czytaj_rozmowe"]["inputSchema"]
    assert czytaj["required"] == ["rozmowa"]
    assert czytaj["properties"]["ile"] == {"type": "integer", "minimum": 1, "maximum": 50, "default": 10,
                                          "description": "Liczba wiadomości"}
    assert narzedzia["szukaj_w_historii"]["inputSchema"]["properties"]["pomin_zrodla"]["anyOf"][1] == {"type": "null"}


def test_instrukcje_serwera_mowia_kiedy_szukac(serwer):
    assert "szukaj_w_historii" in instrukcje(serwer)


def test_uzgadnia_wersje_protokolu(serwer):
    znana = zadanie(serwer, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    nowsza = zadanie(serwer, "initialize", {"protocolVersion": "2026-07-28"})["result"]

    assert znana["protocolVersion"] == "2025-06-18"
    assert nowsza["protocolVersion"] == serwer_mcp.WERSJE_PROTOKOLU[0]
    assert znana["serverInfo"]["name"] == "skerimai"
    assert znana["capabilities"] == {"tools": {"listChanged": False}}


def test_server_discover_od_razu_nieznana_metoda(serwer):
    """Claude Code pyta najpierw o protokół 2026-07-28; szybkie „nie znam” kieruje go od razu na `initialize`."""
    odpowiedz = zadanie(serwer, "server/discover")

    assert odpowiedz["error"]["code"] == -32601


def test_powiadomienia_bez_odpowiedzi(serwer):
    assert serwer.obsluz({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert zadanie(serwer, "ping")["result"] == {}


def test_nieznane_narzedzie(serwer):
    assert zadanie(serwer, "tools/call", {"name": "nie_ma", "arguments": {}})["error"]["code"] == -32602


def test_bledne_argumenty_to_czytelny_blad_narzedzia(serwer):
    bez_numeru = wywolaj(serwer, "czytaj_rozmowe", {"od": 1})
    za_duzo = wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": 1, "ile": 500})
    nie_liczba = wywolaj(serwer, "lista_rozmow", {"limit": "dużo"})

    assert bez_numeru["isError"] and "brak wymaganego argumentu rozmowa" in tekst(bez_numeru)
    assert za_duzo["isError"] and "ile musi być ≤ 50" in tekst(za_duzo)
    assert nie_liczba["isError"] and "limit musi być liczbą całkowitą" in tekst(nie_liczba)


def test_czytanie_rozmowy_przyjmuje_id_i_numer_jako_napis(serwer):
    """Tak wołał model w Supervisor1 30.10.2026: {'id': '337', 'od': 25}."""
    numer = numer_rozmowy_sklep(serwer)

    wynik = wywolaj(serwer, "czytaj_rozmowe", {"id": str(numer), "od": 2, "ile": 1})

    assert not wynik["isError"]
    assert "Sprawdź moją pocztę w Outlook" in tekst(wynik)


def test_szukanie_synchronizuje_archiwum_przy_pierwszym_uzyciu(serwer):
    wynik = wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools"})

    assert not wynik["isError"]
    assert "Problem z NodeJsTools" in tekst(wynik)
    assert "wiadomość 0" in tekst(wynik)


def test_szukanie_z_filtrem_bez_wynikow(serwer):
    wynik = wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools", "projekt": "budzik"})

    assert tekst(wynik).startswith("Brak wyników")


def numer_rozmowy_sklep(serwer) -> int:
    lista = tekst(wywolaj(serwer, "lista_rozmow", {"projekt": "sklep"}))
    return int(lista.split("rozmowa ")[1].split(":")[0])


def test_lista_i_czytanie_rozmowy(serwer):
    numer = numer_rozmowy_sklep(serwer)

    rozmowa = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer, "od": 2, "ile": 1}))

    assert "[2] UŻYTKOWNIK" in rozmowa
    assert "Sprawdź moją pocztę w Outlook" in rozmowa
    assert rozmowa.count("załącznik:") == 2
    assert "[dalej: od=3]" in rozmowa


def test_czytanie_konca_rozmowy_ujemnym_od(serwer):
    """„Na czym skończyliśmy” – końcówka jednym wywołaniem, bez wyliczania numeru z liczby wiadomości."""
    numer = numer_rozmowy_sklep(serwer)

    ostatnia = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer, "od": -1}))
    przedostatnia = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer, "od": -2, "ile": 1}))
    cala = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer, "od": -50}))

    assert "[3] AI" in ostatnia and "[2]" not in ostatnia and "[dalej" not in ostatnia
    assert "[2] UŻYTKOWNIK" in przedostatnia and "[dalej: od=3]" in przedostatnia
    assert all(f"[{lp}]" in cala for lp in range(4))


def test_zalacznik_ukrytej_wiadomosci_i_rozmowy_nie_do_pobrania(serwer, sciezki):
    """Skrót mógł zostać w kontekście agenta sprzed ukrycia – ukryta treść nie może wyjść tą drogą."""
    numer = numer_rozmowy_sklep(serwer)
    rozmowa = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer, "od": 2, "ile": 1}))
    sha = rozmowa.split("sha256 ")[1][:16]
    polaczenie = polacz(sciezki.baza)
    try:
        for ukryj in (lambda u: edycja.ukryj_wiadomosci(polaczenie, numer, [2], u),
                      lambda u: edycja.ukryj_rozmowy(polaczenie, [numer], u)):
            ukryj(True)
            assert "Nie znaleziono" in tekst(wywolaj(serwer, "pobierz_zalacznik", {"sha256": sha}))
            ukryj(False)
            assert wywolaj(serwer, "pobierz_zalacznik", {"sha256": sha})["content"][0]["type"] == "image"
    finally:
        polaczenie.close()


def test_czytanie_rozmowy_konczy_sie_na_limicie_wyniku(serwer, monkeypatch):
    numer = numer_rozmowy_sklep(serwer)
    monkeypatch.setattr(serwer_mcp, "_MAKS_ZNAKOW_WYNIKU", 1)

    rozmowa = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer, "od": 0, "ile": 50}))

    assert "[0] UŻYTKOWNIK" in rozmowa
    assert "[1]" not in rozmowa
    assert "[dalej: od=1 – reszta nie zmieściła się w limicie wyniku]" in rozmowa


def test_pobranie_obrazu(serwer):
    rozmowa = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer_rozmowy_sklep(serwer), "od": 2, "ile": 1}))
    sha = rozmowa.split("sha256 ")[1][:16]

    wynik = wywolaj(serwer, "pobierz_zalacznik", {"sha256": sha})

    assert wynik["content"][0]["type"] == "image"
    assert wynik["content"][0]["mimeType"] == "image/png"


def test_nieznany_zalacznik(serwer):
    wynik = wywolaj(serwer, "pobierz_zalacznik", {"sha256": "00000000deadbeef"})

    assert "Nie znaleziono" in tekst(wynik)


def test_dziala_mimo_bledu_synchronizacji(projekty, sciezki):
    class Zepsuty(ClaudeCode):
        def znajdz_sesje(self):
            raise OSError("dysk niedostępny")

    serwer = utworz_serwer(sciezki, [Zepsuty(projekty)])

    wynik = wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "cokolwiek"})

    assert not wynik["isError"]
    assert tekst(wynik).startswith("Brak wyników")


def test_zajete_archiwum_nie_blokuje_szukania_a_synchronizacja_wraca_pozniej(serwer, sciezki, monkeypatch):
    monkeypatch.setattr(serwer_mcp, "_CZEKAJ_NA_ARCHIWUM_S", 0.2)

    with blokada(sciezki.blokada, 0):
        zajete = wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools"})
    wolne = wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools"})

    assert tekst(zajete).startswith("Brak wyników")
    assert "Problem z NodeJsTools" in tekst(wolne)


def test_dlugo_na_cudza_archiwizacje_czeka_tylko_pierwsze_narzedzie(serwer, sciezki, monkeypatch):
    czekania = []
    prawdziwa = serwer_mcp._synchronizuj
    monkeypatch.setattr(serwer_mcp, "_CZEKAJ_NA_ARCHIWUM_S", 0.3)
    monkeypatch.setattr(serwer_mcp, "_CZEKAJ_NA_ARCHIWUM_POTEM_S", 0.1)
    monkeypatch.setattr(serwer_mcp, "_synchronizuj",
                        lambda polaczenie, sciezki, adaptery, czekaj_s: (
                            czekania.append(czekaj_s), prawdziwa(polaczenie, sciezki, adaptery, czekaj_s))[1])

    with blokada(sciezki.blokada, 0):
        for _ in range(3):
            wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools"})
    wolne = wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools"})

    assert czekania == [0.3, 0.1, 0.1, 0.1]
    assert "Problem z NodeJsTools" in tekst(wolne)


def test_start_serwera_dogania_zaleglosci(projekty, sciezki):
    dogon_zaleglosci(sciezki, [ClaudeCode(projekty)])

    polaczenie = polacz(sciezki.baza)
    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 2
    polaczenie.close()


class KartridzZClaude(ClaudeCode):
    """Te same sesje co w Claude Code, ale jako kartridż KreionAI – do sprawdzenia, co widzi który klient."""

    nazwa = "kreion_kartridz"


@pytest.fixture
def kartridz(projekty, tmp_path):
    """Kopia plików – archiwizator rozpoznaje stan źródła po ścieżce pliku."""
    return KartridzZClaude(Path(shutil.copytree(projekty, tmp_path / "kartridz")))


@pytest.fixture
def serwer_kreion(projekty, kartridz, sciezki):
    return utworz_serwer(sciezki, [ClaudeCode(projekty), kartridz], klient="kreion")


def test_klient_kreion_domyslnie_nie_dostaje_wlasnego_kartridza(serwer_kreion):
    wynik = tekst(wywolaj(serwer_kreion, "szukaj_w_historii", {"zapytanie": "NodeJsTools"}))
    lista = tekst(wywolaj(serwer_kreion, "lista_rozmow"))

    assert "claude_code" in wynik and "kreion_kartridz" not in wynik
    assert "claude_code" in lista and "kreion_kartridz" not in lista


def test_klient_kreion_dostaje_kartridz_na_zyczenie(serwer_kreion):
    wprost = tekst(wywolaj(serwer_kreion, "szukaj_w_historii", {"zapytanie": "NodeJsTools", "zrodlo": "kreion_kartridz"}))
    bez_pomijania = tekst(wywolaj(serwer_kreion, "lista_rozmow", {"pomin_zrodla": []}))

    assert "kreion_kartridz" in wprost
    assert "kreion_kartridz" in bez_pomijania


def test_pomin_zrodla_dla_pozostalych_klientow(projekty, kartridz, sciezki):
    serwer = utworz_serwer(sciezki, [ClaudeCode(projekty), kartridz])

    wszystko = tekst(wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools"}))
    bez_claude = tekst(wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools", "pomin_zrodla": ["claude_code"]}))

    assert "claude_code" in wszystko and "kreion_kartridz" in wszystko
    assert "claude_code" not in bez_claude and "kreion_kartridz" in bez_claude


def test_klient_kreion_ma_wlasne_instrukcje(serwer_kreion, serwer):
    kreion, zwykle = instrukcje(serwer_kreion), instrukcje(serwer)

    assert "kartridż" in kreion and "z zewnątrz" in kreion
    assert kreion != zwykle
    # Powrót do projektu i prośba wprost sięgają do SkerimAI, nie kończą się na kartridżu.
    assert "wróćmy do projektu" in kreion and "sprawdź w SkerimAI" in kreion
    assert "na czym skończyliśmy" in zwykle and "sprawdź w SkerimAI" in zwykle


def test_stan_archiwum_bez_synchronizacji(serwer, sciezki):
    przed = tekst(wywolaj(serwer, "stan_archiwum"))
    wywolaj(serwer, "lista_rozmow")
    po = tekst(wywolaj(serwer, "stan_archiwum"))

    assert "SkerimAI" in przed and "0 rozmów" in przed  # stan sprawdza się bez dociągania sesji
    assert "2 rozmowy" in po and "claude_code 2" in po


def test_start_w_trybie_kreion_zapisuje_czas_polaczenia(sciezki, monkeypatch):
    monkeypatch.setenv("SKERIMAI_DIR", str(sciezki.katalog))
    uruchomione = []
    monkeypatch.setattr(serwer_mcp, "dogon_zaleglosci", lambda *a, **k: None)
    monkeypatch.setattr(serwer_mcp.Serwer, "run", lambda self, *a, **k: uruchomione.append(self))

    serwer_mcp.main(["--klient", "kreion"])

    assert uruchomione
    assert sciezki.polaczenie_kreion.is_file()


def test_zwykly_start_nie_zapisuje_polaczenia_kreion(sciezki, monkeypatch):
    monkeypatch.setenv("SKERIMAI_DIR", str(sciezki.katalog))
    monkeypatch.setattr(serwer_mcp, "dogon_zaleglosci", lambda *a, **k: None)
    monkeypatch.setattr(serwer_mcp.Serwer, "run", lambda self, *a, **k: None)

    serwer_mcp.main([])

    assert not sciezki.polaczenie_kreion.exists()


def adres(zrodlo: str, sesja: str) -> str:
    return skrot_sesji(zrodlo, sesja)[:16]


def test_klient_kreion_dostaje_tresc_ze_znacznikiem_zrodla(serwer_kreion):
    wynik = tekst(wywolaj(serwer_kreion, "szukaj_w_historii", {"zapytanie": "NodeJsTools"}))

    poczatek = f"[skerimai zrodlo=claude_code sesja={adres('claude_code', 'sesja-1')} wiad=0 ts=2026-09-27T19:00:00"
    assert poczatek in wynik
    assert "NodeJsTools" in wynik.split(poczatek)[1].split("[/skerimai]")[0]


def test_czytanie_rozmowy_przez_kreion_ma_znacznik_przy_kazdej_wiadomosci(serwer_kreion):
    numer = int(tekst(wywolaj(serwer_kreion, "lista_rozmow", {"projekt": "sklep"})).split("rozmowa ")[1].split(":")[0])

    rozmowa = tekst(wywolaj(serwer_kreion, "czytaj_rozmowe", {"rozmowa": numer, "od": 1, "ile": 2}))

    assert rozmowa.count("[skerimai zrodlo=claude_code") == 2
    assert rozmowa.count("[/skerimai]") == 2
    assert f"sesja={adres('claude_code', 'sesja-1')} wiad=1 " in rozmowa
    assert "Sprawdzam log instalacji." in rozmowa


def test_ukryta_wiadomosc_dla_kreion_bez_znacznika(serwer_kreion, sciezki):
    numer = int(tekst(wywolaj(serwer_kreion, "lista_rozmow", {"projekt": "sklep"})).split("rozmowa ")[1].split(":")[0])
    polaczenie = polacz(sciezki.baza)
    with polaczenie:
        polaczenie.execute("INSERT INTO ukryte_wiadomosci (rozmowa_id, lp) VALUES (?, 1)", (numer,))
    polaczenie.close()

    rozmowa = tekst(wywolaj(serwer_kreion, "czytaj_rozmowe", {"rozmowa": numer, "od": 1, "ile": 1}))

    assert "[wiadomość ukryta przez użytkownika]" in rozmowa
    assert "[skerimai" not in rozmowa


def test_pozostali_klienci_bez_znacznika(serwer):
    wynik = tekst(wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "NodeJsTools"}))
    rozmowa = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": numer_rozmowy_sklep(serwer)}))

    assert "[skerimai" not in wynik and "[skerimai" not in rozmowa


def test_rozmowa_po_stdio(sciezki):
    """Prawdziwy proces jak w Claude Code: najpierw `server/discover`, potem `initialize`, lista i wywołanie."""
    wiadomosci = [
        {"jsonrpc": "2.0", "id": 0, "method": "server/discover", "params": {}},
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "stan_archiwum", "arguments": {}}},
    ]
    wejscie = "".join(json.dumps(w, ensure_ascii=False) + "\n" for w in wiadomosci).encode("utf-8")

    wynik = subprocess.run([sys.executable, "-m", "skerimai.serwer_mcp"], input=wejscie, capture_output=True,
                           timeout=60, env={**os.environ, "SKERIMAI_DIR": str(sciezki.katalog)}, check=True)

    odpowiedzi = {o["id"]: o for o in map(json.loads, wynik.stdout.decode("utf-8").splitlines())}
    assert odpowiedzi[0]["error"]["code"] == -32601
    assert odpowiedzi[1]["result"]["protocolVersion"] == "2025-06-18"
    assert len(odpowiedzi[2]["result"]["tools"]) == 5
    assert "SkerimAI" in odpowiedzi[3]["result"]["content"][0]["text"]


def test_kazde_zadanie_dostaje_odpowiedz_mimo_blednych_parametrow(serwer):
    """Klient czeka na odpowiedź do każdego żądania z id – brak odpowiedzi to zawieszone wywołanie."""
    import io

    wiadomosci = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": ["pozycyjne"]},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": ["lista"], "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "stan_archiwum", "arguments": [1]}},
        {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": "tekst"},
    ]
    wejscie = io.BytesIO("".join(json.dumps(w) + "\n" for w in wiadomosci).encode("utf-8"))
    wyjscie = io.BytesIO()

    serwer.run(wejscie, wyjscie)

    odpowiedzi = {o["id"]: o for o in map(json.loads, wyjscie.getvalue().decode("utf-8").splitlines())}
    assert set(odpowiedzi) == {1, 2, 3, 4}
    assert all(o["error"]["code"] == -32602 for o in odpowiedzi.values())


def test_nieoczekiwany_wyjatek_daje_blad_wewnetrzny(serwer, monkeypatch):
    import io

    def awaria(wiadomosc):
        raise RuntimeError("awaria")

    monkeypatch.setattr(serwer, "obsluz", awaria)
    wyjscie = io.BytesIO()

    serwer.run(io.BytesIO(b'{"jsonrpc": "2.0", "id": 7, "method": "ping"}\n'), wyjscie)

    assert json.loads(wyjscie.getvalue())["error"]["code"] == -32603


def test_obraz_ktorego_nie_da_sie_odczytac_to_blad_narzedzia(serwer):
    from pathlib import Path

    serwer.narzedzia["stan_archiwum"].funkcja = lambda: serwer_mcp.Obraz(Path("/nie/ma/takiego.png"), "image/png")

    wynik = wywolaj(serwer, "stan_archiwum")

    assert wynik["isError"] is True


def test_bledna_data_to_blad_argumentu(serwer):
    for narzedzie, argumenty in (("szukaj_w_historii", {"zapytanie": "NodeJsTools", "od": "wczoraj"}),
                                 ("lista_rozmow", {"do": "2026-13-01"})):
        wynik = wywolaj(serwer, narzedzie, argumenty)

        assert wynik["isError"] is True
        assert "Błędny argument" in tekst(wynik) and "RRRR-MM-DD" in tekst(wynik)
