# callback_server.py
import http.server
import socket
import urllib.parse
import threading
import logging
from typing import Tuple, Optional

logger = logging.getLogger("danmaku_bridge.callback_server")

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Prevent spamming console with standard HTTP logs
        logger.debug(format % args)

    def do_GET(self):
        # We only expect GET requests to /callback
        url_parsed = urllib.parse.urlparse(self.path)
        if url_parsed.path == "/callback":
            query_params = urllib.parse.parse_qs(url_parsed.query)
            
            # Save query params on the server instance
            self.server.received_code = query_params.get("code", [None])[0]
            self.server.received_state = query_params.get("state", [None])[0]
            self.server.received_error = query_params.get("error", [None])[0]
            self.server.received_error_description = query_params.get("error_description", [None])[0]
            
            # Respond to the user with a nice HTML page
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            
            if self.server.received_error:
                html = f"""<!doctype html>
                <html lang="ja">
                <head>
                    <meta charset="utf-8">
                    <title>認証失敗</title>
                    <style>
                        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; text-align: center; padding-top: 50px; background: #0f0f15; color: #f3f4f6; }}
                        .container {{ max-width: 500px; margin: 0 auto; background: rgba(255,255,255,0.05); padding: 30px; border-radius: 12px; border: 1px solid rgba(255,0,51,0.3); }}
                        h1 {{ color: #ff0033; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>認証に失敗しました</h1>
                        <p>理由: {self.server.received_error_description or self.server.received_error}</p>
                        <p>このタブを閉じて、アプリケーションに戻り再度お試しください。</p>
                    </div>
                </body>
                </html>"""
            else:
                html = """<!doctype html>
                <html lang="ja">
                <head>
                    <meta charset="utf-8">
                    <title>認証完了</title>
                    <style>
                        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; text-align: center; padding-top: 50px; background: #0f0f15; color: #f3f4f6; }
                        .container { max-width: 500px; margin: 0 auto; background: rgba(255,255,255,0.05); padding: 30px; border-radius: 12px; border: 1px solid rgba(83,252,24,0.3); }
                        h1 { color: #53fc18; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>認証が完了しました！</h1>
                        <p>正常にトークンが取得されました。</p>
                        <p>このブラウザタブを閉じて、アプリケーションに戻ってください。</p>
                    </div>
                </body>
                </html>"""
            
            self.wfile.write(html.encode("utf-8"))
            
            # Stop the HTTP server in a separate thread so this request can finish cleanly
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

class CallbackServer(http.server.HTTPServer):
    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.received_code = None
        self.received_state = None
        self.received_error = None
        self.received_error_description = None

def get_free_port() -> int:
    """
    Finds a random free port on localhost.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def run_callback_server(expected_state: str, port: int, timeout_seconds: int = 120) -> Tuple[Optional[str], Optional[str]]:
    """
    Binds to the specified port on localhost, waits for a single /callback request,
    validates the state, and returns (auth_code, error_message).
    """
    server_address = ('127.0.0.1', port)
    
    server = CallbackServer(server_address, CallbackHandler)
    logger.info(f"Temporary OAuth Callback Server listening on http://127.0.0.1:{port}/callback")
    
    # We run the serve_forever in a daemon thread so it can time out or be aborted
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    # Wait for the thread to finish (it will call server.shutdown on request handling)
    # or time out
    server_thread.join(timeout=timeout_seconds)
    
    if server_thread.is_alive():
        logger.warning("OAuth authorization flow timed out.")
        server.shutdown()
        server_thread.join()
        return None, "Authorization timed out."
        
    if server.received_error:
        return None, f"{server.received_error}: {server.received_error_description}"
        
    if server.received_state != expected_state:
        logger.error(f"CSRF state mismatch. Expected: {expected_state}, Got: {server.received_state}")
        return None, "CSRF state validation failed."
        
    return server.received_code, None

if __name__ == "__main__":
    # Test execution
    logging.basicConfig(level=logging.INFO)
    port = get_free_port()
    code, err = run_callback_server("test_state", port, timeout_seconds=15)
    print(f"Code: {code}, Err: {err}")
