import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from skerimai import instalacja, zasobnik
from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.archiwizator import archiwizuj
from skerimai.baza import polacz
from skerimai.blokada import blokada
from skerimai.instalacja import (
    BLAD,
    INFO,
    OK,
    UWAGA,
    BladInstalacji,
    Miejsca,
    Program,
    instaluj,
    konfiguruj,
    sprawdz,
    wstaw_wskazowke,
)
from skerimai.konfiguracja import Sciezki

# Wskazówka dokładnie w takiej postaci, w jakiej leży w prawdziwym ~\.claude\CLAUDE.md (CRLF, puste linie na początku).
CLAUDE_MD = (
    "\r\n\r\n# graphify\r\n- **graphify** (`~/.claude/skills/graphify/SKILL.md`) - any input to knowledge graph.\r\n"
    "When the user types `/graphify`, invoke the Skill tool.\r\n\r\n"
    "# SkerimAI – archiwum wcześniejszych rozmów\r\n"
    "Gdy użytkownik nawiązuje do wcześniejszych rozmów, ustaleń, decyzji lub plików („jak ustaliliśmy”, „pamiętasz…”, "
    "„co robiliśmy z…”), najpierw użyj narzędzia `szukaj_w_historii` z serwera MCP `skerimai`, a dopiero potem "
    "odpowiadaj.\r\n"
    "Gdy wraca do projektu („przypomnij sobie projekt”, „wróćmy do projektu”, „na czym skończyliśmy”, „co jest "
    "do zrobienia”), zanim zajrzysz do plików, wywołaj `lista_rozmow` z nazwą projektu (katalogu roboczego) "
    "i doczytaj przez `czytaj_rozmowe` końcówkę ostatniej rozmowy z pracą nad projektem – bieżącą i krótkie "
    "rozmowy na boczny temat pomiń. Kod SkerimAI: `C:\\Projekty\\SkerimAI`.\r\n"
)


class UdawaneCli:
    """`claude mcp` i `codex mcp`: zapisuje wywołania i zmienia pliki jak prawdziwe programy."""

    def __init__(self, miejsca: Miejsca):
        self.miejsca = miejsca
        self.wywolania: list[list[str]] = []

    def __call__(self, polecenie: list[str]) -> subprocess.CompletedProcess:
        self.wywolania.append(polecenie)
        program, _, akcja, *reszta = polecenie
        if program == "claude":
            plik = self.miejsca.claude_json
            dane = json.loads(plik.read_text(encoding="utf-8")) if plik.exists() else {"numStartups": 7}
            serwery = dane.setdefault("mcpServers", {})
            if akcja == "add":
                serwery[reszta[-2]] = {"type": "stdio", "command": reszta[-1], "args": [], "env": {}}
            else:
                del serwery[reszta[-1]]
            plik.write_text(json.dumps(dane), encoding="utf-8")
        else:
            plik = self.miejsca.config_codex
            serwery = tomllib.loads(plik.read_text(encoding="utf-8")).get("mcp_servers", {}) if plik.exists() else {}
            if akcja == "add":
                serwery[reszta[0]] = {"command": reszta[-1]}
            else:
                del serwery[reszta[0]]
            plik.write_text("".join(f"[mcp_servers.{n}]\ncommand = '{s['command']}'\n" for n, s in serwery.items()),
                            encoding="utf-8")
        return subprocess.CompletedProcess(polecenie, 0, "", "")


@pytest.fixture
def miejsca(tmp_path) -> Miejsca:
    m = Miejsca(tmp_path / "dom")
    m.claude.mkdir(parents=True)
    m.codex.mkdir()
    m.opencode.mkdir(parents=True)
    return m


@pytest.fixture
def program(tmp_path) -> Program:
    skrypty = tmp_path / "venv" / "Scripts"
    skrypty.mkdir(parents=True)
    p = Program(skrypty, kod=tmp_path / "SkerimAI")
    p.hook.touch()
    p.serwer_mcp.touch()
    p.zasobnik.touch()
    return p


@pytest.fixture
def cli(miejsca) -> UdawaneCli:
    return UdawaneCli(miejsca)


def stany(wyniki) -> dict[str, str]:
    return {w.nazwa: w.stan for w in wyniki}


def test_konfiguruje_wszystko_na_czystym_komputerze(miejsca, program, cli):
    wyniki = konfiguruj(miejsca, program, wykonaj=cli)

    assert stany(wyniki) == {
        "Hook SessionEnd w Claude Code": "dodano",
        "Czas na start serwera MCP w Claude Code": "dodano",
        "Serwer MCP w Claude Code": "dodano",
        "Serwer MCP w Codex": "dodano",
        "Serwer MCP w OpenCode": "dodano",
        "Wskazówka w ~\\.claude\\CLAUDE.md": "dodano",
        "Wskazówka w ~\\.codex\\AGENTS.md": "dodano",
        "Powiadomienia Windows": "pominięto",  # SKERIMAI_POWIADOMIENIA=0 z conftest – testy nie ruszają rejestru
        "Autostart ikony w zasobniku": "dodano" if os.name == "nt" else "pominięto",  # klucz testowy z conftest
    }
    ustawienia = json.loads(miejsca.ustawienia_claude.read_text(encoding="utf-8"))
    assert ustawienia["hooks"]["SessionEnd"] == [{"hooks": [
        {"type": "command", "command": program.hook.as_posix(), "args": ["hook"], "timeout": 5}]}]
    assert ustawienia["env"] == {"MCP_TIMEOUT": "60000"}
    assert cli.wywolania == [
        ["claude", "mcp", "add", "--scope", "user", "skerimai", str(program.serwer_mcp)],
        ["codex", "mcp", "add", "skerimai", "--", str(program.serwer_mcp)],
    ]
    for plik in (miejsca.claude_md, miejsca.agents_md):
        assert plik.read_text(encoding="utf-8").splitlines() == instalacja.tekst_wskazowki(program)
    assert not list(miejsca.dom.rglob("*.skerimai.tmp"))
    if os.name == "nt":
        assert zasobnik.wpis_autostartu() == f'"{program.zasobnik}"'


def test_ponowna_konfiguracja_niczego_nie_zmienia(miejsca, program, cli):
    konfiguruj(miejsca, program, wykonaj=cli)
    pliki = {p: p.read_bytes() for p in miejsca.dom.rglob("*") if p.is_file()}
    cli.wywolania.clear()

    wyniki = konfiguruj(miejsca, program, wykonaj=cli)

    assert {w.stan for w in wyniki} == {"bez zmian", "pominięto"}
    assert cli.wywolania == []
    assert {p: p.read_bytes() for p in miejsca.dom.rglob("*") if p.is_file()} == pliki


def test_zachowuje_cudze_ustawienia_i_hooki(miejsca, program, cli):
    cudze = {
        "model": "opus",
        "hooks": {
            "SessionEnd": [{"hooks": [{"type": "command", "command": "inny.exe"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "telegram.exe"}]}],
        },
    }
    miejsca.ustawienia_claude.write_text(json.dumps(cudze), encoding="utf-8")

    konfiguruj(miejsca, program, wykonaj=cli)

    ustawienia = json.loads(miejsca.ustawienia_claude.read_text(encoding="utf-8"))
    assert ustawienia["model"] == "opus"
    assert ustawienia["hooks"]["Stop"] == cudze["hooks"]["Stop"]
    assert [h["command"] for g in ustawienia["hooks"]["SessionEnd"] for h in g["hooks"]] == ["inny.exe", program.hook.as_posix()]


def test_aktualizuje_wpisy_ze_starej_lokalizacji(miejsca, program, cli, tmp_path):
    stary = Program(tmp_path / "stary" / "Scripts")
    stary.skrypty.mkdir(parents=True)
    stary.hook.touch()
    stary.serwer_mcp.touch()
    konfiguruj(miejsca, stary, wykonaj=cli)
    cli.wywolania.clear()

    wyniki = konfiguruj(miejsca, program, wykonaj=cli)

    assert stany(wyniki)["Hook SessionEnd w Claude Code"] == "zaktualizowano"
    assert stany(wyniki)["Serwer MCP w Claude Code"] == "zaktualizowano"
    assert stany(wyniki)["Serwer MCP w Codex"] == "zaktualizowano"
    assert stany(wyniki)["Wskazówka w ~\\.claude\\CLAUDE.md"] == "zaktualizowano"  # inny katalog z kodem
    assert cli.wywolania[0] == ["claude", "mcp", "remove", "--scope", "user", "skerimai"]
    wpisy = {w.nazwa: w.stan for w in sprawdz(miejsca, program, Sciezki(tmp_path / "brak"), [])}
    assert {wpisy[n] for n in stany(wyniki) if n in wpisy and n not in ("Powiadomienia Windows",)} == {OK}


def test_usuwa_wszystkie_wpisy_i_zostawia_reszte(miejsca, program, cli):
    miejsca.ustawienia_claude.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    miejsca.claude_md.write_text("# Moje zasady\nPisz po polsku.\n", encoding="utf-8")
    konfiguruj(miejsca, program, wykonaj=cli)

    wyniki = konfiguruj(miejsca, program, usun=True, wykonaj=cli)

    assert {w.stan for w in wyniki} == {"usunięto", "pominięto"}
    assert json.loads(miejsca.ustawienia_claude.read_text(encoding="utf-8")) == {"model": "opus"}
    assert miejsca.claude_md.read_text(encoding="utf-8") == "# Moje zasady\nPisz po polsku.\n"
    assert json.loads(miejsca.claude_json.read_text(encoding="utf-8"))["mcpServers"] == {}
    assert {w.stan for w in konfiguruj(miejsca, program, usun=True, wykonaj=cli)} == {"brak", "pominięto"}


def test_pomija_codex_gdy_go_nie_ma(miejsca, program, cli):
    miejsca.codex.rmdir()

    wyniki = konfiguruj(miejsca, program, wykonaj=cli)

    assert stany(wyniki)["Serwer MCP w Codex"] == "pominięto"
    assert stany(wyniki)["Wskazówka w AGENTS.md"] == "pominięto"
    assert all(p[0] == "claude" for p in cli.wywolania)
    assert not miejsca.codex.exists()


def test_opencode_dopisuje_do_istniejacego_pliku_i_usuwa_tylko_swoje(miejsca, program, cli):
    plik = miejsca.opencode / "opencode.jsonc"
    cudze = {"$schema": "https://opencode.ai/config.json", "mcp": {"servers": {"inny": {"type": "local", "command": ["x"]}}}}
    plik.write_text(json.dumps(cudze), encoding="utf-8")

    konfiguruj(miejsca, program, wykonaj=cli)

    serwery = json.loads(plik.read_text(encoding="utf-8"))["mcp"]["servers"]
    assert serwery["skerimai"] == {"type": "local", "command": [str(program.serwer_mcp)]}
    assert serwery["inny"] == cudze["mcp"]["servers"]["inny"]
    assert not (miejsca.opencode / "opencode.json").exists()

    konfiguruj(miejsca, program, usun=True, wykonaj=cli)

    assert json.loads(plik.read_text(encoding="utf-8")) == cudze


def test_opencode_nie_rusza_pliku_z_komentarzami(miejsca, program, cli):
    plik = miejsca.opencode / "opencode.jsonc"
    tekst = '{\n  // moje ustawienia\n  "theme": "dark"\n}\n'
    plik.write_text(tekst, encoding="utf-8")

    wyniki = konfiguruj(miejsca, program, wykonaj=cli)

    assert stany(wyniki)["Serwer MCP w OpenCode"] == BLAD
    assert plik.read_text(encoding="utf-8") == tekst


def test_pomija_opencode_gdy_go_nie_ma(miejsca, program, cli):
    miejsca.opencode.rmdir()

    wyniki = konfiguruj(miejsca, program, wykonaj=cli)

    assert stany(wyniki)["Serwer MCP w OpenCode"] == "pominięto"
    assert not miejsca.opencode.exists()


def test_brak_cli_to_blad_z_poleceniem_do_wykonania_recznie(miejsca, program):
    def bez_cli(polecenie):
        raise FileNotFoundError(polecenie[0])

    wyniki = {w.nazwa: w for w in konfiguruj(miejsca, program, wykonaj=bez_cli)}

    serwer = wyniki["Serwer MCP w Claude Code"]
    assert serwer.stan == BLAD
    assert f"claude mcp add --scope user skerimai {program.serwer_mcp}" in serwer.opis
    assert wyniki["Hook SessionEnd w Claude Code"].stan == "dodano"  # reszta i tak się konfiguruje


def test_nieudane_polecenie_cli(miejsca, program):
    wyniki = {w.nazwa: w for w in konfiguruj(
        miejsca, program, wykonaj=lambda p: subprocess.CompletedProcess(p, 1, "", "Error: config locked"))}

    assert wyniki["Serwer MCP w Codex"].stan == BLAD
    assert "config locked" in wyniki["Serwer MCP w Codex"].opis


def test_nie_rusza_nieczytelnego_settings_json(miejsca, program, cli):
    miejsca.ustawienia_claude.write_text('{"model": "opus",', encoding="utf-8")

    wyniki = stany(konfiguruj(miejsca, program, wykonaj=cli))

    assert wyniki["Hook SessionEnd w Claude Code"] == BLAD
    assert miejsca.ustawienia_claude.read_text(encoding="utf-8") == '{"model": "opus",'


def test_czas_na_start_serwera_mcp_nie_rusza_cudzych_ustawien(miejsca, program, cli):
    def czas():
        return json.loads(miejsca.ustawienia_claude.read_text(encoding="utf-8")).get("env", {}).get("MCP_TIMEOUT")

    nazwa = "Czas na start serwera MCP w Claude Code"
    miejsca.ustawienia_claude.write_text(json.dumps({"env": {"MCP_TIMEOUT": "10000", "INNA": "1"}}), encoding="utf-8")
    assert stany(konfiguruj(miejsca, program, wykonaj=cli))[nazwa] == "zaktualizowano"
    assert czas() == "60000"
    assert stany(konfiguruj(miejsca, program, usun=True, wykonaj=cli))[nazwa] == "usunięto"
    assert json.loads(miejsca.ustawienia_claude.read_text(encoding="utf-8")) == {"env": {"INNA": "1"}}

    miejsca.ustawienia_claude.write_text(json.dumps({"env": {"MCP_TIMEOUT": "90000"}}), encoding="utf-8")
    assert stany(konfiguruj(miejsca, program, wykonaj=cli))[nazwa] == "bez zmian"  # większego nie zmniejsza
    assert stany(konfiguruj(miejsca, program, usun=True, wykonaj=cli))[nazwa] == "brak"  # nie nasze – zostaje
    assert czas() == "90000"
    assert instalacja._sprawdz_czas_mcp(miejsca).stan == OK

    miejsca.ustawienia_claude.write_text(json.dumps({"env": {"MCP_TIMEOUT": "30000"}}), encoding="utf-8")
    assert instalacja._sprawdz_czas_mcp(miejsca).stan == UWAGA


def dziennik_mcp(miejsca: Miejsca, projekt: str, czas: str, limit_ms: int, trwalo_ms: int | None) -> Path:
    """Dziennik połączenia w postaci, w jakiej zapisuje go Claude Code 2.1."""
    katalog = miejsca.dzienniki_mcp / projekt / "mcp-logs-skerimai"
    katalog.mkdir(parents=True, exist_ok=True)
    wpisy = [{"debug": f"Starting connection with timeout of {limit_ms}ms", "timestamp": czas}]
    if trwalo_ms is None:
        wpisy += [
            {"debug": f"Connection timeout triggered after {limit_ms + 450}ms (limit: {limit_ms}ms)"},
            {"error": f'Connection failed (CONNECT_TIMEOUT): MCP server "skerimai" connection timed out after {limit_ms}ms'},
        ]
    else:
        wpisy += [{"debug": f"Successfully connected (transport: stdio) in {trwalo_ms}ms"},
                  {"debug": "Connection established with capabilities: {}"}]
    plik = katalog / f"{czas.replace(':', '-')}.jsonl"
    plik.write_text("\n".join(json.dumps(w) for w in wpisy) + "\n", encoding="utf-8")
    return plik


def test_sprawdz_polaczenia_z_serwerem_mcp(miejsca):
    def wynik():
        return instalacja._sprawdz_polaczenia(miejsca)

    assert wynik().stan == INFO
    dziennik_mcp(miejsca, "C--Projekty-SkerimAI", "2026-10-13T09:48:47.558Z", 30000, 7497)
    dziennik_mcp(miejsca, "C--Projekty-SkerimAI", "2026-10-13T10:00:00.000Z", 30000, 12230)
    stary = dziennik_mcp(miejsca, "C--Projekty-stary", "2026-09-01T10:00:00.000Z", 30000, None)
    os.utime(stary, (0, 0))  # sprzed tygodnia – pomijany
    assert (wynik().stan, wynik().opis) == (OK, "2 starty w ostatnich 7 dniach, najdłuższy 12.2 s")

    dziennik_mcp(miejsca, "C--Projekty-budzik", "2026-10-12T11:34:17.349Z", 30000, None)
    assert wynik().stan == OK  # przy domyślnych 30 s – sprzed konfiguracji
    assert "nieudane: 1, ostatnie 2026-10-12 w C--Projekty-budzik przy limicie 30 s" in wynik().opis

    dziennik_mcp(miejsca, "C--Projekty-budzik", "2026-10-14T07:00:00.000Z", 60000, None)
    assert wynik().stan == UWAGA
    assert "nieudane: 2, ostatnie 2026-10-14" in wynik().opis

    # Sesja zamknięta w trakcie startu serwera – nie liczy się ani jako start, ani jako nieudany.
    porzucone = dziennik_mcp(miejsca, "C--Projekty-SkerimAI", "2026-10-25T18:47:25.091Z", 60000, 7000)
    porzucone.write_text("\n".join(json.dumps(w) for w in [
        {"debug": "Starting connection with timeout of 60000ms"},
        {"debug": "Connection abandoned after 1598ms (Claude Code is shutting down)"},
    ]) + "\n", encoding="utf-8")
    assert "4 starty" in wynik().opis
    assert "nieudane: 2, ostatnie 2026-10-14" in wynik().opis


def test_odmawia_bez_zainstalowanego_programu(miejsca, tmp_path, cli):
    with pytest.raises(FileNotFoundError, match="zainstaluj pakiet"):
        konfiguruj(miejsca, Program(tmp_path / "pusty"), wykonaj=cli)


windows = pytest.mark.skipif(os.name != "nt", reason="autostart i ikona w zasobniku tylko w Windows")


@windows
def test_autostart_tylko_dla_kopii_z_ikona(miejsca, program, cli, tmp_path):
    bez_ikony = Program(tmp_path / "stary" / "Scripts")
    bez_ikony.skrypty.mkdir(parents=True)
    bez_ikony.hook.touch()
    bez_ikony.serwer_mcp.touch()

    wyniki = {w.nazwa: w for w in konfiguruj(miejsca, bez_ikony, wykonaj=cli)}

    assert wyniki["Autostart ikony w zasobniku"].stan == "pominięto"
    assert "skerimai-zasobnik.exe" in wyniki["Autostart ikony w zasobniku"].opis
    assert zasobnik.wpis_autostartu() is None
    assert stany(konfiguruj(miejsca, program, wykonaj=cli))["Autostart ikony w zasobniku"] == "dodano"


@windows
def test_sprawdz_ikone_w_zasobniku(program, sciezki, tmp_path):
    def stan():
        return instalacja._sprawdz_zasobnik(program, sciezki)

    assert stan().stan == UWAGA and "nie uruchamia się z Windows" in stan().opis
    zasobnik.ustaw_autostart(r'"C:\inna\skerimai-zasobnik.exe"')
    assert stan().stan == UWAGA and "wskazuje na" in stan().opis
    zasobnik.ustaw_autostart(zasobnik.polecenie_autostartu(program.zasobnik))
    assert stan().stan == UWAGA and "nie działa" in stan().opis

    with blokada(sciezki.blokada_zasobnika, 0):  # tak trzyma ją działająca ikona
        assert stan().stan == OK

        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, zasobnik.KLUCZ_ZATWIERDZEN) as klucz:
            winreg.SetValueEx(klucz, zasobnik.NAZWA_AUTOSTARTU, 0, winreg.REG_BINARY, bytes([3]) + bytes(11))
        assert stan().stan == UWAGA and "Menedżerze zadań" in stan().opis

    assert "zaktualizuj" in instalacja._sprawdz_zasobnik(Program(tmp_path / "Scripts", kod=tmp_path), sciezki).opis


# ---------------------------------------------------------------- wskazówka w CLAUDE.md / AGENTS.md

BLOK = instalacja.tekst_wskazowki(Program(instalacja.Path("x"), kod=instalacja.Path(r"C:\Projekty\SkerimAI")))


def test_prawdziwy_claude_md_jest_aktualny():
    assert wstaw_wskazowke(CLAUDE_MD, BLOK) == CLAUDE_MD


def test_podmienia_nieaktualna_wskazowke_w_srodku_pliku():
    tekst = "# A\na\n\n# SkerimAI – archiwum wcześniejszych rozmów\nstara treść\n\n\n# B\nb\n"

    assert wstaw_wskazowke(tekst, BLOK) == "# A\na\n\n" + "\n".join(BLOK) + "\n\n# B\nb\n"


def test_dopisuje_wskazowke_na_koncu_z_odstepem():
    assert wstaw_wskazowke("# A\na", BLOK) == "# A\na\n\n" + "\n".join(BLOK) + "\n"
    assert wstaw_wskazowke("", BLOK) == "\n".join(BLOK) + "\n"


def test_usuwa_wskazowke_i_scala_sasiednie_sekcje():
    tekst = "# A\r\na\r\n\r\n" + "\r\n".join(BLOK) + "\r\n\r\n# B\r\nb\r\n"

    assert wstaw_wskazowke(tekst, None) == "# A\r\na\r\n\r\n# B\r\nb\r\n"
    assert wstaw_wskazowke(CLAUDE_MD, None) == CLAUDE_MD.split("\r\n\r\n# SkerimAI")[0] + "\r\n"


def test_usuwanie_bez_wskazowki_zostawia_plik_bajt_w_bajt():
    tekst = "# A\n\n\na   \n\n"

    assert wstaw_wskazowke(tekst, None) == tekst


def test_wskazowka_bez_katalogu_z_kodem(tmp_path):
    assert "Kod:" not in instalacja.tekst_wskazowki(Program(tmp_path))[1]


# ---------------------------------------------------------------- sprawdz

@pytest.fixture
def archiwum(projekty, sciezki):
    polaczenie = polacz(sciezki.baza)
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    polaczenie.close()
    return sciezki


def test_sprawdz_po_konfiguracji(miejsca, program, cli, archiwum, projekty):
    konfiguruj(miejsca, program, wykonaj=cli)
    archiwum.dziennik.write_text("2026-10-01 08:17:31 INFO SessionEnd/other, sesja 12345678: Sesje: 2\n", encoding="utf-8")

    wyniki = {w.nazwa: w for w in sprawdz(miejsca, program, archiwum, [ClaudeCode(projekty)])}

    assert {n: w.stan for n, w in wyniki.items()} == {
        "Program": OK,
        "Hook SessionEnd w Claude Code": OK,
        "Czas na start serwera MCP w Claude Code": OK,
        "Połączenia Claude Code z serwerem MCP": INFO,  # brak dzienników Claude Code w testowym katalogu domowym
        "Serwer MCP w Claude Code": OK,
        "Serwer MCP w Codex": OK,
        "Serwer MCP w OpenCode": OK,
        "Serwer MCP w KreionAI": INFO,  # nigdy nie błąd – KreionAI może być nieużywany
        "Wskazówka w ~\\.claude\\CLAUDE.md": OK,
        "Wskazówka w ~\\.codex\\AGENTS.md": OK,
        "Powiadomienia Windows": INFO,
        "Ikona w zasobniku": UWAGA if os.name == "nt" else INFO,  # wpisana do autostartu, ale nie działa
        "Archiwum": OK,
        "Ostatnia archiwizacja w tle": OK,
        "Sesje w źródłach": INFO,
    }
    assert "2 rozmowy, 6 wiadomości" in wyniki["Archiwum"].opis
    assert "dysk lokalny" in wyniki["Archiwum"].opis
    assert wyniki["Sesje w źródłach"].opis == "claude_code 2"


def test_sprawdz_kreion_tylko_informuje(miejsca, program, sciezki):
    nazwa = "Serwer MCP w KreionAI"
    nigdy = {w.nazwa: w for w in sprawdz(miejsca, program, sciezki, [])}[nazwa]
    sciezki.katalog.mkdir(parents=True, exist_ok=True)
    sciezki.polaczenie_kreion.write_text("2026-09-01T10:00:00+0200", encoding="utf-8")  # dawno – i tak bez ostrzeżenia
    dawno = {w.nazwa: w for w in sprawdz(miejsca, program, sciezki, [])}[nazwa]

    assert nigdy.stan == INFO and dawno.stan == INFO
    assert f"{program.serwer_mcp} --klient kreion" in nigdy.opis  # gotowy wpis do formatki KreionAI
    assert "ostatnie połączenie 2026-09-01 10:00" in dawno.opis


def test_sprawdz_na_nieskonfigurowanym_komputerze(miejsca, program, sciezki):
    wyniki = {w.nazwa: w for w in sprawdz(miejsca, program, sciezki, [])}

    for nazwa in ("Hook SessionEnd w Claude Code", "Serwer MCP w Claude Code", "Serwer MCP w Codex",
                  "Wskazówka w ~\\.claude\\CLAUDE.md", "Archiwum"):
        assert wyniki[nazwa].stan == BLAD, nazwa
    assert "skerimai konfiguruj" in wyniki["Serwer MCP w Codex"].opis
    assert "skerimai archiwizuj" in wyniki["Archiwum"].opis
    assert not sciezki.baza.exists()  # diagnostyka nie zakłada pustej bazy
    assert wyniki["Ostatnia archiwizacja w tle"].stan == INFO


def test_sprawdz_wykrywa_hook_z_innej_instalacji_i_podwojny(miejsca, program, cli, tmp_path):
    konfiguruj(miejsca, program, wykonaj=cli)
    inny = Program(tmp_path / "inny" / "Scripts")

    wynik = {w.nazwa: w for w in sprawdz(miejsca, inny, Sciezki(tmp_path / "brak"), [])}["Hook SessionEnd w Claude Code"]
    assert wynik.stan == BLAD and "wskazuje na" in wynik.opis

    ustawienia = json.loads(miejsca.ustawienia_claude.read_text(encoding="utf-8"))
    ustawienia["hooks"]["SessionEnd"].append({"hooks": [instalacja._hook(program)]})
    miejsca.ustawienia_claude.write_text(json.dumps(ustawienia), encoding="utf-8")

    wynik = {w.nazwa: w for w in sprawdz(miejsca, program, Sciezki(tmp_path / "brak"), [])}["Hook SessionEnd w Claude Code"]
    assert wynik.stan == BLAD and "2 wpisy" in wynik.opis
    assert stany(konfiguruj(miejsca, program, wykonaj=cli))["Hook SessionEnd w Claude Code"] == "zaktualizowano"
    assert len(instalacja._nasze_hooki(json.loads(miejsca.ustawienia_claude.read_text(encoding="utf-8")))) == 1


@pytest.mark.parametrize(("linie", "stan"), [
    (["2026-10-01 08:17:31 INFO SessionEnd/other: Sesje: 2"], OK),
    (["2026-10-01 08:17:31 INFO ok", "2026-10-01 09:00:00 WARNING test: archiwum zajęte"], UWAGA),
    (["2026-10-01 09:00:00 ERROR test: archiwizacja nie powiodła się", "Traceback (most recent call last):",
      "OSError: dysk"], BLAD),
])
def test_sprawdz_ostatni_wpis_dziennika(sciezki, linie, stan):
    sciezki.katalog.mkdir(parents=True)
    sciezki.dziennik.write_text("\n".join(linie) + "\n", encoding="utf-8")

    wynik = instalacja._sprawdz_dziennik(sciezki)

    assert wynik.stan == stan
    assert wynik.opis.startswith(linie[0][:19] if stan == OK else "2026-10-01 09:00:00")


def test_rodzaj_dysku(tmp_path, monkeypatch):
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))

    assert instalacja.rodzaj_dysku(tmp_path / "skerimai") == "dysk lokalny"
    assert instalacja.rodzaj_dysku(instalacja.Path(r"\\serwer\udzial\skerimai")) == "udział sieciowy"
    assert instalacja.rodzaj_dysku(tmp_path / "OneDrive" / "skerimai") == "folder OneDrive"
    assert instalacja.rodzaj_dysku(tmp_path / "OneDriveInny") == "dysk lokalny"


def test_archiwum_w_folderze_onedrive_to_blad(archiwum, monkeypatch):
    monkeypatch.setenv("OneDrive", str(archiwum.katalog.parent))

    wynik = instalacja._sprawdz_archiwum(archiwum)

    assert wynik.stan == BLAD
    assert "folder OneDrive" in wynik.opis and "skerimai kopia" in wynik.opis


def test_wykonaj_zglasza_brak_programu():
    with pytest.raises(FileNotFoundError):
        instalacja.wykonaj(["nie-ma-takiego-programu-skerimai"])


# ---------------------------------------------------------------- kopie programu i instaluj

COMMIT = "7066ec5c696726f50f9bc9515823cd9e2b725518"


def utworz_srodowisko(srodowisko: Path, direct_url: dict | None, wersja: str = "0.1.0") -> None:
    """Środowisko jak po `uv tool install` / `pip install -e .`: programy w Scripts i metadane pakietu."""
    skrypty = srodowisko / ("Scripts" if os.name == "nt" else "bin")
    skrypty.mkdir(parents=True)
    for nazwa in ("skerimai", "skerimai-mcp"):
        (skrypty / instalacja._exe(nazwa)).touch()
    pakiety = srodowisko / "Lib" / "site-packages" if os.name == "nt" else srodowisko / "lib" / "python3.13" / "site-packages"
    info = pakiety / f"skerimai-{wersja}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: skerimai\nVersion: {wersja}\n", encoding="utf-8")
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")


def test_kopia_z_gita_zna_repozytorium_i_commit(tmp_path):
    utworz_srodowisko(tmp_path / "skerimai", {"url": "file:///C:/Projekty/SkerimAI",
                                              "vcs_info": {"vcs": "git", "commit_id": COMMIT}})

    program = instalacja.program_srodowiska(tmp_path / "skerimai")

    assert (program.kod, program.commit, program.edytowalny, program.wersja) == (
        Path(r"C:\Projekty\SkerimAI"), COMMIT, False, "0.1.0")
    assert instalacja.tekst_wskazowki(program) == BLOK  # wskazówka w CLAUDE.md zachowuje ścieżkę kodu


def test_kopia_rozwojowa_i_kopia_bez_zrodla(tmp_path):
    utworz_srodowisko(tmp_path / "venv", {"url": "file:///C:/Projekty/SkerimAI", "dir_info": {"editable": True}})
    utworz_srodowisko(tmp_path / "pypi", None, wersja="0.2.0")

    rozwojowa = instalacja.program_srodowiska(tmp_path / "venv")
    z_pypi = instalacja.program_srodowiska(tmp_path / "pypi")

    assert rozwojowa.edytowalny and rozwojowa.commit is None and rozwojowa.kod == Path(r"C:\Projekty\SkerimAI")
    assert (z_pypi.kod, z_pypi.commit, z_pypi.wersja) == (None, None, "0.2.0")
    assert instalacja.program_srodowiska(tmp_path / "brak") is None


def test_biezacy_program_w_testach_to_kopia_rozwojowa():
    program = instalacja.biezacy_program()

    assert program.edytowalny and program.kod is not None and program.commit is None


class UdawaneUv:
    """`uv` i `git` dla instaluj: wersja uv, katalog narzędzi, stan repozytorium; instalacja tworzy środowisko
    z `direct_url.json` jak prawdziwe `uv tool install git+file://…`."""

    def __init__(self, narzedzia: Path, repo: Path):
        self.narzedzia, self.repo = narzedzia, repo
        self.wersja, self.head, self.zmiany, self.kod_wyjscia = "0.12.21", COMMIT, "", 0
        self.zmienione_pliki_programu = "skerimai/cli.py\n"  # git diff --name-only między commitami
        self.instalacje: list[list[str]] = []

    def __call__(self, polecenie: list[str]) -> subprocess.CompletedProcess:
        match polecenie:
            case ["uv", "--version"]:
                wyjscie = f"uv {self.wersja} (7af826859 2026-09-29 x86_64-pc-windows-msvc)\n"
            case ["uv", "tool", "dir"]:
                wyjscie = f"{self.narzedzia}\n"
            case ["git", "-C", _, "rev-parse", "HEAD"]:
                wyjscie = self.head + "\n"
            case ["git", "-C", _, "status", "--porcelain"]:
                wyjscie = self.zmiany
            case ["git", "-C", _, "diff", "--name-only", _, _, "--", "skerimai", "pyproject.toml"]:
                wyjscie = self.zmienione_pliki_programu
            case _:
                raise AssertionError(polecenie)
        return subprocess.CompletedProcess(polecenie, 0, wyjscie, "")

    def instaluj(self, polecenie: list[str]) -> int:
        self.instalacje.append(polecenie)
        if self.kod_wyjscia:
            return self.kod_wyjscia
        shutil.rmtree(self.narzedzia / "skerimai", ignore_errors=True)
        if polecenie[-1].startswith("https://"):  # wydanie z GitHuba: archiwum ZIP tagu vX.Y.Z
            wersja = polecenie[-1].rsplit("/v", 1)[1].removesuffix(".zip")
            utworz_srodowisko(self.narzedzia / "skerimai", {"url": polecenie[-1], "archive_info": {}}, wersja)
            return 0
        utworz_srodowisko(self.narzedzia / "skerimai", {"url": self.repo.as_uri(),
                                                        "vcs_info": {"vcs": "git", "commit_id": self.head}})
        return 0


@pytest.fixture
def repo(tmp_path) -> Path:
    (tmp_path / "SkerimAI" / ".git").mkdir(parents=True)
    return tmp_path / "SkerimAI"


@pytest.fixture
def rozwojowa(repo) -> Program:
    return Program(repo / ".venv" / "Scripts", kod=repo, edytowalny=True)


@pytest.fixture
def uv(tmp_path, repo) -> UdawaneUv:
    return UdawaneUv(tmp_path / "uv-tools", repo)


def zainstaluj(program: Program, uv: UdawaneUv, komunikaty: list[str] | None = None, **opcje) -> Program:
    komunikaty = komunikaty if komunikaty is not None else []
    return instaluj(program, wykonaj=uv, uv=uv.instaluj, komunikat=komunikaty.append, **opcje)


def test_instaluje_biezacy_commit_z_repozytorium(rozwojowa, uv, repo):
    komunikaty: list[str] = []

    program = zainstaluj(rozwojowa, uv, komunikaty)

    assert uv.instalacje == [["uv", "tool", "install", "--reinstall", "--compile-bytecode", "--python",
                              f"{sys.version_info[0]}.{sys.version_info[1]}", "--python-preference", "only-managed",
                              f"git+{repo.as_uri()}"]]
    assert (program.skrypty, program.kod, program.commit) == (uv.narzedzia / "skerimai" / "Scripts", repo, COMMIT)
    assert not any("niezacommitowane" in k for k in komunikaty)
    assert instalacja.program_docelowy(uv) == program  # konfiguruj i sprawdz z kopii rozwojowej wskazują na nią


def test_program_docelowy_bez_kopii_z_uv_to_biezacy(uv):
    def bez_uv(polecenie):
        raise FileNotFoundError(polecenie[0])

    assert instalacja.program_docelowy(uv) == instalacja.biezacy_program()  # katalog narzędzi uv jest pusty
    assert instalacja.program_docelowy(bez_uv) == instalacja.biezacy_program()


def test_ostrzega_o_niezacommitowanych_zmianach(rozwojowa, uv):
    uv.zmiany = " M skerimai/cli.py\n"
    komunikaty: list[str] = []

    zainstaluj(rozwojowa, uv, komunikaty)

    assert "niezacommitowane zmiany" in komunikaty[0] and COMMIT[:7] in komunikaty[0]


def test_odmawia_przy_zbyt_starym_uv(rozwojowa, uv):
    uv.wersja = "0.11.11"

    with pytest.raises(BladInstalacji, match="uv self update"):
        zainstaluj(rozwojowa, uv)
    assert uv.instalacje == []


def test_bez_repozytorium_i_bez_sieci_nie_instaluje(uv, tmp_path):
    with pytest.raises(BladInstalacji, match="najnowszej wersji na GitHubie"):
        zainstaluj(Program(tmp_path / "Scripts"), uv)
    assert uv.instalacje == []


def pyproject(wersja: str):
    return lambda adres: f'[project]\nname = "skerimai"\nversion = "{wersja}"\n'


def test_bez_repozytorium_instaluje_najnowsze_wydanie(uv, tmp_path):
    """Pierwsza instalacja u innych: `uvx --from <archiwum main> skerimai instaluj` – kopia bez repozytorium."""
    program = zainstaluj(Program(tmp_path / "uvx" / "Scripts", wersja="0.2.0"), uv, pobierz=pyproject("0.3.0"))

    assert uv.instalacje[0][-1] == "https://github.com/szewczyk74/SkerimAI/archive/refs/tags/v0.3.0.zip"
    assert "--python-preference" in uv.instalacje[0] and "only-managed" in uv.instalacje[0]
    assert (program.wersja, program.kod, program.commit) == ("0.3.0", None, None)
    assert program == instalacja.program_srodowiska(uv.narzedzia / "skerimai")


def test_kopia_rozwojowa_instaluje_wskazane_wydanie(rozwojowa, uv):
    program = zainstaluj(rozwojowa, uv, wersja="0.2.0")

    assert uv.instalacje[0][-1].endswith("/v0.2.0.zip") and program.wersja == "0.2.0"


def test_zainstalowana_kopia_z_wydania_podpowiada_aktualizacje(uv, tmp_path):
    zainstalowana = zainstaluj(Program(tmp_path / "Scripts"), uv, wersja="0.2.0")

    with pytest.raises(BladInstalacji, match="nie podmieni samej siebie") as blad:
        zainstaluj(zainstalowana, uv, pobierz=pyproject("0.3.0"))
    assert "Zainstaluj aktualizację" in str(blad.value)
    assert "uv tool run --python-preference only-managed --from" in str(blad.value)
    assert "skerimai instaluj --wersja 0.3.0" in str(blad.value)


def test_kopia_z_wydania_nie_ma_repozytorium(tmp_path):
    utworz_srodowisko(tmp_path / "github", {"url": instalacja.adres_wydania("0.2.0"), "archive_info": {}}, "0.2.0")
    utworz_srodowisko(tmp_path / "zip", {"url": (tmp_path / "v.zip").as_uri(), "archive_info": {}}, "0.2.0")

    for nazwa in ("github", "zip"):
        program = instalacja.program_srodowiska(tmp_path / nazwa)
        assert (program.kod, program.commit, program.edytowalny, program.wersja) == (None, None, False, "0.2.0")


def test_najnowsza_wersja_raz_na_dobe(tmp_path):
    plik, teraz, pytania = tmp_path / "wydanie.json", [1000.0], []

    def pobierz(adres):
        pytania.append(adres)
        if wersja is None:
            raise OSError("brak sieci")
        return pyproject(wersja)(adres)

    def najnowsza():
        return instalacja.najnowsza_wersja(plik, pobierz, lambda: teraz[0])

    wersja = "0.2.0"
    assert najnowsza() == "0.2.0"
    wersja = "0.3.0"
    teraz[0] += 3600
    assert najnowsza() == "0.2.0" and len(pytania) == 1  # zapamiętana
    teraz[0] += instalacja.SPRAWDZANIE_WERSJI_S
    assert najnowsza() == "0.3.0" and len(pytania) == 2
    assert pytania[0] == "https://raw.githubusercontent.com/szewczyk74/SkerimAI/main/pyproject.toml"

    wersja = None  # brak sieci – ostatnia znana, następna próba też po dobie
    teraz[0] += instalacja.SPRAWDZANIE_WERSJI_S
    assert najnowsza() == "0.3.0" and len(pytania) == 3
    assert najnowsza() == "0.3.0" and len(pytania) == 3

    plik.write_text("zepsuty", encoding="utf-8")
    wersja = "0.4.0"
    assert najnowsza() == "0.4.0"


def test_sprawdz_ostrzega_o_nowszym_wydaniu(tmp_path):
    utworz_srodowisko(tmp_path / "github", {"url": instalacja.adres_wydania("0.2.0"), "archive_info": {}}, "0.2.0")
    program = instalacja.program_srodowiska(tmp_path / "github")

    def bez_gita(polecenie):
        raise AssertionError(polecenie)

    nowsza = instalacja._sprawdz_program(program, bez_gita, lambda: "0.10.0")
    ta_sama = instalacja._sprawdz_program(program, bez_gita, lambda: "0.2.0")
    bez_sieci = instalacja._sprawdz_program(program, bez_gita, lambda: None)

    assert nowsza.stan == UWAGA and "nowsza wersja 0.10.0" in nowsza.opis and "Zainstaluj aktualizację" in nowsza.opis
    assert (ta_sama.stan, bez_sieci.stan) == (OK, OK)
    z_katalogu = Program(program.skrypty, kod=tmp_path, wersja="0.1.0")  # bez repozytorium gita, ale z kodem
    assert instalacja._sprawdz_program(z_katalogu, bez_gita, lambda: "9.9.9").stan == OK


def krotka_sciezka(sciezka: Path) -> Path:
    """Ścieżka z nazwami 8.3 (MIROSL~1.HER) – taką podaje m.in. zmienna TEMP; bez nazw 8.3 na dysku – ta sama."""
    if os.name != "nt":
        return sciezka
    import ctypes

    bufor = ctypes.create_unicode_buffer(1024)
    return Path(bufor.value) if ctypes.windll.kernel32.GetShortPathNameW(str(sciezka), bufor, 1024) else sciezka


def test_zainstalowana_kopia_nie_podmienia_samej_siebie(rozwojowa, uv):
    zainstalowana = zainstaluj(rozwojowa, uv)

    for skrypty in (zainstalowana.skrypty, krotka_sciezka(zainstalowana.skrypty)):
        with pytest.raises(BladInstalacji, match="nie podmieni samej siebie") as blad:
            zainstaluj(Program(skrypty, kod=rozwojowa.kod, commit=COMMIT), uv)
        assert instalacja.polecenie_instalacji(rozwojowa.kod) in str(blad.value)
    assert len(uv.instalacje) == 1


def test_odmawia_gdy_zainstalowana_kopia_dziala(rozwojowa, uv, monkeypatch):
    zainstaluj(rozwojowa, uv)
    monkeypatch.setattr(instalacja, "zajete_programy", lambda skrypty: ["python.exe", "skerimai-mcp.exe"])

    with pytest.raises(BladInstalacji, match="python.exe, skerimai-mcp.exe.*zamknij sesje"):
        zainstaluj(rozwojowa, uv)
    assert len(uv.instalacje) == 1


@pytest.mark.skipif(os.name != "nt", reason="blokada działającego programu to zachowanie Windows")
def test_zajete_programy_wykrywa_dzialajacy_program(tmp_path):
    (tmp_path / "skerimai.exe").touch()
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", tmp_path / "skerimai-mcp.exe")
    proces = subprocess.Popen([str(tmp_path / "skerimai-mcp.exe"), "-n", "30", "127.0.0.1"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert instalacja.zajete_programy(tmp_path) == ["skerimai-mcp.exe"]
    finally:
        proces.kill()
        proces.wait()
    assert instalacja.zajete_programy(tmp_path) == []


def test_zamyka_ikone_w_zasobniku_przed_instalacja(rozwojowa, uv, monkeypatch):
    zainstaluj(rozwojowa, uv)
    stany_plikow = [["pythonw.exe", "skerimai-zasobnik.exe"], ["pythonw.exe"], []]
    monkeypatch.setattr(instalacja, "zajete_programy", lambda skrypty: stany_plikow.pop(0) if stany_plikow else [])
    zamkniecia, komunikaty = [], []

    instaluj(rozwojowa, wykonaj=uv, uv=uv.instaluj, komunikat=komunikaty.append,
             zamknij_zasobnik=lambda: zamkniecia.append(1) or True)

    assert zamkniecia == [1]
    assert len(uv.instalacje) == 2
    assert any("Zamknięto ikonę" in k for k in komunikaty)


def test_nie_zamyka_ikony_gdy_dziala_serwer_mcp(rozwojowa, uv, monkeypatch):
    zainstaluj(rozwojowa, uv)
    monkeypatch.setattr(instalacja, "zajete_programy",
                        lambda skrypty: ["python.exe", "pythonw.exe", "skerimai-mcp.exe", "skerimai-zasobnik.exe"])

    with pytest.raises(BladInstalacji, match=r"\(python.exe, skerimai-mcp.exe\)"):
        instaluj(rozwojowa, wykonaj=uv, uv=uv.instaluj, zamknij_zasobnik=lambda: pytest.fail("zamknięta ikona"))
    assert len(uv.instalacje) == 1


def test_odmawia_przy_otwartym_oknie_pamieci(rozwojowa, uv, monkeypatch):
    zainstaluj(rozwojowa, uv)
    zajete = [["pythonw.exe", "skerimai-pamiec.exe"], ["python.exe", "pythonw.exe", "skerimai-mcp.exe", "skerimai-pamiec.exe"]]
    monkeypatch.setattr(instalacja, "zajete_programy", lambda skrypty: zajete[0])

    with pytest.raises(BladInstalacji, match="^Okno SkerimAI jest otwarte – zamknij okna"):
        instaluj(rozwojowa, wykonaj=uv, uv=uv.instaluj, zamknij_zasobnik=lambda: pytest.fail("zamknięta ikona"))
    zajete.pop(0)
    with pytest.raises(BladInstalacji, match=r"\(python.exe, skerimai-mcp.exe\) – zamknij sesje .*, zamknij okna"):
        instaluj(rozwojowa, wykonaj=uv, uv=uv.instaluj)
    assert len(uv.instalacje) == 1


def test_odmawia_gdy_ikona_sie_nie_zamyka(rozwojowa, uv, monkeypatch):
    zainstaluj(rozwojowa, uv)
    monkeypatch.setattr(instalacja, "zajete_programy", lambda skrypty: ["pythonw.exe", "skerimai-zasobnik.exe"])

    for zamknij in (None, lambda: False):
        with pytest.raises(BladInstalacji, match="nie zamknęła się"):
            instaluj(rozwojowa, wykonaj=uv, uv=uv.instaluj, zamknij_zasobnik=zamknij)
    assert len(uv.instalacje) == 1


def test_blad_uv(rozwojowa, uv):
    uv.kod_wyjscia = 2

    with pytest.raises(BladInstalacji, match="uv zakończył się błędem"):
        zainstaluj(rozwojowa, uv)


def test_sprawdz_ostrzega_gdy_repozytorium_ma_nowszy_commit(rozwojowa, uv):
    program = zainstaluj(rozwojowa, uv)

    aktualny = instalacja._sprawdz_program(program, uv)
    uv.head = "f" * 40
    starszy = instalacja._sprawdz_program(program, uv)
    uv.zmienione_pliki_programu = ""
    bez_zmian_w_programie = instalacja._sprawdz_program(program, uv)

    assert aktualny.stan == OK and f"commit {COMMIT[:7]}" in aktualny.opis
    assert starszy.stan == UWAGA and "fffffff" in starszy.opis
    assert instalacja.polecenie_instalacji(rozwojowa.kod) in starszy.opis
    assert bez_zmian_w_programie.stan == OK and "bez zmian w programie" in bez_zmian_w_programie.opis
    assert "kopia rozwojowa" in instalacja._sprawdz_program(
        Program(program.skrypty, kod=rozwojowa.kod, edytowalny=True), uv).opis
