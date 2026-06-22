# main.py
import os
import json
import logging
import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

# Import local modules
from writer import CommentWriter
from twitch_client import TwitchChatClient
from youtube_client import YouTubeChatClient
from kick_client import KickChatClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("comment_bridge")

app = FastAPI(title="OBS Comment Bridge & OAuth Foundation")

# Global instances
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
comment_writer = None
active_clients = {
    "twitch": None,
    "youtube": None,
    "kick": None
}
websocket_connections: List[WebSocket] = []

# Pydantic models for request/response validation
class AppConfig(BaseModel):
    comments_file_path: str = "K:\\obs_comments.txt"
    twitch_channel: str = ""
    twitch_token: str = ""
    twitch_nickname: str = ""
    youtube_mode: str = "scraper" # "oauth" or "scraper"
    youtube_video_id: str = ""
    youtube_token: str = ""
    kick_channel: str = ""
    kick_chatroom_id: str = ""

def load_config() -> AppConfig:
    # Set default path to K:\ if it exists, else local temp file
    default_path = "K:\\obs_comments.txt" if os.path.exists("K:\\") else os.path.join(os.path.dirname(__file__), "obs_comments.txt")
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Override default file path if it was empty in json
                if not data.get("comments_file_path"):
                    data["comments_file_path"] = default_path
                return AppConfig(**data)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
    
    return AppConfig(comments_file_path=default_path)

def save_config(config: AppConfig):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=4, ensure_ascii=False)
        # Update active writer path
        global comment_writer
        if comment_writer:
            comment_writer.set_file_path(config.comments_file_path)
        else:
            comment_writer = CommentWriter(config.comments_file_path)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")

# Initialize CommentWriter on startup
current_config = load_config()
comment_writer = CommentWriter(current_config.comments_file_path)

# --- Core Callback Function for clients ---
def handle_incoming_comment(author: str, text: str, platform: str):
    # 1. Write to the comments file
    formatted_comment = f"[{platform}] {author}: {text}"
    if comment_writer:
        # We append directly in one-line format
        comment_writer.write(formatted_comment)
    
    # 2. Broadcast to all active UI web sockets
    if websocket_connections:
        payload = {
            "author": author,
            "text": text,
            "platform": platform,
            "formatted": formatted_comment
        }
        asyncio.create_task(broadcast_ws(payload))

async def broadcast_ws(payload: dict):
    # Make a copy of connections to avoid race conditions
    targets = list(websocket_connections)
    for ws in targets:
        try:
            await ws.send_json(payload)
        except Exception:
            if ws in websocket_connections:
                websocket_connections.remove(ws)

# --- API Endpoints ---
@app.get("/api/config", response_model=AppConfig)
async def get_config():
    return load_config()

@app.post("/api/config")
async def update_config(config: AppConfig):
    save_config(config)
    return {"status": "success", "message": "Configuration saved."}

@app.get("/api/status")
async def get_status():
    file_size_kb = 0.0
    path = load_config().comments_file_path
    if os.path.exists(path):
        try:
            file_size_kb = os.path.getsize(path) / 1024.0
        except Exception:
            pass
    return {
        "twitch": active_clients["twitch"] is not None and active_clients["twitch"].running,
        "youtube": active_clients["youtube"] is not None and active_clients["youtube"].running,
        "kick": active_clients["kick"] is not None and active_clients["kick"].running,
        "comments_file_path": path,
        "file_size_kb": round(file_size_kb, 2)
    }

@app.post("/api/start")
async def start_fetching(platforms: List[str]):
    config = load_config()
    started = []
    failed = []

    # Clear comments file on session start to avoid old comments showing up
    comment_writer.clear()

    # --- 1. Twitch ---
    if "twitch" in platforms:
        if config.twitch_channel:
            # Stop existing client if any
            if active_clients["twitch"]:
                await active_clients["twitch"].stop()
            
            try:
                active_clients["twitch"] = TwitchChatClient(
                    channel=config.twitch_channel,
                    token=config.twitch_token,
                    nickname=config.twitch_nickname,
                    on_comment_cb=handle_incoming_comment
                )
                await active_clients["twitch"].start()
                started.append("twitch")
            except Exception as e:
                logger.error(f"Failed to start Twitch client: {e}")
                failed.append(f"twitch: {str(e)}")

    # --- 2. YouTube ---
    if "youtube" in platforms:
        # Check YouTube configuration validity
        is_valid = (config.youtube_mode == "scraper" and config.youtube_video_id) or \
                   (config.youtube_mode == "oauth" and config.youtube_token)
        
        if is_valid:
            if active_clients["youtube"]:
                await active_clients["youtube"].stop()
            
            try:
                active_clients["youtube"] = YouTubeChatClient(
                    mode=config.youtube_mode,
                    video_id_or_url=config.youtube_video_id,
                    token=config.youtube_token,
                    on_comment_cb=handle_incoming_comment
                )
                await active_clients["youtube"].start()
                started.append("youtube")
            except Exception as e:
                logger.error(f"Failed to start YouTube client: {e}")
                failed.append(f"youtube: {str(e)}")

    # --- 3. Kick ---
    if "kick" in platforms:
        if config.kick_channel:
            if active_clients["kick"]:
                await active_clients["kick"].stop()
            
            try:
                active_clients["kick"] = KickChatClient(
                    channel_slug=config.kick_channel,
                    chatroom_id=config.kick_chatroom_id,
                    on_comment_cb=handle_incoming_comment
                )
                await active_clients["kick"].start()
                started.append("kick")
            except Exception as e:
                logger.error(f"Failed to start Kick client: {e}")
                failed.append(f"kick: {str(e)}")

    return {
        "status": "success" if not failed else "partial",
        "started": started,
        "failed": failed
    }

@app.post("/api/stop")
async def stop_fetching(platforms: List[str]):
    stopped = []
    for platform in platforms:
        if platform in active_clients and active_clients[platform]:
            await active_clients[platform].stop()
            active_clients[platform] = None
            stopped.append(platform)
    return {"status": "success", "stopped": stopped}

# --- OAuth Redirection Callbacks ---
@app.get("/callback/twitch")
async def twitch_callback():
    # Return HTML that captures the hash fragment from the URL and POSTs it back
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Twitch Authentication Callback</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0e0e10; color: #efeff1; text-align: center; padding-top: 100px; }
            .spinner { border: 4px solid rgba(255,255,255,0.1); width: 50px; height: 50px; border-radius: 50%; border-left-color: #9146ff; animation: spin 1s linear infinite; margin: 20px auto; }
            @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        </style>
    </head>
    <body>
        <div class="spinner"></div>
        <h2>Authenticating Twitch Access...</h2>
        <p>Please wait while we secure your connection.</p>
        <script>
            // Capture the token from hash parameters
            const hash = window.location.hash.substring(1);
            const params = new URLSearchParams(hash);
            const token = params.get("access_token");
            
            if (token) {
                // Fetch the user's nickname using the token to auto-populate config
                fetch("https://api.twitch.tv/helix/users", {
                    headers: {
                        "Client-ID": "gp762nuuoqcoxypm4t5665g5lumhz1", // Common helper Client ID
                        "Authorization": `Bearer ${token}`
                    }
                })
                .then(r => r.json())
                .then(userData => {
                    const nickname = userData.data[0].login;
                    
                    // Post the token and nickname back to local API
                    return fetch("/api/save_oauth/twitch", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ token: token, nickname: nickname })
                    });
                })
                .then(() => {
                    document.body.innerHTML = "<h1>Authentication Successful!</h1><p>You can close this tab and return to the dashboard.</p>";
                    setTimeout(() => window.close(), 1500);
                })
                .catch(err => {
                    document.body.innerHTML = "<h1>Error saving token</h1><p>" + err + "</p>";
                });
            } else {
                document.body.innerHTML = "<h1>Authentication Failed</h1><p>No access token found in redirect.</p>";
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

class OAuthSavePayload(BaseModel):
    token: str
    nickname: Optional[str] = ""

@app.post("/api/save_oauth/twitch")
async def save_twitch_oauth(payload: OAuthSavePayload):
    config = load_config()
    config.twitch_token = payload.token
    if payload.nickname:
        config.twitch_nickname = payload.nickname
        config.twitch_channel = payload.nickname # Set default channel to self
    save_config(config)
    return {"status": "success"}

# --- WebSocket server for frontend telemetry ---
@app.websocket("/ws/comments")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.append(websocket)
    try:
        while True:
            # We don't expect client messages, but we need to keep the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)
    except Exception:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)

# --- Mount Static Files (Frontend UI) ---
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

# Mount it to the root
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

@app.on_event("shutdown")
async def shutdown_event():
    # Stop all active clients on exit
    for platform, client in active_clients.items():
        if client:
            await client.stop()
    logger.info("All clients stopped and server shut down successfully.")
