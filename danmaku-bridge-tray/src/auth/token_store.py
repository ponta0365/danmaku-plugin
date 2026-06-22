# token_store.py
import os
import sqlite3
import datetime
import logging
import win32crypt

logger = logging.getLogger("danmaku_bridge.token_store")

class TokenStore:
    def __init__(self, db_path: str = None):
        if db_path is None:
            # Place it in ../../data/token_store.db relative to this file
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            data_dir = os.path.join(base_dir, "data")
            os.makedirs(data_dir, exist_ok=True)
            self.db_path = os.path.join(data_dir, "token_store.db")
        else:
            self.db_path = db_path
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS oauth_tokens (
                        provider TEXT PRIMARY KEY,
                        account_id TEXT,
                        account_name TEXT,
                        access_token BLOB,
                        refresh_token BLOB,
                        expires_at INTEGER,
                        scopes TEXT,
                        updated_at TEXT
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize SQLite token database: {e}")

    def _encrypt(self, text: str) -> bytes:
        if not text:
            return b""
        try:
            # win32crypt.CryptProtectData takes bytes and returns bytes
            data_bytes = text.encode("utf-8")
            # CryptProtectData(data_in, description, entropy, reserved, prompt, flags)
            # Flag 1 is CRYPTPROTECT_UI_FORBIDDEN (prevent prompt UI dialogs)
            return win32crypt.CryptProtectData(data_bytes, None, None, None, None, 1)
        except Exception as e:
            logger.error(f"DPAPI encryption failure: {e}")
            raise

    def _decrypt(self, cipher_bytes: bytes) -> str:
        if not cipher_bytes:
            return ""
        try:
            # win32crypt.CryptUnprotectData returns (description, decrypted_data_bytes)
            _, decrypted_bytes = win32crypt.CryptUnprotectData(cipher_bytes, None, None, None, 1)
            return decrypted_bytes.decode("utf-8")
        except Exception as e:
            logger.error(f"DPAPI decryption failure: {e}")
            raise

    def save_tokens(self, provider: str, account_id: str, account_name: str, 
                    access_token: str, refresh_token: str, expires_in_seconds: int, scopes: list):
        """
        Encrypts tokens with Windows DPAPI and stores them securely in the SQLite database.
        """
        try:
            enc_access = self._encrypt(access_token)
            enc_refresh = self._encrypt(refresh_token)
            
            # Compute expires_at (absolute epoch timestamp)
            now = datetime.datetime.now(datetime.timezone.utc)
            expires_at = int(now.timestamp() + expires_in_seconds) if expires_in_seconds else 0
            
            scopes_str = ",".join(scopes) if isinstance(scopes, list) else (scopes or "")
            updated_at_str = datetime.datetime.now().isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO oauth_tokens (provider, account_id, account_name, access_token, refresh_token, expires_at, scopes, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider) DO UPDATE SET
                        account_id=excluded.account_id,
                        account_name=excluded.account_name,
                        access_token=excluded.access_token,
                        refresh_token=excluded.refresh_token,
                        expires_at=excluded.expires_at,
                        scopes=excluded.scopes,
                        updated_at=excluded.updated_at
                """, (provider, account_id, account_name, enc_access, enc_refresh, expires_at, scopes_str, updated_at_str))
                conn.commit()
            logger.info(f"Successfully stored secure tokens in DB for provider: {provider}")
        except Exception as e:
            logger.error(f"Failed to store tokens in secure database: {e}")
            raise

    def get_tokens(self, provider: str) -> dict:
        """
        Retrieves, decrypts, and returns credentials for the specified provider.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM oauth_tokens WHERE provider = ?", (provider,))
                row = cursor.fetchone()
                
            if not row:
                return None
                
            decrypted_access = self._decrypt(row["access_token"])
            decrypted_refresh = self._decrypt(row["refresh_token"])
            
            scopes = row["scopes"].split(",") if row["scopes"] else []
            
            return {
                "provider": row["provider"],
                "account_id": row["account_id"],
                "account_name": row["account_name"],
                "access_token": decrypted_access,
                "refresh_token": decrypted_refresh,
                "expires_at": row["expires_at"],
                "scopes": scopes,
                "updated_at": row["updated_at"]
            }
        except Exception as e:
            logger.error(f"Failed to retrieve/decrypt tokens from database: {e}")
            return None

    def delete_tokens(self, provider: str):
        """
        Deletes credentials for the specified provider from SQLite.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM oauth_tokens WHERE provider = ?", (provider,))
                conn.commit()
            logger.info(f"Successfully deleted secure tokens from DB for provider: {provider}")
        except Exception as e:
            logger.error(f"Failed to delete tokens from secure database: {e}")
            raise
