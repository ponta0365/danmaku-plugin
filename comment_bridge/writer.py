# writer.py
import os
import threading
import logging

logger = logging.getLogger("comment_bridge.writer")

class CommentWriter:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.lock = threading.Lock()
        self.ensure_directory()

    def ensure_directory(self):
        try:
            dir_name = os.path.dirname(self.file_path)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory for comments file: {e}")

    def set_file_path(self, new_path: str):
        with self.lock:
            self.file_path = new_path
            self.ensure_directory()
            logger.info(f"Comments file path updated to: {new_path}")

    def clear(self):
        with self.lock:
            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    f.truncate(0)
                logger.info(f"Cleared comments file at {self.file_path}")
            except Exception as e:
                logger.error(f"Failed to clear comments file: {e}")

    def write(self, comment_text: str):
        # Strip newlines to avoid breaking the one-comment-per-line format
        clean_text = comment_text.replace("\r", "").replace("\n", " ").strip()
        if not clean_text:
            return

        with self.lock:
            try:
                # Open with utf-8 encoding to avoid MSVC C2001-style encoding mismatches or OBS reading errors
                with open(self.file_path, "a", encoding="utf-8-sig") as f:
                    f.write(clean_text + "\n")
                    f.flush()
            except Exception as e:
                logger.error(f"Failed to write comment '{clean_text}': {e}")
