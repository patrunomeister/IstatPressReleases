# IstatPressReleases
Monitoraggio Comunicati Stampa Istat

## Script Copyscape

Nel progetto e presente lo script `copyscape_url_check.py` per verificare se il contenuto di una pagina web compare altrove sul web tramite Copyscape Premium API.

### Requisiti

- Account Copyscape Premium
- Username e API key
- Python 3

### Configurazione credenziali

Puoi fornire le credenziali Copyscape in 3 modi (in ordine di priorità):

1. **Costanti nello script** (veloce per utilizzi ripetuti):
   Apri `copyscape_url_check.py` e modifica:
   ```python
   COPYSCAPE_USERNAME = "tuo_username"
   COPYSCAPE_API_KEY = "tua_api_key"
   ```

2. **Variabili di ambiente** (sicuro per automatizzazioni):
   ```powershell
   $env:COPYSCAPE_USERNAME="tuo_username"
   $env:COPYSCAPE_API_KEY="tua_api_key"
   ```

3. **Parametri CLI** (usa questi per override):
   ```powershell
   python .\copyscape_url_check.py "https://example.com" --username "username" --api-key "key"
   ```

### Esecuzione

Dopo aver configurato le credenziali, esegui:

```powershell
python .\copyscape_url_check.py "https://example.com/articolo"
```

Opzioni utili:

- `--full-comparisons 1..10` per richiedere confronti completi
- `--ignore-sites "dominio1.com,dominio2.com"` per escludere siti
- `--spend-limit 0.50` per limitare il costo massimo
- `--example-test` per usare il test ufficiale Copyscape (non addebitato)
- `--output risultati.csv` per specificare il nome del file CSV (default: `copyscape_results_<timestamp>.csv`)
- `--raw-json` per stampare la risposta JSON completa

### Output CSV

Lo script salva automaticamente un file CSV con i risultati nel formato locale, contenente:

- **Metadata**: URL cercato, data/ora, numero totale di risultati, costo, numero parole
- **Tabella risultati**: indice, URL, titolo, parole corrispondenti, percentuale di corrispondenza, snippet di testo, link di visualizzazione

Esempio:
```powershell
python .\copyscape_url_check.py "https://example.com/articolo" --output "miei_risultati.csv"
```

Il file verrà salvato in locale e il path assoluto stampato nel terminale.

## Scheduler dei controlli Copyscape (`copyscape_scheduler.py`)

Analizzare tutti i comunicati stampa su Copyscape nello stesso momento non
permette di individuare plagi comparsi solo a distanza di tempo dalla
pubblicazione. Lo script `copyscape_scheduler.py` risolve il problema
pianificando, per ciascun comunicato presente in `get_print_reviews.csv`, un
piano di controlli distanziati nel tempo (ad es. dopo 1 giorno, dopo 1
settimana, dopo 1 mese), invece di interrogare Copyscape per tutti i testi
in un'unica soluzione.

Gli offset sono calcolati a partire dal campo `data` del comunicato (la sua
data di rilascio), non dalla data in cui lo scheduler lo incontra per la
prima volta. Ad esempio, se un comunicato ha `data` uguale a oggi e il
piano prevede gli offset `[7, 30]`, i controlli scadranno tra 7 e 30 giorni
da oggi; se invece `data` risale a 2 giorni fa e il piano prevede l'offset
`15`, il controllo scadra' tra 13 giorni (15 - 2) da oggi. Se il campo
`data` manca o non è interpretabile, si usa come base la data odierna di
registrazione (comportamento precedente).

### File di configurazione

Il piano di controlli si personalizza tramite `copyscape_schedule_config.json`
(creato automaticamente con valori di default alla prima esecuzione):

```jsonc
{
  "default_offsets_days": [1, 7, 30],
  "overrides": {
    // chiave = campo "codice" per intero (non solo i primi caratteri) o
    // "id_hash" di uno specifico comunicato
    "PROINDGIU2026": [1, 3, 10, 30]
  },
  "max_attempts": 3
}
```

### File di stato

Lo scheduler tiene traccia dell'avanzamento del piano nel file
`copyscape_schedule_state.json`: per ogni comunicato (chiave `id_hash`)
registra la data di prima registrazione nello scheduler (`first_seen`), la
data di rilascio usata come base per il calcolo delle scadenze
(`release_date`, presa dal campo `data` di `get_print_reviews.csv`), il
testo da analizzare (congelato al momento della registrazione, cosi' resta
disponibile anche quando il comunicato non compare piu' nel feed RSS piu'
recente) e lo stato di ciascun controllo pianificato (`pending`, `done`,
`error`, `failed`).

### Esecuzione

Va eseguito periodicamente (ad es. una volta al giorno tramite Task
Scheduler di Windows o cron):

```powershell
python .\copyscape_scheduler.py
```

Opzioni utili:

- `--dry-run` per vedere quali comunicati verrebbero registrati e quali
  controlli risultano in scadenza, senza chiamare Copyscape ne' modificare
  lo stato
- `--ignore-schedule` per eseguire subito tutti i controlli pianificati non
  ancora completati, indipendentemente dalla data prevista (utile per test)
- `--example-test` per usare il test ufficiale Copyscape (non addebitato)
- `--config`, `--state`, `--input` per usare percorsi alternativi

I risultati vengono salvati con lo stesso formato di `copyscape_check.py`
(`copyscape_results_<timestamp>.csv` e `copyscape_log.csv`), restando
compatibili con `merge_copyscape_results.py` e `upload_sqlite.py`.

## Pipeline completa e orchestratore (`run_pipeline.py`)

L'intero flusso di monitoraggio e' composto da 5 script da eseguire in
sequenza, uno dopo l'altro:

1. `rss_latest_hash.py` — legge `rss_feeds.txt` e aggiorna
   `rss_latest_hash_output.csv` (colonne `data, titolo, codice, id_hash,
   link`) con i nuovi comunicati trovati nei feed RSS.
2. `get_print_reviews.py` — legge `rss_latest_hash_output.csv`, scarica il
   testo di ogni link e aggiorna `get_print_reviews.csv` (aggiunge la
   colonna `testo`).
3. `copyscape_scheduler.py` — legge `get_print_reviews.csv`, registra i
   nuovi comunicati nel piano ed esegue solo i controlli Copyscape in
   scadenza in quel momento, producendo `copyscape_results_<timestamp>.csv`
   e aggiornando `copyscape_log.csv`.
4. `merge_copyscape_results.py` — unisce tutti i `copyscape_results_*.csv`
   in `output_cumulativo.csv`, scartando i duplicati (stesso `id_hash`,
   stesso giorno, stesso URL).
5. `upload_sqlite.py` — carica `output_cumulativo.csv` in
   `db\copyscape_results.db`, aggiungendo solo le righe non gia' presenti.

Per non dover schedulare 5 comandi separati, e' disponibile lo script
`run_pipeline.py` che li esegue tutti in sequenza con lo stesso interprete
Python in uso, fermandosi al primo errore (cosi' non si propagano dati
incompleti agli step successivi):

```powershell
python .\run_pipeline.py
```

Opzioni utili:

- `--skip rss_latest_hash.py,get_print_reviews.py` per saltare uno o piu'
  step (elenco separato da virgole, per nome file)
- `--scheduler-args "--ignore-schedule --example-test"` per passare
  argomenti extra a `copyscape_scheduler.py` (ad es. per un test forzato)
- `--continue-on-error` per proseguire con gli step successivi anche se uno
  step fallisce, invece di fermarsi subito

### Pianificazione (Task Scheduler di Windows)

Va pianificata **una sola esecuzione al giorno** di `run_pipeline.py` (e'
lo scheduler interno, allo step 3, a decidere quali controlli Copyscape
sono effettivamente dovuti quel giorno — non serve pianificare offset
diversi per ogni comunicato a livello di Task Scheduler).

Per creare l'attivita' pianificata da PowerShell (eseguita come utente
corrente, una volta al giorno, ad es. alle 06:00):

```powershell
$action = New-ScheduledTaskAction -Execute "python.exe" `
    -Argument "run_pipeline.py" `
    -WorkingDirectory "C:\percorso\completo\IstatPressReleases"
$trigger = New-ScheduledTaskTrigger -Daily -At 6:00am
Register-ScheduledTask -TaskName "IstatPressReleasesPipeline" `
    -Action $action -Trigger $trigger -Description "Pipeline monitoraggio comunicati stampa Istat"
```

In alternativa si puo' usare l'interfaccia grafica "Utilita' di
pianificazione" (Task Scheduler), creando un'attivita' che esegue
`python.exe` con argomento `run_pipeline.py` e cartella di lavoro impostata
sulla root del progetto.

Il servizio API (`api\main.py`) e' invece un processo sempre attivo (non va
schedulato): va avviato una volta e lasciato in esecuzione, ad es. con:

```powershell
cd api
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

