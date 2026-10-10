# Setup: Windows and Linux

Requires Python **3.11 or newer**. The build was tested with Python 3.12.3 on Linux; see `BUILD_STATUS.md` for exact package versions.

## Windows (PowerShell)

These commands call `.venv\Scripts\python.exe` directly, so you never need `Activate.ps1`, and PowerShell's script execution policy cannot block you.

```powershell
# 1. Open PowerShell in the extracted ShopDetective folder
cd $HOME\Desktop\ShopDetective

# 2. Check Python (install from python.org if missing; tick "Add python.exe to PATH")
py -3 --version

# 3. Create the virtual environment and install
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. Run the app (opens http://localhost:8501)
.venv\Scripts\python.exe -m streamlit run app.py
```

If `py` is not found, use `python` instead of `py -3`.

### Optional: enable Live AI

```powershell
Copy-Item .env.example .env
notepad .env        # set GEMINI_API_KEY=your-key  (optionally GEMINI_MODEL=...)
.venv\Scripts\python.exe -m streamlit run app.py
```

`.env` is git-ignored and excluded from the release ZIP. Never paste a key into source files or screenshots.

### Tests, lint, evaluation, packaging

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe scripts\evaluate.py
.venv\Scripts\python.exe scripts\package_release.py
```

Optional live smoke test (calls Gemini with the small synthetic review-case file only):

```powershell
$env:RUN_LIVE_TESTS = "1"; $env:GEMINI_API_KEY = "your-key"
.venv\Scripts\python.exe -m pytest tests\test_live.py -q -rs
Remove-Item Env:GEMINI_API_KEY; Remove-Item Env:RUN_LIVE_TESTS
```

## Linux

```bash
cd ~/Desktop/ShopDetective
python3 --version                       # 3.11+
python3 -m venv .venv                   # Debian/Ubuntu may need: sudo apt install python3-venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

Live AI: `cp .env.example .env` and set `GEMINI_API_KEY`.

Dev checks:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
.venv/bin/python scripts/evaluate.py
.venv/bin/python scripts/package_release.py
RUN_LIVE_TESTS=1 GEMINI_API_KEY=your-key .venv/bin/python -m pytest tests/test_live.py -q -rs
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Live AI needs a Gemini API key" | Set `GEMINI_API_KEY` in `.env` (local) or Streamlit secrets (cloud) and restart |
| "Gemini rejected the API key" | Check the key in Google AI Studio; it is never retried automatically |
| "configured Gemini model was not found" | Set `GEMINI_MODEL` to a model listed at https://ai.google.dev/gemini-api/docs/models |
| HTTP 429 / quota | Completed results are kept. Wait, then click **Retry pending** |
| "not UTF-8 text" on upload | In Excel use *Save As → CSV UTF-8 (Comma delimited)* |
| "uses ';' as the separator" | Re-export as comma-separated CSV |
| Port 8501 busy | `... -m streamlit run app.py --server.port 8502` |
