"""Magazyn załączników: plik zapisywany raz, pod nazwą z SHA-256 treści."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .model import Zalacznik

ROZSZERZENIA_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


class Magazyn:
    def __init__(self, katalog: Path, polaczenie: sqlite3.Connection):
        self._katalog = katalog
        self._polaczenie = polaczenie

    def sciezka(self, wzgledna: str) -> Path:
        return self._katalog / wzgledna

    def zapisz(self, zalacznik: Zalacznik) -> tuple[str, bool] | None:
        """Zwraca (sha256, czy_nowy) albo None, gdy pliku źródłowego już nie ma na dysku."""
        dane = zalacznik.dane
        if dane is None:
            if zalacznik.sciezka is None or not zalacznik.sciezka.is_file():
                return None
            dane = zalacznik.sciezka.read_bytes()

        sha = hashlib.sha256(dane).hexdigest()
        ext = Path(zalacznik.nazwa).suffix.lower() or ROZSZERZENIA_MIME.get(zalacznik.mime or "", "")
        wzgledna = f"{sha[:2]}/{sha}{ext}"
        cel = self._katalog / wzgledna

        nowy = not cel.exists()
        if nowy:
            cel.parent.mkdir(parents=True, exist_ok=True)
            tymczasowy = cel.with_suffix(cel.suffix + ".tmp")
            tymczasowy.write_bytes(dane)
            os.replace(tymczasowy, cel)

        self._polaczenie.execute(
            """INSERT OR IGNORE INTO zalaczniki (sha256, ext, rozmiar, mime, sciezka, pierwsza_nazwa, dodano)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sha, ext, len(dane), zalacznik.mime, wzgledna, zalacznik.nazwa, datetime.now(timezone.utc).isoformat()),
        )
        return sha, nowy
