"""Adapter kartridża KreionAI: cartridge\\logs\\<pierwsza wiadomość>.jsonl + cartridge\\attachments\\.

Kartridż jest źródłem prawdy KreionAI – jedna rozmowa to jeden plik (z BOM). Rekordy:
`project` (przypisanie do projektu), `user`, `agent` i `error` (błędy połączenia – pomijane).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from ..model import ROLA_ASYSTENT, ROLA_UZYTKOWNIK, Rozmowa, Wiadomosc, Zalacznik
from .wspolne import czytaj_jsonl, tytul_z_pierwszej

_KATALOG_KARTRIDZA = Path(r"C:\Kreion\aae_ai_local_data\cartridge")
# Komunikaty wygenerowane przez klienta, nie przez model, np. „You've hit your session limit”.
_MODEL_SYNTETYCZNY = "<synthetic>"


def katalog_kartridza() -> Path:
    """Katalog z SKERIMAI_KREION_DIR albo domyślna instalacja KreionAI."""
    katalog = os.environ.get("SKERIMAI_KREION_DIR")
    return Path(katalog) if katalog else _KATALOG_KARTRIDZA


class KreionKartridz:
    nazwa = "kreion_kartridz"

    def __init__(self, katalog: Path | None = None):
        self.katalog = katalog or katalog_kartridza()

    def znajdz_sesje(self) -> dict[str, list[Path]]:
        logi = self.katalog / "logs"
        if not logi.is_dir():
            return {}
        return {plik.stem: [plik] for plik in sorted(logi.glob("*.jsonl"))}

    def wczytaj(self, sesja: str, pliki: list[Path]) -> Rozmowa:
        rozmowa = Rozmowa(zrodlo=self.nazwa, sesja=sesja, pliki=list(pliki))
        for rekord in (r for plik in pliki for r in czytaj_jsonl(plik)):
            rodzaj = rekord.get("kind")
            tekst = (rekord.get("text") or "").strip()
            if rodzaj == "project":
                rozmowa.projekt = rekord.get("title") or rozmowa.projekt
            elif rodzaj == "user":
                zalaczniki = [self._zalacznik(z) for z in rekord.get("attachments") or [] if isinstance(z, dict) and z.get("path")]
                if tekst or zalaczniki:
                    rozmowa.wiadomosci.append(Wiadomosc(ROLA_UZYTKOWNIK, czas_utc(rekord.get("timestamp")), tekst, zalaczniki))
            elif rodzaj == "agent" and tekst and rekord.get("agent_model") != _MODEL_SYNTETYCZNY:
                rozmowa.wiadomosci.append(Wiadomosc(ROLA_ASYSTENT, czas_utc(rekord.get("timestamp")), tekst))

        rozmowa.tytul = tytul_z_pierwszej(rozmowa) or sesja
        return rozmowa

    def _zalacznik(self, opis: dict) -> Zalacznik:
        sciezka = self.katalog / "attachments" / opis["path"]  # ścieżka względna; bezwzględna zastępuje katalog
        return Zalacznik(nazwa=opis.get("fname") or PureWindowsPath(opis["path"]).name, sciezka=sciezka)


def projekty_kartridza(katalog: Path) -> dict[str, str]:
    """Plik zasad projektu (np. user_project_2026-08-30_20-38-47.md) → nazwa projektu.

    KreionAI dopisuje rekord `project` na początku pliku rozmowy, więc wystarczy pierwsza linia.
    """
    projekty: dict[str, str] = {}
    logi = katalog / "logs"
    if not logi.is_dir():
        return projekty
    for plik in logi.glob("*.jsonl"):
        with plik.open(encoding="utf-8-sig", errors="replace") as f:
            pierwsza = f.readline()
        try:
            rekord = json.loads(pierwsza)
        except json.JSONDecodeError:
            continue
        if isinstance(rekord, dict) and rekord.get("kind") == "project" and rekord.get("userProjectFile"):
            projekty[rekord["userProjectFile"]] = rekord.get("title") or rekord["userProjectFile"]
    return projekty


def czas_utc(znacznik: str | None) -> str | None:
    """„2026-08-30 niedziela 20:35:48” (czas lokalny) → „2026-08-30T18:35:48.000Z”, jak w pozostałych źródłach."""
    if not znacznik:
        return None
    czesci = znacznik.split()
    try:
        lokalny = datetime.strptime(f"{czesci[0]} {czesci[-1]}", "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError):
        return znacznik
    return lokalny.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
