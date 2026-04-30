# Plex Inventory - Windows Portable

App desktop Windows per generare l'inventario Plex in CSV/XLSX partendo dal token Plex, con UI per le opzioni principali dello script originale.

## Cosa include

- UI Windows con PySide6.
- Token Plex salvati con etichetta.
- Su Windows i token sono cifrati con DPAPI, legati all'utente Windows corrente.
- Caricamento automatico server Plex dopo inserimento token.
- Caricamento librerie Movies/TV selezionabili.
- Checkbox separate CSV/XLSX.
- Scelta manuale cartella output.
- Barra progresso e log errori.
- Build Windows portable in formato cartella + ZIP, senza installer.
- GitHub Actions già pronta.

## Come compilare su GitHub

1. Crea un nuovo repository GitHub.
2. Copia tutti i file di questa cartella nel repository.
3. Fai commit e push.
4. Vai in **Actions**.
5. Seleziona **Build Windows portable**.
6. Premi **Run workflow**.
7. A fine build scarica l'artifact **PlexInventory-windows-portable**.
8. Estrai lo ZIP su Windows e avvia `PlexInventory.exe`.

## Come compilare localmente su Windows

Installa Python 3.11+ e poi esegui:

```bat
build_windows.bat
```

Alla fine troverai:

```text
PlexInventory-windows-portable.zip
```

## Uso app

1. Inserisci un'etichetta token, per esempio `Token casa`.
2. Inserisci il Plex token.
3. Premi **Salva token**.
4. Premi **Carica server**.
5. Seleziona il server Plex.
6. Premi **Carica librerie**.
7. Seleziona le librerie da includere. Se non selezioni nulla, vengono incluse tutte le librerie Movies/TV.
8. Scegli CSV/XLSX, cartella output e opzioni.
9. Premi **Avvia inventario**.

## Analisi duplicati integrata

- Usa il tab **Analisi duplicati** per selezionare un report inventario XLSX (oppure quello appena generato dal tab Inventario Plex viene proposto automaticamente).
- Le regole di classificazione sono integrate nel codice Python (policy v12), non vengono caricate a runtime da JSON.
- Il report inventario originale non viene modificato.
- L'app genera un secondo file: `report_duplicati_plex_classificato_YYYYMMDD_HHMMSS.xlsx`.
- **Stato implementazione policy:** prima versione integrata; alcune regole avanzate della policy v12 sono parziali/in evoluzione.
- **Nota Debug:** i fogli `Debug_XML` e `Debug_Streams` non sono ancora sfruttati completamente in questa prima integrazione.

## Note sicurezza token

Non inserire mai token Plex nel codice e non committare file locali `tokens.json`.
La build GitHub non contiene token. I token vengono inseriti solo a runtime dall'app sul tuo PC.

## Opzioni mappate dallo script

- `RUN_PRESET`: `FAST_PRECISE` / `SLOW_PRECISE`
- `OUTPUT_PROFILE`: `SLIM_BUDGET` / `SLIM_RAW` / `FULL`
- `DURATION_OUTPUT`: `HMS` / `BOTH`
- `WRITE_CSV`
- `WRITE_XLSX`
- `MAX_WORKERS`
- `HTTP_CONCURRENCY_FAST`
- `HTTP_CONCURRENCY_SLOW`
- `TOP_N_MOVIES`
- `TOP_N_SHOWS`
- `SKIP_SHORT_CLIPS`
- `CLIP_MIN_SECONDS`
- `DEBUG`

## Primo test consigliato

Per il primo avvio imposta:

- `RUN_PRESET = FAST_PRECISE`
- `OUTPUT_PROFILE = SLIM_BUDGET`
- `WRITE_XLSX = true`
- `TOP_N_MOVIES = 5`
- `TOP_N_SHOWS = 1`

Così verifichi connessione, permessi e output senza elaborare tutta la libreria.
