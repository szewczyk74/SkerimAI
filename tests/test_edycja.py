import gzip
import json

import pytest

from skerimai import edycja, sekrety
from skerimai.adaptery.claude_code import ClaudeCode
from skerimai.adaptery.kreion_kartridz import KreionKartridz
from skerimai.archiwizator import archiwizuj
from skerimai.blokada import blokada
from skerimai.edycja import UKRYTE, W_PAMIECI, ArchiwumZajete
from skerimai.sekrety import ZNACZNIK, Sekrety
from skerimai.serwer_mcp import utworz_serwer
from skerimai.zapytania import Filtry, czytaj_rozmowe, lista_rozmow, szukaj
from tests.conftest import asystent, pdf, uzytkownik, zapisz_jsonl
from tests.test_serwer_mcp import tekst, wywolaj

SEKRET = "sk-test-ABCD1234xyz"


@pytest.fixture
def archiwum(projekty, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(projekty)])
    return polaczenie


def numer(polaczenie, projekt: str) -> int:
    return polaczenie.execute("SELECT id FROM rozmowy WHERE projekt = ?", (projekt,)).fetchone()[0]


# ---------------------------------------------------------------- ukrywanie, tytuł, projekt

def test_ukryta_rozmowa_znika_z_wyszukiwania_i_listy(archiwum):
    sklep = numer(archiwum, "sklep.pl")

    edycja.ukryj_rozmowy(archiwum, [sklep])

    assert szukaj(archiwum, "NodeJsTools") == []
    assert [r.id for r in lista_rozmow(archiwum)] == [numer(archiwum, "budzik")]
    assert szukaj(archiwum, "NodeJsTools", Filtry(ukryte=True))
    edycja.ukryj_rozmowy(archiwum, [sklep], False)
    assert szukaj(archiwum, "NodeJsTools")


def test_ukryta_wiadomosc_poza_wyszukiwaniem_reszta_rozmowy_zostaje(archiwum):
    sklep = numer(archiwum, "sklep.pl")

    edycja.ukryj_wiadomosci(archiwum, sklep, [2])

    assert szukaj(archiwum, "Outlook") == []
    assert szukaj(archiwum, "NodeJsTools")
    _, wiadomosci = czytaj_rozmowe(archiwum, sklep)
    assert [w.ukryta for w in wiadomosci] == [False, False, True, False]


def test_ukrycie_idzie_za_trescia_gdy_zrodlo_dolozy_wiadomosci_wczesniej(tmp_path, sciezki, polaczenie):
    plik = tmp_path / "projekty" / "C--Projekty-mapa" / "sesja-u.jsonl"
    rozmowa = [
        uzytkownik("Pierwsze pytanie", "2026-10-01T10:00:00.000Z"),
        asystent([{"type": "text", "text": "Dobrze."}], "2026-10-01T10:00:05.000Z"),
        uzytkownik("Prywatny temat Zanzibar", "2026-10-01T10:01:00.000Z"),
        asystent([{"type": "text", "text": "Dobrze."}], "2026-10-01T10:01:05.000Z"),
    ]
    zapisz_jsonl(plik, rozmowa)
    archiwizuj(polaczenie, sciezki, [ClaudeCode(plik.parent.parent)])
    rozmowa_id = polaczenie.execute("SELECT id FROM rozmowy").fetchone()[0]
    edycja.ukryj_wiadomosci(polaczenie, rozmowa_id, [2, 3])

    # Claude Code sortuje rekordy po czasie – wcześniejsze wiadomości przesuwają numery
    zapisz_jsonl(plik, [
        uzytkownik("Wcześniejsze pytanie", "2026-10-01T09:00:00.000Z"),
        asystent([{"type": "text", "text": "Dobrze."}], "2026-10-01T09:00:05.000Z"),
        *rozmowa,
    ])
    archiwizuj(polaczenie, sciezki, [ClaudeCode(plik.parent.parent)])

    _, wiadomosci = czytaj_rozmowe(polaczenie, rozmowa_id)
    assert [w.ukryta for w in wiadomosci] == [False, False, False, False, True, True]
    assert szukaj(polaczenie, "Zanzibar") == []
    assert szukaj(polaczenie, "Pierwsze pytanie")


@pytest.mark.parametrize("ukryte, dawne, nowe, oczekiwane", [
    # ta sama rozmowa – bez zmian
    ({1}, ["a", "b", "c"], ["a", "b", "c"], {1}),
    # wiadomość dołożona na początku
    ({1}, ["a", "b", "c"], ["x", "a", "b", "c"], {2}),
    # jednakowe wiadomości także we wstawce – liczy się kolejność, nie treść sama
    ({3}, ["a", "ok", "b", "ok"], ["x", "ok", "a", "ok", "b", "ok"], {5}),
    # treść zmieniona w źródle (rosnąca odpowiedź) po przesunięciu
    ({1}, ["a", "b"], ["x", "a", "b i dalej"], {2}),
    # dwie tury scalone w jedną – ukrycie przechodzi na scaloną
    ({2}, ["a", "b1", "b2", "c"], ["a", "b1 b2", "c"], {1}),
    # wiadomość usunięta ze źródła – ukrycie przepada
    ({1}, ["a", "b", "c"], ["a", "c"], set()),
])
def test_przeniesione_ukrycia(ukryte, dawne, nowe, oczekiwane):
    from skerimai.archiwizator import przeniesione_ukrycia

    jako_wiadomosci = lambda teksty: [("user", t) for t in teksty]  # noqa: E731

    assert przeniesione_ukrycia(ukryte, jako_wiadomosci(dawne), jako_wiadomosci(nowe)) == oczekiwane


def test_dokument_w_ukrytej_wiadomosci_poza_wyszukiwaniem(kartridz_z_dokumentami, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [KreionKartridz(kartridz_z_dokumentami)])
    rozmowa = polaczenie.execute("SELECT id FROM rozmowy").fetchone()[0]
    assert {t.lp for t in szukaj(polaczenie, "Przekaznik")} == {0}

    edycja.ukryj_wiadomosci(polaczenie, rozmowa, [0])

    assert {t.lp for t in szukaj(polaczenie, "Przekaznik")} == {2}  # ten sam schemat dołączony jeszcze raz


def test_edycja_przetrwa_ponowna_archiwizacje(archiwum, projekty, sciezki):
    sklep, budzik = numer(archiwum, "sklep.pl"), numer(archiwum, "budzik")
    edycja.ukryj_rozmowy(archiwum, [budzik])
    edycja.ukryj_wiadomosci(archiwum, sklep, [2])
    edycja.ustaw_tytul(archiwum, sklep, "Instalacja NodeJsTools – rozwiązane")
    edycja.ustaw_projekt(archiwum, [sklep], "KreionAI")

    archiwizuj(archiwum, sciezki, [ClaudeCode(projekty)], pelne=True)

    opis, wiadomosci = czytaj_rozmowe(archiwum, sklep)
    assert (opis.tytul, opis.projekt) == ("Instalacja NodeJsTools – rozwiązane", "KreionAI")
    assert wiadomosci[2].ukryta
    assert czytaj_rozmowe(archiwum, budzik)[0].ukryta


def test_wlasny_tytul_i_projekt_w_wynikach_i_filtrach(archiwum):
    sklep = numer(archiwum, "sklep.pl")

    edycja.ustaw_tytul(archiwum, sklep, "  Nowy tytuł ")
    edycja.ustaw_projekt(archiwum, [sklep], "KreionAI")

    trafienie = szukaj(archiwum, "NodeJsTools")[0]
    assert (trafienie.tytul, trafienie.projekt) == ("Nowy tytuł", "KreionAI")
    assert [r.id for r in lista_rozmow(archiwum, Filtry(projekt="kreion"))] == [sklep]
    assert lista_rozmow(archiwum, Filtry(projekt="sklep")) == []
    assert edycja.projekty(archiwum) == ["budzik", "KreionAI"]

    edycja.ustaw_tytul(archiwum, sklep, "")
    edycja.ustaw_projekt(archiwum, [sklep], None)

    opis, _ = czytaj_rozmowe(archiwum, sklep)
    assert (opis.tytul, opis.projekt) == ("Problem z NodeJsTools", "sklep.pl")


def test_lista_do_okna(archiwum):
    sklep, budzik = numer(archiwum, "sklep.pl"), numer(archiwum, "budzik")
    edycja.ukryj_rozmowy(archiwum, [budzik])
    edycja.ustaw_tytul(archiwum, sklep, "Mój tytuł")

    wszystkie = edycja.rozmowy(archiwum)

    assert [(r.id, r.ukryta, r.wlasny_tytul) for r in wszystkie] == [(sklep, False, True), (budzik, True, False)]
    assert [r.id for r in edycja.rozmowy(archiwum, pokaz=W_PAMIECI)] == [sklep]
    assert [r.id for r in edycja.rozmowy(archiwum, pokaz=UKRYTE)] == [budzik]
    assert [r.id for r in edycja.rozmowy(archiwum, "grzałki")] == [budzik]  # treść, także ukrytej
    assert [r.id for r in edycja.rozmowy(archiwum, "mój tyt")] == [sklep]  # fragment tytułu
    assert edycja.rozmowy(archiwum, "nieistniejącesłowoxyz") == []
    assert [r.id for r in edycja.rozmowy(archiwum, projekt="budzik")] == [budzik]


def test_serwer_mcp_nie_pokazuje_ukrytych(archiwum, projekty, sciezki):
    sklep, budzik = numer(archiwum, "sklep.pl"), numer(archiwum, "budzik")
    edycja.ukryj_rozmowy(archiwum, [budzik])
    edycja.ukryj_wiadomosci(archiwum, sklep, [2])
    serwer = utworz_serwer(sciezki, [ClaudeCode(projekty)], synchronizuj=False)

    ukryta = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": budzik}))
    rozmowa = tekst(wywolaj(serwer, "czytaj_rozmowe", {"rozmowa": sklep}))

    assert "jest ukryta" in ukryta and "Grzałka" not in ukryta
    assert "[wiadomość ukryta przez użytkownika]" in rozmowa
    assert "Outlook" not in rozmowa and "załącznik:" not in rozmowa
    assert "NodeJsTools" in rozmowa
    assert "Grzałka" not in tekst(wywolaj(serwer, "szukaj_w_historii", {"zapytanie": "grzałka"}))


# ---------------------------------------------------------------- usuwanie na stałe

def test_usuniecie_na_stale(archiwum, projekty, sciezki):
    sklep = numer(archiwum, "sklep.pl")
    pliki = [sciezki.zalaczniki / w[0] for w in archiwum.execute("SELECT sciezka FROM zalaczniki")]
    surowa = sciezki.surowe / "claude_code" / "sesja-1.jsonl.gz"
    assert surowa.is_file() and len(pliki) == 2

    assert edycja.usun_rozmowy(archiwum, sciezki, [sklep]) == 1

    assert archiwum.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 1
    assert archiwum.execute("SELECT count(*) FROM wiadomosci").fetchone()[0] == 2
    assert archiwum.execute("SELECT count(*) FROM fts_wiadomosci").fetchone()[0] == 2
    assert archiwum.execute("SELECT count(*) FROM zalaczniki").fetchone()[0] == 0
    assert not surowa.exists() and not any(p.exists() for p in pliki)
    assert szukaj(archiwum, "NodeJsTools", Filtry(ukryte=True)) == []

    raport = archiwizuj(archiwum, sciezki, [ClaudeCode(projekty)], pelne=True)

    assert raport.zaktualizowane == 1  # tylko budzik – usuniętej nie wczytuje ponownie
    assert archiwum.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 1
    assert not surowa.exists()


def test_usuniecie_czeka_na_archiwizacje(archiwum, sciezki):
    with blokada(sciezki.blokada, 0):
        with pytest.raises(ArchiwumZajete):
            edycja.usun_rozmowy(archiwum, sciezki, [numer(archiwum, "sklep.pl")], czekaj_s=0)

    assert archiwum.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 2


# ---------------------------------------------------------------- wymazywanie sekretów

@pytest.fixture
def sesja_z_sekretem(projekty):
    zapisz_jsonl(projekty / "C--Projekty-tajne" / "sesja-3.jsonl", [
        {"type": "custom-title", "customTitle": f"Klucz {SEKRET}", "sessionId": "sesja-3"},
        {**uzytkownik(f"Mój klucz API to {SEKRET}, zapisz go w .env", "2026-09-25T10:00:00.000Z"),
         "cwd": r"C:\Projekty\tajne"},
        asystent([{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": ".env"}}],
                 "2026-09-25T10:00:02.000Z"),
        uzytkownik([{"type": "tool_result", "tool_use_id": "t1", "content": f'API_KEY="{SEKRET}"\nDEBUG=1'}],
                   "2026-09-25T10:00:03.000Z"),
        asystent([{"type": "text", "text": f"Zapisałem {SEKRET} w pliku .env."}], "2026-09-25T10:00:05.000Z"),
    ])
    return projekty


def zawartosc_bazy(sciezki) -> bytes:
    return b"".join(p.read_bytes() for p in sciezki.katalog.glob("skerimai.db*"))


def test_policzenie_wystapien_niczego_nie_zmienia(sesja_z_sekretem, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(sesja_z_sekretem)])

    wynik = edycja.policz_wystapienia(polaczenie, sciezki, f"  {SEKRET}\n")

    assert (wynik.wiadomosci, wynik.rozmowy, wynik.tytuly, wynik.surowe) == (2, 1, 1, 1)
    assert szukaj(polaczenie, "ABCD1234xyz")


def test_wymazanie_sekretu(sesja_z_sekretem, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(sesja_z_sekretem)])
    komunikaty = []

    wynik = edycja.wymaz_sekret(polaczenie, sciezki, SEKRET, komunikaty.append)

    assert (wynik.wiadomosci, wynik.rozmowy, wynik.tytuly, wynik.surowe) == (2, 1, 1, 1)
    assert komunikaty[-1] == "Porządkuję bazę…"
    teksty = [w[0] for w in polaczenie.execute("SELECT tekst FROM wiadomosci WHERE instr(tekst, ?)", (ZNACZNIK,))]
    assert teksty == [f"Mój klucz API to {ZNACZNIK}, zapisz go w .env", f"Zapisałem {ZNACZNIK} w pliku .env."]
    assert polaczenie.execute("SELECT tytul FROM rozmowy WHERE sesja = 'sesja-3'").fetchone()[0] == f"Klucz {ZNACZNIK}"
    assert not polaczenie.execute("SELECT 1 FROM fts_slownik WHERE term = 'abcd1234xyz'").fetchone()
    assert not polaczenie.execute("SELECT 1 FROM rdzenie WHERE slowo = 'abcd1234xyz'").fetchone()  # przed szukaj()
    assert szukaj(polaczenie, "ABCD1234xyz", Filtry(ukryte=True)) == []
    assert szukaj(polaczenie, "zapisałem")  # reszta wiadomości dalej w indeksie
    surowa = gzip.decompress((sciezki.surowe / "claude_code" / "sesja-3.jsonl.gz").read_bytes())
    assert SEKRET.encode() not in surowa and b'API_KEY=\\"[wymazano]\\"' in surowa
    assert SEKRET.encode() not in zawartosc_bazy(sciezki)


def test_wymazany_sekret_nie_wraca_z_pliku_aplikacji(sesja_z_sekretem, sciezki, polaczenie):
    archiwizuj(polaczenie, sciezki, [ClaudeCode(sesja_z_sekretem)])
    edycja.wymaz_sekret(polaczenie, sciezki, SEKRET)

    archiwizuj(polaczenie, sciezki, [ClaudeCode(sesja_z_sekretem)], pelne=True)

    assert szukaj(polaczenie, "ABCD1234xyz", Filtry(ukryte=True)) == []
    assert SEKRET.encode() not in gzip.decompress((sciezki.surowe / "claude_code" / "sesja-3.jsonl.gz").read_bytes())
    assert SEKRET.encode() not in zawartosc_bazy(sciezki)
    assert polaczenie.execute("SELECT count(*) FROM sekrety").fetchone()[0] == 1


def test_wymazanie_z_dokumentow(tmp_path, sciezki, polaczenie):
    katalog = tmp_path / "kartridz_sekret"
    (katalog / "attachments").mkdir(parents=True)
    (katalog / "attachments" / "env.txt").write_text(f"API_KEY={SEKRET}\nżółw=1\n", encoding="cp1250")
    (katalog / "attachments" / "umowa.pdf").write_bytes(pdf(f"Klucz {SEKRET}"))
    zapisz_jsonl(katalog / "logs" / "Konfiguracja.jsonl", [
        {"kind": "user", "text": "Pliki konfiguracji", "timestamp": "2026-08-28 piątek 15:15:38",
         "attachments": [{"fname": "env.txt", "path": "env.txt"}, {"fname": "umowa.pdf", "path": "umowa.pdf"}]},
    ])
    archiwizuj(polaczenie, sciezki, [KreionKartridz(katalog)])

    wynik = edycja.wymaz_sekret(polaczenie, sciezki, SEKRET)

    assert wynik.dokumenty == 2 and wynik.niewymazywalne == ["umowa.pdf"]
    assert szukaj(polaczenie, "ABCD1234xyz") == []
    pliki = {w["ext"]: sciezki.zalaczniki / w["sciezka"] for w in polaczenie.execute("SELECT ext, sciezka FROM zalaczniki")}
    assert pliki[".txt"].read_text(encoding="cp1250") == f"API_KEY={ZNACZNIK}\nżółw=1\n"
    assert SEKRET.encode() in pliki[".pdf"].read_bytes()  # PDF zostaje – tylko jego tekst w bazie jest wymazany
    assert SEKRET.encode() not in zawartosc_bazy(sciezki)


def test_sekret_w_nazwie_sesji_zgloszony_a_usuniecie_zabiera_wszystko(tmp_path, sciezki, polaczenie):
    """KreionAI nazywa plik rozmowy jej pierwszą wiadomością – nazwy sesji nie da się wymazać, tylko usunąć rozmowę."""
    katalog = tmp_path / "kartridz_nazwa"
    zapisz_jsonl(katalog / "logs" / f"Klucz {SEKRET}.jsonl", [
        {"kind": "user", "text": f"Klucz {SEKRET}", "timestamp": "2026-08-28 piątek 15:15:38"},
    ])
    kartridz = KreionKartridz(katalog)
    archiwizuj(polaczenie, sciezki, [kartridz])

    wynik = edycja.wymaz_sekret(polaczenie, sciezki, SEKRET)

    assert wynik.wiadomosci == 1 and [r.tytul for r in wynik.w_nazwie_sesji] == [f"Klucz {ZNACZNIK}"]
    edycja.usun_rozmowy(polaczenie, sciezki, [r.id for r in wynik.w_nazwie_sesji])
    archiwizuj(polaczenie, sciezki, [kartridz], pelne=True)

    assert polaczenie.execute("SELECT count(*) FROM rozmowy").fetchone()[0] == 0
    assert SEKRET.encode() not in zawartosc_bazy(sciezki)
    assert not [p for p in sciezki.surowe.rglob("*") if SEKRET in p.name]


@pytest.mark.parametrize("sekret", ["abc", "   ", "wymazano"])
def test_odrzuca_za_krotki_sekret_i_znacznik(archiwum, sciezki, sekret):
    with pytest.raises(ValueError):
        edycja.wymaz_sekret(archiwum, sciezki, sekret)


def test_sekret_w_napisach_json():
    sekret = 'hasło "a/b\\c"'
    wymazane = Sekrety([sekret])
    zagniezdzony = json.dumps({"wynik": json.dumps({"haslo": sekret})})

    for dane in (json.dumps(sekret), json.dumps(sekret, ensure_ascii=False), zagniezdzony,
                 json.dumps(sekret).replace("/", "\\/")):
        bajty = dane.encode("utf-8")
        assert wymazane.wystepuje_w(bajty)
        assert ZNACZNIK.encode() in wymazane.bajty(bajty) and "hasło".encode() not in wymazane.bajty(bajty)


def test_sekret_zapisany_zaszyfrowany(polaczenie):
    sekrety.zapamietaj(polaczenie, SEKRET)
    sekrety.zapamietaj(polaczenie, SEKRET)

    szyfry = [w[0] for w in polaczenie.execute("SELECT szyfr FROM sekrety")]
    assert len(szyfry) == 1 and SEKRET.encode() not in szyfry[0]
    assert SEKRET in sekrety.wczytaj(polaczenie)


def test_nieodszyfrowany_sekret_pominiety(polaczenie):
    polaczenie.execute("INSERT INTO sekrety (szyfr, dodano) VALUES (?, '2026-10-02')", (b"nie DPAPI",))

    assert not sekrety.wczytaj(polaczenie)
