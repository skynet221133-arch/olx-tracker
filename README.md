# OLX tracker

Co 4 godziny sprawdza twoje wyszukiwanie na OLX, zapisuje historię cen i publikuje stronę WWW z wynikami:
ostatnie obniżki, aktywne ogłoszenia (cena, wykres ceny, liczba obniżek, zmian ceny i tytułu, odświeżeń,
dni w sprzedaży) oraz zakończone ogłoszenia. Strona działa na telefonie i komputerze, sortujesz ją listą
u góry ogłoszeń. Żadne maile ani hasła nie są potrzebne.

## Uruchomienie krok po kroku

1. **Repozytorium.** Na github.com: New repository, nazwa np. `olx-tracker`.
   Ustaw **Public**. Darmowe konto GitHub publikuje strony tylko z publicznych repozytoriów
   (patrz uwaga o prywatności niżej).
2. **Pliki.** Add file → Upload files: wgraj `olx_tracker.py`, `requirements.txt`, `README.md`
   oraz `search_url.txt` (albo pomiń go i zrób krok 5, jeśli nie chcesz pokazywać filtrów publicznie).
3. **Workflow.** Add file → Create new file. W polu nazwy wpisz dokładnie `.github/workflows/tracker.yml`
   i wklej zawartość pliku `tracker.yml`. Zatwierdź (Commit).
4. **Włącz publikację.** Settings → Pages → w polu *Source* wybierz **GitHub Actions**.
5. *(opcjonalnie)* **Ukryj filtry.** Settings → Secrets and variables → Actions → New repository secret:
   nazwa `SEARCH_URL`, wartość: link z wyszukiwarką OLX. Skrypt użyje go zamiast `search_url.txt`.
6. **Pierwszy przebieg.** Zakładka Actions → OLX tracker → Run workflow. Po 1–2 minutach przebieg
   pokaże zielony znaczek, a w kroku „Opublikuj stronę" pojawi się adres strony. Ma postać
   `https://TWOJA-NAZWA.github.io/olx-tracker/`. Otwórz go na telefonie i dodaj do ekranu głównego
   (w Chrome: menu → Dodaj do ekranu głównego, w Safari: Udostępnij → Do ekranu początkowego).
7. Od tej chwili strona odświeża się sama co 4 godziny. Pierwszy przebieg zapisuje stan wyjściowy,
   więc obniżki zobaczysz dopiero wtedy, gdy cena któregoś ogłoszenia spadnie.

## Zmiana filtrów

Edytuj `search_url.txt` albo sekret `SEARCH_URL` (link z paska adresu OLX z ustawionymi filtrami).

## Gdy coś nie działa

- **Błąd w Actions „HTTP 403"**: OLX blokuje adresy serwerów GitHuba. Uruchom skrypt u siebie
  (komputer lub Raspberry Pi): `pip install requests`, potem `python olx_tracker.py`; strona powstanie
  w folderze `site/` (otwierasz `site/index.html`). Do stałego działania użyj cron lub Harmonogramu zadań.
- **Błąd „Nie udało się odczytać ogłoszeń"**: OLX zmienił układ strony. Pobierz plik `debug_last.html`
  z zakładki Actions (Artifacts) i wyślij mi go, poprawię parser.
- GitHub wysyła maila o każdym nieudanym przebiegu workflow, więc o awarii dowiesz się sam.
  Data „Ostatnie sprawdzenie" na górze strony pokazuje, czy dane są świeże.

## Prywatność

Repozytorium jest publiczne, więc każdy, kto zna jego adres, może zobaczyć kod, plik `data/history.json`
(historię cen publicznych ogłoszeń) oraz, jeśli wgrasz `search_url.txt`, twoje filtry razem z
okolicą wyszukiwania. Strona ma znacznik zakazujący indeksowania przez wyszukiwarki, ale adres nie jest
tajny. Sekret `SEARCH_URL` z kroku 5 ukrywa filtry. Prywatne strony wymagają płatnego planu GitHuba.

## Uwaga

Skrypt pobiera publiczne strony wyników z niewielką częstotliwością, tylko do użytku osobistego.
Regulamin OLX ogranicza automatyczne pobieranie danych, więc nie zwiększaj częstotliwości.
