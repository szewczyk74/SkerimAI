"""Okno „SkerimAI – pamięć” (`skerimai-pamiec`, w menu ikony „Edytuj pamięć…”): przegląd rozmów w archiwum i ich
edycja – ukrywanie rozmów i wiadomości, własny tytuł i projekt, usuwanie na stałe, wymazywanie sekretów.

Zmiany zapisują się od razu. Usuwanie i wymazywanie trwają dłużej (blokada archiwizatora, surowe kopie, VACUUM),
więc działają w osobnym wątku z własnym połączeniem do bazy – okno w tym czasie nie zamarza, ale nie przyjmuje
innych zmian.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox, ttk

from . import dialogi, edycja, wyglad
from .adaptery import ADAPTERY
from .baza import polacz
from .edycja import UKRYTE, W_PAMIECI, WSZYSTKIE, RozmowaEdycji, Wystapienia
from .konfiguracja import Sciezki, domyslne_sciezki
from .model import ROLA_UZYTKOWNIK
from .tekst import liczba
from .zapytania import WiadomoscArchiwum, czas_lokalny, czytaj_rozmowe

log = logging.getLogger("skerimai.okno")

TYTUL_OKNA = "SkerimAI – pamięć"
W_PAMIECI_ZNAK, UKRYTA_ZNAK = "☑", "☐"
_WLASNE = " ✎"  # tytuł lub projekt nadany ręcznie
_PODGLAD = 160
_MAKS_TYTULOW_W_PYTANIU = 5
_ZRODLA = {nazwa: zrodlo for zrodlo, nazwa in wyglad.NAZWY_ZRODEL.items()}  # z listy „Źródło” do nazwy w bazie
_ROZMIAR_DZIENNIKA = 500_000

_OPIS_SEKRETU = (
    "Sekret (hasło, klucz API…) zostanie zamieniony na [wymazano] we wszystkich rozmowach: w wiadomościach, "
    "tytułach, indeksie wyszukiwania, tekście dokumentów, plikach tekstowych załączników i surowych kopiach sesji "
    "(tam są wyniki narzędzi, np. odczytany plik .env). SkerimAI zapamięta go zaszyfrowany przez Windows, żeby nie "
    "wrócił przy następnej archiwizacji.\n\n"
    "Bez zmian zostają: pliki samych aplikacji (Claude Code, Codex, KreionAI), wcześniejsze kopie zapasowe "
    "archiwum oraz pliki PDF i DOCX w załącznikach (wymazany jest tylko ich tekst w bazie)."
)


def opis_wystapien(w: Wystapienia) -> str:
    czesci = []
    if w.wiadomosci:
        czesci.append(f"{liczba(w.wiadomosci, 'wiadomość', 'wiadomości', 'wiadomości')} "
                      f"w {liczba(w.rozmowy, 'rozmowie', 'rozmowach', 'rozmowach')}")
    if w.tytuly:
        czesci.append(liczba(w.tytuly, "tytuł", "tytuły", "tytułów"))
    if w.dokumenty:
        czesci.append(liczba(w.dokumenty, "dokument", "dokumenty", "dokumentów"))
    if w.surowe:
        czesci.append(liczba(w.surowe, "surowa kopia sesji", "surowe kopie sesji", "surowych kopii sesji"))
    return ", ".join(czesci) or "nigdzie"


def _jedna_linia(tekst: str, dlugosc: int = _PODGLAD) -> str:
    tekst = " ".join(tekst.split())
    return tekst if len(tekst) <= dlugosc else tekst[:dlugosc - 1] + "…"


class OknoPamieci:
    def __init__(self, okno: tk.Tk, sciezki: Sciezki):
        self.okno = okno
        self.sciezki = sciezki
        self.polaczenie = polacz(sciezki.baza)
        self.rozmowy: dict[int, RozmowaEdycji] = {}
        self.wiadomosci: dict[int, WiadomoscArchiwum] = {}
        self.zalaczniki: list[Path] = []  # pliki pokazanej wiadomości, w kolejności linii „📎” w treści
        self.biezaca: int | None = None
        self.zajete = False
        self._zdarzenia: queue.Queue = queue.Queue()
        self.paleta = wyglad.zastosuj_motyw(okno)
        self._zbuduj()
        self._przywroc_uklad()
        self.odswiez()
        self.okno.after(100, self._odbierz)

    # ------------------------------------------------------------ budowa okna

    def _zbuduj(self) -> None:
        okno = self.okno
        okno.title(TYTUL_OKNA)
        okno.geometry("1200x780")
        okno.minsize(900, 560)
        okno.protocol("WM_DELETE_WINDOW", self.zamknij)

        self.stan = tk.StringVar()
        ttk.Label(okno, textvariable=self.stan, anchor="w", padding=(12, 4, 12, 6), style="Szary.TLabel").pack(
            side="bottom", fill="x")
        ttk.Separator(okno).pack(side="bottom", fill="x")
        glowna = ttk.Frame(okno, padding=(12, 12, 12, 6))
        glowna.pack(fill="both", expand=True)

        pasek = ttk.Frame(glowna)
        pasek.pack(fill="x")
        self.szukane = tk.StringVar()
        self.zrodlo = tk.StringVar(value=WSZYSTKIE)
        self.projekt = tk.StringVar(value=WSZYSTKIE)
        self.pokaz = tk.StringVar(value=WSZYSTKIE)
        pole = ttk.Entry(pasek, textvariable=self.szukane, width=34)
        pole.pack(side="left", padx=(0, 6))
        pole.bind("<Return>", lambda _: self.odswiez())
        wyglad.Dymek(pole, "Słowa z treści rozmów albo fragment tytułu – Enter szuka")
        ttk.Button(pasek, text="Szukaj", command=self.odswiez, style="Accent.TButton").pack(side="left", padx=(0, 18))
        for etykieta, zmienna, wartosci, szerokosc, stale in (
            ("Źródło", self.zrodlo, [WSZYSTKIE, *(wyglad.NAZWY_ZRODEL.get(z, z) for z in ADAPTERY)], 18, True),
            ("Projekt", self.projekt, [WSZYSTKIE], 20, False),
            ("Pokaż", self.pokaz, [WSZYSTKIE, W_PAMIECI, UKRYTE], 11, True),
        ):
            ttk.Label(pasek, text=etykieta, style="Szary.TLabel").pack(side="left")
            wybor = ttk.Combobox(pasek, textvariable=zmienna, values=wartosci, width=szerokosc,
                                 state="readonly" if stale else "normal")
            wybor.pack(side="left", padx=(6, 14))
            wybor.bind("<<ComboboxSelected>>", lambda _: self.odswiez())
            wybor.bind("<Return>", lambda _: self.odswiez())
            if zmienna is self.projekt:
                self.wybor_projektu = wybor
        ttk.Button(pasek, text="Wymaż sekret…", command=self.wymaz_sekret).pack(side="right")

        self.podzial = podzial = ttk.Panedwindow(glowna, orient="vertical")
        podzial.pack(fill="both", expand=True, pady=(12, 0))

        gora = ttk.Frame(podzial)
        podzial.add(gora, weight=3)
        self.lista = self._tabela(gora, [
            ("pamiec", "W pamięci", 80, "center", False), ("data", "Ostatnia wiadomość", 130, "w", False),
            ("zrodlo", "Źródło", 120, "w", False), ("projekt", "Projekt", 150, "w", False),
            ("tytul", "Tytuł", 500, "w", True), ("wiad", "Wiad.", 60, "e", False),
        ])
        przyciski = ttk.Frame(gora)
        przyciski.pack(fill="x", pady=(8, 8))
        for tekst, polecenie, podpowiedz in (
            ("W pamięci / ukryta", self.przelacz_rozmowy, "Spacja – ukrywa albo przywraca zaznaczone rozmowy"),
            ("Zmień tytuł…", self.zmien_tytul, "F2"),
            ("Zmień projekt…", self.zmien_projekt, "Też z menu pod prawym przyciskiem myszy"),
            ("Usuń na stałe…", self.usun, "Delete – usuwa z archiwum bez możliwości cofnięcia"),
        ):
            przycisk = ttk.Button(przyciski, text=tekst, command=polecenie)
            przycisk.pack(side="left", padx=(0, 6))
            wyglad.Dymek(przycisk, podpowiedz)
        self.licznik = tk.StringVar()
        ttk.Label(przyciski, textvariable=self.licznik, style="Szary.TLabel").pack(side="right")

        self.dol = dol = ttk.Panedwindow(podzial, orient="horizontal")
        podzial.add(dol, weight=2)
        lewa = ttk.Frame(dol)
        dol.add(lewa, weight=1)
        self.lista_wiadomosci = self._tabela(lewa, [
            ("pamiec", "W pamięci", 80, "center", False), ("nr", "#", 50, "center", False), ("kto", "Kto", 50, "center", False),
            ("czas", "Czas", 125, "w", False), ("tekst", "Początek", 320, "w", True),
        ])
        przycisk = ttk.Button(lewa, text="W pamięci / ukryta", command=self.przelacz_wiadomosci)
        przycisk.pack(anchor="w", pady=(8, 0))
        wyglad.Dymek(przycisk, "Spacja – ukrywa albo przywraca zaznaczone wiadomości")
        prawa = ttk.Frame(dol, padding=(8, 0, 0, 0))
        dol.add(prawa, weight=1)
        self.tresc = tk.Text(prawa, wrap="word", state="disabled", relief="flat", borderwidth=0,
                             highlightthickness=0, padx=14, pady=10, cursor="arrow")
        wyglad.ustaw_znaczniki_tresci(self.tresc, self.paleta)
        przewijanie = ttk.Scrollbar(prawa, orient="vertical", command=self.tresc.yview)
        self.tresc.configure(yscrollcommand=przewijanie.set)
        przewijanie.pack(side="right", fill="y")
        self.tresc.pack(fill="both", expand=True)

        self.lista.bind("<Button-1>", lambda e: self._klik(e, self.lista, self.przelacz_rozmowy))
        self.lista.bind("<space>", lambda _: self.przelacz_rozmowy())
        self.lista.bind("<F2>", lambda _: self.zmien_tytul())
        self.lista.bind("<Delete>", lambda _: self.usun())
        self.lista.bind("<<TreeviewSelect>>", lambda _: self._wybrano_rozmowe())
        self.lista.bind("<Button-3>", self._menu_rozmowy)
        self.lista_wiadomosci.bind("<Button-1>", lambda e: self._klik(e, self.lista_wiadomosci, self.przelacz_wiadomosci))
        self.lista_wiadomosci.bind("<space>", lambda _: self.przelacz_wiadomosci())
        self.lista_wiadomosci.bind("<<TreeviewSelect>>", lambda _: self._wybrano_wiadomosc())

        self.menu = tk.Menu(okno, tearoff=False)
        self.menu.add_command(label="W pamięci / ukryta", command=self.przelacz_rozmowy)
        self.menu.add_command(label="Zmień tytuł…", command=self.zmien_tytul)
        self.menu.add_command(label="Zmień projekt…", command=self.zmien_projekt)
        self.menu.add_command(label="Przywróć tytuł i projekt ze źródła", command=self.przywroc_ze_zrodla)
        self.menu.add_separator()
        self.menu.add_command(label="Usuń na stałe…", command=self.usun)
        pole.focus_set()

    def _tabela(self, rodzic: ttk.Frame, kolumny: list[tuple[str, str, int, str, bool]]) -> ttk.Treeview:
        ramka = ttk.Frame(rodzic)
        ramka.pack(fill="both", expand=True)
        tabela = ttk.Treeview(ramka, columns=[k[0] for k in kolumny], show="headings", selectmode="extended")
        for nazwa, naglowek, szerokosc, wyrownanie, rozciagaj in kolumny:
            tabela.heading(nazwa, text=naglowek, anchor="center" if wyrownanie == "center" else wyrownanie)
            tabela.column(nazwa, width=szerokosc, minwidth=40, anchor=wyrownanie, stretch=rozciagaj)
        tabela.tag_configure("parzysta", background=self.paleta.zebra)
        tabela.tag_configure("ukryta", foreground=self.paleta.szary)
        przewijanie = ttk.Scrollbar(ramka, orient="vertical", command=tabela.yview)
        tabela.configure(yscrollcommand=przewijanie.set)
        przewijanie.pack(side="right", fill="y")
        tabela.pack(fill="both", expand=True)
        return tabela

    @staticmethod
    def _znaczniki(tabela: ttk.Treeview, iid: str, ukryta: bool) -> tuple[str, ...]:
        """Naprzemienne tło wierszy i wyszarzenie ukrytych – przy każdej zmianie wiersza oba naraz."""
        parzysta = ("parzysta",) if tabela.index(iid) % 2 else ()
        return (*parzysta, "ukryta") if ukryta else parzysta

    def _przywroc_uklad(self) -> None:
        uklad = wyglad.wczytaj_uklad(self.sciezki.uklad_okna)
        if geometria := wyglad.geometria_na_ekranie(uklad.get("geometria")):
            self.okno.geometry(geometria)
        if uklad.get("zmaksymalizowane"):
            self.okno.state("zoomed")

        def podzialy() -> None:
            for podzial, klucz in ((self.podzial, "podzial_pion"), (self.dol, "podzial_poziom")):
                if isinstance(uklad.get(klucz), int):
                    try:
                        podzial.sashpos(0, uklad[klucz])
                    except tk.TclError:
                        pass

        self.okno.after(150, podzialy)  # pozycje podziałów da się ustawić dopiero w rozłożonym oknie

    def _zapisz_uklad(self) -> None:
        uklad = wyglad.wczytaj_uklad(self.sciezki.uklad_okna)
        stan = self.okno.state()
        if stan == "normal":  # zmaksymalizowane zostawia rozmiar sprzed maksymalizacji
            uklad["geometria"] = self.okno.wm_geometry()
        uklad["zmaksymalizowane"] = stan == "zoomed"
        try:
            uklad["podzial_pion"], uklad["podzial_poziom"] = self.podzial.sashpos(0), self.dol.sashpos(0)
        except tk.TclError:
            pass
        wyglad.zapisz_uklad(self.sciezki.uklad_okna, uklad)

    # ------------------------------------------------------------ lista rozmów

    def odswiez(self) -> None:
        zaznaczone = set(self.lista.selection())
        self.okno.configure(cursor="watch")
        self.okno.update_idletasks()
        try:
            wiersze = edycja.rozmowy(
                self.polaczenie, self.szukane.get(),
                zrodlo=None if self.zrodlo.get() == WSZYSTKIE else _ZRODLA.get(self.zrodlo.get(), self.zrodlo.get()),
                projekt=None if self.projekt.get().strip() in ("", WSZYSTKIE) else self.projekt.get().strip(),
                pokaz=self.pokaz.get(),
            )
            projekty = edycja.projekty(self.polaczenie)
        finally:
            self.okno.configure(cursor="")
        self.rozmowy = {r.id: r for r in wiersze}
        self.lista.delete(*self.lista.get_children())
        for nr, r in enumerate(wiersze):
            self.lista.insert("", "end", iid=str(r.id), values=self._wartosci(r),
                              tags=(("parzysta",) if nr % 2 else ()) + (("ukryta",) if r.ukryta else ()))
        self.wybor_projektu.configure(values=[WSZYSTKIE, *projekty])
        pozostale = [i for i in zaznaczone if self.lista.exists(i)]
        self.lista.selection_set(pozostale)
        if not pozostale:
            self._pokaz_wiadomosci(None)
        self._policz()
        self.stan.set("Zmiany zapisują się od razu. Ukryte rozmowy i wiadomości nie trafiają do wyszukiwania "
                      "ani do agentów (Claude Code, Codex); można je przywrócić.")

    @staticmethod
    def _wartosci(r: RozmowaEdycji) -> tuple:
        return (
            UKRYTA_ZNAK if r.ukryta else W_PAMIECI_ZNAK, czas_lokalny(r.ostatnia_ts),
            wyglad.NAZWY_ZRODEL.get(r.zrodlo, r.zrodlo),
            (r.projekt or "—") + (_WLASNE if r.wlasny_projekt else ""),
            (r.tytul or "") + (_WLASNE if r.wlasny_tytul else ""), r.liczba_wiad,
        )

    def _policz(self) -> None:
        ukryte = sum(r.ukryta for r in self.rozmowy.values())
        self.licznik.set(f"{liczba(len(self.rozmowy), 'rozmowa', 'rozmowy', 'rozmów')} na liście, "
                         f"w tym {liczba(ukryte, 'ukryta', 'ukryte', 'ukrytych')}")

    def _zaznaczone_rozmowy(self) -> list[int]:
        return [int(i) for i in self.lista.selection()]

    def przelacz_rozmowy(self, ids: list[int] | None = None) -> None:
        """Wszystkie zaznaczone w pamięci – ukrywa je; inaczej przywraca do pamięci wszystkie."""
        ids = ids or self._zaznaczone_rozmowy()
        if not ids or not self._wolne():
            return
        ukryj = not any(self.rozmowy[i].ukryta for i in ids)
        edycja.ukryj_rozmowy(self.polaczenie, ids, ukryj)
        for i in ids:
            r = self.rozmowy[i]
            r.ukryta = ukryj
            self.lista.item(str(i), values=self._wartosci(r), tags=self._znaczniki(self.lista, str(i), ukryj))
        self._policz()
        self.stan.set(f"{'Ukryto' if ukryj else 'Przywrócono do pamięci'}: "
                      f"{liczba(len(ids), 'rozmowa', 'rozmowy', 'rozmów')}.")

    def zmien_tytul(self) -> None:
        ids = self._zaznaczone_rozmowy()
        if len(ids) != 1:
            dialogi.pokaz(self.okno, TYTUL_OKNA, "Zaznacz jedną rozmowę.")
            return
        if not self._wolne():
            return
        r = self.rozmowy[ids[0]]
        tytul = dialogi.zapytaj(self.okno, "Tytuł rozmowy", "Nowy tytuł (puste – tytuł ze źródła):", r.tytul or "")
        if tytul is None:
            return
        edycja.ustaw_tytul(self.polaczenie, r.id, tytul)
        self.odswiez()

    def zmien_projekt(self) -> None:
        ids = self._zaznaczone_rozmowy()
        if not ids or not self._wolne():
            return
        obecne = {self.rozmowy[i].projekt for i in ids}
        projekt = dialogi.zapytaj(
            self.okno, "Projekt",
            f"Projekt dla: {liczba(len(ids), 'rozmowa', 'rozmowy', 'rozmów')} – istniejący z listy albo nowa nazwa "
            "(puste – projekt ze źródła):",
            next(iter(obecne)) or "" if len(obecne) == 1 else "", podpowiedzi=edycja.projekty(self.polaczenie))
        if projekt is None:
            return
        edycja.ustaw_projekt(self.polaczenie, ids, projekt)
        self.odswiez()

    def przywroc_ze_zrodla(self) -> None:
        ids = self._zaznaczone_rozmowy()
        if not ids or not self._wolne():
            return
        for i in ids:
            edycja.ustaw_tytul(self.polaczenie, i, None)
        edycja.ustaw_projekt(self.polaczenie, ids, None)
        self.odswiez()

    def usun(self) -> None:
        ids = self._zaznaczone_rozmowy()
        if not ids or not self._wolne():
            return
        tytuly = "\n".join(f"• {self.rozmowy[i].tytul or '(bez tytułu)'}" for i in ids[:_MAKS_TYTULOW_W_PYTANIU])
        if len(ids) > _MAKS_TYTULOW_W_PYTANIU:
            tytuly += f"\n• … i {len(ids) - _MAKS_TYTULOW_W_PYTANIU} więcej"
        if not dialogi.pytaj(
            self.okno, "Usuń na stałe",
            f"Usunąć na stałe: {liczba(len(ids), 'rozmowę', 'rozmowy', 'rozmów')}?\n\n{tytuly}\n\n"
            "Tego nie da się cofnąć. Rozmowa zniknie z archiwum razem z surową kopią i załącznikami, których nie ma "
            "w innych rozmowach, i nie zostanie wczytana ponownie, nawet jeśli aplikacja wciąż ją ma. Kopie zapasowe "
            "archiwum zostają bez zmian.\n\nŻeby tylko wyłączyć ją z wyszukiwania, wystarczy ją odznaczyć (ukryć).",
            tak="Usuń na stałe", ostrzezenie=True,
        ):
            return

        def po(usuniete: int) -> None:
            self.odswiez()
            self.stan.set(f"Usunięto na stałe: {liczba(usuniete, 'rozmowa', 'rozmowy', 'rozmów')}.")

        self.w_tle(lambda polaczenie, postep: edycja.usun_rozmowy(polaczenie, self.sciezki, ids), po,
                   "Usuwam rozmowy i porządkuję archiwum…")

    def _menu_rozmowy(self, zdarzenie: tk.Event) -> None:
        wiersz = self.lista.identify_row(zdarzenie.y)
        if not wiersz:
            return
        if wiersz not in self.lista.selection():
            self.lista.selection_set(wiersz)
        self.menu.tk_popup(zdarzenie.x_root, zdarzenie.y_root)

    @staticmethod
    def _klik(zdarzenie: tk.Event, tabela: ttk.Treeview, przelacz: Callable[[list[int]], None]) -> str | None:
        """Klik w pole „W pamięci” przełącza ten jeden wiersz i nie zmienia zaznaczenia."""
        if tabela.identify_region(zdarzenie.x, zdarzenie.y) != "cell" or tabela.identify_column(zdarzenie.x) != "#1":
            return None
        wiersz = tabela.identify_row(zdarzenie.y)
        if wiersz:
            przelacz([int(wiersz)])
        return "break"

    # ------------------------------------------------------------ wiadomości rozmowy

    def _wybrano_rozmowe(self) -> None:
        ids = self._zaznaczone_rozmowy()
        self._pokaz_wiadomosci(ids[0] if len(ids) == 1 else None)
        if len(ids) > 1:
            self._pokaz_tekst(f"Zaznaczono {liczba(len(ids), 'rozmowę', 'rozmowy', 'rozmów')}.")

    def _pokaz_wiadomosci(self, rozmowa_id: int | None) -> None:
        if rozmowa_id == self.biezaca and rozmowa_id is not None:
            return
        self.biezaca = rozmowa_id
        self.lista_wiadomosci.delete(*self.lista_wiadomosci.get_children())
        self.wiadomosci = {}
        self._pokaz_tekst("")
        if rozmowa_id is None:
            return
        _, wiadomosci = czytaj_rozmowe(self.polaczenie, rozmowa_id, 0, 1_000_000)
        for nr, w in enumerate(wiadomosci):
            self.wiadomosci[w.lp] = w
            self.lista_wiadomosci.insert("", "end", iid=str(w.lp), values=self._wartosci_wiadomosci(w),
                                         tags=(("parzysta",) if nr % 2 else ()) + (("ukryta",) if w.ukryta else ()))

    @staticmethod
    def _wartosci_wiadomosci(w: WiadomoscArchiwum) -> tuple:
        return (UKRYTA_ZNAK if w.ukryta else W_PAMIECI_ZNAK, w.lp, "Ty" if w.rola == ROLA_UZYTKOWNIK else "AI",
                czas_lokalny(w.ts), _jedna_linia(w.tekst) + (" 📎" if w.zalaczniki else ""))

    def _wybrano_wiadomosc(self) -> None:
        zaznaczone = self.lista_wiadomosci.selection()
        if len(zaznaczone) != 1:
            self._pokaz_tekst("")
            return
        w = self.wiadomosci[int(zaznaczone[0])]
        ty = w.rola == ROLA_UZYTKOWNIK
        naglowek = [("Ty" if ty else "AI", ("autor_ty" if ty else "autor_ai",)),
                    (f"   [{w.lp}] · {czas_lokalny(w.ts)}{' · ukryta' if w.ukryta else ''}\n\n", ("meta",))]
        self._pokaz_tekst(naglowek + wyglad.fragmenty(w.tekst.rstrip()),
                          [(z.nazwa or z.sha256[:12], self.sciezki.zalaczniki / z.sciezka) for z in w.zalaczniki])

    def _pokaz_tekst(self, tekst: str | list[tuple[str, tuple[str, ...]]],
                     zalaczniki: list[tuple[str, Path]] = ()) -> None:
        """Tekst albo fragmenty ze znacznikami (`wyglad.fragmenty`). Załączniki pod treścią jako kafelki do
        kliknięcia – otwierają plik z magazynu w jego programie."""
        self.zalaczniki = [plik for _, plik in zalaczniki]
        self.tresc.configure(state="normal")
        self.tresc.delete("1.0", "end")
        for znacznik in self.tresc.tag_names():
            if znacznik.startswith("zalacznik-"):
                self.tresc.tag_delete(znacznik)
        for fragment, znaczniki in [(tekst, ())] if isinstance(tekst, str) else tekst:
            self.tresc.insert("end", fragment, znaczniki)
        if zalaczniki:
            self.tresc.insert("end", "\n\nZałączniki (kliknij, żeby otworzyć):\n", "podpis")
        for nr, (nazwa, plik) in enumerate(zalaczniki):
            znacznik = f"zalacznik-{nr}"
            try:
                rozmiar = plik.stat().st_size
            except OSError:
                rozmiar = None
            self.tresc.insert("end", f"📎  {nazwa}", ("kafel", "odnosnik", znacznik))
            self.tresc.insert("end", f"   {wyglad.rozmiar_pliku(rozmiar)}\n", ("kafel", "rozmiar", znacznik))
            self.tresc.tag_bind(znacznik, "<Button-1>", lambda _, nr=nr: self.otworz_zalacznik(nr))
            self.tresc.tag_bind(znacznik, "<Enter>", lambda _: self.tresc.configure(cursor="hand2"))
            self.tresc.tag_bind(znacznik, "<Leave>", lambda _: self.tresc.configure(cursor=""))
        self.tresc.configure(state="disabled")

    def otworz_zalacznik(self, nr: int) -> None:
        plik = self.zalaczniki[nr]
        try:
            os.startfile(plik)  # program przypisany w Windows do tego rodzaju pliku
        except (OSError, AttributeError) as blad:  # brak pliku albo programu; poza Windows nie ma startfile
            dialogi.pokaz(self.okno, TYTUL_OKNA, f"Nie udało się otworzyć załącznika:\n{plik}\n\n{blad}", blad=True)

    def przelacz_wiadomosci(self, lps: list[int] | None = None) -> None:
        lps = lps or [int(i) for i in self.lista_wiadomosci.selection()]
        if not lps or self.biezaca is None or not self._wolne():
            return
        ukryj = not any(self.wiadomosci[lp].ukryta for lp in lps)
        edycja.ukryj_wiadomosci(self.polaczenie, self.biezaca, lps, ukryj)
        for lp in lps:
            w = self.wiadomosci[lp]
            w.ukryta = ukryj
            self.lista_wiadomosci.item(str(lp), values=self._wartosci_wiadomosci(w),
                                       tags=self._znaczniki(self.lista_wiadomosci, str(lp), ukryj))
        self.stan.set(f"{'Ukryto' if ukryj else 'Przywrócono do pamięci'}: "
                      f"{liczba(len(lps), 'wiadomość', 'wiadomości', 'wiadomości')}.")

    def zaznacz(self, ids: list[int]) -> None:
        """Pokazuje i zaznacza wskazane rozmowy – bez filtrów, które mogłyby je ukryć."""
        self.szukane.set("")
        for zmienna in (self.zrodlo, self.projekt, self.pokaz):
            zmienna.set(WSZYSTKIE)
        self.odswiez()
        widoczne = [str(i) for i in ids if self.lista.exists(str(i))]
        self.lista.selection_set(widoczne)
        if widoczne:
            self.lista.see(widoczne[0])
            self.lista.focus_set()

    # ------------------------------------------------------------ sekrety

    def wymaz_sekret(self) -> None:
        if self._wolne():
            OknoSekretu(self)

    # ------------------------------------------------------------ praca w tle

    def _wolne(self) -> bool:
        if self.zajete:
            self.okno.bell()
        return not self.zajete

    def w_tle(self, praca: Callable, po: Callable[[object], None], opis: str,
              postep: Callable[[str], None] | None = None) -> None:
        """`praca(polaczenie, postep)` w osobnym wątku z własnym połączeniem; `po(wynik)` – znów w wątku okna."""
        self.zajete = True
        self.okno.configure(cursor="watch")
        (postep or self.stan.set)(opis)

        def watek() -> None:
            polaczenie = polacz(self.sciezki.baza)
            try:
                wynik = praca(polaczenie, lambda tekst: self._zdarzenia.put((postep or self.stan.set, tekst)))
            except Exception as blad:
                if not isinstance(blad, (edycja.ArchiwumZajete, ValueError)):
                    log.exception("%s – nie powiodło się", opis)
                self._zdarzenia.put((self._blad, blad))
            else:
                self._zdarzenia.put((self._gotowe, (po, wynik)))
            finally:
                polaczenie.close()

        threading.Thread(target=watek, name="skerimai-pamiec", daemon=True).start()

    def _odbierz(self) -> None:
        try:
            while True:
                funkcja, argument = self._zdarzenia.get_nowait()
                funkcja(argument)
        except queue.Empty:
            pass
        self.okno.after(100, self._odbierz)

    def _gotowe(self, po_i_wynik: tuple) -> None:
        self.zajete = False
        self.okno.configure(cursor="")
        po, wynik = po_i_wynik
        po(wynik)

    def _blad(self, blad: Exception) -> None:
        self.zajete = False
        self.okno.configure(cursor="")
        self.stan.set(f"Nie udało się: {blad}")
        dialogi.pokaz(self.okno, TYTUL_OKNA, str(blad) or type(blad).__name__, blad=True)

    def zamknij(self) -> None:
        if self.zajete:
            dialogi.pokaz(self.okno, TYTUL_OKNA, "Poczekaj, aż skończy się bieżąca operacja.")
            return
        self._zapisz_uklad()
        self.polaczenie.close()
        self.okno.destroy()


class OknoSekretu:
    """Dwa kroki: „Sprawdź, gdzie jest” (bez zmian w archiwum), potem „Wymaż” – dopiero po sprawdzeniu."""

    def __init__(self, glowne: OknoPamieci):
        self.glowne = glowne
        self.okno = okno = tk.Toplevel(glowne.okno)
        okno.withdraw()  # pasek tytułu w kolorze motywu ustawia się przed pokazaniem
        okno.title("Wymaż sekret")
        okno.transient(glowne.okno)
        okno.resizable(False, False)
        ramka = ttk.Frame(okno, padding=14)
        ramka.pack(fill="both", expand=True)
        ttk.Label(ramka, text=_OPIS_SEKRETU, wraplength=520, justify="left").pack(anchor="w")

        wiersz = ttk.Frame(ramka)
        wiersz.pack(fill="x", pady=(12, 4))
        ttk.Label(wiersz, text="Sekret:").pack(side="left")
        self.sekret = tk.StringVar()
        self.pole = ttk.Entry(wiersz, textvariable=self.sekret, show="•", width=48)
        self.pole.pack(side="left", padx=(6, 8), fill="x", expand=True)
        self.jawnie = tk.BooleanVar()
        ttk.Checkbutton(wiersz, text="Pokaż", variable=self.jawnie,
                        command=lambda: self.pole.configure(show="" if self.jawnie.get() else "•")).pack(side="left")
        self.sekret.trace_add("write", lambda *_: self._zmieniono())

        self.wynik = tk.StringVar(value="Najpierw sprawdź, gdzie sekret występuje w archiwum.")
        ttk.Label(ramka, textvariable=self.wynik, wraplength=520, justify="left").pack(anchor="w", pady=(4, 12))

        przyciski = ttk.Frame(ramka)
        przyciski.pack(fill="x")
        self.do_zaznaczenia: list[int] = []
        self.przycisk_zaznacz = ttk.Button(przyciski, text="Zaznacz te rozmowy", command=self.zaznacz_w_oknie)
        ttk.Button(przyciski, text="Zamknij", command=self.zamknij).pack(side="right")
        self.przycisk_wymaz = ttk.Button(przyciski, text="Wymaż", command=self.wymaz, state="disabled")
        self.przycisk_wymaz.pack(side="right", padx=(0, 6))
        ttk.Button(przyciski, text="Sprawdź, gdzie jest", command=self.sprawdz).pack(side="right", padx=(0, 6))
        okno.protocol("WM_DELETE_WINDOW", self.zamknij)
        self.pole.bind("<Return>", lambda _: self.sprawdz())
        wyglad.ciemny_pasek_tytulu(okno, wyglad.ciemny_motyw(okno))
        okno.deiconify()
        okno.grab_set()
        self.pole.focus_set()

    def _zmieniono(self) -> None:
        self.przycisk_wymaz.configure(state="disabled")

    def sprawdz(self) -> None:
        if not self.glowne._wolne():
            return
        sekret = self.sekret.get()

        def po(wynik: Wystapienia) -> None:
            if not wynik.razem:
                self.wynik.set("Tego sekretu nie ma w archiwum." + self._uwaga_o_nazwach(wynik))
                return
            uwaga = (f"\n\nPliki, w których zostanie (wymazany będzie tylko ich tekst w bazie): "
                     f"{', '.join(wynik.niewymazywalne)}." if wynik.niewymazywalne else "")
            self.wynik.set(f"Znaleziono: {opis_wystapien(wynik)}.{uwaga}{self._uwaga_o_nazwach(wynik)}")
            self.przycisk_wymaz.configure(state="normal")

        self.glowne.w_tle(lambda polaczenie, postep: edycja.policz_wystapienia(polaczenie, self.glowne.sciezki, sekret,
                                                                              postep),
                          po, "Szukam…", self.wynik.set)

    def wymaz(self) -> None:
        if not self.glowne._wolne():
            return
        if not dialogi.pytaj(self.okno, "Wymaż sekret", "Wymazać sekret ze wszystkich rozmów? Tego nie da się cofnąć.",
                             tak="Wymaż", ostrzezenie=True):
            return
        sekret = self.sekret.get()
        self.przycisk_wymaz.configure(state="disabled")

        def po(wynik: Wystapienia) -> None:
            self.sekret.set("")
            self.wynik.set(f"Wymazano: {opis_wystapien(wynik)}.{self._uwaga_o_nazwach(wynik)}")
            biezaca, self.glowne.biezaca = self.glowne.biezaca, None
            self.glowne.odswiez()
            self.glowne._pokaz_wiadomosci(biezaca)
            self.glowne.stan.set("Sekret wymazany z archiwum.")

        self.glowne.w_tle(lambda polaczenie, postep: edycja.wymaz_sekret(polaczenie, self.glowne.sciezki, sekret, postep),
                          po, "Wymazuję…", self.wynik.set)

    def _uwaga_o_nazwach(self, wynik: Wystapienia) -> str:
        """Sekret w nazwie sesji (KreionAI nazywa plik rozmowy jej pierwszą wiadomością) – przycisk zaznaczy te
        rozmowy w głównym oknie, żeby można je było usunąć na stałe."""
        self.do_zaznaczenia = [r.id for r in wynik.w_nazwie_sesji]
        if not self.do_zaznaczenia:
            self.przycisk_zaznacz.pack_forget()
            return ""
        self.przycisk_zaznacz.pack(side="left")
        tytuly = ", ".join(f"„{r.tytul or '(bez tytułu)'}”" for r in wynik.w_nazwie_sesji)
        return (f"\n\nSekret jest też w nazwie pliku źródłowego: {tytuly}. Tej nazwy nie da się wymazać, bo wiąże "
                "rozmowę z plikiem aplikacji – jeśli trzeba, usuń tę rozmowę na stałe.")

    def zaznacz_w_oknie(self) -> None:
        ids = self.do_zaznaczenia
        self.zamknij()
        self.glowne.zaznacz(ids)

    def zamknij(self) -> None:
        if self.glowne.zajete:
            self.glowne.okno.bell()
            return
        self.okno.grab_release()
        self.okno.destroy()


def wskaz_biblioteki_tcl() -> None:
    """Python 3.13.0 w środowisku wirtualnym (venv, uv tool) szuka Tcl/Tk obok środowiska, a nie obok Pythona,
    z którego je utworzono – tk.Tk() kończy się „Can't find a usable init.tcl” (poprawione w 3.13.1)."""
    for zmienna, katalog in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        sciezka = Path(sys.base_prefix) / "tcl" / katalog
        if zmienna not in os.environ and sciezka.is_dir():
            os.environ[zmienna] = str(sciezka)


def uruchom_okno(tytul: str, zbuduj: Callable[[tk.Tk, Sciezki], object]) -> None:
    """Start okna SkerimAI jako osobnego programu: dziennik, Tcl/Tk, ostrość na HiDPI, ikona; `zbuduj` stosuje motyw
    (`wyglad.zastosuj_motyw`) i buduje okno, pokazane dopiero potem – z paskiem tytułu w kolorze motywu."""
    sciezki = domyslne_sciezki()
    sciezki.katalog.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[RotatingFileHandler(sciezki.dziennik_zasobnika, maxBytes=_ROZMIAR_DZIENNIKA, backupCount=1,
                                      encoding="utf-8")],
    )
    wskaz_biblioteki_tcl()
    wyglad.ostry_obraz_na_ekranach_hidpi()
    okno = tk.Tk()
    okno.withdraw()  # pokazane dopiero z motywem, układem i paskiem tytułu w kolorze motywu
    okno.report_callback_exception = lambda typ, blad, slad: (
        log.error("błąd w oknie", exc_info=(typ, blad, slad)),
        messagebox.showerror(tytul, f"{type(blad).__name__}: {blad}", parent=okno),
    )
    try:
        from PIL import ImageTk

        from .ikona import ikony_okna

        okno.ikony = [ImageTk.PhotoImage(obraz) for obraz in ikony_okna()]  # Tk nie trzyma referencji
        okno.iconphoto(True, *okno.ikony)
    except Exception:
        log.warning("bez ikony okna", exc_info=True)
    zbuduj(okno, sciezki)
    wyglad.ciemny_pasek_tytulu(okno, wyglad.ciemny_motyw(okno))
    okno.deiconify()
    okno.mainloop()


def main() -> None:
    uruchom_okno(TYTUL_OKNA, OknoPamieci)


if __name__ == "__main__":
    main()
