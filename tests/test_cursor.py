import gzip
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from skerimai.adaptery.cursor import Cursor
from skerimai.archiwizator import archiwizuj
from skerimai.model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK
from skerimai.zapytania import Filtry, lista_rozmow, szukaj

from .conftest import PNG

MAPA, TERMINAL, SCIEZKI, OBSZAR = "rozmowa-mapa", "rozmowa-terminal", "rozmowa-sciezki", "rozmowa-obszar"


def zapisz(plik: Path, tabela: str, wpisy: dict) -> None:
    plik.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(plik)) as baza:
        with baza:
            baza.execute(f"CREATE TABLE IF NOT EXISTS {tabela} (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
            baza.executemany(f"INSERT INTO {tabela} VALUES (?, ?)",
                             [(k, json.dumps(v, ensure_ascii=False)) for k, v in wpisy.items()])


def rozmowa(id_, babelki, **pola):
    wpisy = {f"composerData:{id_}": {"_v": 10, "composerId": id_, "createdAt": 1754900000000,
                                     "lastUpdatedAt": 1754900600000, **pola,
                                     "fullConversationHeadersOnly": [{"bubbleId": f"b{i}", "type": b["type"]}
                                                                     for i, b in enumerate(babelki)]}}
    wpisy.update({f"bubbleId:{id_}:b{i}": {"_v": 3, "bubbleId": f"b{i}", **b} for i, b in enumerate(babelki)})
    return wpisy


def pytanie(tekst, **pola):
    return {"type": 1, "text": tekst, **pola}


def odpowiedz(tekst="", **pola):
    return {"type": 2, "text": tekst, **pola}


@pytest.fixture
def obraz(tmp_path: Path) -> Path:
    plik = tmp_path / "Cursor-obrazy" / "image-1.png"
    plik.parent.mkdir()
    plik.write_bytes(PNG + b"cursor")
    return plik


@pytest.fixture
def cursor(tmp_path: Path, obraz: Path) -> Path:
    katalog = tmp_path / "Cursor" / "User"
    wpisy = {}
    wpisy |= rozmowa(MAPA, [
        pytanie("Dodaj do kontraktu pole ceny transportu", createdAt="2025-08-10T09:00:00.000Z"),
        odpowiedz(toolFormerData={"name": "read_file", "params": "ContractController.php"}),
        odpowiedz("Najpierw sprawdzę kontroler.", thinking={"text": "myślę o kontrakcie"},
                  timingInfo={"clientStartTime": 27858338, "clientRpcSendTime": 1754816460000}),
        odpowiedz("Gotowe: pole ceny dodane."),
        pytanie("Na zrzucie widać błąd", context={"selectedImages": [{"uuid": "x", "path": str(obraz)}]}),
        odpowiedz("Poprawione."),
    ], name="Pole ceny w kontrakcie")
    wpisy |= rozmowa(TERMINAL, [pytanie("Wysyłka maili", lastTerminalCwd=r"c:\Projekty\hr"), odpowiedz("Zrobione.")])
    wpisy |= rozmowa(SCIEZKI, [pytanie("Utwórz plik markdown"), odpowiedz("Utworzyłem plik.")],
                     originalFileStates={"file:///c%3A/Projekty/sklep.pl/file.md": {},
                                         "file:///c%3A/Projekty/sklep.pl/app/Models/RentalItem.php": {}})
    wpisy |= rozmowa(OBSZAR, [pytanie("Zakoduj szachy"), odpowiedz("Szachownica gotowa.")],
                     workspaceIdentifier={"id": "abc", "uri": {"$mid": 1, "fsPath": r"c:\Projekty\Szachy"}})
    wpisy |= rozmowa("rozmowa-pusta", [])
    wpisy |= rozmowa("rozmowa-subagenta", [pytanie("Zadanie subagenta"), odpowiedz("Wynik subagenta")],
                     subagentInfo={"parentComposerId": MAPA})
    zapisz(katalog / "globalStorage" / "state.vscdb", "cursorDiskKV", wpisy)

    obszar = katalog / "workspaceStorage" / "a1b2"
    zapisz(obszar / "state.vscdb", "ItemTable", {"composer.composerData": {"allComposers": [{"composerId": MAPA}]}})
    (obszar / "workspace.json").write_text(json.dumps({"folder": "file:///c%3A/Projekty/sklep.pl"}), encoding="utf-8")
    nadrzedny = katalog / "workspaceStorage" / "c3d4"  # cały C:\Projekty – nie może wygrać z sklep.pl
    nadrzedny.mkdir()
    (nadrzedny / "workspace.json").write_text(json.dumps({"folder": "file:///c%3A/Projekty"}), encoding="utf-8")
    return katalog


def test_rozmowy_z_wiadomosciami_bez_subagentow(cursor):
    assert set(Cursor(cursor).znajdz_sesje()) == {MAPA, TERMINAL, SCIEZKI, OBSZAR}


def test_brak_cursora(tmp_path):
    assert Cursor(tmp_path / "nie-ma").znajdz_sesje() == {}


def test_wiadomosci_i_tury_asystenta(cursor, obraz):
    adapter = Cursor(cursor)
    sesje = adapter.znajdz_sesje()

    r = adapter.wczytaj(MAPA, sesje[MAPA])

    assert r.tytul == "Pole ceny w kontrakcie"
    assert [w.rola for w in r.wiadomosci] == [ROLA_UZYTKOWNIK, ROLA_ASYSTENT, ROLA_UZYTKOWNIK, ROLA_ASYSTENT]
    assert r.wiadomosci[1].tekst == "Najpierw sprawdzę kontroler.\n\nGotowe: pole ceny dodane."
    assert not any("myślę" in w.tekst or "read_file" in w.tekst for w in r.wiadomosci)
    assert [z.sciezka for z in r.wiadomosci[2].zalaczniki] == [obraz]
    # czas własny, z timingInfo, a bez czasu – czas poprzedniej wiadomości
    assert [w.ts for w in r.wiadomosci] == ["2025-08-10T09:00:00.000Z", "2025-08-10T09:01:00.000Z",
                                            "2025-08-10T09:01:00.000Z", "2025-08-10T09:01:00.000Z"]


def test_folder_projektu(cursor):
    adapter = Cursor(cursor)
    sesje = adapter.znajdz_sesje()

    projekty = {s: adapter.wczytaj(s, sesje[s]).projekt for s in sesje}

    assert projekty == {MAPA: "sklep.pl", TERMINAL: "hr", SCIEZKI: "sklep.pl", OBSZAR: "Szachy"}


def test_archiwizacja(cursor, sciezki, polaczenie):
    raport = archiwizuj(polaczenie, sciezki, [Cursor(cursor)])

    assert (raport.zaktualizowane, raport.nowe_zalaczniki) == (4, 1)
    assert {r.projekt for r in lista_rozmow(polaczenie)} == {"sklep.pl", "hr", "Szachy"}
    trafienia = szukaj(polaczenie, "pole ceny", Filtry(zrodlo="cursor"))
    assert trafienia and trafienia[0].tytul == "Pole ceny w kontrakcie"
    surowa = gzip.decompress((sciezki.surowe / "cursor" / f"{MAPA}.jsonl.gz").read_bytes()).decode("utf-8")
    assert "read_file" in surowa and "myślę o kontrakcie" in surowa  # pełna kopia, także narzędzia

    assert archiwizuj(polaczenie, sciezki, [Cursor(cursor)]).zaktualizowane == 0


def test_archiwizuje_tylko_zmieniona_rozmowe(cursor, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [Cursor(cursor)])
    zapisz(cursor / "globalStorage" / "state.vscdb", "cursorDiskKV",
           rozmowa(TERMINAL, [pytanie("Wysyłka maili", lastTerminalCwd=r"c:\Projekty\hr"), odpowiedz("Zrobione."),
                              pytanie("Dodaj stopkę"), odpowiedz("Stopka dodana.")], lastUpdatedAt=1754901000000))

    raport = archiwizuj(polaczenie, sciezki, [Cursor(cursor)])

    assert raport.zaktualizowane == 1
    assert szukaj(polaczenie, "stopka")
