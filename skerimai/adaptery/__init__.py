"""Adaptery źródeł: każdy sprowadza format jednej aplikacji do wspólnego modelu."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..model import Rozmowa


class Adapter(Protocol):
    nazwa: str

    def znajdz_sesje(self) -> dict[str, list[Path]]:
        """Id sesji → pliki źródłowe tej sesji."""
        ...

    def wczytaj(self, sesja: str, pliki: list[Path]) -> Rozmowa: ...

    # Opcjonalnie – źródło, które trzyma wiele rozmów w jednym pliku (baza Cursora):
    # stan_sesji(sesja) -> {klucz: (rozmiar, czas zmiany)} zamiast stanu plików i surowe(sesja) -> bytes
    # zamiast ich treści w surowej kopii.


def _rejestr() -> dict[str, type]:
    from .claude_code import ClaudeCode
    from .codex import Codex
    from .cursor import Cursor
    from .kreion_kartridz import KreionKartridz
    from .kreion_silniki import KreionSilniki
    from .opencode import OpenCode

    return {klasa.nazwa: klasa for klasa in (ClaudeCode, Codex, KreionKartridz, KreionSilniki, Cursor, OpenCode)}


ADAPTERY: dict[str, type] = _rejestr()

# Źródła powielające inne (robocze kopie rozmów) – wyszukiwanie i lista pomijają je,
# chyba że filtr wskaże je wprost.
ZRODLA_POMOCNICZE = frozenset({"kreion_silniki"})
