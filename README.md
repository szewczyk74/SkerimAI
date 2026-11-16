# SkerimAI – instrukcja obsługi

SkerimAI to trwałe archiwum wszystkich rozmów z aplikacjami AI: **Claude Code**, **Codex**,
**Cursor** i **OpenCode**.
Zapisuje pełną treść rozmów i załączniki (obrazy, PDF, DOCX, TXT) we własnej bazie, więc nic nie ginie,
nawet gdy aplikacja skasuje swoje sesje. Claude Code, Codex i OpenCode przeszukują to archiwum same – przez serwer MCP
`skerimai` – gdy pytasz o wcześniejsze ustalenia („jak ustaliliśmy…”, „pamiętasz…”, „co robiliśmy z…”).

## Spis treści

1. [Jak to działa](#jak-to-działa)
2. [Instalacja](#instalacja)
3. [Aktualizacja](#aktualizacja)
4. [Ikona w zasobniku](#ikona-w-zasobniku)
5. [Edycja pamięci](#edycja-pamięci)
6. [Korzystanie w Claude Code i Codex](#korzystanie-w-claude-code-i-codex)
7. [Wiersz poleceń](#wiersz-poleceń)
8. [Kopia zapasowa](#kopia-zapasowa)
9. [Gdzie są dane](#gdzie-są-dane)
10. [Ustawienia](#ustawienia)
11. [Rozwiązywanie problemów](#rozwiązywanie-problemów)
12. [Odinstalowanie](#odinstalowanie)
13. [Dla programisty](#dla-programisty)

## Jak to działa

Archiwum uzupełnia się samo, bez Twojego udziału:

| Kiedy | Co archiwizuje |
|---|---|
| po zamknięciu sesji Claude Code (hook `SessionEnd`) | tę sesję; wynik w powiadomieniu Windows |
| 20 s po starcie każdej sesji Claude Code / Codex i przed każdym wyszukiwaniem (serwer MCP) | wszystkie źródła – także bieżącą rozmowę |
| co 5 minut, gdy coś się zmieniło (ikona w zasobniku) | Codex, Cursor i OpenCode – nawet bez otwierania Claude Code |
| na żądanie: menu ikony „Archiwizuj teraz” albo `skerimai archiwizuj` | wszystkie źródła |

Archiwizacja zużywa zero tokenów. Wyszukiwanie przez agenta kosztuje zwykle 5–10 tys. tokenów na pytanie
w rodzaju „na czym skończyliśmy?”.

## Instalacja

### Wymagania

- Windows 10/11,
- co najmniej jedna z aplikacji: Claude Code, Codex, OpenCode, Cursor.

Pythona ani gita nie trzeba instalować: instalator pobiera [uv](https://docs.astral.sh/uv/), a uv – własnego
Pythona.

### Pierwsza instalacja

1. **Zamknij wszystkie sesje Claude Code i Codex** (aplikację Claude zakończ z jej ikony w zasobniku).
2. W PowerShell (zwykłym, nie jako administrator) wklej:

   ```powershell
   irm https://raw.githubusercontent.com/szewczyk74/SkerimAI/main/instaluj.ps1 | iex
   ```

   Skrypt instaluje uv (jeśli go nie ma) i najnowszą wersję SkerimAI, a potem wykonuje `skerimai konfiguruj`, czyli:
   - wpisuje hook `SessionEnd` do `~\.claude\settings.json`,
   - w tym samym pliku wydłuża do 60 s czas, przez jaki Claude Code czeka na start serwera MCP (`env.MCP_TIMEOUT`;
     domyślne 30 s tuż po włączeniu komputera bywa za mało – sesja rusza wtedy bez narzędzi SkerimAI),
   - rejestruje serwer MCP `skerimai` w Claude Code (zakres użytkownika), w Codex (`~\.codex\config.toml`)
     i w OpenCode,
   - dopisuje wskazówkę o archiwum do `~\.claude\CLAUDE.md` i `~\.codex\AGENTS.md`,
   - rejestruje powiadomienia Windows,
   - dodaje autostart ikony w zasobniku i od razu ją uruchamia.

   Pierwsza instalacja trwa 1–2 minuty (uv pobiera Pythona).

3. Sprawdź stan – w **nowym** oknie terminala:

   ```powershell
   skerimai sprawdz
   ```

   Przy pierwszym uruchomieniu archiwizacja wczytuje całą dotychczasową historię – to może potrwać kilka minut.

4. Uruchom ponownie Claude Code i Codex – dopiero nowe sesje widzą serwer MCP.

### Inny komputer

Na nowym komputerze zainstaluj SkerimAI jak wyżej. Bez przenoszenia archiwum zaczyna od pustego – pierwsza
archiwizacja wczytuje to, co Claude Code, Codex, Cursor i OpenCode mają na tym komputerze. Żeby zabrać pamięć ze
starego komputera:

1. Na starym: `skerimai kopia D:\Przenosne` (albo **Kopia zapasowa…** z menu ikony).
2. Na nowym, **przed** instalacją albo przy zamkniętych sesjach Claude Code / Codex i zamkniętej ikonie,
   skopiuj zawartość powstałego folderu `skerimai-RRRR-MM-DD_GGMMSS` do `%USERPROFILE%\.skerimai`.
3. Zainstaluj (albo uruchom ponownie aplikacje) – nowe rozmowy dopisują się do przeniesionego archiwum.

Ograniczenia:

- **Archiwa się nie scalają.** Po przeniesieniu każdy komputer zbiera swoje rozmowy osobno; kopia przeniesiona
  drugi raz zastąpi to, co nowy komputer zdążył zebrać.
- Archiwum musi leżeć na dysku lokalnym (nie sieciowym ani synchronizowanym), a w drodze traktuj je jak dane
  poufne – [surowe kopie mogą zawierać hasła](#gdzie-są-dane).

## Aktualizacja

SkerimAI raz na dobę sprawdza, czy na GitHubie jest nowsza wersja. Gdy jest, ikona w zasobniku dostaje żółtą
kropkę, a jej menu – pozycję **Zainstaluj aktualizację**.

1. Zamknij wszystkie sesje Claude Code i Codex (ich serwery MCP blokują pliki programu; aplikację Claude zakończ
   z jej ikony w zasobniku). Dopóki działają, pozycja w menu jest nieaktywna i podaje, ile sesji zostało.
2. Wybierz **Zainstaluj aktualizację**. SkerimAI pobiera nową wersję i po chwili pokazuje
   [okno z postępem](#okna-poleceń). Ikona zamyka się na czas instalacji i wraca sama.

To samo robi ponowne uruchomienie [skryptu instalacyjnego](#pierwsza-instalacja) – także wtedy, gdy ikona
nie działa. Okna SkerimAI (pamięć, stan, kopia) trzeba przed aktualizacją zamknąć.

## Ikona w zasobniku

Ikona (niebieski znak z „S” w zasobniku systemowym obok zegara) pokazuje, czy archiwum działa, i archiwizuje
Codex, Cursor i OpenCode, które nie mają własnego hooka. Zajmuje ok. 48 MB pamięci, nie zużywa tokenów.

### Uruchamianie

- **Samo** – po zalogowaniu do Windows (autostart wpisuje `skerimai konfiguruj` i `skerimai instaluj`).
- **Od razu** – `skerimai zasobnik` (albo `skerimai konfiguruj`) uruchamia ikonę, jeśli nie działa.
- **Ręcznie** – `%APPDATA%\uv\tools\skerimai\Scripts\skerimai-zasobnik.exe`
  (z kopii rozwojowej: `.venv\Scripts\skerimai-zasobnik.exe`).

Działa najwyżej jedna ikona naraz – druga kopia po prostu nie wystartuje.

Gdy ikona nie wstaje po zalogowaniu, sprawdź w Menedżerze zadań → **Uruchamianie**, czy wpis „SkerimAI”
nie jest wyłączony (`skerimai sprawdz` też to wykrywa).

Windows potrafi zgubić ikonę (np. po uśpieniu lub zablokowaniu komputera) – program działa dalej, tylko ikony nie
widać. Ikona sprawdza to co minutę i sama dodaje się ponownie. Gdyby mimo to jej nie było, a `skerimai sprawdz`
pokazuje „działa”: `skerimai zasobnik --restart`. Sprawdź też strzałkę **^** obok zegara – tam Windows chowa
nowe ikony.

### Kropka stanu

| Ikona | Znaczenie |
|---|---|
| bez kropki | wszystko w porządku |
| 🟡 żółta kropka w rogu | uwaga – np. ostrzeżenie w dzienniku albo jest nowsza wersja SkerimAI |
| 🔴 czerwona kropka w rogu | błąd ostatniej archiwizacji albo konfiguracji |

Ikona jest rysowana w rozmiarze zasobnika przy bieżącym skalowaniu ekranu, więc jest ostra także przy 125–200 %.

Podpowiedź po najechaniu myszą: liczba rozmów w archiwum, czas i wynik ostatniej archiwizacji, liczba
działających serwerów MCP (czyli otwartych sesji Claude Code / Codex) i pierwszy problem, jeśli jest.

Kropka i podpowiedź odświeżają się co 30 s, pełne sprawdzenie (jak `skerimai sprawdz`) – co 10 min.

### Menu (prawy przycisk)

| Pozycja | Działanie |
|---|---|
| *SkerimAI · N rozmów – w porządku / uwaga / błąd* | nagłówek ze stanem; pod nim do 5 problemów (klik – okno stanu) |
| **Edytuj pamięć…** | okno [edycji pamięci](#edycja-pamięci): ukrywanie, tytuły, usuwanie, wymazywanie sekretów |
| **Archiwizuj teraz** | archiwizuje wszystkie źródła; wynik w powiadomieniu |
| **Sprawdź stan** | [okno stanu](#okno-stanu) (to samo robi **lewy klik** w ikonę) |
| **Więcej ▸ Jak rozmawiać z pamięcią…** | ściąga ze zwrotami dla Claude Code i Codex w Notatniku ([`skerimai/jak_rozmawiac.txt`](skerimai/jak_rozmawiac.txt)) |
| **Więcej ▸ Kopia zapasowa…** | wybór folderu, potem [kopia archiwum](#kopia-zapasowa) w [oknie z postępem](#okna-poleceń) |
| **Więcej ▸ Otwórz folder archiwum** | `%USERPROFILE%\.skerimai` w Eksploratorze |
| **Więcej ▸ Otwórz dziennik** | `archiwizator.log` w Notatniku |
| **Zainstaluj aktualizację** | tylko gdy jest nowsza wersja – patrz [Aktualizacja](#aktualizacja) |
| **Zakończ** | zamyka ikonę (do następnego logowania albo `skerimai konfiguruj`) |

### Okno stanu

Okno **SkerimAI – stan** pokazuje to samo co `skerimai sprawdz`: na górze ✓ / ! / ✗ z podsumowaniem, niżej
listę kontroli – kolorem wyróżnione są tylko uwagi i błędy, pierwszy problem jest od razu zaznaczony, a jego
pełny opis (np. polecenie do wykonania) widać pod listą.

- **Sprawdź ponownie** (F5) – nowe sprawdzenie, trwa kilka sekund,
- **Napraw konfigurację…** – `skerimai konfiguruj` w oknie z postępem, potem ponowne sprawdzenie,
- **Kopiuj raport** – wynik tekstem, jak w konsoli (do wklejenia w rozmowę albo zgłoszenie).

### Okna poleceń

Kopia zapasowa, aktualizacja i naprawa konfiguracji działają w oknie z paskiem postępu i wynikiem na żywo.
Na końcu okno pokazuje ✓ **Gotowe** albo ✗ z kodem błędu; **Kopiuj wynik** kopiuje cały tekst. Okna nie da się
zamknąć, dopóki polecenie trwa – przerwana kopia albo instalacja zostawiłaby niedokończone pliki.

### Wygląd okien

Okna SkerimAI mają wygląd Windows 11 (motyw Sun Valley) – jasny albo ciemny według ustawienia Windows
(Ustawienia → Personalizacja → Kolory → tryb aplikacji). Tryb jest odczytywany przy otwarciu okna; po zmianie
w Windows wystarczy otworzyć okno ponownie.

## Edycja pamięci

Okno **SkerimAI – pamięć** otwiera się z menu ikony: **Edytuj pamięć…** (albo
`%APPDATA%\uv\tools\skerimai\Scripts\skerimai-pamiec.exe`). Zmiany zapisują się od razu. Okno zapamiętuje
rozmiar, położenie i podziały (`okno_pamieci.json` w folderze archiwum); skróty klawiszowe przycisków
podpowiada dymek po najechaniu myszą.

### Rozmowy i wiadomości poza pamięcią

Każda rozmowa i każda wiadomość ma pole **W pamięci** (☑ / ☐):

- **odznaczona (☐)** – nie trafia do wyszukiwania (`szukaj_w_historii`, `skerimai szukaj`), na listę rozmów ani
  do agenta: `czytaj_rozmowe` zamiast niej zwraca „ukryta przez użytkownika”. Zostaje w archiwum i w oknie
  (wyszarzona) – można ją w każdej chwili zaznaczyć z powrotem. Agent nie pobierze też załącznika, który jest
  tylko przy ukrytych wiadomościach i rozmowach,
- klik w pole ☑ / ☐ przełącza jeden wiersz, **spacja** – wszystkie zaznaczone (zaznaczanie wielu: Ctrl / Shift),
- ponowna archiwizacja nie zmienia ukrycia, także gdy rozmowa trwa dalej.

Treść wiadomości jest lekko sformatowana: autor („Ty” / „AI”) w kolorze, bloki kodu i `kod` czcionką o stałej
szerokości na tle, nagłówki, listy, cytaty i pogrubienie z Markdowna. Załączniki (📎) są pod treścią jako kafelki
z rozmiarem pliku – kliknięcie otwiera plik z magazynu (`%USERPROFILE%\.skerimai\zalaczniki`) w programie
przypisanym do niego w Windows.

Górna lista to rozmowy (od najnowszej), dolna – wiadomości wybranej rozmowy, z prawej pełna treść wiadomości.
Filtry: **Szukaj** (słowa w treści, jak wyszukiwanie agenta, albo fragment tytułu), **Źródło**, **Projekt**,
**Pokaż**: wszystkie / w pamięci / ukryte.

### Tytuł i projekt

**Zmień tytuł…** (F2) i **Zmień projekt…** (także dla wielu rozmów naraz; lista podpowiada istniejące projekty,
można też wpisać nową nazwę) – np. „Witaj” → „Etap 8 – instalacja”.
Własne wartości mają znak ✎ i pierwszeństwo przed tymi ze źródła – w wyszukiwaniu, filtrach i odpowiedziach
dla agenta. Puste pole albo menu kontekstowe (prawy przycisk) → **Przywróć tytuł i projekt ze źródła** wraca
do oryginału.

Gdy zmienisz projekt **wszystkim** rozmowom danego projektu (np. `pszczola` → `Pszczoła`), nowe rozmowy z tego
projektu dostaną nową nazwę same – także sesje Claude Code z folderu `C:\Projekty\pszczola`. Przeniesienie
pojedynczej rozmowy do innego projektu nie zmienia nazwy kolejnych.

### Usuwanie na stałe

**Usuń na stałe…** (Delete; w pytaniu Enter i Esc oznaczają „Anuluj”) usuwa rozmowę z bazy razem z surową kopią i załącznikami, których nie ma w innych
rozmowach. SkerimAI zapamiętuje ją (tylko jako skrót SHA-256) i nie wczyta jej ponownie, nawet jeśli aplikacja
wciąż ją ma. **Tego nie da się cofnąć** – żeby tylko wyłączyć rozmowę z wyszukiwania, wystarczy ją odznaczyć.

### Wymazywanie sekretów

**Wymaż sekret…** (prawy górny róg) – dla hasła, klucza API itp., które trafiło do rozmowy:

1. Wpisz albo wklej sekret (**Pokaż** odsłania wpisane znaki).
2. **Sprawdź, gdzie jest** – liczy wystąpienia w wiadomościach, tytułach, dokumentach i surowych kopiach sesji
   (tam są wyniki narzędzi, np. odczytany plik `.env`); niczego nie zmienia, trwa kilka sekund.
3. **Wymaż** – zamienia sekret na `[wymazano]` wszędzie w archiwum: w wiadomościach, tytułach, indeksie
   wyszukiwania, tekście dokumentów, plikach tekstowych załączników i surowych kopiach. Na koniec przepisuje
   plik bazy, żeby nie został w nim żaden ślad (kilkanaście sekund).

SkerimAI zapamiętuje wymazany sekret zaszyfrowany przez Windows (DPAPI, klucz Twojego konta) i wymazuje go
także przy każdej kolejnej archiwizacji – inaczej wróciłby z plików aplikacji. Wielkość liter ma znaczenie,
a sekret musi mieć co najmniej 4 znaki.

Bez zmian zostają:

- pliki samych aplikacji (`~\.claude\projects`, `~\.codex\sessions`) – sekret trzeba usunąć także tam
  albo po prostu zmienić hasło / unieważnić klucz,
- wcześniejsze kopie zapasowe archiwum,
- pliki PDF i DOCX w załącznikach – wymazany jest tylko ich tekst w bazie; okno podaje ich nazwy,

## Korzystanie w Claude Code i Codex

Nie trzeba niczego wywoływać – wystarczy pytać naturalnie:

- „Jak ustaliliśmy obsługę logowania?”
- „Pamiętasz, co robiliśmy z kartą KSM?”
- „Na czym skończyliśmy wczoraj w projekcie sklep?”
- „Pokaż schemat wklejony w zeszłym tygodniu.”
- „Przypomnij sobie projekt” / „wróćmy do projektu” – agent najpierw przegląda rozmowy z tego projektu
  i doczytuje końcówkę ostatniej.

Gdy agent mimo to odpowiada z samych plików, powiedz wprost: „sprawdź w SkerimAI…”. Wywołania narzędzi
`skerimai` widać w Claude Code jako zwinięte linie (np. `skerimai - lista_rozmow (MCP)`).

### Gotowe zwroty

Pełna ściąga: [`skerimai/jak_rozmawiac.txt`](skerimai/jak_rozmawiac.txt), w ikonie w zasobniku
pod **Jak rozmawiać z pamięcią…**.

| Sytuacja | Zwrot |
|---|---|
| początek rozmowy | „wróćmy do projektu, przeczytaj końcówkę ostatniej rozmowy i PLAN.md” |
| pytanie w trakcie | „sprawdź w SkerimAI, co ustaliliśmy o …” |
| ostatnie rozmowy | „pokaż z SkerimAI rozmowy z projektu X z ostatniego tygodnia” |
| agent nie sięgnął do archiwum | „poszukaj w historii SkerimAI…” |

Agent korzysta z czterech narzędzi serwera `skerimai`:

| Narzędzie | Do czego |
|---|---|
| `szukaj_w_historii` | wyszukiwanie w rozmowach i tekście załączonych dokumentów; rozumie polską odmianę i poprawia literówki |
| `lista_rozmow` | przegląd rozmów (źródło, projekt, zakres dat) |
| `czytaj_rozmowe` | odczyt rozmowy stronami, bez zalewania kontekstu; `od=-10` – dziesięć ostatnich wiadomości |
| `pobierz_zalacznik` | obraz do obejrzenia albo tekst dokumentu |

Gdy agent nie sięga do archiwum sam, poproś wprost: „poszukaj w historii SkerimAI…”.

### Projekty

Każda rozmowa ma projekt: w Claude Code i Codex to nazwa folderu, w którym pracowała sesja
(`C:\Projekty\sklep.pl` → `sklep.pl`). Nazwę można zmienić w oknie
[edycji pamięci](#tytuł-i-projekt), np. żeby połączyć `pszczola` z Claude Code i `Pszczoła` z Codex.

Wyszukiwanie obejmuje zawsze całe archiwum, bez względu na to, w jakim folderze pracuje sesja – rozmowa
bywa prowadzona w innym projekcie, niż dotyczy (np. pomysł na nowy program bywa w rozmowie z projektu `sklep.pl`).
Projekt widać przy każdym trafieniu. Żeby zawęzić:

- **słowem w pytaniu** – „poszukaj w historii: budzik kalendarz” znajdzie rozmowy, w których pada „budzik”,
  także te zapisane w innym projekcie,
- **filtrem** – „szukaj tylko w projekcie budzik”: agent poda `projekt`, a wyniki będą wyłącznie z rozmów
  o tej nazwie projektu; rozmowę z niewłaściwym projektem przenieś wtedy w oknie edycji. Wystarczy fragment
  nazwy, a wielkość liter i polskie znaki nie mają znaczenia („pszczola” znajdzie „Pszczoła”).

## Wiersz poleceń

Te same możliwości są dostępne w terminalu. Pomoc: `skerimai --help`, `skerimai <polecenie> --help`.

| Polecenie | Działanie |
|---|---|
| `skerimai szukaj <słowa>` | wyszukiwanie; filtry `--zrodlo`, `--projekt`, `--od`, `--do`, `--limit` |
| `skerimai rozmowy` | lista rozmów (te same filtry) |
| `skerimai czytaj <nr>` | treść rozmowy; `--od-wiad` (ujemna liczy od końca), `--ile` |
| `skerimai zalacznik <sha>` | ścieżka załącznika po początku skrótu SHA-256; `--tekst` – także wyciągnięty tekst |
| `skerimai stat` | statystyki archiwum |
| `skerimai archiwizuj` | archiwizacja teraz; `--zrodlo` – tylko wybrane, `--pelne` – wszystko od nowa |
| `skerimai kopia <folder>` | [kopia zapasowa](#kopia-zapasowa) |
| `skerimai sprawdz` | stan instalacji, konfiguracji, ikony i archiwum |
| `skerimai zasobnik` | uruchamia ikonę w zasobniku, jeśli nie działa; `--restart` – zamyka ją i uruchamia od nowa |
| `skerimai konfiguruj` | wpisuje / naprawia całą konfigurację; `--usun` – usuwa ją |
| `skerimai instaluj` | instalacja / aktualizacja kopii używanej: najnowsze wydanie z GitHuba (`--wersja X.Y.Z` – wskazane), z kopii rozwojowej – bieżący commit |

Źródła (`--zrodlo`): `claude_code`, `codex`, `cursor`, `opencode`.

Przykłady:

```powershell
skerimai szukaj logowanie hasło
skerimai szukaj "karta KSM" --zrodlo codex
skerimai rozmowy --projekt sklep --od 2026-09-01
skerimai czytaj 42 --od-wiad 20
skerimai zalacznik 3fa9c1 --tekst
```

## Kopia zapasowa

```powershell
skerimai kopia D:\Kopie
```

albo z menu ikony **Więcej ▸ Kopia zapasowa…** (okno z postępem). Powstaje folder `D:\Kopie\skerimai-RRRR-MM-DD_GGMMSS` – kompletne,
spójne archiwum, nawet gdy Claude Code czy Codex właśnie pracują. Kopie mogą leżeć na dysku sieciowym
lub w folderze synchronizowanym.

Żeby używać kopii jako archiwum (np. na innym komputerze), wskaż ją zmienną środowiskową i uruchom ponownie
aplikacje:

```powershell
setx SKERIMAI_DIR "D:\Kopie\skerimai-2026-10-02_120000"
```

Samo archiwum musi leżeć na **dysku lokalnym** – nie na udziale sieciowym ani w folderze synchronizowanym.

## Gdzie są dane

Wszystko w `%USERPROFILE%\.skerimai` (albo w folderze z `SKERIMAI_DIR`):

| Plik / folder | Zawartość |
|---|---|
| `skerimai.db` | baza z rozmowami i indeksem wyszukiwania |
| `surowe\` | oryginalne pliki sesji (`.jsonl.gz`) – z wynikami narzędzi |
| `zalaczniki\` | załączniki, każdy plik zapisany raz |
| `archiwizator.log` | dziennik archiwizacji w tle |
| `zasobnik.log` | błędy samej ikony w zasobniku i okien SkerimAI |
| `okno_pamieci.json` | rozmiar, położenie i podziały okna „pamięć” |
| `*.lock`, `zasobnik.stop` | pliki pomocnicze – nie usuwać przy działających programach |

**Uwaga:** surowe kopie zawierają wyniki narzędzi, więc mogą zawierać hasła i klucze (np. odczyty plików
`.env`). Nie udostępniaj tego folderu i nie umieszczaj go w gicie ani na pulpicie sieciowym. Pojedyncze sekrety
usuwa [Wymaż sekret…](#wymazywanie-sekretów).

## Ustawienia

| Zmienna środowiskowa | Działanie |
|---|---|
| `SKERIMAI_DIR` | inny folder archiwum niż `%USERPROFILE%\.skerimai` |
| `SKERIMAI_POWIADOMIENIA=0` | wyłącza powiadomienia Windows |
| `SKERIMAI_OPENCODE_POMIN` | nazwy katalogów (po przecinku), z których rozmowy OpenCode nie trafiają do archiwum; domyślnie sondy Herdr: `herdr-oc-probe,oc-models`; pusta – nic nie pomija |

Ustawienie na stałe: `setx NAZWA wartość`, potem ponowne uruchomienie Claude Code, Codex i ikony.

## Rozwiązywanie problemów

Zawsze zacznij od:

```powershell
skerimai sprawdz
```

Pokazuje każdy element (wersja kopii, hook, serwery MCP, wskazówki, powiadomienia, ikona i autostart, archiwum,
ostatnia archiwizacja, źródła rozmów) ze znakiem ✓ / ! / ✗. Większość problemów z konfiguracją naprawia
`skerimai konfiguruj` – można go uruchamiać dowolnie wiele razy.

| Objaw | Co zrobić |
|---|---|
| `instaluj`: „Zainstalowana kopia działa (skerimai-mcp.exe)…” | zamknij wszystkie sesje Claude Code i Codex (aplikację Claude zakończ z jej ikony w zasobniku), zatrzymaj usługę OpenCode (`opencode service stop`), poczekaj kilka sekund, spróbuj ponownie |
| „brakujące załączniki” po archiwizacji | aplikacja skasowała plik, zanim SkerimAI go skopiował (zrzuty i pliki z `%TEMP%`) – nie do odzyskania. Gdy aktualizacja SkerimAI poprawia odczyt rozmów, zarchiwizuj dane źródło raz od nowa: `skerimai archiwizuj --pelne --zrodlo <źródło>` – zwykła archiwizacja wczytuje tylko rozmowy, które się zmieniły |
| `instaluj`: „Okno SkerimAI jest otwarte…” | zamknij okna SkerimAI (pamięć, stan, kopia) i spróbuj ponownie |
| `instaluj`: „To jest zainstalowana kopia…” | zaktualizuj z menu ikony albo ponownie [skryptem instalacyjnym](#pierwsza-instalacja); kopię z repozytorium – z kopii rozwojowej: `.venv\Scripts\skerimai.exe instaluj` |
| `instaluj`: „uv … jest za stare” | `uv self update` |
| żółta kropka, „jest nowsza wersja” albo „nowszy commit” | [zaktualizuj](#aktualizacja) |
| czerwona kropka | menu → **Więcej ▸ Otwórz dziennik**, przeczytaj ostatni wpis; potem **Archiwizuj teraz** |
| „Archiwum zajęte” | inna archiwizacja jeszcze trwa – spróbuj za chwilę |
| brak ikony po zalogowaniu | `skerimai sprawdz`; Menedżer zadań → Uruchamianie → „SkerimAI” włączone |
| brak ikony, a `sprawdz` mówi „działa” | strzałka **^** obok zegara; potem `skerimai zasobnik --restart` |
| agent nie widzi narzędzi `skerimai` | w Claude Code `/mcp` → `skerimai` → połącz ponownie; `skerimai sprawdz` – pozycja „Połączenia Claude Code z serwerem MCP” pokazuje nieudane starty z ostatnich 7 dni |
| agent ma narzędzia, ale z nich nie korzysta | powiedz wprost „sprawdź w SkerimAI…”; `skerimai sprawdz` – czy wskazówka w `CLAUDE.md` / `AGENTS.md` jest aktualna |
| brak powiadomień | Ustawienia Windows → Powiadomienia → „SkerimAI” włączone; czy nie jest ustawione `SKERIMAI_POWIADOMIENIA=0` |
| nie ma najnowszej rozmowy w wynikach | `skerimai archiwizuj` albo menu → **Archiwizuj teraz**; czy nie jest ukryta (**Edytuj pamięć…** → Pokaż: ukryte) |
| agent wciąż trafia w nieaktualną rozmowę | odznacz ją w **Edytuj pamięć…** |

Skany PDF (bez warstwy tekstu) nie trafiają do wyszukiwania – SkerimAI nie robi OCR.

## Odinstalowanie

1. Zamknij sesje Claude Code i Codex.
2. Usuń wpisy z konfiguracji (zamyka też ikonę i usuwa autostart):

   ```powershell
   skerimai konfiguruj --usun
   ```

3. Usuń program:

   ```powershell
   uv tool uninstall skerimai
   ```

Archiwum w `%USERPROFILE%\.skerimai` zostaje – usuń je ręcznie, jeśli naprawdę nie jest potrzebne.

## Dla programisty

### Dwie kopie programu

- **rozwojowa** – `.venv` w repozytorium (np. `C:\Projekty\SkerimAI\.venv`), do zmian w kodzie i testów;
  kod w `skerimai\` jest podpięty edytowalnie, więc zmiany działają w niej od razu,
- **używana** – zainstalowana przez `uv tool` w `%APPDATA%\uv\tools\skerimai`; z niej korzystają hook,
  serwery MCP i ikona w zasobniku. Zmiany w kodzie trafiają do niej dopiero przez `skerimai instaluj`
  z kopii rozwojowej.

### Instalacja z repozytorium

```powershell
git clone https://github.com/szewczyk74/SkerimAI.git C:\Projekty\SkerimAI
cd C:\Projekty\SkerimAI
uv sync --extra test
```

`uv sync` tworzy `.venv` i instaluje w nim program edytowalnie razem z testami. Potem – przy zamkniętych sesjach
Claude Code i Codex – kopia używana:

```powershell
.venv\Scripts\skerimai.exe instaluj
```

Polecenie instaluje **ostatni commit** repozytorium (`uv tool install git+file://…`, wymaga gita i uv co najmniej
0.12.21), a potem wykonuje `skerimai konfiguruj`. Niezacommitowane zmiany nie trafiają do kopii używanej –
`instaluj` o nich ostrzega. Ikony w zasobniku nie trzeba zamykać – `instaluj` zamknie ją sam i uruchomi nową.
`instaluj --wersja X.Y.Z` instaluje zamiast tego wydanie z GitHuba.

Kopia zainstalowana z repozytorium nie sprawdza wydań na GitHubie: `skerimai sprawdz` (i żółta kropka ikony)
zgłasza, gdy repozytorium ma nowszy commit ze zmianami w `skerimai\` lub `pyproject.toml`, a **Zainstaluj
aktualizację** w menu ikony uruchamia `instaluj` z kopii rozwojowej.

### Wskazówki

- Testy: `.venv\Scripts\python -m pytest`.
- `uv sync --extra test` uruchamiaj tylko przy zamkniętych sesjach Claude Code i Codex, które korzystają
  z kopii rozwojowej (zajęty `skerimai-mcp.exe` przerywa instalację w połowie); nowe zależności –
  `uv add <pakiet>` (dopisuje do `pyproject.toml` i `uv.lock`).
- Serwer MCP (`serwer_mcp.py`) nie używa pakietu `mcp` – własna obsługa JSON-RPC po stdio, żeby start trwał
  ułamek sekundy. Nowe narzędzie: `@serwer.narzedzie(nazwa, opis, Parametr(...), ...)` w `utworz_serwer`.
- Wydania: tag `vX.Y.Z` na każde wydanie, na gałęzi `main` zawsze najnowsze. Kopie zainstalowane z wydania
  czytają najnowszą wersję z `pyproject.toml` na `main` i instalują archiwum ZIP tagu.
