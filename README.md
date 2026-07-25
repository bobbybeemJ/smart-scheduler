# NxD Smart Scheduler

Voice-enabled scheduling chatbot backed by Google Calendar. Deterministic Python resolves all
dates/times; the LLM (Gemini Flash-Lite) only extracts structured intent from natural language.

> **Status: Phase 0 (scaffold + sanity checks) in progress.** Full setup steps and design
> rationale will be written in Phase 11 once the pipeline is complete. See
> `C:\Users\rajpu\.claude\plans\nxd-smart-scheduler-drifting-pie.md` for the full implementation plan.

## Quick start (dev, work in progress)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Then fill in `.env` and run the Phase 0 sanity checks under `scripts/sanity/`.
