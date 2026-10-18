# OpenDeal — Interview & Local Demonstration Guide

This guide provides step-by-step instructions for running the OpenDeal demonstration on an **Apple Silicon Mac** (or Linux/Windows), verified against the repository codebase.

---

## 1. Prerequisites & Environment Setup

### System Requirements
- **Hardware:** Apple Silicon Mac (M1/M2/M3/M4) or Intel Core i5 with 8 GB+ RAM.
- **Python:** Python 3.9, 3.10, 3.11, or 3.12.
- **Operating System:** macOS 12+ (tested on macOS Sequoia / Darwin).

### Repository & Virtual Environment Setup

Open Terminal on your Mac and navigate to the project directory:

```bash
cd /Users/uday/Documents/uday_projects/opendeal
```

Verify your active Python environment or create an isolated one:

```bash
# If using the existing project virtual environment:
source .venv/bin/activate

# Verify OpenDeal is installed in editable mode:
pip install -e .
```

---

## 2. Launching the Interactive Demo

Launch the self-contained interactive demo with a single command:

```bash
opendeal demo
```

You will see the startup confirmation banner:

```text
================================================================================
🚀 OPENDEAL INTERACTIVE DEMO (Institutional Financial Intelligence)
================================================================================
URL:              http://127.0.0.1:8000
Demo Database:    /tmp/opendeal_demo.db
Mode:             Interactive Public Demo (Synthetic Dataset)
Valuation Math:   100% Deterministic Audited Python Engine
Safety Status:    Live Ingestion & Privileged Workflows Gated
================================================================================

Press Ctrl+C to stop the server.
```

### Alternative Ports or Remote Binding
- **Custom Port:** If port 8000 is occupied:
  ```bash
  opendeal demo --port 8080
  ```
- **Allowing LAN Access (Optional for Mobile/Tablet Testing):**
  ```bash
  opendeal demo --host 0.0.0.0 --port 8000 --allow-remote
  ```

---

## 3. Confirming Readiness (Health Check)

In a separate terminal tab or web browser, verify the health status:

```bash
curl -s http://127.0.0.1:8000/healthz | jq .
```

Expected JSON response:
```json
{
  "status": "healthy",
  "version": "0.11.0rc1",
  "mode": "demo"
}
```

Now open your browser to:
**[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 4. Resetting Demo Data

The demo environment is completely isolated from your real personal data. Real user data is stored at `~/Library/Application Support/OpenDeal/opendeal.db` and is **never** touched by demo mode.

To completely reset the demo database to a pristine state:

```bash
# 1. Stop the demo server (Ctrl+C in terminal)
# 2. Remove the temporary demo database file:
rm -f /tmp/opendeal_demo.db /tmp/opendeal_demo.db-wal /tmp/opendeal_demo.db-shm

# 3. Relaunch: the seeder will auto-populate fresh synthetic records instantly:
opendeal demo
```

---

## 5. Troubleshooting Common Issues

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| `Error: Port 8000 is already in use` | Another server (e.g. earlier uvicorn) is holding port 8000. | Run `lsof -i :8000` to find PID, kill it with `kill -9 <PID>`, or start on an alternate port: `opendeal demo --port 8081`. |
| `Security Policy Rejection: Binding to non-loopback interface` | Attempting to bind `0.0.0.0` or a public IP without authorization. | Add `--allow-remote` to explicitly authorize external network exposure. |
| `ModuleNotFoundError: No module named 'opendeal'` | Running outside the virtual environment. | Run `source .venv/bin/activate` or prefix commands with `PYTHONPATH=src`. |
| `Host header rejected by local security policy (403)` | Accessing from an unauthorized domain name. | Local access works on `localhost` and `127.0.0.1`. For custom domains, set `OPENDEAL_ALLOWED_HOSTS="myhost.local,localhost"`. |

---

## 6. Verification Checklist Before an Interview

- [ ] Run `opendeal demo` and confirm terminal banner prints without errors.
- [ ] Open `http://127.0.0.1:8000` in Safari or Chrome.
- [ ] Confirm the purple **DEMO MODE** banner appears at the top.
- [ ] Navigate to **Companies** -> Click `DEMO-NEXUS` -> Confirm 2024 Revenue ($152.5M) links to SHA-256 evidence.
- [ ] Navigate to **Valuation** -> Click **DCF** -> Click **Calculate Valuation** -> Confirm Enterprise Value ($445.69M) computes instantly.
- [ ] Navigate to **Discovery** -> View screened targets -> Confirm Meridian Logistics has an explainable -20% penalty.
