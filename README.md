# Project Review Demo

<p align="right"><a href="README_zh.md">中文</a></p>

A project and report review demo with separate user and administrator workspaces. This is a source snapshot prepared on 2026-10-20. Existing business documents, databases, session data, model credentials and generated review results are excluded.

## Features

- User workspace: tasks, project groups, individual projects, reports, search, dates and pagination.
- Reports and attachments are grouped automatically. Additional attachments can be assigned to an existing report.
- Review dialogue supports selecting and searching reports.
- Administrator workspace: project oversight, work records, rule management, model settings and accounts.
- Account switching opens the login page. The initial demo accounts are `user1` / `12345678` (user) and `manner` / `00000000` (administrator).

## Start locally

Use Python 3.12 or 3.13. An existing Conda Python installation can create the local environment. Run from the repository root in PowerShell:

```powershell
python -m venv .runtime
.\.runtime\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
.\start-demo.ps1
```

Open the [user workspace](http://127.0.0.1:8080/index.html) or [administrator workspace](http://127.0.0.1:8080/admin.html). The API uses port 8000. The startup script keeps existing listeners on these ports; stop an older instance before switching source versions.

For container startup, use `docker compose up --build -d`. Runtime data is stored in the `review-data` volume. Local startup stores data under `backend/data/`.

## Model configuration and limitations

The sample configuration starts with external model calls disabled. Enter a valid API key, endpoint and model name in your local `.env`, then enable `LLM_ENABLED` and `LLM_RETRIEVAL_ENABLED` and restart the API. Set `OCR_MODEL` only when using a compatible image model. Model settings are also available in the administrator workspace. Keep `.env` private.

Legacy Office document conversion requires LibreOffice. PDF/image extraction may require the external OCR and PDF tools installed by the [backend container](backend/Dockerfile). This demo does not implement model retraining. Production deployment and replacement of the known demo passwords require a separate deployment decision.

## Source map

| Area | Entry points |
| --- | --- |
| User and administrator pages | [index.html](index.html), [admin.html](admin.html) |
| Workspace interaction | [user-workspace.js](user-workspace.js), [admin-workspace.js](admin-workspace.js), [account-switcher.js](account-switcher.js) |
| Report and attachment workflow | [app.js](app.js), [report-workflows.js](report-workflows.js) |
| API, accounts and data | [backend/main.py](backend/main.py), [backend/accounts.py](backend/accounts.py), [backend/workbench.py](backend/workbench.py) |
| Rules and knowledge tables | [csv/](csv/) |
| Startup and configuration | [start-demo.ps1](start-demo.ps1), [.env.example](.env.example), [docker-compose.yml](docker-compose.yml) |
| Focused backend checks | [test_batch_review.py](backend/test_batch_review.py), [test_document_processing.py](backend/test_document_processing.py), [test_visual_assets.py](backend/test_visual_assets.py) |
| Current browser checks | [check_user_workspace_ui.py](scripts/check_user_workspace_ui.py), [check_admin_workspace.py](scripts/check_admin_workspace.py), [check_login_routes.py](scripts/check_login_routes.py) |

## Validation

From the repository root, install the development requirements and run the focused backend checks:

```powershell
.\.runtime\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
Push-Location backend
..\.runtime\Scripts\python.exe -m unittest test_batch_review test_document_processing test_visual_assets
Pop-Location
.\.runtime\Scripts\python.exe -m playwright install chromium
.\.runtime\Scripts\python.exe scripts/check_user_workspace_ui.py
.\.runtime\Scripts\python.exe scripts/check_admin_workspace.py
.\.runtime\Scripts\python.exe scripts/check_login_routes.py
```

Run browser checks one at a time: they use ports 18000 and 18080, temporary test databases and simulated model responses. Generated check output goes to `artifacts/` and is ignored. Some historical checks depend on excluded business examples or baseline outputs; the commands above identify the current self-contained checks. Live external-model quality is outside these checks.
