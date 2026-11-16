"""Wspólny model danych, do którego adaptery sprowadzają formaty aplikacji."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROLA_UZYTKOWNIK = "uzytkownik"
ROLA_ASYSTENT = "asystent"


@dataclass
class Zalacznik:
    """Plik dołączony do wiadomości: albo bajty (np. obraz base64), albo ścieżka na dysku."""

    nazwa: str
    mime: str | None = None
    dane: bytes | None = None
    sciezka: Path | None = None


@dataclass
class Wiadomosc:
    rola: str
    ts: str | None
    tekst: str
    zalaczniki: list[Zalacznik] = field(default_factory=list)


@dataclass
class Rozmowa:
    zrodlo: str
    sesja: str
    pliki: list[Path]
    projekt: str | None = None
    cwd: str | None = None
    galaz: str | None = None
    tytul: str | None = None
    wiadomosci: list[Wiadomosc] = field(default_factory=list)

    @property
    def pierwsza_ts(self) -> str | None:
        return next((w.ts for w in self.wiadomosci if w.ts), None)

    @property
    def ostatnia_ts(self) -> str | None:
        return next((w.ts for w in reversed(self.wiadomosci) if w.ts), None)
