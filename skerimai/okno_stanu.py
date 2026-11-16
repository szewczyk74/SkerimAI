"""Okno „SkerimAI – stan” (menu ikony „Sprawdź stan”) i okno postępu polecenia (kopia zapasowa, aktualizacja,
naprawa konfiguracji) – zamiast konsoli z `pause`. Uruchamiane jak okno pamięci, tym samym programem:
`skerimai-pamiec --stan` i `skerimai-pamiec --polecenie TYTUŁ -- PROGRAM ARGUMENTY…`.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import subprocess
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from . import dialogi, wyglad
from .cli import opis_wyniku, podsumowanie_stanu, znak_wyniku
from .instalacja import BLAD, INFO, UWAGA, Miejsca, Wynik, program_docelowy, sprawdz
from .konfiguracja import Sciezki

log = logging.getLogger("skerimai.okno_stanu")

TYTUL_STANU = "SkerimAI – stan"
_ODSWIEZANIE_MS = 100


def tekst_raportu(wyniki: list[Wynik]) -> str:
    """Jak `skerimai sprawdz` w konsoli – do wklejenia w rozmowę albo zgłoszenie."""
    linie = ["Stan SkerimAI"]
    for w in wyniki:
        opis = opis_wyniku(w)
        linie.append(f"  {znak_wyniku(w)} {w.nazwa}" + (f": {opis}" if opis else ""))
    linie.append(podsumowanie_stanu(wyniki))
    return "\n".join(linie)


def _znacznik(w: Wynik) -> str:
    return {BLAD: "blad", UWAGA: "uwaga", INFO: "info"}.get(w.stan, "ok")


class _Praca:
    """Wątek w tle z wynikami odbieranymi w wątku okna (tkinter nie jest wielowątkowy)."""

    def __init__(self, okno: tk.Misc):
        self.okno = okno
        self._zdarzenia: queue.Queue = queue.Queue()
        self._odbieraj()

    def zlec(self, funkcja: Callable[[], object], po: Callable[[object], None]) -> None:
        def watek() -> None:
            try:
                wynik = funkcja()
            except Exception as blad:  # w oknie, nie w dzienniku bez śladu
                log.exception("praca w tle nie powiodła się")
                wynik = blad
            self._zdarzenia.put((po, wynik))

        threading.Thread(target=watek, daemon=True).start()

    def przekaz(self, funkcja: Callable[[object], None], argument: object) -> None:
        self._zdarzenia.put((funkcja, argument))

    def _odbieraj(self) -> None:
        try:
            while True:
                funkcja, argument = self._zdarzenia.get_nowait()
                funkcja(argument)
        except queue.Empty:
            pass
        if self.okno.winfo_exists():
            self.okno.after(_ODSWIEZANIE_MS, self._odbieraj)


class OknoStanu:
    def __init__(self, okno: tk.Tk, sciezki: Sciezki,
                 sprawdzaj: Callable[[], list[Wynik]] | None = None,
                 polecenie_naprawy: Callable[[], list[str]] | None = None):
        self.okno = okno
        self.sciezki = sciezki
        self.sprawdzaj = sprawdzaj or (lambda: sprawdz(Miejsca(Path.home()), program_docelowy(), sciezki))
        self.polecenie_naprawy = polecenie_naprawy or (lambda: [str(program_docelowy().hook), "konfiguruj"])
        self.wyniki: list[Wynik] = []
        self.zajete = False
        self.paleta = wyglad.zastosuj_motyw(okno)
        self.praca = _Praca(okno)
        self._zbuduj()
        self.odswiez()

    def _zbuduj(self) -> None:
        okno, paleta = self.okno, self.paleta
        okno.title(TYTUL_STANU)
        okno.geometry("900x600")
        okno.minsize(640, 420)
        ramka = ttk.Frame(okno, padding=(18, 16, 18, 14))
        ramka.pack(fill="both", expand=True)

        naglowek = ttk.Frame(ramka)
        naglowek.pack(fill="x")
        self.znak = ttk.Label(naglowek, font=(wyglad.CZCIONKA, 26, "bold"), width=2, anchor="center")
        self.znak.pack(side="left", padx=(0, 12))
        teksty = ttk.Frame(naglowek)
        teksty.pack(side="left", fill="x", expand=True)
        self.tytul = ttk.Label(teksty, font=(wyglad.CZCIONKA, 14, "bold"))
        self.tytul.pack(anchor="w")
        self.podtytul = ttk.Label(teksty, style="Szary.TLabel")
        self.podtytul.pack(anchor="w")
        self.postep = ttk.Progressbar(naglowek, mode="indeterminate", length=140)

        przyciski = ttk.Frame(ramka)
        przyciski.pack(side="bottom", fill="x", pady=(14, 0))  # przed tabelą – w niskim oknie zostają widoczne
        self.szczegoly = tk.Text(ramka, height=4, wrap="word", state="disabled", relief="flat", borderwidth=0,
                                 highlightthickness=0, padx=10, pady=8, cursor="arrow")
        wyglad.ustaw_znaczniki_tresci(self.szczegoly, paleta)
        self.szczegoly.configure(background=paleta.kod_tlo)
        self.szczegoly.pack(side="bottom", fill="x", pady=(10, 0))

        tabela_ramka = ttk.Frame(ramka)
        tabela_ramka.pack(fill="both", expand=True, pady=(14, 0))
        self.tabela = tabela = ttk.Treeview(tabela_ramka, columns=("znak", "nazwa", "opis"), show="headings",
                                            selectmode="browse")
        for kolumna, tekst, szerokosc, wyrownanie, rozciagaj in (
            ("znak", "", 44, "center", False), ("nazwa", "Element", 280, "w", False), ("opis", "Opis", 500, "w", True),
        ):
            tabela.heading(kolumna, text=tekst, anchor=wyrownanie)
            tabela.column(kolumna, width=szerokosc, minwidth=40, anchor=wyrownanie, stretch=rozciagaj)
        # Kolor tylko tam, gdzie trzeba coś zrobić – zielona lista ukrywałaby problemy.
        for znacznik, kolor in (("uwaga", paleta.uwaga), ("blad", paleta.blad), ("info", paleta.szary)):
            tabela.tag_configure(znacznik, foreground=kolor)
        przewijanie = ttk.Scrollbar(tabela_ramka, orient="vertical", command=tabela.yview)
        tabela.configure(yscrollcommand=przewijanie.set)
        przewijanie.pack(side="right", fill="y")
        tabela.pack(fill="both", expand=True)
        tabela.bind("<<TreeviewSelect>>", lambda _: self._pokaz_szczegoly())

        ttk.Button(przyciski, text="Kopiuj raport", command=self.kopiuj).pack(side="left")
        ttk.Button(przyciski, text="Zamknij", command=self.zamknij).pack(side="right")
        self.przycisk_napraw = ttk.Button(przyciski, text="Napraw konfigurację…", command=self.napraw)
        self.przycisk_napraw.pack(side="right", padx=(0, 8))
        wyglad.Dymek(self.przycisk_napraw, "skerimai konfiguruj – wpisuje na nowo hook, serwery MCP i wskazówki")
        self.przycisk_odswiez = ttk.Button(przyciski, text="Sprawdź ponownie", command=self.odswiez,
                                           style="Accent.TButton")
        self.przycisk_odswiez.pack(side="right", padx=(0, 8))
        okno.bind("<F5>", lambda _: self.odswiez())
        okno.bind("<Escape>", lambda _: self.zamknij())
        okno.protocol("WM_DELETE_WINDOW", self.zamknij)

    # ------------------------------------------------------------ sprawdzanie

    def _zajmij(self, zajete: bool) -> None:
        self.zajete = zajete
        stan = "disabled" if zajete else "normal"
        self.przycisk_odswiez.configure(state=stan)
        self.przycisk_napraw.configure(state=stan)
        if zajete:
            self.postep.pack(side="right")
            self.postep.start(12)
        else:
            self.postep.stop()
            self.postep.pack_forget()

    def odswiez(self) -> None:
        if self.zajete:
            return
        self._zajmij(True)
        self.znak.configure(text="…", foreground=self.paleta.szary)
        self.tytul.configure(text="Sprawdzam…")
        self.podtytul.configure(text="Instalacja, konfiguracja Claude Code, Codex i OpenCode, archiwum, źródła rozmów")
        self.praca.zlec(self.sprawdzaj, self._sprawdzono)

    def _sprawdzono(self, wyniki: list[Wynik] | Exception) -> None:
        self._zajmij(False)
        if isinstance(wyniki, Exception):
            self.znak.configure(text="✗", foreground=self.paleta.blad)
            self.tytul.configure(text="Nie udało się sprawdzić stanu")
            self.podtytul.configure(text=f"{type(wyniki).__name__}: {wyniki}")
            return
        self.wyniki = wyniki
        bledy = sum(w.stan == BLAD for w in wyniki)
        uwagi = sum(w.stan == UWAGA for w in wyniki)
        if bledy:
            self.znak.configure(text="✗", foreground=self.paleta.blad)
        elif uwagi:
            self.znak.configure(text="!", foreground=self.paleta.uwaga)
        else:
            self.znak.configure(text="✓", foreground=self.paleta.ok)
        self.tytul.configure(text="Wszystko w porządku" if not bledy and not uwagi
                             else ", ".join(filter(None, [
                                 bledy and f"Błędy: {bledy}", uwagi and f"Uwagi: {uwagi}"])))
        wskazowka = "  ·  wpisy w konfiguracji naprawia „Napraw konfigurację”" if bledy or uwagi else ""
        self.podtytul.configure(text=f"Sprawdzono {datetime.now():%H:%M:%S}{wskazowka}")

        self.tabela.delete(*self.tabela.get_children())
        pierwszy_problem = None
        for nr, w in enumerate(wyniki):
            iid = str(nr)
            self.tabela.insert("", "end", iid=iid, values=(znak_wyniku(w), w.nazwa, " ".join(opis_wyniku(w).split())),
                               tags=(_znacznik(w),))
            if pierwszy_problem is None and w.stan in (BLAD, UWAGA):
                pierwszy_problem = iid
        if pierwszy_problem is not None:
            self.tabela.selection_set(pierwszy_problem)
            self.tabela.see(pierwszy_problem)
        else:
            self._pokaz_szczegoly()

    def _pokaz_szczegoly(self) -> None:
        zaznaczone = self.tabela.selection()
        if zaznaczone:
            w = self.wyniki[int(zaznaczone[0])]
            fragmenty = [(w.nazwa, ("pogrubienie",)), (f"\n{opis_wyniku(w) or w.stan}", ())]
        else:
            fragmenty = [("Zaznacz wiersz, żeby zobaczyć pełny opis.", ("meta",))]
        self.szczegoly.configure(state="normal")
        self.szczegoly.delete("1.0", "end")
        for tekst, znaczniki in fragmenty:
            self.szczegoly.insert("end", tekst, znaczniki)
        self.szczegoly.configure(state="disabled")

    # ------------------------------------------------------------ polecenia

    def kopiuj(self) -> None:
        if not self.wyniki:
            return
        self.okno.clipboard_clear()
        self.okno.clipboard_append(tekst_raportu(self.wyniki))
        self.podtytul.configure(text="Raport skopiowany do schowka.")

    def napraw(self) -> None:
        if self.zajete:
            return
        okno = tk.Toplevel(self.okno)
        OknoPolecenia(okno, "SkerimAI – naprawa konfiguracji", self.polecenie_naprawy(),
                      po=lambda _: self.odswiez())

    def zamknij(self) -> None:
        self.okno.destroy()


class OknoPolecenia:
    """Polecenie SkerimAI z wynikiem na żywo: pasek postępu, wyjście programu i na końcu „Gotowe” albo błąd."""

    def __init__(self, okno: tk.Tk | tk.Toplevel, tytul: str, polecenie: list[str],
                 po: Callable[[int], None] | None = None):
        self.okno = okno
        self.polecenie = polecenie
        self.po = po
        self.kod: int | None = None
        self.proces: subprocess.Popen | None = None
        if isinstance(okno, tk.Tk):
            self.paleta = wyglad.zastosuj_motyw(okno)
        else:
            okno.withdraw()
            self.paleta = wyglad.paleta_okna(okno)
            okno.transient(okno.master.winfo_toplevel())
        self.praca = _Praca(okno)
        self._zbuduj(tytul)
        if not isinstance(okno, tk.Tk):
            wyglad.ciemny_pasek_tytulu(okno, wyglad.ciemny_motyw(okno))
            okno.deiconify()
            okno.grab_set()
        self._uruchom()

    def _zbuduj(self, tytul: str) -> None:
        okno, paleta = self.okno, self.paleta
        okno.title(tytul)
        okno.geometry("760x460")
        okno.minsize(520, 320)
        ramka = ttk.Frame(okno, padding=(18, 16, 18, 14))
        ramka.pack(fill="both", expand=True)
        naglowek = ttk.Frame(ramka)
        naglowek.pack(fill="x")
        self.znak = ttk.Label(naglowek, text="…", font=(wyglad.CZCIONKA, 22, "bold"), width=2, anchor="center",
                              foreground=paleta.szary)
        self.znak.pack(side="left", padx=(0, 10))
        self.stan = ttk.Label(naglowek, text=f"{tytul.removeprefix('SkerimAI – ').capitalize()} – trwa…",
                              font=(wyglad.CZCIONKA, 13, "bold"))
        self.stan.pack(side="left")
        self.postep = ttk.Progressbar(ramka, mode="indeterminate")
        self.postep.pack(fill="x", pady=(12, 0))
        self.postep.start(12)

        przyciski = ttk.Frame(ramka)
        przyciski.pack(side="bottom", fill="x", pady=(14, 0))  # przed wynikiem – w niskim oknie zostają widoczne
        tekst_ramka = ttk.Frame(ramka)
        tekst_ramka.pack(fill="both", expand=True, pady=(12, 0))
        self.wyjscie = tk.Text(tekst_ramka, wrap="word", state="disabled", relief="flat", borderwidth=0,
                               highlightthickness=0, padx=10, pady=8)
        wyglad.ustaw_znaczniki_tresci(self.wyjscie, paleta)
        self.wyjscie.configure(background=paleta.kod_tlo, font=(wyglad.CZCIONKA_KODU, 9))
        self.wyjscie.tag_configure("ok", foreground=paleta.ok)
        self.wyjscie.tag_configure("blad", foreground=paleta.blad)
        przewijanie = ttk.Scrollbar(tekst_ramka, orient="vertical", command=self.wyjscie.yview)
        self.wyjscie.configure(yscrollcommand=przewijanie.set)
        przewijanie.pack(side="right", fill="y")
        self.wyjscie.pack(fill="both", expand=True)

        self.przycisk_zamknij = ttk.Button(przyciski, text="Zamknij", command=self.zamknij, state="disabled",
                                           style="Accent.TButton")
        self.przycisk_zamknij.pack(side="right")
        ttk.Button(przyciski, text="Kopiuj wynik", command=self.kopiuj).pack(side="left")
        okno.protocol("WM_DELETE_WINDOW", self.zamknij)

    def _uruchom(self) -> None:
        srodowisko = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}  # wynik na bieżąco
        try:
            self.proces = subprocess.Popen(
                self.polecenie, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=srodowisko, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as blad:
            self._dopisz(f"Nie udało się uruchomić: {blad}\n")
            self._koniec(-1)
            return
        threading.Thread(target=self._czytaj, daemon=True).start()

    def _czytaj(self) -> None:
        assert self.proces is not None and self.proces.stdout is not None
        for linia in iter(self.proces.stdout.readline, b""):
            tekst = linia.decode("utf-8", "replace").replace("\r\n", "\n")
            self.praca.przekaz(self._dopisz, tekst.split("\r")[-1])  # pasek postępu uv nadpisuje linię przez \r
        self.praca.przekaz(self._koniec, self.proces.wait())

    def _dopisz(self, tekst: str) -> None:
        if not self.okno.winfo_exists():
            return
        na_dole = self.wyjscie.yview()[1] >= 0.999
        self.wyjscie.configure(state="normal")
        znacznik = "blad" if tekst.lstrip().startswith(("✗", "Traceback", "Błąd")) else (
            "ok" if tekst.lstrip().startswith("✓") else ())
        self.wyjscie.insert("end", tekst, znacznik)
        self.wyjscie.configure(state="disabled")
        if na_dole:
            self.wyjscie.see("end")

    def _koniec(self, kod: int) -> None:
        self.kod = kod
        if not self.okno.winfo_exists():
            return
        self.postep.stop()
        self.postep.configure(mode="determinate", value=100)
        if kod == 0:
            self.znak.configure(text="✓", foreground=self.paleta.ok)
            self.stan.configure(text="Gotowe")
        else:
            self.znak.configure(text="✗", foreground=self.paleta.blad)
            self.stan.configure(text=f"Nie udało się (kod {kod}) – szczegóły poniżej")
        self.przycisk_zamknij.configure(state="normal")
        self.przycisk_zamknij.focus_set()
        self.okno.bind("<Return>", lambda _: self.zamknij())
        self.okno.bind("<Escape>", lambda _: self.zamknij())
        if self.po:
            self.po(kod)

    def kopiuj(self) -> None:
        self.okno.clipboard_clear()
        self.okno.clipboard_append(self.wyjscie.get("1.0", "end").rstrip())

    def zamknij(self) -> None:
        if self.kod is None:
            dialogi.pokaz(self.okno, self.okno.title(), "Polecenie jeszcze trwa – przerwanie mogłoby zostawić "
                                                         "niedokończoną kopię albo instalację. Poczekaj na koniec.")
            return
        self.okno.destroy()


def main(argv: list[str]) -> None:
    from .okno_pamieci import uruchom_okno

    parser = argparse.ArgumentParser(prog="skerimai-pamiec")
    tryb = parser.add_mutually_exclusive_group(required=True)
    tryb.add_argument("--stan", action="store_true")
    tryb.add_argument("--polecenie", metavar="TYTUŁ")
    parser.add_argument("program", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.stan:
        uruchom_okno(TYTUL_STANU, OknoStanu)
        return
    polecenie = args.program[1:] if args.program[:1] == ["--"] else args.program
    if not polecenie:
        parser.error("brak polecenia po --")
    uruchom_okno(args.polecenie, lambda okno, _: OknoPolecenia(okno, args.polecenie, polecenie))
