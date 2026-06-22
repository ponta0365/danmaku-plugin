# youtube_client.py
import asyncio
import re
import json
import requests
import logging

logger = logging.getLogger("comment_bridge.youtube")

class YouTubeChatClient:
    def __init__(self, mode: str, video_id_or_url: str = None, token: str = None, on_comment_cb=None):
        """
        mode: "oauth" or "scraper"
        video_id_or_url: Required for "scraper" mode (e.g. "https://www.youtube.com/watch?v=XXXX" or "XXXX")
        token: Access Token for "oauth" mode
        """
        self.mode = mode.lower().strip()
        self.video_id = self._extract_video_id(video_id_or_url) if video_id_or_url else None
        self.token = token
        self.on_comment = on_comment_cb
        self.running = False
        self.task = None

    def _extract_video_id(self, input_str: str) -> str:
        if not input_str:
            return None
        # Try to match standard YouTube video URLs
        match = re.search(r"(?:v=|\/v\/|embed\/|youtu\.be\/|\/live\/|\/shorts\/|^)([^&\?\/\s]{11})", input_str.strip())
        return match.group(1) if match else input_str.strip()

    async def start(self):
        if self.running:
            return
        self.running = True
        if self.mode == "oauth":
            self.task = asyncio.create_task(self._run_oauth_loop())
            logger.info("YouTube OAuth chat client started")
        else:
            if not self.video_id:
                raise ValueError("YouTube Video ID is required for scraper mode.")
            self.task = asyncio.create_task(self._run_scraper_loop())
            logger.info(f"YouTube Scraper chat client started for Video ID: {self.video_id}")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("YouTube chat client stopped")

    # --- Mode 1: OAuth Polling via Official API ---
    async def _run_oauth_loop(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        live_chat_id = None
        next_page_token = None
        
        # Step 1: Find Active Broadcast and get its LiveChatId
        while self.running and not live_chat_id:
            try:
                url = "https://www.googleapis.com/youtube/v3/liveBroadcasts?broadcastStatus=active&broadcastType=all&part=snippet"
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("items", [])
                    if items:
                        live_chat_id = items[0].get("snippet", {}).get("liveChatId")
                        logger.info(f"Found active YouTube Broadcast. Live Chat ID: {live_chat_id}")
                    else:
                        logger.warning("No active YouTube broadcast found. Retrying in 15 seconds...")
                        await asyncio.sleep(15)
                else:
                    logger.error(f"Failed to fetch live broadcasts (Status {response.status_code}): {response.text}")
                    await asyncio.sleep(15)
            except Exception as e:
                logger.error(f"Error during YouTube broadcast lookup: {e}")
                await asyncio.sleep(15)

        # Step 2: Poll comments
        poll_delay = 5 # Google API minimum safe poll delay
        while self.running:
            try:
                url = f"https://www.googleapis.com/youtube/v3/liveChat/messages?liveChatId={live_chat_id}&part=snippet,authorDetails&maxResults=200"
                if next_page_token:
                    url += f"&pageToken={next_page_token}"
                
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    next_page_token = data.get("nextPageToken")
                    
                    # Convert internal poll delay if provided by the API (in ms)
                    poll_delay_ms = data.get("pollingIntervalMillis", 5000)
                    poll_delay = max(poll_delay_ms / 1000.0, 3.0)

                    # Extract comments
                    for item in data.get("items", []):
                        snippet = item.get("snippet", {})
                        author = item.get("authorDetails", {})
                        display_name = author.get("displayName", "Anonymous")
                        
                        # Handle text comments vs super chats
                        message_text = ""
                        text_details = snippet.get("textMessageDetails", {})
                        if text_details:
                            message_text = text_details.get("messageText", "")
                        else:
                            superchat = snippet.get("superChatDetails", {})
                            if superchat:
                                message_text = f"[Super Chat {superchat.get('amountDisplayString')}] {superchat.get('userComment', '')}"
                        
                        if message_text:
                            logger.debug(f"[YouTube OAuth] {display_name}: {message_text}")
                            if self.on_comment:
                                self.on_comment(display_name, message_text, "YouTube")
                
                elif response.status_code == 401:
                    logger.error("YouTube access token expired or invalid.")
                    break
                else:
                    logger.error(f"YouTube chat API error (Status {response.status_code}): {response.text}")
                
                await asyncio.sleep(poll_delay)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error polling YouTube chat: {e}")
                await asyncio.sleep(poll_delay)

    # --- Mode 2: No-OAuth YouTubei Web Scraper ---
    async def _run_scraper_loop(self):
        # Resolve initial continuation and API Key
        loop = asyncio.get_running_loop()
        api_key, continuation = await loop.run_in_executor(None, self._fetch_initial_chat_params)
        
        if not api_key or not continuation:
            logger.error("Could not fetch YouTube chat scraper parameters (Check if the livestream is offline or invalid Video ID).")
            return

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json"
        })

        poll_delay = 2
        while self.running:
            try:
                url = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={api_key}"
                payload = {
                    "context": {
                        "client": {
                            "clientName": "WEB",
                            "clientVersion": "2.20240101.01.00"
                        }
                    },
                    "continuation": continuation
                }
                
                # Make async-friendly HTTP POST call
                response = await loop.run_in_executor(None, lambda: session.post(url, json=payload, timeout=10))
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Parse actions/chat messages
                    contents = data.get("continuationContents", {})
                    live_chat_cont = contents.get("liveChatContinuation", {})
                    actions = live_chat_cont.get("actions", [])
                    
                    for action in actions:
                        item_action = action.get("addChatItemAction", {})
                        item = item_action.get("item", {})
                        
                        renderer = None
                        is_super_chat = False
                        amount = ""
                        
                        if "liveChatTextMessageRenderer" in item:
                            renderer = item["liveChatTextMessageRenderer"]
                        elif "liveChatPaidMessageRenderer" in item:
                            renderer = item["liveChatPaidMessageRenderer"]
                            is_super_chat = True
                            amount = renderer.get("purchaseAmountText", {}).get("simpleText", "")
                        
                        if renderer:
                            # Extract author name
                            author_name = renderer.get("authorName", {}).get("simpleText", "Anonymous")
                            
                            # Parse message runs (emotes/text mixed)
                            message_runs = renderer.get("message", {}).get("runs", [])
                            message_parts = []
                            for run in message_runs:
                                if "text" in run:
                                    message_parts.append(run["text"])
                                elif "emoji" in run:
                                    # Use the shortcut description or emoji text representation
                                    emoji_map = run["emoji"]
                                    message_parts.append(emoji_map.get("shortcuts", [""])[0] or emoji_map.get("emojiId", ""))
                            
                            message_text = "".join(message_parts)
                            if is_super_chat:
                                text_prefix = f"[Super Chat {amount}] "
                                message_text = text_prefix + message_text
                            
                            if message_text:
                                logger.debug(f"[YouTube Scraper] {author_name}: {message_text}")
                                if self.on_comment:
                                    self.on_comment(author_name, message_text, "YouTube")

                    # Extract next continuation token
                    continuations = live_chat_cont.get("continuations", [])
                    if continuations:
                        cont_data = continuations[0]
                        
                        # Handle different continuation structures
                        if "invalidationContinuationData" in cont_data:
                            continuation = cont_data["invalidationContinuationData"].get("continuation")
                            timeout_ms = cont_data["invalidationContinuationData"].get("timeoutMs", 2000)
                        elif "timedContinuationData" in cont_data:
                            continuation = cont_data["timedContinuationData"].get("continuation")
                            timeout_ms = cont_data["timedContinuationData"].get("timeoutMs", 2000)
                        else:
                            continuation = None
                            timeout_ms = 2000
                        
                        poll_delay = max(timeout_ms / 1000.0, 1.0)
                    else:
                        logger.warning("No continuation token returned in scraper response. Stopping YouTube scraper.")
                        break
                else:
                    logger.error(f"YouTube Scraper error (Status {response.status_code}): {response.text}")
                    await asyncio.sleep(5)
                
                await asyncio.sleep(poll_delay)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in YouTube Scraper loop: {e}")
                await asyncio.sleep(5)

    def _fetch_initial_chat_params(self):
        """
        Fetches the live_chat HTML page for the Video ID and extracts the 
        YouTubei API Key and the initial continuation token.
        """
        url = f"https://www.youtube.com/live_chat?v={self.video_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
        }
        try:
            logger.info(f"Fetching initial parameters from YouTube Live Chat frame: {url}")
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                html = response.text
                
                # Regex patterns to extract parameters
                # 1. API Key
                api_key_match = re.search(r'"innertubeApiKey"\s*:\s*"([^"]+)"', html)
                if not api_key_match:
                    api_key_match = re.search(r'"apiKey"\s*:\s*"([^"]+)"', html)
                api_key = api_key_match.group(1) if api_key_match else None
                
                # 2. Continuation Token
                continuation_match = re.search(r'"continuation"\s*:\s*"([^"]+)"', html)
                continuation = continuation_match.group(1) if continuation_match else None
                
                if api_key and continuation:
                    logger.info("Successfully extracted YouTubei API Key and initial Continuation Token")
                    return api_key, continuation
                
                logger.warning(f"Failed to find API Key or Continuation in HTML response. Found API Key: {bool(api_key)}, Continuation: {bool(continuation)}")
            else:
                logger.error(f"Failed to fetch live chat frame. Status code: {response.status_code}")
        except Exception as e:
            logger.error(f"Exception while retrieving initial YouTube chat parameters: {e}")
        return None, None
