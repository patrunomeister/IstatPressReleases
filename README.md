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
