# KeepRoLLMing - Analisi del Progetto

## Overview

Questo progetto è un **proxy/orchestrator FastAPI** che funziona come intermediario tra client e backend LLM streaming (ad esempio LM Studio) per gestire automaticamente il problema del **overflow del contesto**.

### Caratteristiche principali:
- **Endpoint OpenAI compatibile**: `POST /v1/chat/completions`
- **Supporto profili**: `local/quick`, `local/main`, `local/deep` (configurabili via env)
- **Passthrough mode**: `pass/<BACKEND_MODEL_NAME>` per bypassare il summarization
- **Streaming proxy SSE** supportato
- **Best-effort token accounting**
- **Rolling summary logic** che comprime parte del contesto quando necessario

### Architecture:
- The code is divided into modules, with the main app (`keeprollming/app.py`) handling requests and summarization logic.
- `keeprollming/upstream.py` for communicating with upstream backend
- `keeprollming/rolling_summary.py` for generating context summaries  
- `keeprollming/logger.py` for logging requests and responses (with various log levels)
- `keeprollming/performance.py` for recording performance metrics (TTFT, TPS, etc.)
- `keeprollming/summary_cache.py` for summary caching
- `keeprollming/config.py` for environment-based configuration

### Tests:
- Uses pytest with unit/integration/end-to-end tests.
- E2E tests mock the upstream backend (don't require a live LM Studio instance)
- Supports two test modes: fake backend and live backend.

### Struttura del codice:
- Il progetto è ben organizzato in moduli separati
- Usa `httpx.AsyncClient` per chiamate HTTP async
- Gestisce il caching dei riassunti con fingerprinting basato su messaggi utente (con supporto LibreChat)
- Implementa log di performance e streaming progress

### Workflow Guidelines

#### Task Management:
- **ACTIVE_TASK.md**: Used to track the current working task during collaboration
- **COMPLETED_TASKS.md**: Stores completed task history after moving from ACTIVE_TASK.md
- **TODO.md**: Contains a list of possible tasks to pick for future work

This workflow ensures that we maintain clear tracking of ongoing work while preserving historical context and providing organized task selection.

### Considerazioni:
- Il progetto è **modularizzato** e ben strutturato, adatto per un uso in produzione
- La logica di rolling summary è robusta e gestisce overflow del contesto con retry e chunking
- Supporta caching dei riassunti (cache_append mode)
- Usa token counting fallback basato su chars/4 se tiktoken non disponibile

### Risultati:
- Il progetto sembra **ben fatto** per un proxy/orchestrator che gestisce il contesto di LLM in modo efficiente e compatibile con OpenAI API
- La logica di summarization è **robusta**, con fallback e retry logic
- Test coverage è **completo** (unit, integration, E2E)
- Il caching dei riassunti è **efficiente**
- Supporta diverse configurazioni via env

### Possibili miglioramenti:
- Potrebbe essere utile aggiungere test per verificare il comportamento in caso di errore HTTP 400 con specifico messaggio
- Potrebbe essere utile un log più dettagliato per debug (in particolare per streaming)
- Potrebbe essere utile una configurazione più flessibile per modelli e profili

### Conclusione:
Questo progetto è **ben strutturato**, con codice pulito, test coverage completo e architettura modularizzata. È adatto a un uso in produzione come proxy/orchestrator per LLM streaming.

Se hai bisogno di ulteriori informazioni o chiarimenti su specifiche parti del codice, posso aiutarti con dettagli.

## Caching Mechanism

For detailed information about how rolling summaries are cached and reused, please refer to [CACHING_MECHANISM.md](./CACHING_MECHANISM.md).
