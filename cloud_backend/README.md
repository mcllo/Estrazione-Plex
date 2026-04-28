# Plex Inventory Cloud Backend

Backend FastAPI pensato per Google Cloud Run.

L'obiettivo e' eseguire l'inventario Plex da una rete cloud, aggirando i blocchi di rete del PC Windows.

## Endpoint

- `GET /health` verifica che il servizio sia online.
- `POST /inventory` riceve token Plex, server e opzioni, esegue l'inventario e restituisce `plex_inventory_result.zip`.

## Sicurezza

Il token Plex non viene salvato. Viene usato solo per la richiesta corrente.

Puoi proteggere il backend impostando la variabile ambiente:

```text
PLEX_INVENTORY_API_KEY=una_chiave_lunga_a_caso
```

Quando la variabile e' presente, il client deve inviare l'header:

```text
X-Api-Key: una_chiave_lunga_a_caso
```

## Deploy manuale Cloud Run

Da root del repository, dopo aver installato e configurato `gcloud`:

```bash
gcloud run deploy plex-inventory \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars PLEX_INVENTORY_API_KEY=SOSTITUISCI_CON_CHIAVE_LUNGA \
  --timeout 3600 \
  --memory 2Gi \
  --cpu 2
```

Cloud Run passa la porta da ascoltare tramite variabile `PORT`; il Dockerfile usa quella variabile.

## Test locale

```bash
pip install -r cloud_backend/requirements.txt
uvicorn cloud_backend.main:app --reload --port 8080
```

Poi:

```bash
curl http://127.0.0.1:8080/health
```
