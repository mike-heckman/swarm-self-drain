# 004-3het8o4-coder: Create Ansible Deployment Scripts

## Context
- **Objective:** Automate the installation of the worker hook and systemd unit across Swarm worker nodes via Ansible.
- **Reference:** `docs/software-design-document.md`

## Implementation Plan
### Proposed Changes
#### [NEW] [ansible/deploy-worker.yml](file:///home/mike/Projects/mike-heckman/swarm-self-drain/ansible/deploy-worker.yml)
- Ansible playbook to ensure Python 3 is installed.
- Distributes `scripts/worker-hook.py` and `scripts/swarm-self-drain.service`.
- Securely distributes the `API_PSK`, `MANAGER_API_URL`, and optional Discord variables (`DISCORD_WEBHOOK_URL`, `DISCORD_SHUTDOWN`, `DISCORD_STARTUP`) to `/opt/swarm-self-drain/.env`, ensuring it is owned by `root:root` and has `0600` permissions.
- Enables and starts the systemd service.
