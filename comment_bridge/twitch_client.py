# twitch_client.py
import asyncio
import websockets
import re
import logging

logger = logging.getLogger("comment_bridge.twitch")

class TwitchChatClient:
    def __init__(self, channel: str, token: str = None, nickname: str = None, on_comment_cb=None):
        self.channel = channel.lower().strip()
        # Ensure channel starts with # for IRC join command
        self.irc_channel = f"#{self.channel}" if not self.channel.startswith("#") else self.channel
        self.token = token
        self.nickname = nickname.lower().strip() if nickname else "justinfan88371"
        self.on_comment = on_comment_cb
        self.websocket = None
        self.running = False
        self.task = None

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info(f"Twitch chat client started for channel: {self.channel}")

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
                await self.task;
            except asyncio.CancelledError:
                pass
        logger.info("Twitch chat client stopped")

    async def _run_loop(self):
        uri = "wss://irc-ws.chat.twitch.tv:443"
        retry_delay = 2

        while self.running:
            try:
                logger.info(f"Connecting to Twitch Chat WebSocket at {uri}...")
                async with websockets.connect(uri) as websocket:
                    self.websocket = websocket
                    
                    # Authenticate
                    if self.token:
                        # Strip "oauth:" prefix if user provided it manually
                        clean_token = self.token.replace("oauth:", "")
                        await websocket.send(f"PASS oauth:{clean_token}")
                        await websocket.send(f"NICK {self.nickname}")
                    else:
                        # Anonymous access
                        await websocket.send("NICK justinfan12345")
                    
                    # Request capabilities (optional but nice for badges/color)
                    await websocket.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                    
                    # Join channel
                    await websocket.send(f"JOIN {self.irc_channel}")
                    logger.info(f"Successfully joined Twitch channel: {self.irc_channel}")
                    
                    retry_delay = 2 # Reset retry delay on successful connection

                    async for message in websocket:
                        if not self.running:
                            break
                        
                        # Handle PING/PONG
                        if message.startswith("PING"):
                            await websocket.send("PONG :tmi.twitch.tv")
                            continue
                        
                        # Parse Twitch IRC message with tags
                        self._parse_and_handle(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Twitch WebSocket error: {e}")
                if self.running:
                    logger.info(f"Reconnecting to Twitch in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

    def _parse_and_handle(self, raw_message: str):
        # Splitting multiple messages that might be grouped in a single frame
        lines = raw_message.split("\r\n")
        for line in lines:
            if not line:
                continue
            
            # Match PRIVMSG
            # Format with tags: @badge-info=... :user!user@user.tmi.twitch.tv PRIVMSG #channel :message
            # Format without tags: :user!user@user.tmi.twitch.tv PRIVMSG #channel :message
            match = re.search(r"^(?:@\S+ )?:(\S+)!(\S+) PRIVMSG \S+ :(.+)$", line)
            if match:
                username = match.group(1)
                display_name = username
                message_text = match.group(3)
                
                # Try to extract display-name from tags if present
                tags_match = re.search(r"display-name=([^;]+)", line)
                if tags_match:
                    display_name = tags_match.group(1)

                logger.debug(f"[Twitch] {display_name}: {message_text}")
                if self.on_comment:
                    self.on_comment(display_name, message_text, "Twitch")
