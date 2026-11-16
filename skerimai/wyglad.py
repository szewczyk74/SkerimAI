"""Wygląd okien SkerimAI: motyw Sun Valley (sv-ttk) jasny albo ciemny według ustawienia Windows, kolory treści,
dymki z podpowiedziami, zapamiętany układ okna i lekkie formatowanie Markdowna w treści wiadomości."""

from __future__ import annotations

import json
import logging
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

log = logging.getLogger("skerimai.wyglad")

JASNY, CIEMNY = "light", "dark"
CZCIONKA = "Segoe UI"
CZCIONKA_KODU = "Cascadia Mono"  # Windows 11 i Terminal; gdy jej nie ma, Tk bierze najbliższą stałej szerokości

NAZWY_ZRODEL = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "kreion_kartridz": "KreionAI",
    "kreion_silniki": "KreionAI (silniki)",
    "cursor": "Cursor",
    "opencode": "OpenCode",
}


@dataclass(frozen=True)
class Paleta:
    tlo: str
    tekst: str
    szary: str
    odnosnik: str
    ty: str
    ai: str
    kod_tlo: str
    kafel_tlo: str
    zebra: str
    ok: str
    uwaga: str
    blad: str


PALETY = {
    JASNY: Paleta(tlo="#ffffff", tekst="#1c1c1c", szary="#767676", odnosnik="#005fb8", ty="#005fb8", ai="#8a3fb8",
                  kod_tlo="#f3f3f3", kafel_tlo="#eef3fa", zebra="#f3f3f3",
                  ok="#0f7b0f", uwaga="#9d5d00", blad="#c42b1c"),
    CIEMNY: Paleta(tlo="#202020", tekst="#e8e8e8", szary="#9a9a9a", odnosnik="#60cdff", ty="#60cdff", ai="#d3a6f5",
                   kod_tlo="#2c2c2c", kafel_tlo="#26303b", zebra="#232323",
                   ok="#6ccb5f", uwaga="#fce100", blad="#ff99a4"),
}


def tryb_systemu() -> str:
    """Tryb aplikacji z ustawień Windows (Personalizacja → Kolory); poza Windows i przy braku wpisu – jasny."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as klucz:
            jasny, _ = winreg.QueryValueEx(klucz, "AppsUseLightTheme")
    except (ImportError, OSError):
        return JASNY
    return JASNY if jasny else CIEMNY


def zastosuj_motyw(okno: tk.Tk, tryb: str | None = None) -> Paleta:
    """Motyw Sun Valley dla całej aplikacji; bez sv-ttk zostaje motyw domyślny i jasna paleta."""
    tryb = tryb or tryb_systemu()
    try:
        import sv_ttk

        sv_ttk.set_theme(tryb, okno)
    except Exception:  # brak pakietu albo plików motywu – okno ma działać i bez niego
        log.warning("bez motywu sv-ttk", exc_info=True)
        tryb = JASNY
    styl = ttk.Style(okno)
    wiersz = tkfont.Font(okno, family=CZCIONKA, size=10).metrics("linespace")
    styl.configure("Treeview", rowheight=wiersz + 10)
    styl.configure("Szary.TLabel", foreground=PALETY[tryb].szary)
    return PALETY[tryb]


def ciemny_motyw(okno: tk.Misc) -> bool:
    return ttk.Style(okno).theme_use() == "sun-valley-dark"


def paleta_okna(okno: tk.Misc) -> Paleta:
    """Paleta motywu, który aplikacja już ma (`zastosuj_motyw`) – dla okien otwieranych później."""
    return PALETY[CIEMNY if ciemny_motyw(okno) else JASNY]


def ostry_obraz_na_ekranach_hidpi() -> None:
    """Bez tego Windows rysuje okna i ikony w 96 DPI i rozciąga je na ekranach 125–200 % – rozmyte."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def ciemny_pasek_tytulu(okno: tk.Misc, ciemny: bool) -> None:
    """Pasek tytułu w kolorze motywu (DWMWA_USE_IMMERSIVE_DARK_MODE, Windows 10 20H1+). Działa na oknie jeszcze
    niepokazanym – pokazane odświeżyłoby pasek dopiero po zmianie aktywności."""
    try:
        import ctypes

        okno.update_idletasks()
        uchwyt = ctypes.windll.user32.GetParent(okno.winfo_id())
        wartosc = ctypes.c_int(int(ciemny))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(uchwyt, 20, ctypes.byref(wartosc), ctypes.sizeof(wartosc))
    except (AttributeError, OSError):
        pass


class Dymek:
    """Podpowiedź po chwili nad elementem – tu skróty klawiszowe, które wcześniej były w napisach przycisków."""

    def __init__(self, element: tk.Misc, tekst: str, opoznienie_ms: int = 500):
        self.element, self.tekst, self.opoznienie = element, tekst, opoznienie_ms
        self._czekanie: str | None = None
        self._okno: tk.Toplevel | None = None
        element.bind("<Enter>", self._zaplanuj, add="+")
        element.bind("<Leave>", self._schowaj, add="+")
        element.bind("<ButtonPress>", self._schowaj, add="+")

    def _zaplanuj(self, _=None) -> None:
        self._schowaj()
        self._czekanie = self.element.after(self.opoznienie, self._pokaz)

    def _pokaz(self) -> None:
        x = self.element.winfo_rootx() + 8
        y = self.element.winfo_rooty() + self.element.winfo_height() + 4
        self._okno = okno = tk.Toplevel(self.element)
        okno.wm_overrideredirect(True)
        okno.wm_geometry(f"+{x}+{y}")
        ttk.Label(okno, text=self.tekst, padding=(8, 4), relief="solid", borderwidth=1).pack()

    def _schowaj(self, _=None) -> None:
        if self._czekanie:
            self.element.after_cancel(self._czekanie)
            self._czekanie = None
        if self._okno is not None:
            self._okno.destroy()
            self._okno = None


# ------------------------------------------------------------ układ okna

_GEOMETRIA = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")  # jak wm_geometry: lewy monitor to „+-1920”


def wczytaj_uklad(plik: Path) -> dict:
    try:
        uklad = json.loads(plik.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return uklad if isinstance(uklad, dict) else {}


def zapisz_uklad(plik: Path, uklad: dict) -> None:
    try:
        plik.write_text(json.dumps(uklad, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("nie zapisano układu okna", exc_info=True)


def geometria_na_ekranie(geometria: object) -> str | None:
    """Zapisana geometria, o ile jej lewy górny róg leży na którymś monitorze – po odłączeniu monitora okno
    otworzyłoby się poza ekranem. Bez Windows sprawdzany jest tylko zapis."""
    if not isinstance(geometria, str) or not (dopasowanie := _GEOMETRIA.match(geometria)):
        return None
    x, y = int(dopasowanie[3]), int(dopasowanie[4])
    try:
        import ctypes
        from ctypes import wintypes

        punkt = wintypes.POINT(x + 40, y + 10)
        if not ctypes.windll.user32.MonitorFromPoint(punkt, 0):  # MONITOR_DEFAULTTONULL
            return f"{dopasowanie[1]}x{dopasowanie[2]}"
    except (AttributeError, OSError):
        pass
    return geometria


# ------------------------------------------------------------ treść wiadomości

_BLOK_KODU = re.compile(r"^\s*```")
_NAGLOWEK = re.compile(r"^#{1,6}\s+(.*)$")
_LISTA = re.compile(r"^(\s*)(?:[-*+]|(\d+[.)]))\s+(.*)$")
_CYTAT = re.compile(r"^>\s?(.*)$")
_W_LINII = re.compile(r"(`[^`\n]+`|\*\*[^*\n]+\*\*)")


def fragmenty(tekst: str) -> list[tuple[str, tuple[str, ...]]]:
    """Tekst wiadomości jako (fragment, znaczniki) dla tk.Text: bloki ```kodu```, nagłówki, listy, cytaty, `kod`
    i **pogrubienie**. Tylko to, co agenci piszą najczęściej – reszta Markdowna zostaje jak jest."""
    wynik: list[tuple[str, tuple[str, ...]]] = []
    w_kodzie = poprzednia_kodem = False
    for linia in tekst.split("\n"):
        if _BLOK_KODU.match(linia):
            w_kodzie = not w_kodzie
            continue
        if wynik:  # koniec poprzedniej linii – w bloku kodu z tłem na całą szerokość
            wynik.append(("\n", ("kod",) if poprzednia_kodem else ()))
        poprzednia_kodem = w_kodzie
        if w_kodzie:
            wynik.append((linia, ("kod",)))
        elif dopasowanie := _NAGLOWEK.match(linia):
            wynik += _w_linii(dopasowanie[1], ("naglowek",))
        elif dopasowanie := _LISTA.match(linia):
            wciecie, numer, reszta = dopasowanie[1], dopasowanie[2], dopasowanie[3]
            znacznik = "lista2" if len(wciecie.expandtabs(4)) >= 2 else "lista"
            wynik.append((f"{numer} " if numer else "• ", (znacznik,)))
            wynik += _w_linii(reszta, (znacznik,))
        elif dopasowanie := _CYTAT.match(linia):
            wynik += _w_linii(dopasowanie[1], ("cytat",))
        else:
            wynik += _w_linii(linia, ())
    return [(t, z) for t, z in wynik if t]


def _w_linii(linia: str, znaczniki: tuple[str, ...]) -> list[tuple[str, tuple[str, ...]]]:
    wynik = []
    for czesc in _W_LINII.split(linia):
        if len(czesc) > 2 and czesc.startswith("`") and czesc.endswith("`"):
            wynik.append((czesc[1:-1], (*znaczniki, "kod_w_linii")))
        elif len(czesc) > 4 and czesc.startswith("**") and czesc.endswith("**"):
            wynik.append((czesc[2:-2], (*znaczniki, "pogrubienie")))
        else:
            wynik.append((czesc, znaczniki))
    return wynik


def ustaw_znaczniki_tresci(tresc: tk.Text, paleta: Paleta) -> None:
    rozmiar = 10
    tresc.configure(background=paleta.tlo, foreground=paleta.tekst, insertbackground=paleta.tekst,
                    selectbackground="#2f60d8", selectforeground="#ffffff", font=(CZCIONKA, rozmiar),
                    spacing1=2, spacing3=2)
    tresc.tag_configure("autor_ty", foreground=paleta.ty, font=(CZCIONKA, rozmiar + 1, "bold"))
    tresc.tag_configure("autor_ai", foreground=paleta.ai, font=(CZCIONKA, rozmiar + 1, "bold"))
    tresc.tag_configure("meta", foreground=paleta.szary)
    tresc.tag_configure("naglowek", font=(CZCIONKA, rozmiar + 2, "bold"), spacing1=8, spacing3=2)
    tresc.tag_configure("pogrubienie", font=(CZCIONKA, rozmiar, "bold"))
    tresc.tag_configure("kod", font=(CZCIONKA_KODU, rozmiar - 1), background=paleta.kod_tlo,
                        lmargin1=12, lmargin2=12, rmargin=12, spacing1=0, spacing3=0)
    tresc.tag_configure("kod_w_linii", font=(CZCIONKA_KODU, rozmiar - 1), background=paleta.kod_tlo)
    tresc.tag_configure("lista", lmargin1=8, lmargin2=22)
    tresc.tag_configure("lista2", lmargin1=26, lmargin2=40)
    tresc.tag_configure("cytat", foreground=paleta.szary, lmargin1=12, lmargin2=12, font=(CZCIONKA, rozmiar, "italic"))
    tresc.tag_configure("podpis", foreground=paleta.szary, spacing1=10)
    tresc.tag_configure("kafel", background=paleta.kafel_tlo, lmargin1=8, lmargin2=8, rmargin=8,
                        spacing1=3, spacing3=3)
    tresc.tag_configure("odnosnik", foreground=paleta.odnosnik)
    tresc.tag_configure("rozmiar", foreground=paleta.szary)


def rozmiar_pliku(bajty: int | None) -> str:
    if bajty is None:
        return "brak pliku"
    if bajty < 1024:
        return f"{bajty} B"
    for jednostka in ("KB", "MB", "GB"):
        bajty /= 1024
        if bajty < 1024 or jednostka == "GB":
            return f"{bajty:.0f} {jednostka}" if bajty >= 10 else f"{bajty:.1f} {jednostka}".replace(".", ",")
    raise AssertionError
