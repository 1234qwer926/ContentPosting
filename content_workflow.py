#!/usr/bin/env python3
"""
Content Workflow Automation

This script runs every 30 minutes to:
1. Fetch Twitter posts from the last 30-minute window
2. Send posts to Perplexity for analysis
3. Skip posts where Perplexity returns "No verified evidence"
4. Scrape images from Perplexity citations
5. Format and send content to Discord and Telegram

Usage:
    python content_workflow.py
    # Or run with nohup for background execution:
    nohup python content_workflow.py > workflow.log 2>&1 &
"""

import os
import asyncio
import aiohttp
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError
from playwright.async_api import async_playwright

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
TWITTER_HANDLES = os.getenv("TWITTER_HANDLES", "BRICSinfo")
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME")
TWITTER_PASSWORD = os.getenv("TWITTER_PASSWORD")

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"



@dataclass
class TwitterPost:
    """Represents a Twitter post"""
    handle: str
    text: str
    timestamp: datetime
    url: str
    source: str


@dataclass
class PerplexityResult:
    """Represents Perplexity analysis result"""
    original_post: TwitterPost
    question: str
    response_content: str
    citations: List[str]
    has_verified_evidence: bool
    model: str


@dataclass
class ScrapedImage:
    """Represents a scraped image"""
    url: str
    alt: str
    width: Optional[int]
    height: Optional[int]
    source: str


@dataclass
class ProcessedContent:
    """Represents fully processed content ready for publishing"""
    original_post: TwitterPost
    perplexity_result: PerplexityResult
    images: List[ScrapedImage]
    formatted_text: str


class WorkflowState:
    """In-memory workflow state — no file I/O (safe for Render's ephemeral filesystem).
    Processed post IDs are kept in RAM; time windows are derived from datetime.now().
    """

    def __init__(self):
        # Keeps track of posts processed this session to avoid double-publishing
        self.processed_post_ids: set = set()

    def mark_post_processed(self, post_id: str):
        """Mark a post as processed."""
        self.processed_post_ids.add(post_id)
        # Cap memory usage — keep the most recent 500 IDs
        if len(self.processed_post_ids) > 1000:
            self.processed_post_ids = set(list(self.processed_post_ids)[-500:])

    def is_post_processed(self, post_id: str) -> bool:
        """Check if a post has already been processed this session."""
        return post_id in self.processed_post_ids


class TwitterScraper:
    """Scrapes Twitter posts using Playwright"""
    
    async def fetch_posts(self, handle: str, since_time: datetime, until_time: datetime) -> List[TwitterPost]:
        """Fetch posts from a Twitter user within a time window"""
        posts = []
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--disable-gpu',
                    '--window-size=1920,1080',
                ]
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                locale='en-US',
                timezone_id='America/New_York',
                permissions=['notifications'],
            )
            
            # Enhanced stealth script
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
                window.navigator.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            """)
            
            page = await context.new_page()
            
            try:
                url = f"https://x.com/{handle}"
                print(f"[Twitter] Fetching posts from {handle}...")
                print(f"[Twitter] Time window: {since_time.strftime('%H:%M')} to {until_time.strftime('%H:%M')}")
                
                response = await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                
                # Wait for initial content
                await asyncio.sleep(3)
                
                # Try to wait for tweets to appear
                try:
                    await page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
                except:
                    # Scroll to trigger lazy loading
                    await page.evaluate('window.scrollBy(0, 800)')
                    await asyncio.sleep(3)
                
                # Try multiple selectors for tweets
                tweet_selectors = [
                    'article[data-testid="tweet"]',
                    '[data-testid="tweet"]',
                    'article',
                    'div[data-testid="cellInnerDiv"]'
                ]
                
                tweet_elements = []
                for selector in tweet_selectors:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        tweet_elements = elements
                        break
                
                print(f"[Twitter] Found {len(tweet_elements)} tweet elements for @{handle}")
                
                for elem in tweet_elements[:20]:  # Process up to 20 tweets
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
                        
                        # Debug: Print what we found
                        if text and time_str:
                            print(f"[Twitter] Found tweet: '{text[:60]}...' at {time_str}")
                        
                        # Check time window
                        if time_str and text:
                            try:
                                # Parse UTC timestamp from Twitter and convert to local time
                                from datetime import timezone
                                tweet_time_utc = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                                tweet_time_local = tweet_time_utc.astimezone().replace(tzinfo=None)
                                
                                print(f"[Twitter] Tweet time: {tweet_time_local.strftime('%H:%M')} local (was {tweet_time_utc.strftime('%H:%M')} UTC)")
                                print(f"[Twitter] Window: {since_time.strftime('%H:%M')}-{until_time.strftime('%H:%M')} local")
                                
                                if since_time <= tweet_time_local <= until_time:
                                    print(f"[Twitter] ✓ Tweet IN time window!")
                                    posts.append(TwitterPost(
                                        handle=handle,
                                        text=text[:500],
                                        timestamp=tweet_time_local,  # Store as local time
                                        url=tweet_url or f"https://x.com/{handle}",
                                        source='x_scraper'
                                    ))
                                else:
                                    print(f"[Twitter] ✗ Tweet OUTSIDE time window")
                                    
                            except Exception as te:
                                print(f"[Twitter] Time parse error: {te}")
                                
                    except Exception as e:
                        print(f"[Twitter] Element processing error: {e}")
                        continue
                        
            except Exception as e:
                print(f"[Twitter] Error fetching from @{handle}: {e}")
            finally:
                await browser.close()
        
        return posts


class PerplexityAnalyzer:
    """Analyzes content using Perplexity AI"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    async def analyze(self, post: TwitterPost) -> Optional[PerplexityResult]:
        """Send post to Perplexity and get analysis"""
        if not self.api_key:
            print("[Perplexity] API key not configured")
            return None
        
        # Create question from post text
        question = f"Analyze this tweet and provide verified facts: \"{post.text}\""
        
        payload = {
            "model": "sonar",
            "messages": [
                {
                    "role": "system",
                    "content": "Be precise and concise. Only provide information that is verified by sources. If you cannot verify the claim, clearly state 'No verified evidence'."
                },
                {
                    "role": "user",
                    "content": question
                }
            ]
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    PERPLEXITY_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    data = await response.json()
                    
                    if response.status == 200:
                        content = data['choices'][0]['message']['content']
                        citations = data.get('citations', [])
                        
                        # Check if response has verified evidence
                        has_verified = self._has_verified_evidence(content, citations)
                        
                        return PerplexityResult(
                            original_post=post,
                            question=question,
                            response_content=content,
                            citations=citations,
                            has_verified_evidence=has_verified,
                            model="sonar"
                        )
                    else:
                        print(f"[Perplexity] API error: {data}")
                        return None
                        
        except Exception as e:
            print(f"[Perplexity] Error analyzing post: {e}")
            return None
    
    def _has_verified_evidence(self, content: str, citations: list) -> bool:
        """Check if Perplexity response has backing sources.
        
        Simple rule: if Perplexity cited at least 1 source, the content is
        considered verified. Perplexity only adds citations when it found
        real evidence — no citations means it couldn't back the claim.
        """
        return len(citations) > 0


class ImageScraper:
    """Scrapes images from URLs"""
    
    async def scrape_from_citations(self, citations: List[str], min_width: int = 200, min_height: int = 150) -> List[ScrapedImage]:
        """Scrape images from a list of citation URLs"""
        all_images = []
        
        print(f"[Images] Starting image scrape from {len(citations)} citations")
        print(f"[Images] Min size filter: {min_width}x{min_height}")
        
        # Try up to 3 citations to find images
        for i, citation_url in enumerate(citations[:3], 1):
            print(f"[Images] Trying citation {i}/3: {citation_url}")
            try:
                images = await self._scrape_url(citation_url, min_width, min_height)
                if images:
                    print(f"[Images] ✓ Found {len(images)} valid content images from citation {i}")
                    all_images.extend(images)
                    break  # Stop if we found good images
                else:
                    print(f"[Images] ✗ No valid images found from citation {i}")
            except Exception as e:
                print(f"[Images] ✗ Error scraping citation {i}: {e}")
                continue
        
        print(f"[Images] Total images collected: {len(all_images)}")
        return all_images
    
    async def _scrape_url(self, url: str, min_width: int, min_height: int) -> List[ScrapedImage]:
        """Scrape images from a single URL"""
        images = []
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            )
            
            page = await context.new_page()
            
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                
                if not response or response.status >= 400:
                    return images
                
                try:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                except:
                    pass
                
                await asyncio.sleep(2)
                
                # Extract images
                image_elements = await page.query_selector_all('img')
                print(f"[Images] Found {len(image_elements)} <img> elements on page")
                
                for img in image_elements:
                    try:
                        src = await img.get_attribute('src')
                        alt = await img.get_attribute('alt') or ''
                        
                        if not src:
                            continue
                        
                        # Convert to absolute URL
                        src = self._make_absolute(src, url)
                        
                        if not src.startswith(('http://', 'https://')):
                            print(f"[Images] Skipping non-HTTP URL: {src[:50]}...")
                            continue
                        
                        # Skip non-content images (expanded list)
                        skip_patterns = [
                            'logo', 'icon', 'avatar', 'button', 'spinner', 'loading',
                            'close', 'menu', 'arrow', 'tracking', 'pixel', '1x1', 'blank',
                            'badge', 'profile', 'userpic', 'thumbnail', 'thumb',
                            'play', 'pause', 'next', 'prev', 'back', 'forward',
                            'search', 'share', 'like', 'heart', 'star', 'bookmark',
                            'notification', 'bell', 'settings', 'gear', 'more', 'dots',
                            'hamburger', 'nav', 'header', 'footer', 'bg-', 'background',
                            'pattern', 'texture', 'gradient', 'svg', 'gif', 'emoji',
                            'sticker', 'banner-ad', 'advertisement', 'promo', 'social',
                            'facebook', 'twitter', 'instagram', 'linkedin', 'youtube',
                            'tiktok', 'snapchat', 'pinterest', 'reddit', 'whatsapp',
                            'favicon', 'apple-touch', 'safari-pinned', 'mstile'
                        ]
                        if any(p in src.lower() for p in skip_patterns):
                            print(f"[Images] Skipping (pattern match): {src[:60]}...")
                            continue
                        
                        # Get dimensions
                        try:
                            dims = await img.evaluate('el => ({ w: el.naturalWidth, h: el.naturalHeight })')
                            width, height = dims.get('w', 0), dims.get('h', 0)
                        except:
                            width, height = 0, 0
                        
                        # Skip very small images (likely icons/decorative)
                        if width > 0 and height > 0 and (width < 200 or height < 150):
                            print(f"[Images] Skipping (too small: {width}x{height}): {src[:60]}...")
                            continue
                        
                        # Skip SVG data URIs
                        if src.startswith('data:image/svg'):
                            print(f"[Images] Skipping (SVG data URI)")
                            continue
                        
                        # Filter by size - only include reasonably sized content images
                        if width >= min_width or height >= min_height or (width == 0 and height == 0):
                            print(f"[Images] ✓ ACCEPTED: {src[:60]}... ({width}x{height})")
                            images.append(ScrapedImage(
                                url=src,
                                alt=alt,
                                width=width if width > 0 else None,
                                height=height if height > 0 else None,
                                source='src'
                            ))
                        else:
                            print(f"[Images] Skipping (below min size {min_width}x{min_height}): {src[:60]}...")
                            
                    except Exception as e:
                        print(f"[Images] Error processing image element: {e}")
                        continue
                
                # Also check OpenGraph
                try:
                    og_img = await page.query_selector('meta[property="og:image"]')
                    if og_img:
                        og_src = await og_img.get_attribute('content')
                        if og_src:
                            og_src = self._make_absolute(og_src, url)
                            # Check if OG image is not already in list and is valid
                            if og_src not in [i.url for i in images]:
                                # Validate OG image URL
                                if og_src.startswith(('http://', 'https://')) and not any(p in og_src.lower() for p in ['logo', 'icon', 'favicon']):
                                    print(f"[Images] ✓ Adding OpenGraph image: {og_src[:60]}...")
                                    images.append(ScrapedImage(
                                        url=og_src,
                                        alt='OpenGraph Image',
                                        width=None,
                                        height=None,
                                        source='og:image'
                                    ))
                                else:
                                    print(f"[Images] Skipping invalid OG image: {og_src[:60]}...")
                except Exception as e:
                    print(f"[Images] Error checking OpenGraph: {e}")
                    
            except Exception as e:
                print(f"[Images] Error in _scrape_url: {e}")
            finally:
                await browser.close()
        
        # Remove duplicates
        seen = set()
        unique = []
        for img in images:
            if img.url not in seen:
                seen.add(img.url)
                unique.append(img)
        
        print(f"[Images] After deduplication: {len(unique)} unique images")
        return unique
    
    def _make_absolute(self, url: str, base_url: str) -> str:
        """Convert relative URL to absolute"""
        from urllib.parse import urlparse, urljoin
        
        if url.startswith('//'):
            return f"https:{url}"
        elif url.startswith('/'):
            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}{url}"
        elif not url.startswith(('http://', 'https://', 'data:')):
            return urljoin(base_url, url)
        return url


class ContentPublisher:
    """Publishes content to Discord and Telegram"""
    
    def __init__(self):
        self.telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
        self.discord_webhook = DISCORD_WEBHOOK_URL
    
    def format_content_discord(self, processed: ProcessedContent) -> str:
        """Format content for Discord"""
        post = processed.original_post
        result = processed.perplexity_result
        
        lines = [
            f"**📰 {post.handle}**",
            "",
            post.text,
            "",
            "**🔍 Analysis:**",
            result.response_content[:800] + "...",
            "",
            "**🔗 Sources:**"
        ]
        
        # Add citations (up to 3)
        for i, citation in enumerate(result.citations[:3], 1):
            lines.append(f"{i}. {citation}")
        
        lines.append("")
        lines.append(f"🕐 {post.timestamp.strftime('%Y-%m-%d %H:%M')}")
        
        return "\n".join(lines)
    
    def format_content_telegram(self, processed: ProcessedContent) -> str:
        """Format content for Telegram with specific styling"""
        post = processed.original_post
        result = processed.perplexity_result
        
        # Format for Telegram with bold headers and clean structure
        text = f"<b>📰 {post.handle}</b>\n\n"
        text += f"{post.text}\n\n"
        text += f"<b>🔍 Analysis:</b>\n"
        text += f"{result.response_content[:900]}...\n\n"
        text += f"<b>🔗 Sources:</b>\n"
        
        # Add citations (up to 3)
        for i, citation in enumerate(result.citations[:3], 1):
            text += f"{i}. {citation}\n"
        
        return text
    
    def _is_valid_content_image(self, img: ScrapedImage) -> bool:
        """Validate that an image is actual content, not a logo/icon"""
        url = img.url.lower()
        
        # Skip if URL contains suspicious patterns
        bad_patterns = [
            'logo', 'icon', 'favicon', 'avatar', 'button', 'spinner',
            'loading', 'close', 'menu', 'arrow', 'badge', 'profile',
            'userpic', 'thumbnail', 'thumb', 'play', 'pause', 'next',
            'prev', 'search', 'share', 'like', 'heart', 'star',
            'bookmark', 'notification', 'bell', 'settings', 'gear',
            'more', 'dots', 'hamburger', 'nav', 'header', 'footer',
            'bg-', 'background', 'pattern', 'texture', 'gradient',
            'svg', 'gif', 'emoji', 'sticker', 'banner-ad', 'advertisement',
            'promo', 'social', 'facebook', 'twitter', 'instagram',
            'linkedin', 'youtube', 'tiktok', 'snapchat', 'pinterest',
            'reddit', 'whatsapp', 'apple-touch', 'safari-pinned', 'mstile'
        ]
        
        if any(p in url for p in bad_patterns):
            return False
        
        # Skip data URIs
        if url.startswith('data:'):
            return False
        
        # Skip very small images
        if img.width and img.height:
            if img.width < 200 or img.height < 150:
                return False
        
        # Must be HTTP/HTTPS
        if not img.url.startswith(('http://', 'https://')):
            return False
        
        return True
    
    async def publish(self, processed: ProcessedContent) -> Tuple[bool, bool]:
        """Publish to Discord and Telegram"""
        discord_success = False
        telegram_success = False
        
        # Get best valid content image if available
        image_url = None
        if processed.images:
            print(f"[Publisher] Selecting from {len(processed.images)} images...")
            
            # Filter to only valid content images
            valid_images = [img for img in processed.images if self._is_valid_content_image(img)]
            print(f"[Publisher] {len(valid_images)} images passed validation")
            
            if valid_images:
                # Prefer images with dimensions
                for img in valid_images:
                    if img.width and img.height:
                        image_url = img.url
                        print(f"[Publisher] ✓ Selected image with dimensions: {img.width}x{img.height}")
                        print(f"[Publisher]   URL: {img.url[:80]}...")
                        break
                
                # If no image with dimensions, take first valid one
                if not image_url:
                    image_url = valid_images[0].url
                    print(f"[Publisher] ✓ Selected image (no dimensions): {image_url[:80]}...")
            else:
                print(f"[Publisher] ⚠ No valid content images found, will send text only")
        
        # Send to Discord
        if self.discord_webhook:
            try:
                discord_text = self.format_content_discord(processed)
                discord_success = await self._send_to_discord(discord_text, image_url)
            except Exception as e:
                print(f"[Discord] Error: {e}")
        
        # Send to Telegram
        if self.telegram_bot and TELEGRAM_CHANNEL:
            try:
                telegram_text = self.format_content_telegram(processed)
                telegram_success = await self._send_to_telegram(telegram_text, image_url)
            except Exception as e:
                print(f"[Telegram] Error: {e}")
        
        return discord_success, telegram_success
    
    async def _send_to_discord(self, text: str, image_url: Optional[str]) -> bool:
        """Send to Discord webhook"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "content": text,
                "embeds": []
            }
            
            # If we have an image URL, add it as an embed
            if image_url:
                payload["embeds"].append({
                    "image": {"url": image_url}
                })
            
            async with session.post(self.discord_webhook, json=payload) as response:
                if response.status in [200, 204]:
                    print("[Discord] Message sent successfully")
                    return True
                else:
                    error = await response.text()
                    print(f"[Discord] Failed: {error}")
                    return False
    
    async def _send_to_telegram(self, text: str, image_url: Optional[str]) -> bool:
        """Send to Telegram channel"""
        try:
            if image_url:
                # Send with image
                await self.telegram_bot.send_photo(
                    chat_id=TELEGRAM_CHANNEL,
                    photo=image_url,
                    caption=text[:1024],  # Telegram caption limit
                    parse_mode="HTML"
                )
            else:
                # Send text only
                await self.telegram_bot.send_message(
                    chat_id=TELEGRAM_CHANNEL,
                    text=text[:4096],  # Telegram message limit
                    parse_mode="HTML"
                )
            
            print("[Telegram] Message sent successfully")
            return True
            
        except TelegramError as e:
            print(f"[Telegram] API error: {e}")
            return False


class ContentWorkflow:
    """Main workflow orchestrator"""
    
    def __init__(self):
        self.state = WorkflowState()
        self.twitter_scraper = TwitterScraper()
        self.perplexity = PerplexityAnalyzer(PERPLEXITY_API_KEY)
        self.image_scraper = ImageScraper()
        self.publisher = ContentPublisher()
    
    def _get_current_30min_boundary(self, dt: datetime) -> datetime:
        """Get the start of the current 30-minute slot (e.g., 8:17 → 8:00, 8:45 → 8:30)"""
        return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)

    def _get_previous_30min_boundary(self, dt: datetime) -> datetime:
        """Get the start of the PREVIOUS 30-minute slot (e.g., 8:17 → 7:30, 8:45 → 8:00)"""
        current = self._get_current_30min_boundary(dt)
        return current - timedelta(minutes=30)

    def _get_next_30min_boundary(self, dt: datetime) -> datetime:
        """Get the next 30-minute boundary"""
        if dt.minute < 30:
            return dt.replace(minute=30, second=0, microsecond=0)
        else:
            next_hour = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return next_hour
    
    async def run_cycle(self):
        """Run one complete workflow cycle"""
        print(f"\n{'='*60}")
        print(f"[Workflow] Starting cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (local time)")
        print(f"{'='*60}")
        
        # Use local time for all calculations
        now_local = datetime.now()

        # Always derive the window from the current clock — no file state needed.
        # At 12:02  → since=11:30, until=12:02  (covers the 11:30–12:00 slot)
        # At 12:30  → since=12:00, until=12:30  (covers the 12:00–12:30 slot)
        # At 13:00  → since=12:30, until=13:00  (covers the 12:30–13:00 slot)
        since_time = self._get_previous_30min_boundary(now_local)
        until_time = now_local

        print(f"[Workflow] Time window: {since_time.strftime('%H:%M')} to {until_time.strftime('%H:%M')}")
        
        # Parse handles
        handles = [h.strip().replace("@", "") for h in TWITTER_HANDLES.split(",")]
        print(f"[Workflow] Monitoring handles: {', '.join(handles)}")
        
        # Fetch Twitter posts
        all_posts = []
        for handle in handles:
            posts = await self.twitter_scraper.fetch_posts(handle, since_time, until_time)
            all_posts.extend(posts)
        
        print(f"[Workflow] Found {len(all_posts)} new posts")
        
        if not all_posts:
            print("[Workflow] No new posts to process")
            return
        
        # Process each post
        processed_count = 0
        skipped_count = 0
        published_count = 0
        
        print(f"\n[Workflow] Starting to process {len(all_posts)} posts...")
        
        for i, post in enumerate(all_posts, 1):
            # Create unique ID for deduplication
            post_id = f"{post.handle}_{post.timestamp.isoformat()}_{hash(post.text[:50])}"
            
            print(f"\n{'='*60}")
            print(f"[Workflow] Processing post {i}/{len(all_posts)}")
            print(f"[Workflow] Post ID: {post_id}")
            print(f"[Workflow] Content: {post.text[:80]}...")
            print(f"[Workflow] Timestamp: {post.timestamp} UTC")
            
            if self.state.is_post_processed(post_id):
                print(f"[Workflow] ⚠ Already processed, skipping")
                continue
            
            # Step 1: Analyze with Perplexity
            print(f"[Workflow] Step 1/5: Sending to Perplexity for analysis...")
            result = await self.perplexity.analyze(post)
            
            if not result:
                print(f"[Workflow] ✗ Failed to get Perplexity analysis")
                self.state.mark_post_processed(post_id)
                continue
            
            print(f"[Workflow] ✓ Perplexity analysis received ({len(result.response_content)} chars)")
            print(f"[Workflow] Citations: {len(result.citations)}")

            # --- Verbose: print full Perplexity response ---
            print(f"[Perplexity] --- Full Response ({len(result.response_content)} chars) ---")
            # Print in 200-char chunks so it doesn't get cut off in logs
            for chunk_start in range(0, len(result.response_content), 200):
                print(f"[Perplexity] {result.response_content[chunk_start:chunk_start+200]}")
            print(f"[Perplexity] --- End Response ---")

            # --- Verbose: print all citation URLs ---
            if result.citations:
                print(f"[Perplexity] Citations ({len(result.citations)}):")
                for ci, cit in enumerate(result.citations, 1):
                    print(f"[Perplexity]   [{ci}] {cit}")
            else:
                print(f"[Perplexity] No citations returned")

            # Step 2: Skip only if Perplexity returned NO response at all
            # (has_verified_evidence is now purely citation-count based, but
            #  we keep this step for logging clarity)
            print(f"[Workflow] Step 2/5: Evidence check (citations={len(result.citations)})...")
            if not result.has_verified_evidence:
                print(f"[Workflow] ⚠ No citations from Perplexity - SKIPPING")
                skipped_count += 1
                self.state.mark_post_processed(post_id)
                continue

            print(f"[Workflow] ✓ Evidence confirmed ({len(result.citations)} citations)")

            # Step 3: Scrape images from citations
            print(f"[Workflow] Step 3/5: Scraping images from {len(result.citations)} citations...")
            images = []
            if result.citations:
                images = await self.image_scraper.scrape_from_citations(result.citations, min_width=200, min_height=150)
                print(f"[Workflow] Scraped {len(images)} images total")
                if images:
                    for idx, img in enumerate(images, 1):
                        print(f"[Workflow]   Image {idx}: {img.url} ({img.width}x{img.height}) [{img.source}]")
                else:
                    print(f"[Workflow] No images scraped from citations")
            else:
                print(f"[Workflow] No citations available for image scraping")
            
            # Step 4: Create processed content
            print(f"[Workflow] Step 4/5: Creating processed content...")
            processed = ProcessedContent(
                original_post=post,
                perplexity_result=result,
                images=images,
                formatted_text=""
            )
            print(f"[Workflow] ✓ Content ready for publishing")
            
            # Step 5: Publish to Discord and Telegram
            print(f"[Workflow] Step 5/5: Publishing to Discord and Telegram...")
            discord_ok, telegram_ok = await self.publisher.publish(processed)
            
            if discord_ok or telegram_ok:
                published_count += 1
                print(f"[Workflow] ✓✓✓ PUBLISHED SUCCESSFULLY!")
                print(f"[Workflow]   Discord: {'✓' if discord_ok else '✗'}")
                print(f"[Workflow]   Telegram: {'✓' if telegram_ok else '✗'}")
            else:
                print(f"[Workflow] ✗ Failed to publish to both platforms")
            
            processed_count += 1
            self.state.mark_post_processed(post_id)
            print(f"[Workflow] Post marked as processed")
            
            # Small delay between posts
            print(f"[Workflow] Waiting 2 seconds before next post...")
            await asyncio.sleep(2)
        
        print(f"{'='*60}")
        
        # State is in-memory only — no file save needed
        
        print(f"\n[Workflow] Cycle complete:")
        print(f"  - Posts processed: {processed_count}")
        print(f"  - Skipped (no evidence): {skipped_count}")
        print(f"  - Published: {published_count}")
        print(f"{'='*60}\n")
    
    async def run_continuous(self):
        """Run workflow continuously every 30 minutes at fixed boundaries"""
        print("[Workflow] Starting continuous mode (30-minute intervals, local time)")
        print("[Workflow] Press Ctrl+C to stop\n")
        
        while True:
            try:
                await self.run_cycle()
            except Exception as e:
                print(f"[Workflow] Error in cycle: {e}")
            
            # Calculate sleep time until next 30-minute boundary (local time)
            now = datetime.now()
            next_boundary = self._get_next_30min_boundary(now)
            sleep_seconds = (next_boundary - now).total_seconds()
            
            print(f"[Workflow] Next run at {next_boundary.strftime('%H:%M:%S')} (in {int(sleep_seconds/60)}m {int(sleep_seconds%60)}s)")
            await asyncio.sleep(sleep_seconds)


async def main():
    """Main entry point"""
    import sys
    
    # Check for reset flag
    if len(sys.argv) > 1 and sys.argv[1] == '--reset':
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("[Workflow] State file reset. Next run will start fresh.")
        else:
            print("[Workflow] No state file to reset.")
        return
    
    # Validate environment
    required_vars = ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHANNEL', 'DISCORD_WEBHOOK_URL', 'PERPLEXITY_API_KEY']
    missing = [v for v in required_vars if not os.getenv(v)]
    
    if missing:
        print(f"[Error] Missing required environment variables: {', '.join(missing)}")
        print("[Error] Please set these in your .env file")
        return
    
    workflow = ContentWorkflow()
    await workflow.run_continuous()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Workflow] Stopped by user")
    except Exception as e:
        print(f"\n[Workflow] Fatal error: {e}")
