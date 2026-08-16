# 002-3het8o4-coder: Create Dockerfile and Docker Compose File

## Context
- **Objective:** Containerize the FastAPI manager app and provide a realistic Swarm deployment example using Docker Secrets.
- **Reference:** `docs/software-design-document.md`

## Implementation Plan
### Proposed Changes
#### [NEW] [Dockerfile](file:///home/mike/Projects/mike-heckman/swarm-self-drain/Dockerfile)
- Use `python:3.12-slim`.
- Install `uv` and use it to add `fastapi` and `uvicorn`.
- Run Uvicorn on port 8000.

#### [NEW] [docker-compose.yml](file:///home/mike/Projects/mike-heckman/swarm-self-drain/docker-compose.yml)
- Define `swarm-self-drain` service.
- Mount `/var/run/docker.sock`.
- Provide external secret `api_psk` and pass `API_PSK_FILE=/run/secrets/api_psk`.
