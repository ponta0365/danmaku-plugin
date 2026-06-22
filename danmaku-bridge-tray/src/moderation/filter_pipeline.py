# filter_pipeline.py
import logging
from typing import List, Set, Dict

# Import local normalize module
from src.normalize.unified_comment import UnifiedComment

logger = logging.getLogger("danmaku_bridge.filter_pipeline")

class FilterPipeline:
    def __init__(self, ng_words: List[str] = None, ng_users: List[str] = None):
        self.ng_words = set(w.lower().strip() for w in ng_words) if ng_words else set()
        self.ng_users = set(u.lower().strip() for u in ng_users) if ng_users else set()
        self.last_comments: Dict[str, str] = {} # user_name -> last_text for repeat filter

    def update_config(self, ng_words: List[str], ng_users: List[str]):
        self.ng_words = set(w.lower().strip() for w in ng_words) if ng_words else set()
        self.ng_users = set(u.lower().strip() for u in ng_users) if ng_users else set()

    def process(self, comment: UnifiedComment) -> bool:
        """
        Processes a comment through the pipeline.
        Returns True if the comment passes all filters, False if it should be dropped.
        """
        # 1. Basic validation
        if not comment or not comment.text or not comment.text.strip():
            return False

        user_name_lower = comment.user_name.lower().strip()
        text_lower = comment.text.lower()

        # 2. NG Users filter
        if user_name_lower in self.ng_users:
            logger.info(f"Comment dropped: User '{comment.user_name}' is in NG list.")
            return False

        # 3. NG Words filter
        for ng_word in self.ng_words:
            if ng_word in text_lower:
                logger.info(f"Comment dropped: Contains NG word '{ng_word}'.")
                return False

        # 4. Duplicate/Consecutive Spam filter (optional safeguard)
        last_text = self.last_comments.get(user_name_lower)
        if last_text == text_lower:
            # Drop repeated consecutive messages from the same user to prevent spam
            logger.info(f"Comment dropped: Consecutive repeated spam from '{comment.user_name}'.")
            return False
            
        self.last_comments[user_name_lower] = text_lower
        
        # Limit cache size
        if len(self.last_comments) > 1000:
            # Pop the oldest items
            first_key = next(iter(self.last_comments))
            self.last_comments.pop(first_key)

        return True
