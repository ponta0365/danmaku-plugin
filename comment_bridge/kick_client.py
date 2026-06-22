# kick_client.py
import asyncio
import websockets
import json
import requests
import logging

logger = logging.getLogger("comment_bridge.kick")

class KickChatClient:
    def __init__(self, channel_slug: str, chatroom_id: str = None, on_comment_cb=None):
        self.channel_slug = channel_slug.lower().strip()
        self.chatroom_id = chatroom_id
        self.on_comment = on_comment_cb
        self.websocket = None
        self.running = False
        self.task = None

    def fetch_chatroom_id(self) -> str:
        """
        Attempts to fetch the chatroom ID from Kick's channel endpoint.
        Uses a standard browser user agent to minimize Cloudflare blocks.
        """
        url = f"https://kick.com/api/v2/channels/{self.channel_slug}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Origin": "https://kick.com",
            "Referer": f"https://kick.com/{self.channel_slug}"
        }
        try:
            logger.info(f"Attempting to fetch chatroom ID for Kick channel: {self.channel_slug}")
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                room_id = str(data.get("chatroom", {}).get("id", ""))
                if room_id:
                    logger.info(f"Successfully auto-fetched Kick chatroom ID: {room_id}")
                    return room_id
            logger.warning(f"Kick API returned status code {response.status_code} for {self.channel_slug}")
        except Exception as e:
            logger.error(f"Error fetching Kick chatroom ID: {e}")
        return None

    async def start(self):
        if self.running:
            return
        
        # Auto-resolve chatroom_id if not provided
        if not self.chatroom_id:
            # Run blocking HTTP request in executor
            loop = asyncio.get_running_loop()
            self.chatroom_id = await loop.run_in_executor(None, self.fetch_chatroom_id)
            
        if not self.chatroom_id:
            logger.error("Could not start Kick client: chatroom ID is missing and auto-fetch failed.")
            raise ValueError("Kick chatroom ID missing. Please input it manually in the dashboard.")

        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info(f"Kick chat client started for channel '{self.channel_slug}' (Room ID: {self.chatroom_id})")

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
        logger.info("Kick chat client stopped")

    async def _run_loop(self):
        # Kick Pusher WebSocket URL
        app_key = "32cbd69e4b950bf97679"
        uri = f"wss://ws-us2.pusher.com/app/{app_key}?protocol=7&client=js&version=7.6.0&flash=false"
        retry_delay = 2

        while self.running:
            try:
                logger.info(f"Connecting to Kick Pusher WebSocket at {uri}...")
                async with websockets.connect(uri) as websocket:
                    self.websocket = websocket
                    
                    # Pusher connection handshake is completed when we receive pusher:connection_established
                    # We can subscribe to the chatroom channel immediately
                    channel_name = f"chatrooms.{self.chatroom_id}.v2"
                    subscribe_payload = {
                        "event": "pusher:subscribe",
                        "data": {
                            "auth": "",
                            "channel": channel_name
                        }
                    }
                    await websocket.send(json.dumps(subscribe_payload))
                    logger.info(f"Subscribed to Kick Pusher channel: {channel_name}")
                    
                    retry_delay = 2 # Reset retry delay on successful connection

                    async for message in websocket:
                        if not self.running:
                            break
                        
                        self._handle_pusher_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Kick WebSocket error: {e}")
                if self.running:
                    logger.info(f"Reconnecting to Kick in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

    def _handle_pusher_message(self, raw_message: str):
        try:
            event_data = json.loads(raw_message)
            event_type = event_data.get("event")
            
            # Check for Chat Message Event
            if event_type == "App\\Events\\ChatMessageEvent":
                # Data is a serialized JSON string
                chat_data_str = event_data.get("data")
                if chat_data_str:
                    chat_data = json.loads(chat_data_str)
                    sender = chat_data.get("sender", {})
                    display_name = sender.get("username", "Anonymous")
                    message_text = chat_data.get("content", "")
                    
                    logger.debug(f"[Kick] {display_name}: {message_text}")
                    if self.on_comment:
                        self.on_comment(display_name, message_text, "Kick")
            
            elif event_type == "pusher:ping":
                # Reply with pusher:pong to prevent connection timeout
                asyncio.create_task(self.websocket.send(json.dumps({"event": "pusher:pong"})))
                
        except Exception as e:
            logger.error(f"Error parsing Kick Pusher message: {e}")
