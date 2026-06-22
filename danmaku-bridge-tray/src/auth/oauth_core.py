# oauth_core.py
import os
import json
import logging
import webbrowser
import urllib.parse
import requests
import datetime
from typing import Tuple, Optional

# Import local auth modules
from src.auth.pkce import generate_code_verifier, generate_code_challenge, generate_state
from src.auth.callback_server import run_callback_server, get_free_port
from src.auth.token_store import TokenStore

logger = logging.getLogger("danmaku_bridge.oauth_core")

class OAuthCore:
    def __init__(self, providers_path: str = None, token_store: TokenStore = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        if providers_path is None:
            self.providers_path = os.path.join(base_dir, "config", "providers.json")
        else:
            self.providers_path = providers_path
            
        self.token_store = token_store if token_store else TokenStore()
        self.providers_config = self._load_providers_config()

    def _load_providers_config(self) -> dict:
        if os.path.exists(self.providers_path):
            try:
                with open(self.providers_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read providers config from {self.providers_path}: {e}")
        return {}

    def update_provider_credentials(self, provider: str, client_id: str, client_secret: str):
        """
        Updates Client ID and Client Secret in memory and saves to providers.json file.
        """
        if provider not in self.providers_config:
            self.providers_config[provider] = {}
        
        self.providers_config[provider]["client_id"] = client_id.strip()
        self.providers_config[provider]["client_secret"] = client_secret.strip()
        
        try:
            os.makedirs(os.path.dirname(self.providers_path), exist_ok=True)
            with open(self.providers_path, "w", encoding="utf-8") as f:
                json.dump(self.providers_config, f, indent=4, ensure_ascii=False)
            logger.info(f"Saved new developer credentials to providers.json for {provider}")
        except Exception as e:
            logger.error(f"Failed to write updated providers.json: {e}")

    def get_provider_credentials(self, provider: str) -> Tuple[str, str]:
        config = self.providers_config.get(provider, {})
        return config.get("client_id", ""), config.get("client_secret", "")

    def authorize_provider(self, provider: str) -> Tuple[bool, Optional[str]]:
        """
        Executes full authorization code flow with PKCE for the selected provider.
        Opens default browser for user login, starts local callback server,
        receives code, fetches user profiles, and saves encrypted tokens.
        """
        # Reload configuration to ensure we have latest client IDs
        self.providers_config = self._load_providers_config()
        config = self.providers_config.get(provider)
        if not config:
            return False, f"Provider '{provider}' config is missing."
            
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")
        
        if "YOUR_" in client_id or not client_id:
            return False, f"Please configure a valid Client ID for {provider} in Settings."

        # 1. Generate verifier, challenge, state
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        state = generate_state()

        # 2. Get free port or parse fixed port for callback server
        redirect_uri_tpl = config.get("redirect_uri", "http://127.0.0.1:{port}/callback")
        if "{port}" in redirect_uri_tpl:
            port = get_free_port()
            redirect_uri = redirect_uri_tpl.replace("{port}", str(port))
        else:
            try:
                parsed = urllib.parse.urlparse(redirect_uri_tpl)
                port = parsed.port if parsed.port else (443 if parsed.scheme == "https" else 80)
            except Exception:
                port = 8080
            redirect_uri = redirect_uri_tpl

        # 3. Construct Authorize URL
        auth_url_base = config.get("auth_url")
        scopes = config.get("scopes", [])
        scopes_str = " ".join(scopes)

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes_str,
            "state": state
        }
        
        if config.get("pkce", True):
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"

        auth_url = f"{auth_url_base}?{urllib.parse.urlencode(params)}"
        logger.info(f"OAuth URL for {provider}: {auth_url}")

        # 4. Open user browser
        webbrowser.open(auth_url)

        # 5. Run local callback server
        code, err = run_callback_server(expected_state=state, port=port, timeout_seconds=120)
        if err:
            return False, f"Callback failed: {err}"
        if not code:
            return False, "Failed to capture authorization code."

        # 6. Exchange code for token
        try:
            token_payload = {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code_verifier": verifier
            }
            token_url = config.get("token_url")
            
            logger.info(f"Exchanging auth code at {token_url}...")
            response = requests.post(token_url, data=token_payload, timeout=10)
            
            if response.status_code != 200:
                return False, f"Token exchange HTTP {response.status_code}: {response.text}"
                
            token_data = response.json()
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token", "")
            expires_in = token_data.get("expires_in", 3600)
            
            if not access_token:
                return False, "Response did not contain an access_token."

            # 7. Get User Info
            account_id, account_name = self._fetch_user_profile(provider, access_token, client_id)
            if not account_id:
                return False, f"Failed to retrieve user profile for {provider} using access token."

            # 8. Save securely using DPAPI SQLite
            self.token_store.save_tokens(
                provider=provider,
                account_id=account_id,
                account_name=account_name,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in_seconds=expires_in,
                scopes=scopes
            )
            return True, f"Successfully authenticated {provider} as user: {account_name}"
            
        except Exception as e:
            logger.error(f"Failed to authenticate: {e}")
            return False, f"OAuth process error: {str(e)}"

    def _fetch_user_profile(self, provider: str, access_token: str, client_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Calls platforms' profile APIs using the access token to get account IDs and names.
        """
        try:
            if provider == "twitch":
                headers = {
                    "Client-ID": client_id,
                    "Authorization": f"Bearer {access_token}"
                }
                res = requests.get("https://api.twitch.tv/helix/users", headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json().get("data", [])
                    if data:
                        return data[0].get("id"), data[0].get("display_name")
                        
            elif provider == "youtube":
                headers = {
                    "Authorization": f"Bearer {access_token}"
                }
                # Fetch Channel info
                res = requests.get("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", headers=headers, timeout=10)
                if res.status_code == 200:
                    items = res.json().get("items", [])
                    if items:
                        return items[0].get("id"), items[0].get("snippet", {}).get("title")
        except Exception as e:
            logger.error(f"Error fetching user profile for {provider}: {e}")
        return None, None

    def get_valid_token(self, provider: str) -> Optional[str]:
        """
        Retrieves access token from store. Auto-refreshes if expired.
        """
        tokens = self.token_store.get_tokens(provider)
        if not tokens:
            return None
            
        expires_at = tokens.get("expires_at", 0)
        now_epoch = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        
        # Refresh token if it will expire within 5 minutes (300 seconds)
        if expires_at > 0 and (expires_at - now_epoch) < 300:
            logger.info(f"Token for {provider} is expired or close to expiry. Attempting auto-refresh...")
            success = self.refresh_provider_token(provider, tokens.get("refresh_token"))
            if success:
                # Reload refreshed tokens
                tokens = self.token_store.get_tokens(provider)
                return tokens.get("access_token") if tokens else None
            else:
                logger.error(f"Failed to refresh expired token for {provider}.")
                return None
                
        return tokens.get("access_token")

    def refresh_provider_token(self, provider: str, refresh_token: str) -> bool:
        """
        Calls token refresh endpoint using refresh_token and updates secure storage.
        """
        if not refresh_token:
            logger.error(f"Cannot refresh {provider} token: No refresh token stored.")
            return False
            
        config = self.providers_config.get(provider)
        if not config:
            return False
            
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")

        try:
            refresh_payload = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
            token_url = config.get("token_url")
            
            res = requests.post(token_url, data=refresh_payload, timeout=10)
            if res.status_code == 200:
                token_data = res.json()
                new_access = token_data.get("access_token")
                # Some servers send a new refresh token, otherwise reuse old one
                new_refresh = token_data.get("refresh_token", refresh_token)
                expires_in = token_data.get("expires_in", 3600)
                
                # Fetch profiles again to verify and update username if changed
                account_id, account_name = self._fetch_user_profile(provider, new_access, client_id)
                if not account_id:
                    # Fallback to existing account details in store if API call failed temporarily
                    existing = self.token_store.get_tokens(provider)
                    account_id = existing["account_id"]
                    account_name = existing["account_name"]

                self.token_store.save_tokens(
                    provider=provider,
                    account_id=account_id,
                    account_name=account_name,
                    access_token=new_access,
                    refresh_token=new_refresh,
                    expires_in_seconds=expires_in,
                    scopes=config.get("scopes", [])
                )
                logger.info(f"Successfully refreshed and updated tokens for: {provider}")
                return True
            else:
                logger.error(f"Refresh request HTTP {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"Error refreshing token for {provider}: {e}")
        return False
