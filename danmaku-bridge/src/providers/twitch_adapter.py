# twitch_adapter.py
import asyncio
import websockets
import json
import requests
import logging
import datetime
from typing import Optional

# Import local auth & normalize modules
from src.auth.oauth_core import OAuthCore
from src.normalize.unified_comment import UnifiedComment

logger = logging.getLogger("danmaku_bridge.twitch_adapter")

class TwitchAdapter:
    def __init__(self, oauth_core: OAuthCore, on_comment_cb=None):
        self.oauth_core = oauth_core
        self.on_comment = on_comment_cb
        self.websocket = None
        self.running = False
        self.task = None
        self.session_id = None
        self.broadcaster_id = None
        self.client_id = None

    async def start(self):
        if self.running:
            return
            
        # Get credentials and user profile
        self.client_id, _ = self.oauth_core.get_provider_credentials("twitch")
        tokens = self.oauth_core.token_store.get_tokens("twitch")
        
        if not tokens:
            raise ValueError("Twitch is not authenticated. Please log in first.")
            
        self.broadcaster_id = tokens.get("account_id")
        if not self.broadcaster_id:
            raise ValueError("Twitch user ID is missing from secure token store.")

        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info(f"Twitch EventSub Adapter started for broadcaster ID: {self.broadcaster_id}")

    async def stop(self):
        self.running = False
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Twitch EventSub Adapter stopped")

    async def _run_loop(self):
        ws_url = "wss://eventsub.wss.twitch.tv/ws"
        retry_delay = 2

        while self.running:
            try:
                logger.info(f"Connecting to Twitch EventSub WebSocket: {ws_url}...")
                async with websockets.connect(ws_url) as websocket:
                    self.websocket = websocket
                    retry_delay = 2 # Reset retry delay

                    async for raw_message in websocket:
                        if not self.running:
                            break
                        
                        await self._handle_ws_message(raw_message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Twitch EventSub WebSocket connection error: {e}")
                if self.running:
                    logger.info(f"Reconnecting to Twitch in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

    async def _handle_ws_message(self, raw_message: str):
        try:
            msg_data = json.loads(raw_message)
            metadata = msg_data.get("metadata", {})
            message_type = metadata.get("message_type")
            payload = msg_data.get("payload", {})
            
            if message_type == "session_welcome":
                session = payload.get("session", {})
                self.session_id = session.get("id")
                logger.info(f"Received welcome message. Session ID: {self.session_id}")
                
                # Subscribe to chat messages using session ID
                loop = asyncio.get_running_loop()
                success = await loop.run_in_executor(None, self._subscribe_to_chat)
                if not success:
                    logger.error("Failed to register Twitch EventSub chat subscription.")
                    
            elif message_type == "session_keepalive":
                # Connection is active, nothing to do
                logger.debug("Received Twitch EventSub keepalive")
                
            elif message_type == "session_reconnect":
                session = payload.get("session", {})
                reconnect_url = session.get("reconnect_url")
                logger.info(f"Received reconnect request. Reconnecting to: {reconnect_url}...")
                # We can update the ws_url for the outer loop to connect to this url next
                # But to trigger reconnection immediately, we close the current socket
                await self.websocket.close()
                
            elif message_type == "notification":
                sub_type = payload.get("subscription", {}).get("type")
                if sub_type == "channel.chat.message":
                    event = payload.get("event", {})
                    self._parse_and_dispatch_chat(event)
                    
        except Exception as e:
            logger.error(f"Error handling EventSub WS message: {e}")

    def _subscribe_to_chat(self) -> bool:
        """
        Sends HTTP POST to Twitch API to register EventSub chat subscription.
        Uses valid token from OAuthCore.
        """
        token = self.oauth_core.get_valid_token("twitch")
        if not token:
            logger.error("Failed to fetch a valid Twitch access token for subscription.")
            return False

        url = "https://api.twitch.tv/helix/eventsub/subscriptions"
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        body = {
            "type": "channel.chat.message",
            "version": "1",
            "condition": {
                "broadcaster_user_id": self.broadcaster_id,
                "user_id": self.broadcaster_id
            },
            "transport": {
                "method": "websocket",
                "session_id": self.session_id
            }
        }
        
        try:
            logger.info(f"Requesting Twitch chat subscription for broadcaster: {self.broadcaster_id}")
            res = requests.post(url, json=body, headers=headers, timeout=10)
            if res.status_code == 202:
                logger.info("Successfully requested Twitch channel.chat.message EventSub subscription.")
                return True
            elif res.status_code == 409:
                # Subscription already exists
                logger.info("Twitch chat subscription already exists.")
                return True
            else:
                logger.error(f"EventSub subscription request failed (Status {res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"Error creating EventSub subscription: {e}")
        return False

    def _parse_and_dispatch_chat(self, event: dict):
        try:
            message_id = event.get("message_id")
            chatter_name = event.get("chatter_user_name", "Anonymous")
            chatter_id = event.get("chatter_user_id")
            text = event.get("message", {}).get("text", "")
            
            # Metadata
            user_color = event.get("color", "")
            badges_list = [b.get("set_id") for b in event.get("badges", []) if b.get("set_id")]
            timestamp_str = event.get("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat())

            # Source room info
            broadcaster_id = event.get("broadcaster_user_id")
            source_room = {
                "broadcaster_id": broadcaster_id,
                "room_id": broadcaster_id
            }

            comment = UnifiedComment(
                platform="twitch",
                message_id=message_id,
                user_id=chatter_id,
                user_name=chatter_name,
                text=text,
                timestamp=timestamp_str,
                user_color=user_color,
                badges=badges_list,
                source_room=source_room
            )
            
            if self.on_comment:
                self.on_comment(comment)
                
        except Exception as e:
            logger.error(f"Failed to parse incoming Twitch comment event: {e}")
stream_list_template = """
# Alternate future streamList method if available (kept for spec completeness)
# TwitchEventSub is WebSocket-based so streamList is not used.
"""
