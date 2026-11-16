"""Ikona w zasobniku sterowana z zewnątrz: czy działa, zamknięcie, uruchomienie, autostart z Windows.

Sam program ikony jest w `ikona.py` (punkt wejścia `skerimai-zasobnik`, bez okna konsoli). Działająca ikona trzyma
blokadę `zasobnik.lock` – druga kopia nie wystartuje, a `dziala()` widzi ją bez przeglądania procesów. Zamknięcie:
plik `zasobnik.stop`, który ikona sprawdza co sekundę.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .blokada import blokada
from .konfiguracja import Sciezki

NAZWA_AUTOSTARTU = "SkerimAI"
KLUCZ_AUTOSTARTU = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Wyłączenie w Menedżerze zadań → Uruchamianie zostawia wpis w Run, a tu zapisuje, że Windows ma go pominąć.
KLUCZ_ZATWIERDZEN = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
CZEKAJ_NA_ZAMKNIECIE_S = 15.0


def start_ikony() -> None:
    """Punkt wejścia `skerimai-zasobnik`: najpierw konsola, dopiero potem wczytanie programu ikony (~0,6 s)."""
    schowaj_wlasna_konsole()
    from .ikona import main

    main()


def start_okna_pamieci() -> None:
    """Punkt wejścia `skerimai-pamiec` (okno edycji pamięci) – konsola jak przy ikonie. Z argumentami to okno stanu
    albo postępu polecenia (`okno_stanu.py`) – ten sam program, bez osobnego pliku .exe w każdej kopii."""
    schowaj_wlasna_konsole()
    if len(sys.argv) > 1:
        from .okno_stanu import main as okno_stanu

        okno_stanu(sys.argv[1:])
        return
    from .okno_pamieci import main

    main()


def schowaj_wlasna_konsole() -> bool:
    """`pythonw.exe` w venv z Pythona 3.13.0 uruchamia konsolowy `python.exe`, więc ikona dostaje od Windows nowe,
    widoczne okno konsoli. Chowa je tylko wtedy, gdy konsola należy wyłącznie do tego procesu – uruchomiona
    z terminala ikona (albo testy) nie zabierze terminala. True, gdy było co schować."""
    if os.name != "nt":
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    okno = kernel32.GetConsoleWindow()
    procesy = (ctypes.c_uint32 * 2)()
    if not okno or kernel32.GetConsoleProcessList(procesy, 2) != 1:
        return False
    user32 = ctypes.WinDLL("user32")
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow(okno, 0)  # SW_HIDE
    kernel32.FreeConsole()
    sys.stdin = sys.stdout = sys.stderr = None  # jak w pythonw – uchwyty odłączonej konsoli są już nieważne
    return True


def dziala(sciezki: Sciezki) -> bool:
    if not sciezki.blokada_zasobnika.is_file():
        return False
    with blokada(sciezki.blokada_zasobnika, 0) as wolna:
        return not wolna


def zakoncz(sciezki: Sciezki, czekaj_s: float = CZEKAJ_NA_ZAMKNIECIE_S) -> bool:
    """Prosi działającą ikonę o zamknięcie i czeka na nie. True, gdy ikona już nie działa (także gdy nie działała)."""
    if not dziala(sciezki):
        return True
    sciezki.koniec_zasobnika.write_bytes(b"")
    try:
        koniec = time.monotonic() + czekaj_s
        while dziala(sciezki):
            if time.monotonic() >= koniec:
                return False
            time.sleep(0.2)
        return True
    finally:
        sciezki.koniec_zasobnika.unlink(missing_ok=True)


def uruchom(program: Path, *argumenty: str) -> subprocess.Popen:
    """Bez czekania i bez potoków – ikona działa dalej po zamknięciu konsoli, z której ją uruchomiono."""
    if os.name == "nt":
        opcje = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        opcje = {"start_new_session": True}
    return subprocess.Popen([str(program), *argumenty], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True, **opcje)


# ---------------------------------------------------------------- autostart (HKCU, bez uprawnień administratora)

def polecenie_autostartu(program: Path) -> str:
    return f'"{program}"'


def wpis_autostartu() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KLUCZ_AUTOSTARTU) as klucz:
            return str(winreg.QueryValueEx(klucz, NAZWA_AUTOSTARTU)[0])
    except OSError:
        return None


def ustaw_autostart(polecenie: str | None) -> None:
    """Wpisuje polecenie albo (None) usuwa wpis; inne programy w Run zostają."""
    import winreg

    if polecenie is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KLUCZ_AUTOSTARTU, 0, winreg.KEY_SET_VALUE) as klucz:
                winreg.DeleteValue(klucz, NAZWA_AUTOSTARTU)
        except FileNotFoundError:
            pass
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KLUCZ_AUTOSTARTU) as klucz:
        winreg.SetValueEx(klucz, NAZWA_AUTOSTARTU, 0, winreg.REG_SZ, polecenie)


def autostart_wylaczony() -> bool:
    """Pierwszy bajt wpisu: parzysty – włączony, nieparzysty (03) – wyłączony przez użytkownika."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KLUCZ_ZATWIERDZEN) as klucz:
            dane = winreg.QueryValueEx(klucz, NAZWA_AUTOSTARTU)[0]
    except OSError:
        return False
    return isinstance(dane, bytes) and bool(dane) and dane[0] & 1 == 1


# ---------------------------------------------------------------- procesy

def liczba_procesow(nazwa: str) -> int:
    """Ile działa procesów o tej nazwie pliku, np. skerimai-mcp.exe – jeden na otwartą sesję Claude Code lub Codex."""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    class Proces(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Proces)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    migawka = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if migawka in (None, wintypes.HANDLE(-1).value):
        return 0
    try:
        wpis = Proces(dwSize=ctypes.sizeof(Proces))
        ile, jest = 0, kernel32.Process32FirstW(migawka, ctypes.byref(wpis))
        while jest:
            ile += wpis.szExeFile.lower() == nazwa.lower()
            jest = kernel32.Process32NextW(migawka, ctypes.byref(wpis))
        return ile
    finally:
        kernel32.CloseHandle(migawka)
