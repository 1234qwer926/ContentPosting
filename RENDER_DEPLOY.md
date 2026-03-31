# Deploy to Render.com Guide

## Step 1: Prepare Your Code

### 1.1 Create `render.yaml` (Optional but recommended)
Create a `render.yaml` file in your project root:

```yaml
services:
  - type: web
    name: content-posting-api
    runtime: python
    buildCommand: "pip install -r requirements.txt"
    startCommand: "uvicorn main:app --host 0.0.0.0 --port $PORT"
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.0
```

### 1.2 Ensure `requirements.txt` is complete
Your `requirements.txt` should include:
```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-telegram-bot>=20.7
python-multipart>=0.0.6
python-dotenv>=1.0.0
discord.py>=2.3.0
aiohttp>=3.9.0
playwright>=1.40.0
```

### 1.3 Create a `.gitignore` file
```
.env
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.so
*.egg
*.egg-info/
dist/
build/
.pytest_cache/
.coverage
htmlcov/
.tox/
.venv/
venv/
ENV/
.workflow_state.json
```

## Step 2: Push to GitHub

```bash
# Initialize git (if not already done)
git init

# Add all files
git add .

# Commit
git commit -m "Initial commit for Render deployment"

# Add remote (replace with your repo URL)
git remote add origin https://github.com/yourusername/content-posting.git

# Push
git push -u origin main
```

## Step 3: Deploy on Render

### 3.1 Sign up / Log in
1. Go to https://render.com
2. Sign up with GitHub (recommended) or email

### 3.2 Create New Web Service
1. Click "New +" button
2. Select "Web Service"
3. Connect your GitHub repository
4. Select the repository: `content-posting`

### 3.3 Configure Service
Fill in the following:

| Field | Value |
|-------|-------|
| **Name** | `content-posting-api` (or your preferred name) |
| **Environment** | `Python 3` |
| **Region** | Select closest to you (e.g., Singapore for India) |
| **Branch** | `main` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| **Plan** | Free (or paid if needed) |

### 3.4 Add Environment Variables
Click "Advanced" and add these environment variables:

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHANNEL=@your_channel_name
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
PERPLEXITY_API_KEY=your_perplexity_key
TWITTER_HANDLES=BRICSinfo
```

**Important:** 
- Never commit `.env` file to GitHub
- Add all secrets in Render dashboard
- For local testing, use `.env` file

### 3.5 Deploy
Click "Create Web Service"

Render will:
1. Build your application
2. Install dependencies
3. Start the server
4. Provide you with a URL (e.g., `https://content-posting-api.onrender.com`)

## Step 4: Verify Deployment

### 4.1 Check Health Endpoint
Visit: `https://your-app-name.onrender.com/health`

Should return:
```json
{"status": "healthy", "service": "fastapi-backend"}
```

### 4.2 Check Logs
In Render dashboard:
1. Click on your service
2. Go to "Logs" tab
3. You should see:
   ```
   [Startup] 2026-03-31 05:00:00 - Keep-alive task started (5min interval)
   [Keep-Alive] 2026-03-31 05:05:00 - Service is active
   ```

## Step 5: Workflow Runs Automatically (No Extra Setup!)

**Great news:** The workflow now runs automatically inside the FastAPI app as a background task!

### How It Works
When you deploy `main.py` to Render:
1. The FastAPI server starts
2. A background task starts that runs the workflow every 30 minutes
3. The keep-alive task also runs to prevent the service from sleeping
4. Everything runs in one service - no separate workers or cron jobs needed!

### What Gets Executed Automatically
```
Every 30 minutes:
├── Fetch Twitter posts from @BRICSinfo
├── Send to Perplexity for analysis
├── Check for verified evidence
├── Scrape images from citations
└── Post to Discord and Telegram
```

### Logs You'll See
```
[Startup] 2026-03-31 05:00:00 - Keep-alive task started (5min interval)
[Startup] 2026-03-31 05:00:00 - Workflow task started (30min interval)
[Workflow-Task] Running workflow cycle...
[Workflow] Starting cycle at 2026-03-31 05:00:00 (local time)
[Twitter] Fetching posts from BRICSinfo...
...
[Workflow-Task] Cycle complete. Waiting for next 30-minute window...
[Keep-Alive] 2026-03-31 05:05:00 - Service is active
```

### No Additional Configuration Needed
The workflow uses the same environment variables as the API:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL`
- `DISCORD_WEBHOOK_URL`
- `PERPLEXITY_API_KEY`
- `TWITTER_HANDLES`

Just deploy and it works!

## Troubleshooting

### Issue: Build fails
**Solution:** Check `requirements.txt` has all dependencies

### Issue: Environment variables not working
**Solution:** 
- Ensure variables are set in Render dashboard
- Restart the service after adding variables

### Issue: Service goes to sleep (Free tier)
**Solution:** 
- The keep-alive task should prevent this
- Or use a ping service like UptimeRobot to ping your health endpoint every 5 minutes

### Issue: Playwright not working
**Solution:** Add to build command:
```bash
pip install -r requirements.txt && playwright install chromium
```

## Useful URLs After Deployment

| Endpoint | URL |
|----------|-----|
| Health Check | `https://your-app.onrender.com/health` |
| API Docs | `https://your-app.onrender.com/docs` |
| Send to Telegram | `POST /send-to-telegram` |
| Send to Discord | `POST /send-to-discord` |
| Fetch Twitter | `GET /fetch-twitter-posts` |
| Ask Perplexity | `POST /ask-perplexity` |
| Scrape Images | `POST /scrape-citation-images` |

## Free Tier Limitations

- **Web Services:** Spin down after 15 minutes of inactivity (keep-alive prevents this)
- **Bandwidth:** 100GB/month
- **Build time:** 15 minutes max
- **Disk:** Ephemeral (resets on deploy)

For production use, consider upgrading to paid tier.