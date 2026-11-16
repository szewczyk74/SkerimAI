import json
from pathlib import Path

import pytest

from skerimai import edycja
from skerimai.adaptery.kreion_kartridz import KreionKartridz, czas_utc, projekty_kartridza
from skerimai.adaptery.kreion_silniki import KreionSilniki
from skerimai.archiwizator import archiwizuj
from skerimai.model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK
from skerimai.zapytania import Filtry, czas_lokalny, lista_rozmow, szukaj

from .conftest import PNG, asystent, uzytkownik, zapisz_jsonl

ROZMOWA_KSM = "Naprawa karty KSM do sterowania silnikiem"
ZASADY_KSM = "user_project_2026-08-28_15-18-34.md"
SESJA_CLAUDE = "886d017e-d8b9-4b91-bdfc-0aa3a1b2c3d4"
SESJA_CODEX = "01a04a5b-b004-7762-843a-0123456789ab"


def kartridz_uzytkownik(tekst, ts, zalaczniki=()):
    return {"kind": "user", "text": tekst, "timestamp": ts, "user": "Mirek", "agent": "KREION", "role": "master",
            "attachments": [{"fname": n, "path": n, "ext": "JPEG", "isImage": True} for n in zalaczniki]}


def kartridz_agent(tekst, ts, model="Opus 5"):
    return {"kind": "agent", "text": tekst, "timestamp": ts, "agent_model": model, "user": "Mirek", "agent": "KREION"}


def prefiks(ts="08:45:12 sobota", zasady=True):
    zasady = f"[PRZECZYTAJ ZASADY PROJEKTU! odczytaj {{ATTACHMENTS_PATH}}\\{ZASADY_KSM} (action=read).]" if zasady else ""
    return f"[jobContextId:0746f262-4d2b-44ee-8d1e-8dfee005834b]{zasady}[TS:{ts}][myname:Mirek] "


@pytest.fixture
def kartridz(tmp_path: Path) -> Path:
    katalog = tmp_path / "cartridge"
    (katalog / "attachments").mkdir(parents=True)
    (katalog / "attachments" / "IMG_6044.jpeg").write_bytes(PNG + b"zdjecie")
    rekordy = [
        {"kind": "project", "title": "Pompa KSM", "userProjectFile": ZASADY_KSM, "projUID": "2026-08-28_15-18-34"},
        kartridz_uzytkownik("Naprawa karty KSM do sterowania silnikiem wciągarki", "2026-08-28 piątek 15:15:38",
                            ["IMG_6044.jpeg", "zniknal.jpeg"]),
        kartridz_agent("You've hit your session limit", "2026-08-28 piątek 15:15:40", "<synthetic>"),
        {"kind": "error", "text": "Error (500): System Error.  Code: 12029.\n", "timestamp": "2026-08-28 piątek 15:16:00"},
        kartridz_agent("Na karcie KSM spalony jest tranzystor mocy.", "2026-08-28 piątek 15:20:00"),
    ]
    plik = katalog / "logs" / f"{ROZMOWA_KSM}.jsonl"
    plik.parent.mkdir()
    # KreionAI zapisuje z BOM
    plik.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rekordy) + "\n", encoding="utf-8-sig")
    return katalog


@pytest.fixture
def silniki(tmp_path: Path) -> tuple[Path, Path]:
    claude = tmp_path / "kreion" / "projects"
    cwd = {"cwd": r"C:\Kreion\aae_ai_local_data"}
    zapisz_jsonl(
        claude / "C--Kreion-aae-ai-local-data" / f"{SESJA_CLAUDE}.jsonl",
        [
            {**uzytkownik(prefiks() + "Sprawdź co wczoraj robiliśmy", "2026-08-29T06:45:59.000Z"), **cwd},
            {**asystent([{"type": "text", "text": "Wczoraj diagnozowaliśmy kartę KSM."}], "2026-08-29T06:46:30.000Z"), **cwd},
            {**uzytkownik(prefiks(zasady=False) + "[CACHE-PING] To automatyczny ping podtrzymujacy cache. "
                          "Odpowiedz WYLACZNIE pojedynczym znakiem '.'", "2026-08-29T07:36:00.000Z"), **cwd},
            {**asystent([{"type": "text", "text": "."}], "2026-08-29T07:36:02.000Z"), **cwd},
            {**uzytkownik(prefiks() + r"Co na zdjęciu? [załącznik: C:\Kreion\cartridge\attachments\IMG_6044.jpeg]",
                          "2026-08-29T07:40:00.000Z"), **cwd},
            {**asystent([{"type": "text", "text": "Uruchamiam pomiar w tle."}], "2026-08-29T07:40:10.000Z"), **cwd},
            {**uzytkownik("[BACKGROUND_JOB_DONE] jobId=job_1 status=done exitCode=0", "2026-08-29T07:41:00.000Z"), **cwd},
            {**asystent([{"type": "text", "text": "Pomiar skończony: zwarcie na wyjściu."}], "2026-08-29T07:41:05.000Z"), **cwd},
        ],
    )
    codex = tmp_path / "kreion_codex" / "sessions"
    zapisz_jsonl(
        codex / "2026" / "08" / "28" / f"rollout-2026-08-28T23-52-02-{SESJA_CODEX}.jsonl",
        [
            {"timestamp": "2026-08-28T21:52:02.000Z", "type": "session_meta",
             "payload": {"id": SESJA_CODEX, "cwd": r"C:\Kreion\aae_ai_local_data"}},
            {"timestamp": "2026-08-28T21:52:03.000Z", "type": "response_item",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": prefiks("23:52:00 piątek") + "Sprawdź co wymyślił Claude."}]}},
            {"timestamp": "2026-08-28T21:53:00.000Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "Diagnoza Claude jest trafna."}]}},
        ],
    )
    return claude, codex


def test_kartridz_jedna_rozmowa_na_plik(kartridz):
    assert set(KreionKartridz(kartridz).znajdz_sesje()) == {ROZMOWA_KSM}


def test_kartridz_pomija_bledy_i_komunikaty_syntetyczne(kartridz):
    adapter = KreionKartridz(kartridz)
    rozmowa = adapter.wczytaj(ROZMOWA_KSM, adapter.znajdz_sesje()[ROZMOWA_KSM])

    assert [w.rola for w in rozmowa.wiadomosci] == [ROLA_UZYTKOWNIK, ROLA_ASYSTENT]
    assert rozmowa.wiadomosci[1].tekst == "Na karcie KSM spalony jest tranzystor mocy."


def test_kartridz_projekt_tytul_i_zalaczniki(kartridz):
    adapter = KreionKartridz(kartridz)
    rozmowa = adapter.wczytaj(ROZMOWA_KSM, adapter.znajdz_sesje()[ROZMOWA_KSM])

    assert rozmowa.zrodlo == "kreion_kartridz"
    assert rozmowa.projekt == "Pompa KSM"  # mimo BOM w pierwszej linii
    assert rozmowa.tytul == "Naprawa karty KSM do sterowania silnikiem wciągarki"
    zalaczniki = rozmowa.wiadomosci[0].zalaczniki
    assert [z.nazwa for z in zalaczniki] == ["IMG_6044.jpeg", "zniknal.jpeg"]
    assert zalaczniki[0].sciezka == kartridz / "attachments" / "IMG_6044.jpeg"


def test_kartridz_czas_lokalny_na_utc(kartridz):
    adapter = KreionKartridz(kartridz)
    rozmowa = adapter.wczytaj(ROZMOWA_KSM, adapter.znajdz_sesje()[ROZMOWA_KSM])

    assert rozmowa.pierwsza_ts.endswith(".000Z")
    assert czas_lokalny(rozmowa.pierwsza_ts) == "2026-08-28 15:15"


def test_czas_w_nieznanym_formacie_zostaje():
    assert czas_utc("wczoraj") == "wczoraj"
    assert czas_utc(None) is None


def test_projekty_kartridza(kartridz):
    assert projekty_kartridza(kartridz) == {ZASADY_KSM: "Pompa KSM"}


def test_katalog_kartridza_ze_zmiennej(kartridz, monkeypatch):
    monkeypatch.setenv("SKERIMAI_KREION_DIR", str(kartridz))

    assert KreionKartridz().katalog == kartridz
    assert KreionSilniki()._kartridz == kartridz
    assert KreionKartridz().znajdz_sesje()

    monkeypatch.delenv("SKERIMAI_KREION_DIR")
    assert KreionKartridz().katalog == Path(r"C:\Kreion\aae_ai_local_data\cartridge")


def test_silniki_znajduja_sesje_claude_i_codex(silniki, kartridz):
    claude, codex = silniki
    assert set(KreionSilniki(claude, codex, kartridz).znajdz_sesje()) == {SESJA_CLAUDE, SESJA_CODEX}


def test_silniki_bez_prefiksow_i_komunikatow_harnessa(silniki, kartridz):
    claude, codex = silniki
    adapter = KreionSilniki(claude, codex, kartridz)
    rozmowa = adapter.wczytaj(SESJA_CLAUDE, adapter.znajdz_sesje()[SESJA_CLAUDE])

    assert [(w.rola, w.tekst) for w in rozmowa.wiadomosci] == [
        (ROLA_UZYTKOWNIK, "Sprawdź co wczoraj robiliśmy"),
        (ROLA_ASYSTENT, "Wczoraj diagnozowaliśmy kartę KSM."),
        (ROLA_UZYTKOWNIK, "Co na zdjęciu?"),
        (ROLA_ASYSTENT, "Uruchamiam pomiar w tle."),
        (ROLA_ASYSTENT, "Pomiar skończony: zwarcie na wyjściu."),
    ]
    [zalacznik] = rozmowa.wiadomosci[2].zalaczniki
    assert zalacznik.nazwa == "IMG_6044.jpeg"


def test_silniki_zalacznik_z_nawiasem_w_nazwie(tmp_path, kartridz):
    claude = tmp_path / "kreion" / "projects"
    tekst = (prefiks() + "Schematy [z poczty]\n\n"
             r"[załącznik: C:\Kreion\cartridge\attachments\CTL400a[1].630setup_manual.pdf]" "\r\n\n"
             r"[załącznik: C:\Kreion\cartridge\attachments\a.pdf][załącznik: C:\Kreion\cartridge\attachments\b[2].pdf]")
    zapisz_jsonl(claude / "C--Kreion-aae-ai-local-data" / f"{SESJA_CLAUDE}.jsonl",
                 [{**uzytkownik(tekst, "2026-10-27T18:19:43.850Z"), "cwd": r"C:\Kreion\aae_ai_local_data"}])
    adapter = KreionSilniki(claude, tmp_path / "brak", kartridz)
    [wiadomosc] = adapter.wczytaj(SESJA_CLAUDE, adapter.znajdz_sesje()[SESJA_CLAUDE]).wiadomosci

    assert wiadomosc.tekst == "Schematy [z poczty]"
    assert [z.nazwa for z in wiadomosc.zalaczniki] == ["CTL400a[1].630setup_manual.pdf", "a.pdf", "b[2].pdf"]


def test_silniki_projekt_z_pliku_zasad(silniki, kartridz):
    claude, codex = silniki
    adapter = KreionSilniki(claude, codex, kartridz)
    sesje = adapter.znajdz_sesje()

    for sesja in (SESJA_CLAUDE, SESJA_CODEX):
        rozmowa = adapter.wczytaj(sesja, sesje[sesja])
        assert rozmowa.zrodlo == "kreion_silniki"
        assert rozmowa.projekt == "Pompa KSM"
    assert adapter.wczytaj(SESJA_CODEX, sesje[SESJA_CODEX]).tytul == "Sprawdź co wymyślił Claude."


def test_wyszukiwanie_pomija_kopie_robocze_chyba_ze_wskazane(silniki, kartridz, sciezki, polaczenie):
    claude, codex = silniki
    archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz), KreionSilniki(claude, codex, kartridz)])

    assert {t.zrodlo for t in szukaj(polaczenie, "karta KSM")} == {"kreion_kartridz"}
    assert {r.zrodlo for r in lista_rozmow(polaczenie)} == {"kreion_kartridz"}
    assert {t.zrodlo for t in szukaj(polaczenie, "karta KSM", Filtry(zrodlo="kreion_silniki"))} == {"kreion_silniki"}
    assert len(lista_rozmow(polaczenie, Filtry(zrodlo="kreion_silniki"))) == 2


def test_rozmowa_przypisana_pozniej_dostaje_nazwe_nadana_projektowi(kartridz, sciezki, polaczenie):
    """KreionAI przypisuje rozmowę do projektu w jej trakcie – nazwa nadana w oknie „pamięć” obowiązuje i wtedy."""
    plik = kartridz / "logs" / "Pytanie o kartę.jsonl"
    zapisz_jsonl(plik, [kartridz_uzytkownik("Pytanie o kartę", "2026-08-29 sobota 10:00:00")])
    archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz)])
    ksm = polaczenie.execute("SELECT id FROM rozmowy WHERE projekt = 'Pompa KSM'").fetchone()[0]
    edycja.ustaw_projekt(polaczenie, [ksm], "KSM")
    with plik.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "project", "title": "Pompa KSM", "userProjectFile": ZASADY_KSM}) + "\n")

    archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz)])

    assert [r.projekt for r in lista_rozmow(polaczenie)] == ["KSM", "KSM"]
