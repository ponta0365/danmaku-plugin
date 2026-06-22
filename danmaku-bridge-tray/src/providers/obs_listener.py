# obs_listener.py
import asyncio
import json
import logging
import hashlib
import base64
import websockets
from typing import Optional, Callable

logger = logging.getLogger("danmaku_bridge.obs_listener")

class ObsListener:
    def __init__(self, port: int = 4455, password: str = "", 
                 on_stream_state_change: Callable[[bool], None] = None, 
                 on_connection_state_change: Callable[[bool], None] = None):
        self.port = port
        self.password = password
        self.on_stream_state_change = on_stream_state_change
        self.on_connection_state_change = on_connection_state_change
        self.running = False
        self.websocket = None
        self.task = None
        self.connected = False

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info(f"OBS WebSocket Listener task started on port {self.port}")

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
        self._set_connected(False)
        logger.info("OBS WebSocket Listener task stopped")

    def update_config(self, port: int, password: str):
        """
        Updates listener configuration parameters.
        Does not restart connection immediately, but the connection loop will pick it up on the next reconnect.
        """
        self.port = port
        self.password = password

    def _set_connected(self, state: bool):
        if self.connected != state:
            self.connected = state
            if self.on_connection_state_change:
                self.on_connection_state_change(state)

    async def _run_loop(self):
        retry_delay = 5

        while self.running:
            try:
                ws_url = f"ws://127.0.0.1:{self.port}"
                logger.debug(f"Attempting to connect to OBS WebSocket at {ws_url}...")
                async with websockets.connect(ws_url) as websocket:
                    self.websocket = websocket
                    logger.info("Connected to OBS WebSocket. Performing handshake...")
                    
                    # Handshake loop
                    handshake_success = await self._handle_handshake(websocket)
                    if not handshake_success:
                        logger.warning("Handshake failed with OBS WebSocket. Retrying...")
                        await websocket.close()
                        await asyncio.sleep(retry_delay)
                        continue
                    
                    self._set_connected(True)
                    retry_delay = 5 # Reset retry delay on success
                    
                    async for raw_message in websocket:
                        if not self.running:
                            break
                        await self._handle_message(raw_message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._set_connected(False)
                if self.running:
                    logger.debug(f"OBS WebSocket disconnected or connection failed: {e}. Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)

    async def _handle_handshake(self, websocket) -> bool:
        try:
            # 1. Read Hello message
            hello_raw = await websocket.recv()
            hello_data = json.loads(hello_raw)
            
            if hello_data.get("op") != 0:
                logger.error("Expected op 0 (Hello) from OBS")
                return False
                
            d = hello_data.get("d", {})
            auth_info = d.get("authentication")
            
            # 2. Build Identify payload
            identify_payload = {
                "op": 1,
                "d": {
                    "rpcVersion": 1,
                    "eventSubscriptions": 64  # Outputs (Streaming state changes)
                }
            }
            
            if auth_info:
                # Password required
                salt = auth_info.get("salt")
                challenge = auth_info.get("challenge")
                
                if not self.password:
                    logger.error("OBS requested password authentication, but no password is configured.")
                    return False
                    
                # Compute base64(sha256(password + salt))
                secret_concat = self.password + salt
                secret_hash = hashlib.sha256(secret_concat.encode('utf-8')).digest()
                secret_b64 = base64.b64encode(secret_hash).decode('utf-8')
                
                # Compute base64(sha256(secret_b64 + challenge))
                auth_concat = secret_b64 + challenge
                auth_hash = hashlib.sha256(auth_concat.encode('utf-8')).digest()
                auth_b64 = base64.b64encode(auth_hash).decode('utf-8')
                
                identify_payload["d"]["authentication"] = auth_b64
                
            # Send Identify
            await websocket.send(json.dumps(identify_payload))
            
            # 3. Wait for Identified response
            response_raw = await websocket.recv()
            response_data = json.loads(response_raw)
            
            if response_data.get("op") == 2:
                logger.info("OBS Handshake completed successfully. Identified.")
                # Request current streaming status
                await self._request_initial_state(websocket)
                return True
            else:
                logger.error(f"OBS authentication failed or unexpected op code: {response_data}")
                return False
        except Exception as e:
            logger.error(f"Error during OBS handshake: {e}")
            return False

    async def _request_initial_state(self, websocket):
        try:
            req = {
                "op": 6,  # Request
                "d": {
                    "requestType": "GetStreamStatus",
                    "requestId": "init_stream_status"
                }
            }
            await websocket.send(json.dumps(req))
        except Exception as e:
            logger.error(f"Error requesting stream status: {e}")

    async def _handle_message(self, raw_message: str):
        try:
            data = json.loads(raw_message)
            op = data.get("op")
            d = data.get("d", {})
            
            # RequestResponse (op 7)
            if op == 7:
                request_type = d.get("requestType")
                request_id = d.get("requestId")
                
                if request_type == "GetStreamStatus" and request_id == "init_stream_status":
                    response_data = d.get("responseData", {})
                    output_active = response_data.get("outputActive", False)
                    logger.info(f"OBS Initial Stream Status: Active = {output_active}")
                    if self.on_stream_state_change:
                        self.on_stream_state_change(output_active)
            
            # Event (op 5)
            elif op == 5:
                event_type = d.get("eventType")
                event_data = d.get("eventData", {})
                
                if event_type == "StreamStateChanged":
                    output_active = event_data.get("outputActive", False)
                    logger.info(f"OBS Stream State Changed: Active = {output_active}")
                    if self.on_stream_state_change:
                        self.on_stream_state_change(output_active)
                        
        except Exception as e:
            logger.error(f"Error parsing OBS message: {e}")
