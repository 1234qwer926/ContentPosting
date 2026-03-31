# Security Audit Report

## Overview
This document identifies security vulnerabilities and provides recommendations for the Content Posting application.

## Critical Issues

### 1. 🔴 Hardcoded Credentials in .env File
**Risk:** HIGH
**Location:** `.env` file
**Issue:** Environment variables stored in plain text file
**Impact:** If committed to GitHub, credentials are exposed publicly

**Fix:**
```bash
# Add .env to .gitignore (already done ✓)
# Never commit .env to version control
# Use Render dashboard for production secrets
```

### 2. 🔴 No Input Validation on API Endpoints
**Risk:** HIGH
**Location:** `main.py` - All POST endpoints
**Issue:** User input is not properly sanitized

**Example vulnerabilities:**
```python
# In send_to_telegram():
text: str = Form(...)  # No max length, no content validation
image: UploadFile = File(...)  # No file type/size validation
```

**Attack scenarios:**
- Upload malicious files (EXE, PHP, etc.)
- Send extremely long text (DoS)
- Inject HTML/JavaScript in messages

**Fix:**
```python
from fastapi import Form, File, UploadFile, HTTPException
import magic  # python-magic library

# Add validation
def validate_image(file: UploadFile):
    # Check file size (max 10MB)
    contents = file.file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    
    # Check file type
    file_type = magic.from_buffer(contents, mime=True)
    if file_type not in ['image/jpeg', 'image/png', 'image/gif']:
        raise HTTPException(415, "Invalid file type")
    
    file.file.seek(0)
    return file

def validate_text(text: str):
    if len(text) > 4000:  # Telegram limit
        raise HTTPException(400, "Text too long")
    # Sanitize HTML
    import html
    return html.escape(text)
```

### 3. 🔴 No Rate Limiting
**Risk:** MEDIUM-HIGH
**Location:** All API endpoints
**Issue:** No protection against brute force or spam

**Impact:**
- API abuse
- Telegram/Discord rate limits hit
- Perplexity API quota exhausted
- Service degradation

**Fix:**
```python
from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

app = FastAPI()
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/send-to-telegram")
@limiter.limit("10/minute")  # Max 10 requests per minute per IP
async def send_to_telegram(request: Request, ...):
    pass
```

### 4. 🔴 No Authentication on API Endpoints
**Risk:** HIGH
**Location:** All endpoints in `main.py`
**Issue:** Anyone can access the API if they know the URL

**Impact:**
- Unauthorized posting to your channels
- API abuse
- Data exposure

**Fix:**
```python
from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != os.getenv("API_SECRET_KEY"):
        raise HTTPException(403, "Invalid API key")
    return api_key

@app.post("/send-to-telegram", dependencies=[Security(verify_api_key)])
async def send_to_telegram(...):
    pass
```

### 5. 🔴 Sensitive Data in Logs
**Risk:** MEDIUM
**Location:** `content_workflow.py`, `main.py`
**Issue:** API keys, tokens, and credentials may be logged

**Example:**
```python
# BAD - might log sensitive data
print(f"[Perplexity] API error: {response_data}")  # Could contain API key

# BAD - logging full URLs with tokens
print(f"[Discord] Webhook: {DISCORD_WEBHOOK_URL}")  # Exposes webhook URL
```

**Fix:**
```python
import logging

# Mask sensitive data
def mask_sensitive(data: str) -> str:
    if not data:
        return data
    return data[:10] + "..." + data[-4:] if len(data) > 14 else "***"

# Use proper logging levels
logger = logging.getLogger(__name__)
logger.info(f"Discord webhook: {mask_sensitive(DISCORD_WEBHOOK_URL)}")
```

### 6. 🔴 No HTTPS Enforcement
**Risk:** MEDIUM
**Location:** `main.py`, `content_workflow.py`
**Issue:** URLs constructed without HTTPS validation

**Example:**
```python
# In scrape_images_from_url:
if src.startswith('//'):
    src = f"https:{src}"  # Good
elif src.startswith('/'):
    # ... could be HTTP
```

**Fix:**
```python
# Always use HTTPS
if not src.startswith('https://'):
    src = src.replace('http://', 'https://', 1)
    if not src.startswith('https://'):
        src = 'https://' + src
```

### 7. 🟡 Path Traversal Risk
**Risk:** MEDIUM
**Location:** `content_workflow.py` - State file handling
**Issue:** File operations without path validation

**Example:**
```python
STATE_FILE = ".workflow_state.json"
# Could be manipulated if variable is controlled externally
```

**Fix:**
```python
import os
from pathlib import Path

# Use absolute path in safe directory
STATE_FILE = Path(os.path.dirname(os.path.abspath(__file__))) / ".workflow_state.json"
```

### 8. 🟡 No Timeout on External Requests
**Risk:** MEDIUM
**Location:** `main.py` - Discord webhook, Perplexity API
**Issue:** Some requests may hang indefinitely

**Example:**
```python
# In _send_to_discord:
async with session.post(self.discord_webhook, json=payload) as response:
    # No timeout specified!
```

**Fix:**
```python
# Already has timeout in most places, but verify all:
aiohttp.ClientTimeout(total=30)  # 30 second timeout
```

### 9. 🟡 Information Disclosure
**Risk:** LOW-MEDIUM
**Location:** Error messages
**Issue:** Detailed error messages expose internal details

**Example:**
```python
except Exception as e:
    raise HTTPException(500, detail=f"Internal server error: {str(e)}")
    # Exposes internal error details to client
```

**Fix:**
```python
except Exception as e:
    logger.error(f"Internal error: {e}")  # Log full error
    raise HTTPException(500, detail="Internal server error")
    # Generic message to client
```

### 10. 🟡 Dependency Vulnerabilities
**Risk:** LOW-MEDIUM
**Issue:** Dependencies may have known vulnerabilities

**Fix:**
```bash
# Regularly check for vulnerabilities
pip install safety
safety check

# Or use pip-audit
pip install pip-audit
pip-audit
```

## Security Recommendations Summary

### Immediate Actions (Critical)
1. ✅ Ensure `.env` is in `.gitignore` (already done)
2. 🔲 Add API key authentication to all endpoints
3. 🔲 Add input validation (file types, sizes, text length)
4. 🔲 Add rate limiting
5. 🔲 Sanitize logs to remove sensitive data

### Short-term (High Priority)
6. 🔲 Enforce HTTPS on all URLs
7. 🔲 Add request timeouts
8. 🔲 Implement proper error handling
9. 🔲 Use secure file paths

### Long-term (Medium Priority)
10. 🔲 Regular dependency audits
11. 🔲 Add security headers
12. 🔲 Implement request logging/monitoring
13. 🔲 Add CORS policy

## Security Headers to Add

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Add security headers
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
```

## Monitoring & Alerting

Set up alerts for:
- Multiple failed authentication attempts
- Unusual API request patterns
- High error rates
- Perplexity API quota nearing limit

## Conclusion

The application has several security gaps that should be addressed before handling sensitive production data. The most critical are:
1. No authentication
2. No input validation
3. No rate limiting

These should be fixed immediately to prevent abuse.