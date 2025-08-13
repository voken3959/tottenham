# Spurs Bot (Minimal)
Automated X (Twitter) bot for **Tottenham Hotspur** — posts:
- Pre-match reminder (~60 mins before KO)
- Live: goals, halftime, full-time
- Latest BBC Spurs news

## Free Data Sources
- Fixtures + Live: SofaScore (unofficial JSON endpoints)
- News: BBC Spurs RSS

## Deploy (GitHub Actions)
1. **Rotate your X API keys** (never paste them publicly).
2. Create a new **private** GitHub repo and add these files at the repo root:
   - `spurs_bot.py`
   - `requirements.txt`
   - `.github/workflows/spurs.yml`
3. In **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `TWITTER_API_KEY`
   - `TWITTER_API_SECRET`
   - `TWITTER_ACCESS_TOKEN`
   - `TWITTER_ACCESS_SECRET`
4. Push the repo. The workflow runs every 5 minutes.
5. Check **Actions** tab logs.

## Notes
- The bot stores a small `state.json` to avoid duplicate posts.
- Be respectful of source sites’ terms; endpoints can change.
- Keep the repo private and **never** commit secrets.
