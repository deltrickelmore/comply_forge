# Deploying ComplyForge to Streamlit Community Cloud

The repo is **code-only** — reference data (800-53, baselines, CCIs, STIGs) is
downloaded at runtime via the Dashboard's "Initialize data" buttons, so nothing
large is committed.

## 1. Push to GitHub
```bash
cd ~/comply_forge
git add -A && git commit -m "ComplyForge"      # already initialized + committed locally
git remote add origin https://github.com/<you>/comply_forge.git
git branch -M main
git push -u origin main
```

## 2. Create the app on Streamlit Cloud
1. Go to https://share.streamlit.io → **New app**.
2. Pick your repo/branch, main file = `app.py`.
3. (Optional) **Advanced → Secrets** — to enable real LLM drafting instead of the
   deterministic fallback, add:
   ```
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   For CUI, do **not** use the public API — run on Claude on AWS Bedrock GovCloud
   (set `COMPLYFORGE_LLM_PROVIDER=bedrock` and AWS creds) on FedRAMP-authorized
   infrastructure, not Streamlit Community Cloud.
4. Deploy. Python deps come from `requirements.txt`.

## 3. First run
Open the app → **Dashboard → Initialize / update reference data → "Load all"**.
This downloads 800-53, the 800-53B baselines, and the DISA CCI list (~1 min).
Then load STIGs from the **STIG Library** page (paste a DISA STIG `.zip` URL or
upload one).

## Notes
- The SQLite DB lives at `COMPLYFORGE_DB` (defaults to `~/comply_forge.db` in the
  container). Community Cloud storage is **ephemeral** — data resets on restart;
  re-run "Load all". For persistence, point `COMPLYFORGE_DB` at a mounted volume or
  move to Postgres (multi-tenant step).
- **Do not process CUI on Streamlit Community Cloud.** It is not FedRAMP-authorized.
  Use it for demos/synthetic data; deploy to an authorized environment for real data.
