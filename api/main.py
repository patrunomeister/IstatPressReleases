"""
Applicazione REST API con FastAPI per interrogare il database SQLite
"copyscape_results.db" generato dagli script copyscape_check.py /
merge_copyscape_results.py / upload_sqlite.py.

Avvio del server (sviluppo locale, con reload automatico), eseguito dalla
cartella "api" oppure dalla cartella radice del progetto:

    cd api
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Documentazione automatica (Swagger UI) disponibile su:

    http://localhost:8000/docs

Esempi di chiamata con curl:

    # Filtro per id_hash
    curl "http://localhost:8000/results?id_hash=74be3b8beb34752637237ef85d70d850fddfd66b58247b637349562242eec3bf"

    # Filtro per codice
    curl "http://localhost:8000/results?codice=PROINDGIU2026"

    # Filtro combinato: id_hash + data (giorno/mese/anno)
    curl "http://localhost:8000/results?id_hash=74be3b8beb34752637237ef85d70d850fddfd66b58247b637349562242eec3bf&data=17/09/2026"
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
# Il percorso del database e' configurabile tramite la variabile d'ambiente
# COPYSCAPE_DB_PATH. Se non impostata, si usa il percorso di default
# "db/copyscape_results.db" nella cartella radice del progetto (un livello
# sopra la cartella "api" in cui si trova questo file), coerente con quanto
# generato da upload_sqlite.py.
DEFAULT_DB_PATH = Path(__file__).parent.parent / "db" / "copyscape_results.db"
DB_PATH = Path(os.getenv("COPYSCAPE_DB_PATH", str(DEFAULT_DB_PATH)))

TABLE_NAME = "copyscape_results"


# ============================================================================
# MODELLI PYDANTIC
# ============================================================================
class CopyscapeResult(BaseModel):
    """Modello di risposta per un singolo record della tabella copyscape_results."""

    id: int
    source_index: Optional[str] = None
    data: Optional[str] = None
    codice: Optional[str] = None
    id_hash: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    min_words_matched: Optional[str] = None
    percent_matched: Optional[str] = None
    words_matched_full: Optional[str] = None
    text_snippet: Optional[str] = None
    view_url: Optional[str] = None
    row_hash: Optional[str] = None


class HealthResponse(BaseModel):
    """Modello di risposta per l'endpoint di health check."""

    status: str = Field(..., description="Stato del servizio (es. 'ok').")
    db_path: str = Field(..., description="Percorso del database SQLite configurato.")
    db_exists: bool = Field(..., description="Indica se il file del database esiste su disco.")


# ============================================================================
# GESTIONE CONNESSIONE AL DATABASE
# ============================================================================
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Dependency FastAPI che apre una connessione SQLite per ogni richiesta
    e la chiude automaticamente al termine, garantendo che nessuna
    connessione resti aperta piu' del necessario.
    """
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Database non trovato nel percorso configurato: {DB_PATH}",
        )

    connection = sqlite3.connect(str(DB_PATH))
    # row_factory sqlite3.Row permette di accedere alle colonne per nome,
    # utile per convertire ogni riga in un dizionario da passare a Pydantic.
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


# ============================================================================
# UTILITY
# ============================================================================
def parse_data_gg_mm_aaaa(data_str: str) -> str:
    """
    Converte una stringa data nel formato gg/mm/aaaa (es. "17/09/2026") nel
    corrispondente formato ISO "YYYY-MM-DD", da usare per il confronto con
    la parte data del campo "data" (timestamp ISO 8601) memorizzato nel db.

    Solleva HTTPException 400 se il formato non e' valido.
    """
    try:
        parsed_date = datetime.strptime(data_str, "%d/%m/%Y")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Formato data non valido: '{data_str}'. "
                "Il parametro 'data' deve essere nel formato gg/mm/aaaa, "
                "ad esempio 17/09/2026."
            ),
        ) from exc

    return parsed_date.strftime("%Y-%m-%d")


# ============================================================================
# APPLICAZIONE FASTAPI
# ============================================================================
app = FastAPI(
    title="Copyscape Results API",
    description=(
        "API REST per interrogare il database SQLite dei risultati "
        "Copyscape (tabella copyscape_results)."
    ),
    version="1.0.0",
)

# CORS permissivo: utile per test locali da browser/frontend su origini
# diverse. In un ambiente di produzione andrebbe ristretto alle origini
# effettivamente autorizzate.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, summary="Verifica lo stato del servizio")
def health_check() -> HealthResponse:
    """Endpoint di health check: conferma che il servizio sia attivo e riporta
    informazioni sul database configurato (senza aprire una connessione)."""
    return HealthResponse(
        status="ok",
        db_path=str(DB_PATH),
        db_exists=DB_PATH.exists(),
    )


@app.get(
    "/results",
    response_model=list[CopyscapeResult],
    summary="Interroga i risultati Copyscape con filtri opzionali",
)
def get_results(
    id_hash: Optional[str] = Query(
        default=None,
        description=(
            "Filtra per corrispondenza esatta sulla colonna id_hash. "
            "Deve essere fornito almeno uno tra id_hash e codice."
        ),
    ),
    codice: Optional[str] = Query(
        default=None,
        description=(
            "Filtra per corrispondenza esatta sulla colonna codice. "
            "Deve essere fornito almeno uno tra id_hash e codice."
        ),
    ),
    data: Optional[str] = Query(
        default=None,
        description=(
            "Filtra per data nel formato gg/mm/aaaa (es. 17/09/2026). "
            "Viene confrontata solo la parte giorno/mese/anno del campo "
            "'data' (timestamp ISO 8601), ignorando l'ora."
        ),
    ),
    connection: sqlite3.Connection = Depends(get_db_connection),
) -> list[CopyscapeResult]:
    """
    Restituisce i record della tabella copyscape_results che soddisfano i
    filtri forniti (applicati in AND tra loro).

    Regole di validazione:
    - almeno uno tra id_hash e codice e' obbligatorio;
    - se il parametro data non rispetta il formato gg/mm/aaaa viene
      restituito un errore 400;
    - se nessun record corrisponde ai filtri, viene restituita una lista
      vuota con status 200 (non un errore 404).
    """
    # Almeno uno tra id_hash e codice deve essere presente.
    if not id_hash and not codice:
        raise HTTPException(
            status_code=400,
            detail="E' necessario fornire almeno uno tra i parametri 'id_hash' e 'codice'.",
        )

    # Costruzione dinamica della query con placeholder parametrizzati, per
    # evitare qualsiasi rischio di SQL injection.
    conditions: list[str] = []
    params: list[str] = []

    if id_hash:
        conditions.append("id_hash = ?")
        params.append(id_hash)

    if codice:
        conditions.append("codice = ?")
        params.append(codice)

    if data:
        # Converte gg/mm/aaaa -> YYYY-MM-DD, con validazione del formato.
        iso_day = parse_data_gg_mm_aaaa(data)
        # Il campo "data" e' un timestamp ISO 8601 completo (es.
        # "2026-09-17T15:06:49.409064"): per confrontare solo la parte
        # data (giorno) si usa date(...) di SQLite, che estrae "YYYY-MM-DD"
        # da un timestamp ISO 8601.
        conditions.append("date(data) = ?")
        params.append(iso_day)

    where_clause = " AND ".join(conditions)
    query = f"SELECT * FROM {TABLE_NAME} WHERE {where_clause} ORDER BY id"  # noqa: S608

    cursor = connection.execute(query, params)
    rows = cursor.fetchall()

    # Converte ogni sqlite3.Row in un dizionario e poi nel modello Pydantic
    # di risposta.
    return [CopyscapeResult(**dict(row)) for row in rows]


if __name__ == "__main__":
    # Consente anche l'avvio diretto con "python main.py" oltre che con
    # "uvicorn main:app --reload".
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
