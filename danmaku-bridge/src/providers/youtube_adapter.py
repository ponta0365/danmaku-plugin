# youtube_adapter.py
import asyncio
import requests
import logging
import datetime
from typing import Optional

# Import local auth & normalize modules
from src.auth.oauth_core import OAuthCore
from src.normalize.unified_comment import UnifiedComment

logger = logging.getLogger("danmaku_bridge.youtube_adapter")

class YouTubeAdapter:
    def __init__(self, oauth_core: OAuthCore, on_comment_cb=None):
        self.oauth_core = oauth_core
        self.on_comment = on_comment_cb
        self.running = False
        self.task = None
        self.live_chat_id = None
        self.next_page_token = None

    async def start(self):
        if self.running:
            return
            
        # Verify authentication
        tokens = self.oauth_core.token_store.get_tokens("youtube")
        if not tokens:
            raise ValueError("YouTube is not authenticated. Please log in first.")

        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info("YouTube API Polling Adapter started.")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("YouTube API Polling Adapter stopped.")

    async def _run_loop(self):
        # 1. Fetch liveChatId
        loop = asyncio.get_running_loop()
        while self.running and not self.live_chat_id:
            try:
                self.live_chat_id = await loop.run_in_executor(None, self._fetch_live_chat_id)
                if not self.live_chat_id:
                    logger.warning("No active YouTube livestream found. Checking again in 20 seconds...")
                    await asyncio.sleep(20)
            except Exception as e:
                logger.error(f"Error searching for YouTube active broadcast: {e}")
                await asyncio.sleep(20)

        if not self.running:
            return

        logger.info(f"Connected to YouTube Chat. Chat ID: {self.live_chat_id}")
        
        # 2. Poll messages loop
        poll_delay = 5.0
        while self.running:
            try:
                # We execute the polling request in thread pool executor
                poll_delay = await loop.run_in_executor(None, self._poll_chat_messages)
            except Exception as e:
                logger.error(f"Error during YouTube chat message poll: {e}")
                poll_delay = 5.0
            
            await asyncio.sleep(poll_delay)

    def _fetch_live_chat_id(self) -> Optional[str]:
        token = self.oauth_core.get_valid_token("youtube")
        if not token:
            logger.error("Failed to retrieve valid Google access token.")
            return None
            
        url = "https://www.googleapis.com/youtube/v3/liveBroadcasts?broadcastStatus=active&broadcastType=all&part=snippet"
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            logger.info("Fetching active broadcasts from YouTube API...")
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                if items:
                    chat_id = items[0].get("snippet", {}).get("liveChatId")
                    if chat_id:
                        return chat_id
            else:
                logger.error(f"YouTube broadcasts lookup HTTP {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"API request exception during broadcast lookup: {e}")
        return None

    def _poll_chat_messages(self) -> float:
        """
        Polls comments from YouTube Data API and returns the next polling delay (seconds).
        """
        token = self.oauth_core.get_valid_token("youtube")
        if not token:
            logger.error("Failed to fetch a valid YouTube access token for polling.")
            return 8.0

        url = f"https://www.googleapis.com/youtube/v3/liveChat/messages?liveChatId={self.live_chat_id}&part=snippet,authorDetails&maxResults=200"
        if self.next_page_token:
            url += f"&pageToken={self.next_page_token}"
            
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                self.next_page_token = data.get("nextPageToken")
                
                # Fetch next poll interval (in ms) from API
                poll_interval_ms = data.get("pollingIntervalMillis", 5000)
                next_delay = max(poll_interval_ms / 1000.0, 2.0)
                
                # Process messages
                items = data.get("items", [])
                for item in items:
                    self._parse_and_dispatch_chat(item)
                    
                return next_delay
            elif res.status_code == 401:
                logger.error("YouTube access token is invalid or expired.")
                return 15.0
            else:
                logger.error(f"YouTube chat poll HTTP {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"API request exception during chat poll: {e}")
            
        return 5.0

    def _parse_and_dispatch_chat(self, item: dict):
        try:
            message_id = item.get("id")
            snippet = item.get("snippet", {})
            author = item.get("authorDetails", {})
            
            user_id = author.get("channelId")
            user_name = author.get("displayName", "Anonymous")
            text = snippet.get("displayMessage", "")
            timestamp_str = snippet.get("publishedAt", datetime.datetime.now(datetime.timezone.utc).isoformat())
            
            # Map YouTube badges
            badges = []
            if author.get("isChatOwner"):
                badges.append("broadcaster")
            if author.get("isChatModerator"):
                badges.append("moderator")
            if author.get("isChatSponsor"):
                badges.append("member")

            # Check for Super Chat details
            amount = None
            if snippet.get("type") == "superChatEvent":
                superchat = snippet.get("superChatDetails", {})
                amount = {
                    "display": superchat.get("amountDisplayString", ""),
                    "currency": "", # Google API doesn't split it clearly in amountDisplayString
                    "value": 0.0
                }
                # Format message text to highlight Super Chat
                user_msg = superchat.get("userComment", "")
                text = f"[Super Chat {amount['display']}] {user_msg}" if user_msg else f"[Super Chat {amount['display']}]"

            # Room Info
            source_room = {
                "broadcaster_id": self.live_chat_id, # Or use broadcaster profile if available
                "room_id": self.live_chat_id
            }

            comment = UnifiedComment(
                platform="youtube",
                message_id=message_id,
                user_id=user_id,
                user_name=user_name,
                text=text,
                timestamp=timestamp_str,
                badges=badges,
                amount=amount,
                source_room=source_room
            )
            
            if self.on_comment:
                self.on_comment(comment)
                
        except Exception as e:
            logger.error(f"Failed to parse YouTube comment object: {e}")
