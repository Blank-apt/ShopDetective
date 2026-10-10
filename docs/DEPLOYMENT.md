# Deployment: Streamlit Community Cloud

**Status:** deployment-ready files are included. **No public deployment has been made** by this build, and there is no public URL yet. Deploying needs your GitHub and Streamlit accounts.

## Settings

| Setting | Value |
|---|---|
| Repository | your public GitHub repo containing this folder at its root |
| Branch | `main` |
| Main file path | `app.py` |
| Python version | 3.12 (choose under *Advanced settings*; 3.11+ supported) |
| Dependencies | `requirements.txt` (installed automatically) |
| Theme | `.streamlit/config.toml` |

## Secrets

In *App settings → Secrets*, paste:

```toml
GEMINI_API_KEY = "your-key"
GEMINI_MODEL = "gemini-3.5-flash"
```

`config.get_secret` reads environment variables first, then `st.secrets`. The key is used only on the server and is never sent to the browser, written to logs or included in exports. The **Sample demonstration** and **Manual review** modes work without any secret, so a public demo can run keyless.

## Model choice

The default `gemini-3.5-flash` was listed as a stable Flash model at https://ai.google.dev/gemini-api/docs/models when checked on 2026-10-06. Models change: if Gemini reports the model as unavailable, set `GEMINI_MODEL` to a current Flash model from that page. The app never switches model or provider silently. On 2026-10-06 `gemini-3.5-flash` returned HTTP 503 (high demand) while `gemini-3.5-flash-lite` worked. Flash-Lite is the model the live checks were run on.

## Steps

1. Create a GitHub repository and push this folder. `.gitignore` already excludes `.env`, `.streamlit/secrets.toml`, `.venv` and build output. Run `git status` to confirm that no secret file is staged.
2. Go to https://share.streamlit.io, choose **Create app**, select the repo, branch and `app.py`, and set the Python version.
3. Add the secrets above (optional for a keyless demo).
4. Deploy, then open the app, click **Load example store** → **Analyze**, and check that the hoodie numbers match the README.
5. Optional: switch to **Live AI** and click **Analyze** on the review-case example to smoke-test the key.

## Public-demo resource notes

- Community Cloud apps share limited CPU and memory and sleep after inactivity. Session data is in memory and is lost on restart or sleep, so tell judges to download reports.
- Upload limits: Streamlit's `server.maxUploadSize` is set to 10 MB and the app enforces 5 MB per CSV (`SD_MAX_FILE_MB`).
- Every Live AI click spends your Gemini quota. For a public link, consider leaving the key unset and demonstrating Live AI locally, or set a quota or budget alert in Google AI Studio.
- Uploaded data stays in the visitor's own session. There is no shared storage between visitors.

## Other hosts

Any host that can run `python -m streamlit run app.py --server.port $PORT --server.address 0.0.0.0` with `GEMINI_API_KEY` in the environment will work. No other services are required.
