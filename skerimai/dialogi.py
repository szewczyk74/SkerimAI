"""Okienka pytań i komunikatów w motywie okien SkerimAI – zamiast `messagebox` i `simpledialog` z tkinter, które
w trybie ciemnym zostają jasne, a pole projektu nie podpowiada istniejących projektów."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import wyglad

_SZEROKOSC_TEKSTU = 460


class _Dialog:
    def __init__(self, rodzic: tk.Misc, tytul: str):
        self.wynik = None
        self.okno = okno = tk.Toplevel(rodzic)
        okno.withdraw()  # pasek tytułu w kolorze motywu i położenie – przed pokazaniem
        okno.title(tytul)
        okno.transient(rodzic.winfo_toplevel())
        okno.resizable(False, False)
        okno.protocol("WM_DELETE_WINDOW", self.anuluj)
        okno.bind("<Escape>", lambda _: self.anuluj())
        self.ramka = ttk.Frame(okno, padding=(20, 18, 20, 16))
        self.ramka.pack(fill="both", expand=True)
        self.przyciski = ttk.Frame(self.ramka)

    def tekst(self, tekst: str, znak: str = "", kolor: str | None = None) -> None:
        wiersz = ttk.Frame(self.ramka)
        wiersz.pack(fill="x")
        if znak:
            ttk.Label(wiersz, text=znak, foreground=kolor, font=(wyglad.CZCIONKA, 20, "bold")).pack(
                side="left", anchor="n", padx=(0, 14))
        ttk.Label(wiersz, text=tekst, wraplength=_SZEROKOSC_TEKSTU, justify="left").pack(side="left", anchor="w")

    def przycisk(self, tekst: str, wynik: object, glowny: bool = False) -> ttk.Button:
        przycisk = ttk.Button(self.przyciski, text=tekst, command=lambda: self.zamknij(wynik),
                              style="Accent.TButton" if glowny else "TButton")
        przycisk.pack(side="right", padx=(8, 0))
        return przycisk

    def pokaz(self, fokus: tk.Misc):
        self.przyciski.pack(fill="x", pady=(18, 0))
        okno = self.okno
        rodzic = okno.master.winfo_toplevel()
        okno.update_idletasks()
        if rodzic.winfo_viewable():  # na środku okna, z którego pytamy
            x = rodzic.winfo_rootx() + (rodzic.winfo_width() - okno.winfo_reqwidth()) // 2
            y = rodzic.winfo_rooty() + (rodzic.winfo_height() - okno.winfo_reqheight()) // 3
            okno.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        wyglad.ciemny_pasek_tytulu(okno, wyglad.ciemny_motyw(okno))
        okno.deiconify()
        okno.grab_set()
        fokus.focus_set()
        okno.wait_window()
        return self.wynik

    def zamknij(self, wynik: object) -> None:
        self.wynik = wynik
        self.okno.grab_release()
        self.okno.destroy()

    def anuluj(self) -> None:
        self.zamknij(None)


def pytaj(rodzic: tk.Misc, tytul: str, tekst: str, tak: str = "Tak", nie: str = "Anuluj",
          ostrzezenie: bool = False) -> bool:
    """Domyślna (Enter) jest odpowiedź bezpieczna – przy ostrzeżeniu „nie”."""
    dialog = _Dialog(rodzic, tytul)
    paleta = wyglad.paleta_okna(rodzic)
    dialog.tekst(tekst, "!" if ostrzezenie else "?", paleta.blad if ostrzezenie else paleta.odnosnik)
    przycisk_nie = dialog.przycisk(nie, False, glowny=not ostrzezenie)
    przycisk_tak = dialog.przycisk(tak, True, glowny=ostrzezenie)
    domyslny = przycisk_nie if ostrzezenie else przycisk_tak
    dialog.okno.bind("<Return>", lambda _: domyslny.invoke())
    return bool(dialog.pokaz(domyslny))


def pokaz(rodzic: tk.Misc, tytul: str, tekst: str, blad: bool = False) -> None:
    dialog = _Dialog(rodzic, tytul)
    paleta = wyglad.paleta_okna(rodzic)
    dialog.tekst(tekst, "✗" if blad else "i", paleta.blad if blad else paleta.odnosnik)
    ok = dialog.przycisk("OK", None, glowny=True)
    dialog.okno.bind("<Return>", lambda _: ok.invoke())
    dialog.pokaz(ok)


def zapytaj(rodzic: tk.Misc, tytul: str, etykieta: str, wartosc: str = "",
            podpowiedzi: list[str] | None = None) -> str | None:
    """Tekst od użytkownika albo None (Anuluj). Z `podpowiedziami` pole jest listą rozwijaną z możliwością
    wpisania własnej wartości."""
    dialog = _Dialog(rodzic, tytul)
    ttk.Label(dialog.ramka, text=etykieta, wraplength=_SZEROKOSC_TEKSTU, justify="left").pack(anchor="w")
    zmienna = tk.StringVar(value=wartosc)
    if podpowiedzi is None:
        pole = ttk.Entry(dialog.ramka, textvariable=zmienna, width=56)
    else:
        pole = ttk.Combobox(dialog.ramka, textvariable=zmienna, values=podpowiedzi, width=54)
    pole.pack(fill="x", pady=(8, 0))
    pole.select_range(0, "end")
    dialog.przycisk("Anuluj", None)
    ok = dialog.przycisk("Zapisz", True, glowny=True)
    pole.bind("<Return>", lambda _: ok.invoke())
    return zmienna.get() if dialog.pokaz(pole) else None
