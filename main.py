from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from datetime import datetime, timedelta
import os
import aiohttp
import asyncio
import json
import re

# Load environment variables from .env file
load_dotenv()

app = FastAPI(
    title="FastAPI Backend",
    description="Minimal FastAPI backend with health check, Telegram, Discord, and Twitter integration",
    version="1.0.0"
)

# Telegram Bot Configuration from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")

# Discord Webhook Configuration
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Twitter Configuration
TWITTER_HANDLES = os.getenv("TWITTER_HANDLES", "BRICSinfo")
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME")
TWITTER_PASSWORD = os.getenv("TWITTER_PASSWORD")

# Perplexity AI Configuration
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"

# Validate environment variables
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL:
    raise ValueError(
        "Missing required environment variables. "
        "Please ensure TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL are set in your .env file"
    )

# Initialize Telegram Bot
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)


# Import workflow components
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# Keep-alive task for Render (prevents service from going inactive)
async def keep_alive_task():
    """Logs every 5 minutes to keep Render service active"""
    while True:
        await asyncio.sleep(300)  # 5 minutes
        print(f"[Keep-Alive] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Service is active")


# Workflow task - runs the entire content workflow every 30 minutes
async def workflow_task():
    """Run content workflow every 30 minutes"""
    # Import here to avoid circular imports
    from content_workflow import ContentWorkflow
    
    print(f"[Workflow-Task] Starting workflow background task")
    
    # Wait a bit for app to fully start
    await asyncio.sleep(10)
    
    workflow = ContentWorkflow()
    
    while True:
        try:
            print(f"[Workflow-Task] Running workflow cycle...")
            await workflow.run_cycle()
            print(f"[Workflow-Task] Cycle complete. Waiting for next 30-minute window...")
        except Exception as e:
            print(f"[Workflow-Task] Error in workflow cycle: {e}")
        
        # Calculate sleep until next 30-minute boundary
        now = datetime.now()
        if now.minute < 30:
            next_run = now.replace(minute=30, second=0, microsecond=0)
        else:
            next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        
        sleep_seconds = (next_run - now).total_seconds()
        print(f"[Workflow-Task] Next run at {next_run.strftime('%H:%M:%S')} (sleeping {int(sleep_seconds/60)}m)")
        await asyncio.sleep(sleep_seconds)


@app.on_event("startup")
async def startup_event():
    """Start background tasks when app starts"""
    # Start keep-alive task
    asyncio.create_task(keep_alive_task())
    print(f"[Startup] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Keep-alive task started (5min interval)")
    
    # Start workflow task (if environment variables are configured)
    if PERPLEXITY_API_KEY and DISCORD_WEBHOOK_URL:
        asyncio.create_task(workflow_task())
        print(f"[Startup] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Workflow task started (30min interval)")
    else:
        print(f"[Startup] Workflow task not started - missing environment variables")


async def scrape_twitter_user(handle: str, since_time: datetime, until_time: datetime):
    """
    Scrape tweets from a specific Twitter user within a time window.
    Uses direct Twitter/X access with stealth Playwright.
    """
    tweets = []
    errors = []
    debug_info = []
    
    async with async_playwright() as p:
        # Launch with stealth settings
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox'
            ]
        )
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='en-US',
            timezone_id='America/New_York'
        )
        
        # Add stealth script
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            window.chrome = { runtime: {} };
        """)
        
        page = await context.new_page()
        
        try:
            # Try direct Twitter/X access
            url = f"https://x.com/{handle}"
            debug_info.append(f"Navigating to: {url}")
            
            response = await page.goto(url, wait_until='networkidle', timeout=30000)
            debug_info.append(f"Page loaded with status: {response.status if response else 'unknown'}")
            
            # Wait for content to load
            await asyncio.sleep(8)
            
            # Get page title for debugging
            title = await page.title()
            debug_info.append(f"Page title: {title}")
            
            # Try to find tweets with multiple selectors
            tweet_selectors = [
                'article[data-testid="tweet"]',
                '[data-testid="tweet"]',
                'article',
                'div[data-testid="cellInnerDiv"]'
            ]
            
            tweet_elements = []
            for selector in tweet_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    debug_info.append(f"Selector '{selector}': found {len(elements)} elements")
                    if elements and len(elements) > 0:
                        tweet_elements = elements
                        break
                except Exception as e:
                    debug_info.append(f"Selector '{selector}' error: {str(e)[:50]}")
                    continue
            
            if not tweet_elements:
                # Try scrolling to trigger lazy loading
                debug_info.append("No tweets found, trying scroll...")
                await page.evaluate('window.scrollBy(0, 800)')
                await asyncio.sleep(5)
                
                # Try again after scroll
                for selector in tweet_selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        debug_info.append(f"After scroll - Selector '{selector}': found {len(elements)} elements")
                        if elements and len(elements) > 0:
                            tweet_elements = elements
                            break
                    except:
                        continue
            
            # Extract data from found tweets
            debug_info.append(f"Processing {len(tweet_elements)} tweet elements...")
            for elem in tweet_elements[:20]:
                try:
                    # Get tweet text
                    text_selectors = [
                        '[data-testid="tweetText"]',
                        'div[lang]',
                        '.css-901oao',
                        '[dir="auto"]'
                    ]
                    text = ""
                    for sel in text_selectors:
                        text_el = await elem.query_selector(sel)
                        if text_el:
                            text = await text_el.inner_text()
                            if len(text) > 5:
                                break
                    
                    # Get timestamp
                    time_el = await elem.query_selector('time')
                    time_str = None
                    if time_el:
                        time_str = await time_el.get_attribute('datetime')
                    
                    # Get link
                    link_el = await elem.query_selector('a[href*="/status/"]')
                    tweet_url = None
                    if link_el:
                        href = await link_el.get_attribute('href')
                        if href:
                            tweet_url = f"https://x.com{href}" if href.startswith('/') else href
                    
                    debug_info.append(f"Found tweet: text_length={len(text)}, time={time_str is not None}")
                    
                    # Check time window
                    if time_str:
                        try:
                            tweet_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                            tweet_time_naive = tweet_time.replace(tzinfo=None)
                            
                            if since_time <= tweet_time_naive <= until_time and text:
                                tweets.append({
                                    'handle': handle,
                                    'text': text[:500],
                                    'timestamp': tweet_time.isoformat(),
                                    'url': tweet_url or f"https://x.com/{handle}",
                                    'source': 'x_scraper'
                                })
                                debug_info.append(f"Added tweet from {tweet_time_naive}")
                        except Exception as te:
                            debug_info.append(f"Time parse error: {str(te)[:50]}")
                            # If time parsing fails, include anyway
                            if text:
                                tweets.append({
                                    'handle': handle,
                                    'text': text[:500],
                                    'timestamp': datetime.now().isoformat(),
                                    'url': tweet_url or f"https://x.com/{handle}",
                                    'source': 'x_scraper'
                                })
                    elif text:
                        # No timestamp but has text - include with current time
                        tweets.append({
                            'handle': handle,
                            'text': text[:500],
                            'timestamp': datetime.now().isoformat(),
                            'url': tweet_url or f"https://x.com/{handle}",
                            'source': 'x_scraper'
                        })
                        debug_info.append("Added tweet without timestamp")
                except Exception as e:
                    debug_info.append(f"Tweet extraction error: {str(e)[:50]}")
                    continue
                    
        except Exception as e:
            errors.append(f"X/Twitter scrape failed: {str(e)[:100]}")
            debug_info.append(f"Fatal error: {str(e)[:100]}")
        finally:
            await browser.close()
    
    return tweets, errors, debug_info


async def extract_tweet_stats(tweet_elem):
    """Extract engagement stats from a tweet element."""
    stats = {}
    
    try:
        # Replies
        reply_elem = await tweet_elem.query_selector('button[data-testid="reply"]')
        if reply_elem:
            reply_text = await reply_elem.get_attribute('aria-label')
            if reply_text:
                stats['replies'] = extract_number(reply_text)
        
        # Retweets
        retweet_elem = await tweet_elem.query_selector('button[data-testid="retweet"]')
        if retweet_elem:
            retweet_text = await retweet_elem.get_attribute('aria-label')
            if retweet_text:
                stats['retweets'] = extract_number(retweet_text)
        
        # Likes
        like_elem = await tweet_elem.query_selector('button[data-testid="like"]')
        if like_elem:
            like_text = await like_elem.get_attribute('aria-label')
            if like_text:
                stats['likes'] = extract_number(like_text)
    except:
        pass
    
    return stats


def extract_number(text):
    """Extract number from text like '5 replies' or '1.2K likes'."""
    if not text:
        return 0
    
    # Remove non-numeric characters except K, M, .
    match = re.search(r'(\d+\.?\d*)([KM]?)', text)
    if match:
        num = float(match.group(1))
        suffix = match.group(2)
        
        if suffix == 'K':
            return int(num * 1000)
        elif suffix == 'M':
            return int(num * 1000000)
        else:
            return int(num)
    
    return 0


@app.get("/")
async def root():
    return {"message": "Welcome to FastAPI Backend"}


@app.get("/health")
async def health_check():
    return JSONResponse(
        status_code=200,
        content={"status": "healthy", "service": "fastapi-backend"}
    )


@app.post("/send-to-telegram")
async def send_to_telegram(
    text: str = Form(..., description="Text message to send to the channel"),
    image: UploadFile = File(..., description="Image file to send along with the text")
):
    """
    Send a text message with an image to the Telegram channel.
    
    - **text**: The text content to send
    - **image**: The image file to attach (JPG, PNG, etc.)
    
    Returns success status and message details.
    """
    try:
        # Read the uploaded image
        image_content = await image.read()
        
        # Send photo with caption to Telegram channel
        message = await telegram_bot.send_photo(
            chat_id=TELEGRAM_CHANNEL,
            photo=image_content,
            caption=text,
            parse_mode="HTML"
        )
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "message": "Message sent successfully to Telegram channel",
                "channel": TELEGRAM_CHANNEL,
                "message_id": message.message_id,
                "text_preview": text[:100] + "..." if len(text) > 100 else text,
                "image_filename": image.filename
            }
        )
        
    except TelegramError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram API error: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@app.post("/send-to-discord")
async def send_to_discord(
    text: str = Form(..., description="Text message to send to Discord"),
    image: UploadFile = File(..., description="Image file to send along with the text")
):
    """
    Send a text message with an image to Discord via webhook.
    
    - **text**: The text content to send
    - **image**: The image file to attach (JPG, PNG, etc.)
    
    Returns success status and message details.
    
    **Setup Instructions:**
    1. In Discord, go to your server settings
    2. Click "Integrations" → "Webhooks" → "New Webhook"
    3. Select the channel and copy the webhook URL
    4. Set DISCORD_WEBHOOK_URL in your .env file
    """
    if not DISCORD_WEBHOOK_URL:
        raise HTTPException(
            status_code=500,
            detail="Discord webhook URL not configured. Please set DISCORD_WEBHOOK_URL in .env file"
        )
    
    try:
        # Read the uploaded image
        image_content = await image.read()
        
        # Prepare the multipart form data for Discord
        form_data = aiohttp.FormData()
        form_data.add_field("content", text)
        form_data.add_field(
            "file",
            image_content,
            filename=image.filename,
            content_type=image.content_type or "application/octet-stream"
        )
        
        # Send to Discord webhook
        async with aiohttp.ClientSession() as session:
            async with session.post(DISCORD_WEBHOOK_URL, data=form_data) as response:
                if response.status == 204 or response.status == 200:
                    return JSONResponse(
                        status_code=200,
                        content={
                            "success": True,
                            "message": "Message sent successfully to Discord",
                            "text_preview": text[:100] + "..." if len(text) > 100 else text,
                            "image_filename": image.filename
                        }
                    )
                else:
                    error_text = await response.text()
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Discord API error: {error_text}"
                    )
                    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@app.get("/fetch-twitter-posts")
async def fetch_twitter_posts(
    handles: str = Query(None, description="Comma-separated list of Twitter handles (without @). Defaults to TWITTER_HANDLES env var."),
    window_minutes: int = Query(30, description="Time window in minutes to fetch posts. Default is 30 minutes.")
):
    """
    Fetch recent posts from specified Twitter users within a time window.
    
    - **handles**: Comma-separated Twitter handles (e.g., "WatcherGuru,elonmusk"). Uses TWITTER_HANDLES env var if not provided.
    - **window_minutes**: Time window to look back from current time (default: 30 minutes)
    
    Returns posts from the last N minutes for each handle.
    
    **Example:** If called at 7:54 with window_minutes=30, fetches posts from 7:24 to 7:54
    """
    # Use provided handles or fall back to env var
    target_handles = handles if handles else TWITTER_HANDLES
    
    if not target_handles:
        raise HTTPException(
            status_code=400,
            detail="No Twitter handles specified. Provide 'handles' query param or set TWITTER_HANDLES in .env"
        )
    
    # Parse handles
    handle_list = [h.strip().replace("@", "") for h in target_handles.split(",")]
    
    # Calculate time window
    until_time = datetime.now()
    since_time = until_time - timedelta(minutes=window_minutes)
    
    results = {
        "query_info": {
            "handles": handle_list,
            "window_minutes": window_minutes,
            "since": since_time.isoformat(),
            "until": until_time.isoformat()
        },
        "posts": []
    }
    
    try:
        # Scrape each handle
        all_errors = []
        all_debug = []
        for handle in handle_list:
            tweets, errors, debug_info = await scrape_twitter_user(handle, since_time, until_time)
            results["posts"].extend(tweets)
            all_errors.extend(errors)
            all_debug.extend(debug_info)
        
        # Sort by timestamp (newest first)
        results["posts"].sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        if all_errors:
            results["errors"] = all_errors[:5]  # Limit errors shown
        if all_debug:
            results["debug"] = all_debug[:10]  # Limit debug info
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "total_posts": len(results["posts"]),
                "data": results
            }
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching Twitter posts: {str(e)}"
        )


@app.post("/ask-perplexity")
async def ask_perplexity(
    question: str = Form(..., description="The question to ask Perplexity AI"),
    model: str = Form("sonar", description="Perplexity model to use (default: sonar). Options: sonar, sonar-pro, sonar-reasoning")
):
    """
    Send a question to Perplexity AI and return the complete API response.
    
    - **question**: The question or prompt to send to Perplexity
    - **model**: The model to use (sonar, sonar-pro, sonar-reasoning). Default is "sonar".
    
    Returns the full Perplexity API response including citations and metadata.
    """
    if not PERPLEXITY_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Perplexity API key not configured. Please set PERPLEXITY_API_KEY in .env file"
        )
    
    # Validate model selection
    valid_models = ["sonar", "sonar-pro", "sonar-reasoning"]
    if model not in valid_models:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model '{model}'. Valid options: {', '.join(valid_models)}"
        )
    
    try:
        # Prepare the request payload
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Be precise and concise."
                },
                {
                    "role": "user",
                    "content": question
                }
            ]
        }
        
        # Prepare headers
        headers = {
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Send request to Perplexity API
        async with aiohttp.ClientSession() as session:
            async with session.post(
                PERPLEXITY_API_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                response_data = await response.json()
                
                if response.status == 200:
                    return JSONResponse(
                        status_code=200,
                        content={
                            "success": True,
                            "model": model,
                            "question": question,
                            "perplexity_response": response_data
                        }
                    )
                else:
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Perplexity API error: {response_data}"
                    )
                    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error calling Perplexity API: {str(e)}"
        )


async def scrape_images_from_url(url: str):
    """
    Scrape all image URLs from a given webpage.
    Uses Playwright to load the page and extract image sources.
    """
    images = []
    errors = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox'
            ]
        )
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = await context.new_page()
        
        try:
            # Navigate to the URL with longer timeout and less strict wait
            response = await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            
            if not response:
                errors.append("Failed to load page: no response")
                await browser.close()
                return images, errors
            
            if response.status >= 400:
                errors.append(f"Failed to load page: HTTP {response.status}")
                await browser.close()
                return images, errors
            
            # Wait for network to be mostly idle (some resources may still load)
            try:
                await page.wait_for_load_state('networkidle', timeout=15000)
            except:
                pass  # Continue even if networkidle times out
            
            # Wait a bit for dynamic content
            await asyncio.sleep(2)
            
            # Extract all image elements
            image_elements = await page.query_selector_all('img')
            
            for img in image_elements:
                try:
                    # Get src attribute
                    src = await img.get_attribute('src')
                    
                    # Get data-src (lazy loading)
                    data_src = await img.get_attribute('data-src')
                    
                    # Get srcset
                    srcset = await img.get_attribute('srcset')
                    
                    # Get alt text
                    alt = await img.get_attribute('alt') or ''
                    
                    # Process src
                    if src:
                        # Convert relative URLs to absolute
                        if src.startswith('//'):
                            src = f"https:{src}"
                        elif src.startswith('/'):
                            # Extract base URL
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            src = f"{base}{src}"
                        elif not src.startswith(('http://', 'https://', 'data:')):
                            # Relative path without leading slash
                            from urllib.parse import urljoin
                            src = urljoin(url, src)
                        
                        # Skip common non-content images
                        skip_patterns = [
                            'logo', 'icon', 'avatar', 'profile', 'button',
                            'spinner', 'loading', 'close', 'menu', 'arrow',
                            'facebook.com/tr', 'tracking', 'pixel', 'beacon',
                            'data:image/svg+xml', '1x1', 'blank'
                        ]
                        
                        src_lower = src.lower()
                        should_skip = any(pattern in src_lower for pattern in skip_patterns)
                        
                        if src.startswith(('http://', 'https://')) and not should_skip:
                            # Try to get dimensions
                            width = await img.get_attribute('width') or ''
                            height = await img.get_attribute('height') or ''
                            
                            # Evaluate natural dimensions if possible
                            try:
                                dimensions = await img.evaluate('el => ({ w: el.naturalWidth, h: el.naturalHeight })')
                                natural_width = dimensions.get('w', 0)
                                natural_height = dimensions.get('h', 0)
                            except:
                                natural_width = 0
                                natural_height = 0
                            
                            # Only include images that are reasonably sized (not tracking pixels)
                            if natural_width > 50 or natural_height > 50 or (not width and not height):
                                images.append({
                                    'url': src,
                                    'alt': alt,
                                    'width': natural_width or width,
                                    'height': natural_height or height,
                                    'source': 'src'
                                })
                    
                    # Process data-src for lazy-loaded images
                    if data_src and data_src not in [img['url'] for img in images]:
                        if data_src.startswith('//'):
                            data_src = f"https:{data_src}"
                        elif data_src.startswith('/'):
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            data_src = f"{base}{data_src}"
                        elif not data_src.startswith(('http://', 'https://', 'data:')):
                            from urllib.parse import urljoin
                            data_src = urljoin(url, data_src)
                        
                        if data_src.startswith(('http://', 'https://')):
                            images.append({
                                'url': data_src,
                                'alt': alt,
                                'width': None,
                                'height': None,
                                'source': 'data-src'
                            })
                    
                    # Process srcset for responsive images
                    if srcset:
                        # Parse srcset and get the largest image
                        srcset_entries = srcset.split(',')
                        largest_url = None
                        largest_width = 0
                        
                        for entry in srcset_entries:
                            parts = entry.strip().split(' ')
                            if len(parts) >= 1:
                                img_url = parts[0].strip()
                                width_desc = parts[1].strip() if len(parts) > 1 else ''
                                
                                # Parse width descriptor (e.g., "800w")
                                if width_desc.endswith('w'):
                                    try:
                                        w = int(width_desc[:-1])
                                        if w > largest_width:
                                            largest_width = w
                                            largest_url = img_url
                                    except:
                                        pass
                                elif not largest_url:
                                    largest_url = img_url
                        
                        if largest_url and largest_url not in [img['url'] for img in images]:
                            if largest_url.startswith('//'):
                                largest_url = f"https:{largest_url}"
                            elif largest_url.startswith('/'):
                                from urllib.parse import urlparse
                                parsed = urlparse(url)
                                base = f"{parsed.scheme}://{parsed.netloc}"
                                largest_url = f"{base}{largest_url}"
                            elif not largest_url.startswith(('http://', 'https://', 'data:')):
                                from urllib.parse import urljoin
                                largest_url = urljoin(url, largest_url)
                            
                            if largest_url.startswith(('http://', 'https://')):
                                images.append({
                                    'url': largest_url,
                                    'alt': alt,
                                    'width': largest_width,
                                    'height': None,
                                    'source': 'srcset'
                                })
                                
                except Exception as e:
                    errors.append(f"Image extraction error: {str(e)[:50]}")
                    continue
            
            # Also look for meta/OpenGraph images
            try:
                og_image = await page.query_selector('meta[property="og:image"]')
                if og_image:
                    og_src = await og_image.get_attribute('content')
                    if og_src and og_src not in [img['url'] for img in images]:
                        if og_src.startswith('//'):
                            og_src = f"https:{og_src}"
                        elif og_src.startswith('/'):
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            og_src = f"{base}{og_src}"
                        elif not og_src.startswith(('http://', 'https://', 'data:')):
                            from urllib.parse import urljoin
                            og_src = urljoin(url, og_src)
                        
                        if og_src.startswith(('http://', 'https://')):
                            images.append({
                                'url': og_src,
                                'alt': 'OpenGraph Image',
                                'width': None,
                                'height': None,
                                'source': 'og:image'
                            })
                
                # Twitter card image
                twitter_image = await page.query_selector('meta[name="twitter:image"], meta[property="twitter:image"]')
                if twitter_image:
                    twitter_src = await twitter_image.get_attribute('content')
                    if twitter_src and twitter_src not in [img['url'] for img in images]:
                        if twitter_src.startswith('//'):
                            twitter_src = f"https:{twitter_src}"
                        elif twitter_src.startswith('/'):
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            base = f"{parsed.scheme}://{parsed.netloc}"
                            twitter_src = f"{base}{twitter_src}"
                        elif not twitter_src.startswith(('http://', 'https://', 'data:')):
                            from urllib.parse import urljoin
                            twitter_src = urljoin(url, twitter_src)
                        
                        if twitter_src.startswith(('http://', 'https://')):
                            images.append({
                                'url': twitter_src,
                                'alt': 'Twitter Card Image',
                                'width': None,
                                'height': None,
                                'source': 'twitter:image'
                            })
            except Exception as e:
                errors.append(f"Meta image extraction error: {str(e)[:50]}")
            
        except Exception as e:
            errors.append(f"Page scrape failed: {str(e)[:100]}")
        finally:
            await browser.close()
    
    # Remove duplicates based on URL
    seen_urls = set()
    unique_images = []
    for img in images:
        if img['url'] not in seen_urls:
            seen_urls.add(img['url'])
            unique_images.append(img)
    
    return unique_images, errors


@app.post("/scrape-citation-images")
async def scrape_citation_images(
    citations: str = Form(..., description="Comma-separated list of citation URLs from Perplexity"),
    min_width: int = Form(100, description="Minimum image width to include (default: 100px)"),
    min_height: int = Form(100, description="Minimum image height to include (default: 100px)")
):
    """
    Scrape images from Perplexity citation URLs.
    
    - **citations**: Comma-separated list of URLs to scrape for images
    - **min_width**: Minimum width filter for images (default: 100)
    - **min_height**: Minimum height filter for images (default: 100)
    
    Returns a list of image URLs found on each citation page, with metadata.
    
    **Example:**
    citations: "https://example.com/article1, https://example.com/article2"
    """
    if not citations:
        raise HTTPException(
            status_code=400,
            detail="No citations provided. Please provide a comma-separated list of URLs."
        )
    
    # Parse citation URLs
    url_list = [url.strip() for url in citations.split(',') if url.strip()]
    
    if not url_list:
        raise HTTPException(
            status_code=400,
            detail="No valid URLs found in citations."
        )
    
    results = {
        "query_info": {
            "total_urls": len(url_list),
            "urls": url_list,
            "min_width": min_width,
            "min_height": min_height
        },
        "citations": []
    }
    
    try:
        # Scrape images from each URL
        for url in url_list:
            citation_result = {
                "url": url,
                "images": [],
                "errors": [],
                "total_found": 0
            }
            
            try:
                images, errors = await scrape_images_from_url(url)
                
                # Filter images by minimum dimensions
                filtered_images = []
                for img in images:
                    width = img.get('width') or 0
                    height = img.get('height') or 0
                    
                    # Convert to int if string
                    if isinstance(width, str):
                        try:
                            width = int(width)
                        except:
                            width = 0
                    if isinstance(height, str):
                        try:
                            height = int(height)
                        except:
                            height = 0
                    
                    # Apply filters (skip if dimensions known and too small)
                    if (width == 0 or width >= min_width) and (height == 0 or height >= min_height):
                        filtered_images.append(img)
                
                citation_result["images"] = filtered_images
                citation_result["total_found"] = len(filtered_images)
                citation_result["errors"] = errors[:3]  # Limit errors shown
                
            except Exception as e:
                citation_result["errors"].append(f"Failed to scrape: {str(e)[:100]}")
            
            results["citations"].append(citation_result)
        
        # Calculate totals
        total_images = sum(c["total_found"] for c in results["citations"])
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "total_citations": len(url_list),
                "total_images": total_images,
                "data": results
            }
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error scraping citation images: {str(e)}"
        )
