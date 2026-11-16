"""Położenie danych SkerimAI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sciezki:
    """Katalogi i pliki archiwum. Wszystko leży lokalnie w jednym katalogu."""

    katalog: Path

    @property
    def baza(self) -> Path:
        return self.katalog / "skerimai.db"

    @property
    def surowe(self) -> Path:
        return self.katalog / "surowe"

    @property
    def zalaczniki(self) -> Path:
        return self.katalog / "zalaczniki"

    @property
    def blokada(self) -> Path:
        return self.katalog / "archiwizator.lock"

    @property
    def dziennik(self) -> Path:
        """Wyniki i błędy archiwizacji w tle – proces uruchomiony przez hook nie ma konsoli."""
        return self.katalog / "archiwizator.log"

    @property
    def blokada_zasobnika(self) -> Path:
        """Trzyma ją działająca ikona w zasobniku – druga kopia nie wystartuje."""
        return self.katalog / "zasobnik.lock"

    @property
    def koniec_zasobnika(self) -> Path:
        """Prośba o zamknięcie ikony w zasobniku (instaluj, konfiguruj --usun)."""
        return self.katalog / "zasobnik.stop"

    @property
    def dziennik_zasobnika(self) -> Path:
        """Błędy samej ikony – osobno, żeby nie zmieniały koloru, który pokazuje stan archiwizacji."""
        return self.katalog / "zasobnik.log"

    @property
    def uklad_okna(self) -> Path:
        """Rozmiar, położenie i podziały okna „pamięć” z ostatniego zamknięcia."""
        return self.katalog / "okno_pamieci.json"

    @property
    def wydanie(self) -> Path:
        """Najnowsza wersja na GitHubie i czas sprawdzenia – sprawdzana raz na dobę."""
        return self.katalog / "wydanie.json"

    @property
    def polaczenie_kreion(self) -> Path:
        """Czas ostatniego startu serwera MCP dla KreionAI – `sprawdz` pokazuje go bez zaglądania do KreionAI."""
        return self.katalog / "kreion.polaczenie"


def domyslne_sciezki() -> Sciezki:
    """Katalog z SKERIMAI_DIR albo %USERPROFILE%\\.skerimai."""
    katalog = os.environ.get("SKERIMAI_DIR")
    return Sciezki(Path(katalog) if katalog else Path.home() / ".skerimai")
