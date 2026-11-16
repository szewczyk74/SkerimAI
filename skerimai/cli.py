"""Wiersz poleceń: skerimai archiwizuj | szukaj | rozmowy | czytaj | zalacznik | stat | kopia | instaluj | konfiguruj
| sprawdz | hook."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from . import zasobnik
from .adaptery import ADAPTERY, ZRODLA_POMOCNICZE
from .archiwizator import archiwizuj
from .baza import polacz
from .blokada import CZEKAJ_NA_ARCHIWUM_S, blokada
from .hook import zlec_archiwizacje
from .instalacja import (
    BLAD,
    INFO,
    OK,
    UWAGA,
    ZMIANY,
    BladInstalacji,
    Miejsca,
    Program,
    Wynik,
    biezacy_program,
    instaluj,
    konfiguruj,
    program_docelowy,
    sprawdz,
)
from .konfiguracja import domyslne_sciezki
from .kopia import utworz_kopie
from .model import ROLA_UZYTKOWNIK
from .zapytania import (
    Filtry,
    czas_lokalny,
    czytaj_rozmowe,
    lista_rozmow,
    statystyki,
    szukaj,
    znajdz_zalacznik,
)


def _filtry(args: argparse.Namespace) -> Filtry:
    return Filtry(zrodlo=args.zrodlo, projekt=args.projekt, od=args.od, do=args.do)


def _data(tekst: str) -> str:
    try:
        date.fromisoformat(tekst)
    except ValueError:
        raise argparse.ArgumentTypeError(f"oczekiwana data RRRR-MM-DD, jest {tekst!r}") from None
    return tekst


def _dodaj_filtry(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--zrodlo", choices=list(ADAPTERY),
        help=f"tylko jedno źródło; bez tej opcji pomijane są kopie robocze: {', '.join(sorted(ZRODLA_POMOCNICZE))}",
    )
    parser.add_argument("--projekt", help="fragment nazwy projektu, np. sklep")
    parser.add_argument("--od", type=_data, help="data początkowa RRRR-MM-DD")
    parser.add_argument("--do", type=_data, help="data końcowa RRRR-MM-DD (włącznie)")


def _archiwizuj(args, polaczenie, sciezki) -> None:
    adaptery = [ADAPTERY[n]() for n in (args.zrodlo or list(ADAPTERY))]
    with blokada(sciezki.blokada, CZEKAJ_NA_ARCHIWUM_S) as uzyskana:
        if not uzyskana:
            raise SystemExit(f"Archiwum zajęte przez inny proces dłużej niż {CZEKAJ_NA_ARCHIWUM_S:.0f} s – spróbuj później.")
        raport = archiwizuj(polaczenie, sciezki, adaptery, pelne=args.pelne)
    print(raport)


def _szukaj(args, polaczenie, sciezki) -> None:
    trafienia = szukaj(polaczenie, " ".join(args.zapytanie), _filtry(args), args.limit)
    if not trafienia:
        print("Brak wyników.")
        return
    for i, t in enumerate(trafienia, 1):
        kto = "Ty" if t.rola == ROLA_UZYTKOWNIK else "AI"
        print(f"[{i}] {czas_lokalny(t.ts)} · {t.zrodlo} · {t.projekt or '—'} · rozmowa #{t.rozmowa_id} „{t.tytul or ''}”")
        dokument = f", 📎 {t.zalacznik or ''} ({t.sha256[:10]})" if t.sha256 else ""
        print(f"    wiad. {t.lp} ({kto}){dokument}: {t.fragment}\n")


def _rozmowy(args, polaczenie, sciezki) -> None:
    for r in lista_rozmow(polaczenie, _filtry(args), args.limit):
        print(
            f"#{r.id:<5} {czas_lokalny(r.pierwsza_ts)} → {czas_lokalny(r.ostatnia_ts)}  "
            f"{r.zrodlo:<15} {(r.projekt or '—'):<16} {r.liczba_wiad:>4} wiad.  {r.tytul or ''}"
        )


def _czytaj(args, polaczenie, sciezki) -> None:
    opis, wiadomosci = czytaj_rozmowe(polaczenie, args.rozmowa, args.od_wiad, args.ile)
    if opis is None:
        print(f"Nie ma rozmowy #{args.rozmowa}.")
        return
    ukryta = " · ukryta (poza wyszukiwaniem)" if opis.ukryta else ""
    print(f"Rozmowa #{opis.id} „{opis.tytul or ''}” · {opis.zrodlo} · {opis.projekt or '—'} · {opis.liczba_wiad} wiad."
          f"{ukryta}\n")
    for w in wiadomosci:
        kto = "Ty" if w.rola == ROLA_UZYTKOWNIK else "AI"
        print(f"── [{w.lp}] {kto} · {czas_lokalny(w.ts)}" + (" · ukryta" if w.ukryta else ""))
        print(w.tekst)
        for z in w.zalaczniki:
            print(f"   📎 {z.nazwa or ''} → {sciezki.zalaczniki / z.sciezka}")
        print()
    nastepna = (wiadomosci[0].lp if wiadomosci else max(args.od_wiad, 0)) + len(wiadomosci)
    if nastepna < opis.liczba_wiad:
        print(f"… dalej: skerimai czytaj {opis.id} --od-wiad {nastepna}")


def _zalacznik(args, polaczenie, sciezki) -> None:
    zalacznik = znajdz_zalacznik(polaczenie, args.sha)
    if zalacznik is None:
        print("Nie znaleziono (albo skrót niejednoznaczny).")
        return
    print(sciezki.zalaczniki / zalacznik.sciezka)
    if args.tekst:
        print(f"\n{zalacznik.tekst}" if zalacznik.tekst else "Brak tekstu (obraz, skan albo nieobsługiwany format).")


def _stat(args, polaczenie, sciezki) -> None:
    for klucz, wartosc in statystyki(polaczenie).items():
        if klucz in ("najstarsza", "najnowsza"):
            wartosc = czas_lokalny(wartosc)
        print(f"{klucz:>14}: {wartosc}")
    print(f"{'katalog':>14}: {sciezki.katalog}")


def _kopia(args, polaczenie, sciezki) -> None:
    try:
        wynik = utworz_kopie(sciezki, Path(args.folder))
    except (ValueError, TimeoutError) as blad:
        raise SystemExit(str(blad))
    rozmiar = f"{wynik.bajty / 1_000_000:.1f}".replace(".", ",")
    print(f"Kopia gotowa: {wynik.katalog}")
    print(f"  rozmowy: {wynik.rozmowy}, wiadomości: {wynik.wiadomosci}, pliki: {wynik.pliki}, {rozmiar} MB")
    print(f'Żeby używać kopii jako archiwum: setx SKERIMAI_DIR "{wynik.katalog}"')


_ZNAKI = {BLAD: "✗", UWAGA: "!", INFO: "•", "pominięto": "•", "brak": "•"}


def znak_wyniku(w: Wynik) -> str:
    return _ZNAKI.get(w.stan, "✓")


def opis_wyniku(w: Wynik) -> str:
    """Opis z nazwą zmiany („dodano”, „bez zmian”…), gdy stan nie jest samym ok / błąd / uwaga / info."""
    return ", ".join(filter(None, ["" if w.stan in (OK, BLAD, UWAGA, INFO) else w.stan, w.opis]))


def podsumowanie_stanu(wyniki: list[Wynik]) -> str:
    bledy = sum(w.stan == BLAD for w in wyniki)
    uwagi = sum(w.stan == UWAGA for w in wyniki)
    if not bledy and not uwagi:
        return "Wszystko w porządku."
    return f"Błędy: {bledy}, uwagi: {uwagi}. Wpisy w konfiguracji naprawia: skerimai konfiguruj"


def _wypisz(wyniki: list[Wynik]) -> None:
    for w in wyniki:
        opis = opis_wyniku(w)
        print(f"  {znak_wyniku(w)} {w.nazwa}" + (f": {opis}" if opis else ""))


def _konfiguruj(args, sciezki) -> None:
    _konfiguruj_program(program_docelowy(), args.usun, sciezki)


def _konfiguruj_program(program: Program, usun: bool, sciezki) -> None:
    zamknieta = usun and zasobnik.dziala(sciezki) and zasobnik.zakoncz(sciezki)
    try:
        wyniki = konfiguruj(Miejsca(Path.home()), program, usun=usun)
    except FileNotFoundError as blad:
        raise SystemExit(str(blad))
    print(f"{'Usuwanie' if usun else 'Konfiguracja'} SkerimAI ({program.skrypty})")
    _wypisz(wyniki)
    if zamknieta:
        print("  ✓ Ikona w zasobniku: zamknięta")
    elif not usun:
        _uruchom_ikone(program, sciezki)
    if any(w.stan in ZMIANY for w in wyniki):
        print("Zmiany zadziałają po ponownym uruchomieniu Claude Code i Codex.")
    if usun:
        print(f"Archiwum zostaje w {sciezki.katalog}.")
    if any(w.stan == BLAD for w in wyniki):
        raise SystemExit(1)
    if not usun:
        print("Stan całości: skerimai sprawdz")


def _uruchom_ikone(program: Program, sciezki) -> None:
    """Od razu, nie dopiero po ponownym zalogowaniu do Windows."""
    if os.name != "nt" or not program.zasobnik.is_file() or zasobnik.dziala(sciezki):
        return
    try:
        zasobnik.uruchom(program.zasobnik)
    except OSError as blad:
        print(f"  ✗ Ikona w zasobniku: nie uruchomiła się ({blad})")
        return
    print("  ✓ Ikona w zasobniku: uruchomiona")


def _zasobnik(args, sciezki) -> None:
    """Gdy kółka nie widać, choć `sprawdz` mówi, że ikona działa – zamyka ją i uruchamia od nowa."""
    program = program_docelowy()
    if args.restart and zasobnik.dziala(sciezki):
        if not zasobnik.zakoncz(sciezki):
            raise SystemExit("Ikona nie zamknęła się w ciągu 15 s – zakończ skerimai-zasobnik.exe w Menedżerze zadań.")
        print("  ✓ Ikona w zasobniku: zamknięta")
    elif zasobnik.dziala(sciezki):
        print("  ✓ Ikona w zasobniku: już działa (nie widać jej? skerimai zasobnik --restart)")
        return
    if not program.zasobnik.is_file():
        raise SystemExit(f"Brak {program.zasobnik} – najpierw skerimai instaluj")
    _uruchom_ikone(program, sciezki)


def _instaluj(args, sciezki) -> None:
    zamknieta = []

    def zamknij_zasobnik() -> bool:
        zamknieta.append(True)
        return zasobnik.zakoncz(sciezki)

    try:
        program = instaluj(biezacy_program(), zamknij_zasobnik=zamknij_zasobnik, wersja=args.wersja)
    except BladInstalacji as blad:
        if zamknieta:  # ikona nie może zniknąć przez nieudaną aktualizację
            _uruchom_ikone(program_docelowy(), sciezki)
        raise SystemExit(str(blad))
    if zasobnik.dziala(sciezki):  # ikona z innej kopii, np. rozwojowej – ma działać ta zainstalowana
        zasobnik.zakoncz(sciezki)
    commit = f", commit {program.commit[:7]}" if program.commit else ""
    print(f"Zainstalowano SkerimAI {program.wersja}{commit} – {program.skrypty}")
    print("Nowa wersja zadziała w nowych sesjach Claude Code i Codex.")
    _konfiguruj_program(program, False, sciezki)


def _sprawdz(args, sciezki) -> None:
    wyniki = sprawdz(Miejsca(Path.home()), program_docelowy(), sciezki)
    print("Stan SkerimAI")
    _wypisz(wyniki)
    print(podsumowanie_stanu(wyniki))
    if any(w.stan == BLAD for w in wyniki):
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    for strumien in (sys.stdout, sys.stderr):
        strumien.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="skerimai", description="Trwała pamięć rozmów z AI")
    polecenia = parser.add_subparsers(dest="polecenie", required=True)

    p = polecenia.add_parser("archiwizuj", help="wczytaj nowe i zmienione rozmowy")
    p.add_argument("--pelne", action="store_true", help="wczytaj wszystko od nowa")
    p.add_argument("--zrodlo", action="append", choices=list(ADAPTERY), help="tylko wybrane źródło")
    p.set_defaults(funkcja=_archiwizuj)

    p = polecenia.add_parser("szukaj", help="szukaj w historii")
    p.add_argument("zapytanie", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    _dodaj_filtry(p)
    p.set_defaults(funkcja=_szukaj)

    p = polecenia.add_parser("rozmowy", help="lista rozmów")
    p.add_argument("--limit", type=int, default=30)
    _dodaj_filtry(p)
    p.set_defaults(funkcja=_rozmowy)

    p = polecenia.add_parser("czytaj", help="pokaż rozmowę")
    p.add_argument("rozmowa", type=int, help="numer rozmowy (#)")
    p.add_argument("--od-wiad", type=int, default=0, help="od której wiadomości; ujemna liczy od końca")
    p.add_argument("--ile", type=int, default=20)
    p.set_defaults(funkcja=_czytaj)

    p = polecenia.add_parser("zalacznik", help="ścieżka załącznika po skrócie SHA-256")
    p.add_argument("sha")
    p.add_argument("--tekst", action="store_true", help="pokaż też tekst wyciągnięty z dokumentu")
    p.set_defaults(funkcja=_zalacznik)

    p = polecenia.add_parser("stat", help="statystyki archiwum")
    p.set_defaults(funkcja=_stat)

    p = polecenia.add_parser("kopia", help="spójna kopia archiwum, także w trakcie pracy aplikacji")
    p.add_argument("folder", help="gdzie utworzyć podkatalog skerimai-RRRR-MM-DD_GGMMSS")
    p.set_defaults(funkcja=_kopia)

    p = polecenia.add_parser("instaluj", help="zainstaluj lub zaktualizuj kopię używaną (uv tool): z kopii rozwojowej"
                                              " bieżący commit, poza nią najnowsze wydanie z GitHuba; przy zamkniętych sesjach")
    p.add_argument("--wersja", help="wydanie z GitHuba, np. 0.2.0 (także z kopii rozwojowej)")
    p.set_defaults(funkcja=_instaluj, bez_bazy=True)

    p = polecenia.add_parser("konfiguruj", help="wpisz SkerimAI do Claude Code i Codex (hook, serwer MCP, wskazówki)")
    p.add_argument("--usun", action="store_true", help="usuń wszystkie wpisy SkerimAI (archiwum zostaje)")
    p.set_defaults(funkcja=_konfiguruj, bez_bazy=True)

    p = polecenia.add_parser("zasobnik", help="uruchom ikonę w zasobniku, jeśli nie działa")
    p.add_argument("--restart", action="store_true", help="zamknij działającą ikonę i uruchom ją od nowa")
    p.set_defaults(funkcja=_zasobnik, bez_bazy=True)

    p = polecenia.add_parser("sprawdz", help="stan instalacji, konfiguracji i archiwum")
    p.set_defaults(funkcja=_sprawdz, bez_bazy=True)

    polecenia.add_parser("hook", help="dla hooka SessionEnd w Claude Code: archiwizacja w tle, powrót od razu")

    args = parser.parse_args(argv)
    if args.polecenie == "hook":  # bez otwierania bazy – Claude Code daje hookowi 1,5 s
        zlec_archiwizacje(sys.stdin.buffer)
        return
    sciezki = domyslne_sciezki()
    if getattr(args, "bez_bazy", False):  # polacz() założyłby pustą bazę, a sprawdz ma zobaczyć jej brak
        args.funkcja(args, sciezki)
        return
    polaczenie = polacz(sciezki.baza)
    try:
        args.funkcja(args, polaczenie, sciezki)
    finally:
        polaczenie.close()


if __name__ == "__main__":
    main()
