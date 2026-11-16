"""Powiadomienie Windows (toast) – wynik archiwizacji w tle, która nie ma okna.

Bez dodatkowych pakietów: WinRT przez Windows PowerShell 5.1 (PowerShell 7 nie ma projekcji WinRT).
Program bez instalatora nie ma własnego AppUserModelID, a toast z niezarejestrowanym Windows 10
po cichu odrzuca – dlatego rejestracja w HKCU (nazwa w nagłówku toastu: „SkerimAI”).
Wyłączenie: zmienna środowiskowa SKERIMAI_POWIADOMIENIA=0.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
from xml.sax.saxutils import escape

log = logging.getLogger("skerimai.powiadomienie")

AUMID = "SkerimAI.Archiwum"
NAZWA = "SkerimAI"
_KLUCZ = rf"Software\Classes\AppUserModelId\{AUMID}"

# Treść toastu przychodzi w zmiennej środowiskowej, więc skrypt jest stały – żadnego cytowania tekstu.
# Same typy .NET, bez cmdletów: cmdlet uruchamia szukanie modułów, także na udziałach sieciowych.
_SKRYPT = r"""$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml($env:SKERIMAI_TOAST)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:SKERIMAI_AUMID).Show($toast)
"""
# Przed pierwszym pokazanym powiadomieniem Windows nie ma ustawień aplikacji – Setting jest pusty (wtedy "").
_SKRYPT_USTAWIENIA = r"""$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
"$([Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:SKERIMAI_AUMID).Setting)"
"""


def _sam_komunikat(skrypt: str) -> str:
    """Błąd jako jedna linia tekstu – bez tego PowerShell z -EncodedCommand oddaje go na stderr w XML (CLIXML)."""
    return f"try {{\n{skrypt}}} catch {{ [Console]::Error.WriteLine($_.Exception.Message); exit 1 }}\n"


def wlaczone() -> bool:
    return os.name == "nt" and os.environ.get("SKERIMAI_POWIADOMIENIA") != "0"


def xml_toastu(naglowek: str, tresc: str) -> str:
    return (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{escape(naglowek)}</text><text>{escape(tresc)}</text>"
        "</binding></visual></toast>"
    )


def pokaz(naglowek: str, tresc: str) -> None:
    """Pokazuje toast. Nigdy nie zgłasza wyjątku – nieudane powiadomienie trafia tylko do dziennika."""
    if not wlaczone():
        return
    try:
        zarejestruj_aplikacje()
        wynik = powershell(_sam_komunikat(_SKRYPT), {"SKERIMAI_TOAST": xml_toastu(naglowek, tresc)})
    except (OSError, subprocess.SubprocessError) as blad:
        log.warning("powiadomienie nie wyświetlone: %s", blad)
        return
    if wynik.returncode:
        log.warning("powiadomienie nie wyświetlone (PowerShell, kod %s): %s",
                    wynik.returncode, wynik.stderr.decode("oem", "replace").strip()[:500])


def ustawienie_windows() -> str:
    """Czy Windows pokaże toasty SkerimAI: Enabled, DisabledForApplication, DisabledForUser, DisabledByGroupPolicy…
    Pusty tekst – SkerimAI nie pokazał jeszcze żadnego powiadomienia, więc Windows nie ma jego ustawień."""
    wynik = powershell(_sam_komunikat(_SKRYPT_USTAWIENIA))
    if wynik.returncode:
        raise OSError(wynik.stderr.decode("oem", "replace").strip()[:300])
    return wynik.stdout.decode("oem", "replace").strip()


def zarejestrowana() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KLUCZ) as klucz:
            return winreg.QueryValueEx(klucz, "DisplayName")[0] == NAZWA
    except OSError:
        return False


def zarejestruj_aplikacje() -> bool:
    """Wpis AppUserModelID w HKCU; True, gdy trzeba go było dodać."""
    import winreg

    if zarejestrowana():
        return False
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _KLUCZ) as klucz:
        winreg.SetValueEx(klucz, "DisplayName", 0, winreg.REG_SZ, NAZWA)
    return True


def wyrejestruj_aplikacje() -> bool:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _KLUCZ)
    except FileNotFoundError:
        return False
    return True


def powershell(skrypt: str, srodowisko: dict[str, str] | None = None,
               timeout: float | None = 30) -> subprocess.CompletedProcess:
    """Windows PowerShell 5.1 bez okna; skrypt jako -EncodedCommand, dane przez zmienne środowiskowe."""
    program = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), r"System32\WindowsPowerShell\v1.0\powershell.exe")
    return subprocess.run(
        [program, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(skrypt.encode("utf-16-le")).decode("ascii")],
        env={**os.environ, "SKERIMAI_AUMID": AUMID, **(srodowisko or {})},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=timeout,
    )
