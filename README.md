# IstatPressReleases

Monitoraggio dei comunicati stampa Istat: raccolta dal feed RSS, estrazione
del testo, verifica antiplagio tramite Copyscape (con pianificazione nel
tempo), archiviazione in un database SQLite ed esposizione dei risultati
tramite una REST API.

## Indice

- [Flusso di lavoro](#flusso-di-lavoro)
- [Requisiti e installazione](#requisiti-e-installazione)
- [1. Aggiornamento feed RSS — `rss_latest_hash.py`](#1-aggiornamento-feed-rss--rss_latest_hashpy)
- [2. Estrazione del testo — `get_print_reviews.py`](#2-estrazione-del-testo--get_print_reviewspy)
- [3. Controllo Copyscape](#3-controllo-copyscape)
  - [3a. Controllo immediato — `copyscape_check.py`](#3a-controllo-immediato--copyscape_checkpy)
  - [3b. Scheduler dei controlli — `copyscape_scheduler.py`](#3b-scheduler-dei-controlli--copyscape_schedulerpy)
  - [Filtro dei comunicati da monitorare — `filter.txt`](#filtro-dei-comunicati-da-monitorare--filtertxt)
  - [Codice condiviso — `copyscape_common.py`](#codice-condiviso--copyscape_commonpy)
- [4. Merge dei risultati — `merge_copyscape_results.py`](#4-merge-dei-risultati--merge_copyscape_resultspy)
- [5. Caricamento su SQLite — `upload_sqlite.py`](#5-caricamento-su-sqlite--upload_sqlitepy)
- [Pipeline completa e orchestratore — `run_pipeline.py`](#pipeline-completa-e-orchestratore--run_pipelinepy)
- [Pianificazione automatica (Task Scheduler di Windows)](#pianificazione-automatica-task-scheduler-di-windows)
- [Esecuzione automatica con GitHub Actions](#esecuzione-automatica-con-github-actions)
- [REST API — `api/main.py`](#rest-api--apimainpy)
- [Struttura dei file del progetto](#struttura-dei-file-del-progetto)

## Flusso di lavoro

```
rss_feeds.txt
     │
     ▼
1. rss_latest_hash.py        → rss_latest_hash_output.csv
     │                          (data, titolo, codice, id_hash, link)
     ▼
2. get_print_reviews.py      → get_print_reviews.csv
     │                          (+ colonna "testo")
     ▼
3. copyscape_scheduler.py    → copyscape_results_<timestamp>.csv + copyscape_log.csv
     │ (solo controlli in       (solo per i comunicati in scadenza, eventualmente
     │  scadenza, filtrati       filtrati tramite filter.txt)
     │  da filter.txt)
     ▼
4. merge_copyscape_results.py → output_cumulativo.csv
     │                           (deduplicato per id_hash + giorno + URL)
     ▼
5. upload_sqlite.py          → db/copyscape_results.db
                                (append incrementale, nessun duplicato)
                                       │
                                       ▼
                              api/main.py (FastAPI, servizio sempre attivo)
                              → REST API /results, /health
```

Gli step 1-5 vanno eseguiti in sequenza, una volta al giorno (si veda
[Pipeline completa e orchestratore](#pipeline-completa-e-orchestratore--run_pipelinepy)
per eseguirli tutti con un solo comando). L'API (step finale) e' invece un
servizio persistente, non va rischedulata ad ogni esecuzione.

## Requisiti e installazione

- Python 3.10+
- Account Copyscape Premium (username e API key)

Il file [`requirements.txt`](requirements.txt) nella root installa tutte le
dipendenze necessarie all'intera pipeline (RSS, estrazione testo,
Copyscape, merge, SQLite) **e** alla REST API:

```powershell
pip install -r requirements.txt
```

### Configurazione credenziali Copyscape

Le credenziali Copyscape (usate da `copyscape_check.py` e
`copyscape_scheduler.py` tramite `copyscape_common.py`) possono essere
fornite in 3 modi, in ordine di priorità:

1. **Parametri CLI** (hanno sempre la precedenza):
   ```powershell
   python .\copyscape_check.py --username "username" --api-key "key"
   ```
2. **Variabili di ambiente** (consigliato per automatizzazioni):
   ```powershell
   $env:COPYSCAPE_USERNAME = "tuo_username"
   $env:COPYSCAPE_API_KEY = "tua_api_key"
   ```
3. **Costanti nello script** (solo per uso locale, **sconsigliato**: non
   committare mai credenziali reali): in `copyscape_common.py` sono
   `DEFAULT_COPYSCAPE_USERNAME` e `DEFAULT_COPYSCAPE_API_KEY`, vuote per
   default.

Nell'esecuzione automatica su GitHub Actions le credenziali sono lette dai
*secrets* del repository `COPYSCAPE_USERNAME` e `COPYSCAPE_API_KEY`.

## 1. Aggiornamento feed RSS — `rss_latest_hash.py`

Legge gli URL dei feed RSS elencati in `rss_feeds.txt` (uno per riga),
elabora **tutti** gli elementi di ciascun feed e aggiorna in append
`rss_latest_hash_output.csv`:

- ogni comunicato viene identificato in modo univoco dal suo `link`: se un
  link e' gia' presente nel file di output, **non viene reinserito ne'
  sovrascritto**;
- i comunicati nuovi vengono aggiunti al file;
- il file di output viene infine riordinato per `data` dalla **piu' recente
  alla piu' remota** (le righe con data sconosciuta restano in fondo).

Colonne del CSV prodotto: `data, titolo, codice, id_hash, link`
(`id_hash` e' calcolato come hash SHA-256 del titolo normalizzato; `codice`
e' un codice sintetico del comunicato, es. `PRECONAGO2026`, derivato dal
feed di provenienza, dal tipo di comunicato e dal mese/anno nel titolo).

```powershell
python .\rss_latest_hash.py
```

Il formato precedente (3 o 4 colonne, senza la colonna `data`) viene letto
in modo retrocompatibile al primo avvio sul file esistente.

## 2. Estrazione del testo — `get_print_reviews.py`

Legge `rss_latest_hash_output.csv`, scarica la pagina HTML di ogni `link` ed
estrae il testo contenuto nel tag `<div class="guid-article">`, salvando il
risultato in `get_print_reviews.csv` (tutte le colonne di input, piu' la
colonna `testo`).

Lo step e' **incrementale**: i testi gia' presenti e non vuoti in
`get_print_reviews.csv` vengono riutilizzati e vengono scaricate solo le
pagine nuove o per cui il download precedente non ha prodotto testo (i
comunicati senza testo vengono saltati dallo scheduler e riprovati alla
esecuzione successiva). Al termine viene stampato un riepilogo
(riutilizzati / scaricati / senza testo).

```powershell
python .\get_print_reviews.py
```

## 3. Controllo Copyscape

Esistono due modalita' per interrogare Copyscape sui testi estratti:
`copyscape_check.py` (esegue subito tutti i controlli) e
`copyscape_scheduler.py` (pianifica i controlli nel tempo). Entrambi
leggono `get_print_reviews.csv` e producono risultati nello stesso formato,
cosi' da restare compatibili con gli step successivi della pipeline.

### 3a. Controllo immediato — `copyscape_check.py`

Interroga Copyscape **una volta per ciascuna riga** del file di input
(di default `get_print_reviews.csv`), passando il testo nel campo `testo`
(ricerca testuale via POST, non per URL).

```powershell
python .\copyscape_check.py
```

Opzioni utili:

- `--input percorso.csv` per un file di input alternativo (default:
  `get_print_reviews.csv`)
- `--full-comparisons 1..10` per richiedere confronti completi
- `--ignore-sites "dominio1.com,dominio2.com"` per escludere siti dai
  risultati
- `--spend-limit 0.50` per limitare il costo massimo della richiesta
- `--example-test` per usare il test ufficiale Copyscape (non addebitato)
- `--output risultati.csv` / `--log-output log.csv` per percorsi di output
  alternativi (default: `copyscape_results_<timestamp>.csv` e
  `copyscape_log.csv`)

### 3b. Scheduler dei controlli — `copyscape_scheduler.py`

Analizzare tutti i comunicati stampa su Copyscape nello stesso momento non
permette di individuare plagi comparsi solo a distanza di tempo dalla
pubblicazione. `copyscape_scheduler.py` risolve il problema pianificando,
per ciascun comunicato presente in `get_print_reviews.csv`, un piano di
controlli distanziati nel tempo (di default uno al giorno, da 1 a 30
giorni dalla data di rilascio), invece di interrogare Copyscape per tutti i testi in
un'unica soluzione.

Gli offset sono calcolati a partire dal campo `data` del comunicato (la sua
data di rilascio), non dalla data in cui lo scheduler lo incontra per la
prima volta. Ad esempio, se un comunicato ha `data` uguale a oggi e il
piano prevede gli offset `[7, 30]`, i controlli scadranno tra 7 e 30 giorni
da oggi; se invece `data` risale a 2 giorni fa e il piano prevede l'offset
`15`, il controllo scadra' tra 13 giorni (15 - 2) da oggi. Se il campo
`data` manca o non e' interpretabile, si usa come base la data odierna di
registrazione.

#### File di configurazione (`copyscape_schedule_config.json`)

Il piano di controlli si personalizza tramite questo file (creato
automaticamente con valori di default alla prima esecuzione):

```jsonc
{
  "default_offsets_days": [1, 2, 3, ..., 30],
  "overrides": {
    // chiave = campo "codice" per intero (non solo i primi caratteri) o
    // "id_hash" di uno specifico comunicato
    "PROINDGIU2026": [1, 3, 10, 30]
  },
  "max_attempts": 3
}
```

La priorita' di risoluzione e': override per `id_hash` > override per
`codice` (uguaglianza esatta) > `default_offsets_days`.

#### File di stato (`copyscape_schedule_state.json`)

Tiene traccia dell'avanzamento del piano: per ogni comunicato (chiave
`id_hash`) registra la data di prima registrazione nello scheduler
(`first_seen`), la data di rilascio usata come base per il calcolo delle
scadenze (`release_date`), il testo da analizzare (congelato al momento
della registrazione, cosi' resta disponibile anche quando il comunicato non
compare piu' nel feed RSS piu' recente) e lo stato di ciascun controllo
pianificato (`pending`, `done`, `error`, `failed`).

#### Esecuzione

Va eseguito periodicamente (ad es. una volta al giorno tramite Task
Scheduler di Windows o cron): ad ogni esecuzione registra i nuovi
comunicati nel piano ed esegue solo i controlli effettivamente in scadenza
quel giorno.

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
- `--filter filter.txt` per limitare i comunicati monitorati/controllati
  solo a quelli presenti nel file di filtro (si veda sotto)

I controlli che falliscono (es. errore di rete o risposta di errore da
Copyscape) vengono ritentati alle esecuzioni successive fino a
`max_attempts` tentativi, dopodiche' passano allo stato `failed` e non
vengono piu' ritentati.

### Filtro dei comunicati da monitorare — `filter.txt`

Non tutti i comunicati presenti in `get_print_reviews.csv` devono
necessariamente essere analizzati su Copyscape. `filter.txt` e' un file di
testo con una riga per comunicato nel formato `link,codice_man` (la prima
riga di intestazione e le righe vuote sono ignorate; sono ammessi anche i
separatori `;` e tabulazione), ad es.:

```
link,codice_man
https://www.istat.it/comunicato-stampa/prezzi-al-consumo-agosto-2026/,PRECONAGO2026
https://www.istat.it/comunicato-stampa/produzione-industriale-luglio-2026/,PROINDLUG2026
```

Il `codice_man` **sostituisce**, da quel momento in poi, il `codice` che
`get_print_reviews.csv` riporta per quel link: viene usato per gli override
di `copyscape_schedule_config.json`, salvato nello stato dello scheduler
(anche per i comunicati gia' registrati) e scritto nei risultati Copyscape.
Se il `codice_man` manca (riga con il solo link), resta il `codice`
originale.

Passando `--filter filter.txt` a `copyscape_scheduler.py` (o a
`run_pipeline.py`, che lo inoltra automaticamente), vengono registrati e
controllati solo i comunicati il cui `link` e' presente nel file; gli altri
vengono ignorati (ne' registrati nel piano, ne' controllati anche se gia'
presenti nello stato). **Se l'opzione non viene passata, vengono monitorati
tutti i comunicati** (comportamento di default).

```powershell
python .\copyscape_scheduler.py --filter filter.txt
```

### Codice condiviso — `copyscape_common.py`

`copyscape_check.py` e `copyscape_scheduler.py` condividono la logica di
chiamata a Copyscape (costruzione dei parametri, richiesta HTTP POST,
lettura del CSV di input, salvataggio dei CSV di output/log) tramite il
modulo `copyscape_common.py`, cosi' da evitare di mantenere due copie dello
stesso codice. Le credenziali di default e l'elenco dei domini da ignorare
sono anch'essi definiti in questo modulo.

Il modulo definisce anche `RESULT_COLUMNS`, lo schema del CSV dei risultati,
importato da `merge_copyscape_results.py` e `upload_sqlite.py` in modo che le
colonne restino coerenti lungo tutta la pipeline.

Entrambi gli script producono gli stessi file di output
(`copyscape_results_<timestamp>.csv` e `copyscape_log.csv`), restando
compatibili con `merge_copyscape_results.py` e `upload_sqlite.py`.

## 4. Merge dei risultati — `merge_copyscape_results.py`

Unisce tutti i file `copyscape_results_*.csv` presenti nella cartella
corrente in un unico file cumulativo `output_cumulativo.csv`, elaborandoli
in ordine cronologico (ordine dei nomi file). Per evitare duplicati quando
Copyscape viene interrogato piu' volte per lo stesso comunicato, le righe
con lo stesso `id_hash`, la stessa data (giorno/mese/anno nel campo `data`)
e lo stesso `URL` vengono scartate dopo la prima occorrenza incontrata.

```powershell
python .\merge_copyscape_results.py
```

Opzioni utili:

- `--input-glob "copyscape_results_*.csv"` per un pattern di file diverso
- `--output output_cumulativo.csv` per un percorso di output alternativo

## 5. Caricamento su SQLite — `upload_sqlite.py`

Carica `output_cumulativo.csv` nel database SQLite
`db\copyscape_results.db` (tabella `copyscape_results`), salvando ogni file
collegato al database nella cartella `db`. Ad ogni esecuzione confronta il
contenuto di ciascuna riga (tramite un hash di deduplicazione) con quanto
gia' presente nel database: vengono aggiunte **solo le righe mancanti**
(append incrementale), evitando di importare piu' volte le stesse righe.

```powershell
python .\upload_sqlite.py
```

Opzioni utili:

- `--input output_cumulativo.csv` per un file di input alternativo
- `--db-dir db` / `--db-name copyscape_results.db` per percorsi del
  database alternativi

## Pipeline completa e orchestratore — `run_pipeline.py`

Per non dover schedulare 5 comandi separati, `run_pipeline.py` esegue in
sequenza tutti gli step 1-5 con lo stesso interprete Python in uso,
fermandosi al primo errore (cosi' non si propagano dati incompleti agli
step successivi):

```powershell
python .\run_pipeline.py
```

Opzioni utili:

- `--skip rss_latest_hash.py,get_print_reviews.py` per saltare uno o piu'
  step (elenco separato da virgole, per nome file)
- `--scheduler-args "--ignore-schedule --example-test"` per passare
  argomenti extra a `copyscape_scheduler.py` (ad es. per un test forzato)
- `--filter filter.txt` per limitare i controlli Copyscape (step 3) solo ai
  comunicati elencati in quel file; se omesso, vengono processati tutti i
  comunicati presenti in `get_print_reviews.csv` (comportamento di default)
- `--continue-on-error` per proseguire con gli step successivi anche se uno
  step fallisce, invece di fermarsi subito

## Pianificazione automatica (Task Scheduler di Windows)

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

## Esecuzione automatica con GitHub Actions

Il workflow [`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml)
esegue ogni giorno alle 20:00 (ora italiana, estiva) — e manualmente dalla
tab *Actions* — il comando `python run_pipeline.py --filter filter.txt`
sul branch `main`, poi committa i file aggiornati (CSV, stato dello
scheduler, database SQLite). Richiede i secrets `COPYSCAPE_USERNAME` e
`COPYSCAPE_API_KEY`. Per cambiare i comunicati monitorati basta modificare
`filter.txt` (formato `link,codice_man`) su `main`.

## REST API — `api/main.py`

Applicazione FastAPI che interroga il database SQLite
`db/copyscape_results.db` generato da `upload_sqlite.py`. E' un processo
sempre attivo: va avviato una volta e lasciato in esecuzione (non va
schedulato insieme alla pipeline).

```powershell
pip install -r requirements.txt
cd api
uvicorn main:app --host 0.0.0.0 --port 8000
```

Documentazione automatica (Swagger UI) disponibile su
`http://localhost:8000/docs`. Il percorso del database e' configurabile
tramite la variabile d'ambiente `COPYSCAPE_DB_PATH` (default:
`db/copyscape_results.db` nella root del progetto).

### Endpoint

- `GET /health` — verifica che il servizio sia attivo e riporta il percorso
  e l'esistenza del database.
- `GET /results` — interroga i record di `copyscape_results` con filtri
  opzionali, applicati in AND:
  - `id_hash` (corrispondenza esatta)
  - `codice` (corrispondenza esatta)
  - `data` (formato `gg/mm/aaaa`, confrontata solo sulla parte
    giorno/mese/anno del campo `data`, un timestamp ISO 8601)

  E' obbligatorio fornire almeno uno tra `id_hash` e `codice` (altrimenti
  risposta 400). Se nessun record corrisponde ai filtri viene restituita
  una lista vuota con status 200.

Esempi con curl:

```powershell
# Filtro per id_hash
curl "http://localhost:8000/results?id_hash=74be3b8beb34752637237ef85d70d850fddfd66b58247b637349562242eec3bf"

# Filtro per codice
curl "http://localhost:8000/results?codice=PROINDGIU2026"

# Filtro combinato: id_hash + data (giorno/mese/anno)
curl "http://localhost:8000/results?id_hash=74be3b8beb34752637237ef85d70d850fddfd66b58247b637349562242eec3bf&data=17/09/2026"
```

## Struttura dei file del progetto

| File / cartella | Descrizione |
| --- | --- |
| `rss_feeds.txt` | Elenco degli URL dei feed RSS da monitorare (uno per riga). |
| `rss_latest_hash.py` | Step 1: aggiorna `rss_latest_hash_output.csv` dai feed RSS. |
| `rss_latest_hash_output.csv` | Output dello step 1 (`data, titolo, codice, id_hash, link`). |
| `get_print_reviews.py` | Step 2: estrae il testo dei comunicati. |
| `get_print_reviews.csv` | Output dello step 2 (input dello step 3). |
| `copyscape_common.py` | Funzioni condivise per chiamare Copyscape e leggere i CSV di input. |
| `copyscape_check.py` | Step 3a: controllo Copyscape immediato di tutti i testi. |
| `copyscape_scheduler.py` | Step 3b: controllo Copyscape pianificato nel tempo. |
| `copyscape_schedule_config.json` | Configurazione del piano di controlli dello scheduler. |
| `copyscape_schedule_state.json` | Stato persistente dello scheduler (generato automaticamente). |
| `filter.txt` | Filtro opzionale `link,codice_man` dei comunicati da controllare su Copyscape. |
| `.github/workflows/pipeline.yml` | Esecuzione giornaliera automatica della pipeline su GitHub Actions. |
| `copyscape_results_*.csv` / `copyscape_log.csv` | Output del controllo Copyscape (per singola esecuzione / log cumulativo). |
| `merge_copyscape_results.py` | Step 4: unisce i risultati Copyscape in un unico file. |
| `output_cumulativo.csv` | Output dello step 4 (input dello step 5). |
| `upload_sqlite.py` | Step 5: carica i risultati cumulativi nel database SQLite. |
| `db/` | Cartella con il database SQLite `copyscape_results.db`. |
| `run_pipeline.py` | Orchestratore che esegue in sequenza gli step 1-5. |
| `api/main.py` | REST API FastAPI per interrogare il database SQLite. |
| `requirements.txt` | Dipendenze Python dell'intero progetto (pipeline + API). |
