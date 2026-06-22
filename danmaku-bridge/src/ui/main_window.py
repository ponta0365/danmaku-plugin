# main_window.py
import sys
import os

# Fix sys.path for direct execution to find the 'src' directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import logging
import datetime
import threading
import asyncio
from PySide6.QtCore import Qt, QObject, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QTextEdit, QCheckBox, QComboBox,
    QGroupBox, QFormLayout, QSplitter, QMessageBox, QTabWidget
)
from PySide6.QtGui import QFont, QColor, QTextCursor

# Import local modules
from src.auth.token_store import TokenStore
from src.auth.oauth_core import OAuthCore
from src.normalize.unified_comment import UnifiedComment
from src.providers.twitch_adapter import TwitchAdapter
from src.providers.youtube_adapter import YouTubeAdapter
from src.providers.obs_listener import ObsListener
from src.output.text_writer import TextWriter
from src.output.jsonl_writer import JsonlWriter
from src.moderation.filter_pipeline import FilterPipeline

logger = logging.getLogger("danmaku_bridge.ui")

class BridgeSignals(QObject):
    comment_received = Signal(object)  # Emits UnifiedComment
    status_changed = Signal(str, bool) # Emits (platform, is_running)
    log_emitted = Signal(str, str)     # Emits (message, level)
    stats_updated = Signal(float, int) # Emits (file_size_kb, total_count)
    obs_stream_active = Signal(bool)
    obs_connection_changed = Signal(bool)

class QtLogHandler(logging.Handler):
    """
    Custom log handler that forwards python logging calls to a Qt Signal
    so they can be safely displayed in the UI console log.
    """
    def __init__(self, signals: BridgeSignals):
        super().__init__()
        self.signals = signals

    def emit(self, record):
        msg = self.format(record)
        self.signals.log_emitted.emit(msg, record.levelname)

class AsyncioLoopThread(threading.Thread):
    """
    Separate thread that runs the Asyncio event loop
    so EventSub WebSockets and API polling run smoothly in the background.
    """
    def __init__(self):
        super().__init__(daemon=True)
        self.loop = None
        self.started_event = threading.Event()

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        logger.info("Background Asyncio event loop started.")
        self.started_event.set()
        self.loop.run_forever()

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
            logger.info("Background Asyncio event loop stopped.")

    def run_coro(self, coro):
        # Block momentarily until the background event loop is fully initialized
        self.started_event.wait()
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OBS Danmaku OAuth Bridge")
        self.resize(1100, 700)
        
        # Paths Setup
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.settings_path = os.path.join(self.base_dir, "config", "settings.json")
        self.providers_path = os.path.join(self.base_dir, "config", "providers.json")

        self.signals = BridgeSignals()
        self._setup_logger()
        
        # Initialize Backend Components
        self.token_store = TokenStore()
        self.oauth_core = OAuthCore(token_store=self.token_store)
        self.settings = self._load_settings()
        
        # Configure output writers and filter pipeline
        self.filter_pipeline = FilterPipeline(
            ng_words=self.settings.get("moderation", {}).get("ng_words", []),
            ng_users=self.settings.get("moderation", {}).get("ng_users", [])
        )
        self.text_writer = TextWriter(
            file_path=self.settings.get("output", {}).get("text_path", "K:\\obs_comments.txt"),
            format_config=self.settings.get("output", {})
        )
        self.jsonl_writer = JsonlWriter(
            file_path=self.settings.get("output", {}).get("jsonl_path", "K:\\obs_comments.jsonl")
        )
        
        # Runtime states
        self.total_comments_received = 0
        self.is_capturing = False
        self.obs_stream_active_state = False
        
        # Setup Asyncio Background Thread
        self.async_thread = AsyncioLoopThread()
        self.async_thread.start()
        
        # Clients dictionary
        self.twitch_adapter = None
        self.youtube_adapter = None

        # Setup OBS Listener
        obs_cfg = self.settings.get("obs_link", {})
        self.obs_listener = ObsListener(
            port=obs_cfg.get("port", 4455),
            password=obs_cfg.get("password", ""),
            on_stream_state_change=self._async_obs_stream_callback,
            on_connection_state_change=self._async_obs_conn_callback
        )
        self.async_thread.run_coro(self.obs_listener.start())

        # Build UI layout
        self._init_ui()
        self._apply_dark_theme()
        
        # Connect Qt Signals
        self.signals.comment_received.connect(self.on_comment_received)
        self.signals.log_emitted.connect(self.on_log_emitted)
        self.signals.stats_updated.connect(self.on_stats_updated)
        self.signals.status_changed.connect(self.on_status_changed)
        self.signals.obs_stream_active.connect(self.on_obs_stream_active)
        self.signals.obs_connection_changed.connect(self.on_obs_connection_changed)
        
        # Load details into GUI
        self.load_settings_to_ui()
        self.update_account_display()

    def _setup_logger(self):
        # Create logs directory and log file handler
        log_dir = os.path.join(self.base_dir, "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "danmaku-bridge.log")
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
            logging.getLogger().addHandler(file_handler)
        except Exception as e:
            # Fallback if logs directory cannot be created
            print(f"Failed to create file log handler: {e}")

        # Route local log events to the Qt Log Console
        handler = QtLogHandler(self.signals)
        handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%H:%M:%S'))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

    def _load_settings(self) -> dict:
        default_text_path = "K:\\obs_comments.txt" if os.path.exists("K:\\") else os.path.join(self.base_dir, "danmaku_input.txt")
        default_jsonl_path = "K:\\obs_comments.jsonl" if os.path.exists("K:\\") else os.path.join(self.base_dir, "danmaku_input.jsonl")
        
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read settings.json: {e}")
                
        return {
            "output": {
                "mode": "text",
                "text_path": default_text_path,
                "jsonl_path": default_jsonl_path,
                "include_user_name": False,
                "include_platform": False,
                "max_text_length": 80
            },
            "moderation": {
                "ng_words": [],
                "ng_users": []
            },
            "obs_link": {
                "enabled": False,
                "port": 4455,
                "password": ""
            }
        }

    def save_settings(self):
        # Collect values from UI
        self.settings["output"]["mode"] = self.mode_select.currentText().lower()
        self.settings["output"]["text_path"] = self.text_path_input.text().strip()
        self.settings["output"]["jsonl_path"] = self.jsonl_path_input.text().strip()
        self.settings["output"]["include_user_name"] = self.chk_username.isChecked()
        self.settings["output"]["include_platform"] = self.chk_platform.isChecked()
        
        try:
            self.settings["output"]["max_text_length"] = int(self.max_len_input.text())
        except ValueError:
            self.settings["output"]["max_text_length"] = 80

        # Moderation settings
        words = [w.strip() for w in self.ng_words_input.toPlainText().split(",") if w.strip()]
        users = [u.strip() for u in self.ng_users_input.toPlainText().split(",") if u.strip()]
        self.settings["moderation"]["ng_words"] = words
        self.settings["moderation"]["ng_users"] = users

        # OBS Settings
        try:
            obs_port = int(self.obs_port_input.text().strip())
        except ValueError:
            obs_port = 4455
            
        self.settings["obs_link"] = {
            "enabled": self.chk_obs_sync.isChecked(),
            "port": obs_port,
            "password": self.obs_pw_input.text()
        }

        try:
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
            
            # Sync configuration to writers, pipeline and listener
            self.filter_pipeline.update_config(words, users)
            self.text_writer.update_config(self.settings["output"]["text_path"], self.settings["output"])
            self.jsonl_writer.update_config(self.settings["output"]["jsonl_path"])
            
            # Update background OBS WebSocket config
            self.obs_listener.update_config(obs_port, self.obs_pw_input.text())
            
            logger.info("Saved local settings to settings.json")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")

    def load_settings_to_ui(self):
        out = self.settings.get("output", {})
        self.text_path_input.setText(out.get("text_path", ""))
        self.jsonl_path_input.setText(out.get("jsonl_path", ""))
        
        mode = out.get("mode", "text")
        idx = self.mode_select.findText(mode.upper())
        if idx >= 0:
            self.mode_select.setCurrentIndex(idx)
            
        self.chk_username.setChecked(out.get("include_user_name", False))
        self.chk_platform.setChecked(out.get("include_platform", False))
        self.max_len_input.setText(str(out.get("max_text_length", 80)))

        mod = self.settings.get("moderation", {})
        self.ng_words_input.setPlainText(", ".join(mod.get("ng_words", [])))
        self.ng_users_input.setPlainText(", ".join(mod.get("ng_users", [])))

        # Load OBS link settings
        obs_cfg = self.settings.get("obs_link", {})
        self.chk_obs_sync.setChecked(obs_cfg.get("enabled", False))
        self.obs_port_input.setText(str(obs_cfg.get("port", 4455)))
        self.obs_pw_input.setText(obs_cfg.get("password", ""))

        # Load API credentials to Settings inputs
        t_id, t_sec = self.oauth_core.get_provider_credentials("twitch")
        g_id, g_sec = self.oauth_core.get_provider_credentials("youtube")
        
        self.twitch_client_id.setText(t_id)
        self.twitch_client_secret.setText(t_sec)
        self.youtube_client_id.setText(g_id)
        self.youtube_client_secret.setText(g_sec)

    def _init_ui(self):
        # Main central widget
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Splitter to resize panels
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # ------------------ LEFT SIDEBAR: Config & Authentication ------------------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        tabs = QTabWidget()
        left_layout.addWidget(tabs)
        
        # TAB 1: OAuth & Account Manager
        account_tab = QWidget()
        account_layout = QVBoxLayout(account_tab)
        
        # Twitch Account Group
        twitch_group = QGroupBox("Twitch Authentication")
        twitch_g_layout = QFormLayout(twitch_group)
        self.lbl_twitch_status = QLabel("State: Disconnected ⚪")
        self.btn_twitch_login = QPushButton("Login Twitch")
        self.btn_twitch_login.clicked.connect(lambda: self.on_oauth_login_click("twitch"))
        self.btn_twitch_logout = QPushButton("Disconnect")
        self.btn_twitch_logout.clicked.connect(lambda: self.on_oauth_logout_click("twitch"))
        
        twitch_g_layout.addRow(self.lbl_twitch_status)
        twitch_h_btn = QHBoxLayout()
        twitch_h_btn.addWidget(self.btn_twitch_login)
        twitch_h_btn.addWidget(self.btn_twitch_logout)
        twitch_g_layout.addRow(twitch_h_btn)
        account_layout.addWidget(twitch_group)

        # YouTube Account Group
        youtube_group = QGroupBox("YouTube Authentication")
        youtube_g_layout = QFormLayout(youtube_group)
        self.lbl_youtube_status = QLabel("State: Disconnected ⚪")
        self.btn_youtube_login = QPushButton("Login YouTube")
        self.btn_youtube_login.clicked.connect(lambda: self.on_oauth_login_click("youtube"))
        self.btn_youtube_logout = QPushButton("Disconnect")
        self.btn_youtube_logout.clicked.connect(lambda: self.on_oauth_logout_click("youtube"))
        
        youtube_g_layout.addRow(self.lbl_youtube_status)
        youtube_h_btn = QHBoxLayout()
        youtube_h_btn.addWidget(self.btn_youtube_login)
        youtube_h_btn.addWidget(self.btn_youtube_logout)
        youtube_g_layout.addRow(youtube_h_btn)
        account_layout.addWidget(youtube_group)
        
        account_layout.addStretch()
        tabs.addTab(account_tab, "Accounts")

        # TAB 2: Settings Panel
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        
        output_group = QGroupBox("Output Settings")
        output_g_layout = QFormLayout(output_group)
        
        self.mode_select = QComboBox()
        self.mode_select.addItems(["TEXT", "JSONL"])
        
        self.text_path_input = QLineEdit()
        self.jsonl_path_input = QLineEdit()
        self.chk_username = QCheckBox("Show username in text format")
        self.chk_platform = QCheckBox("Show platform in text format")
        self.max_len_input = QLineEdit()
        
        output_g_layout.addRow("Output Format:", self.mode_select)
        output_g_layout.addRow("Text Path:", self.text_path_input)
        output_g_layout.addRow("JSONL Path:", self.jsonl_path_input)
        output_g_layout.addRow(self.chk_username)
        output_g_layout.addRow(self.chk_platform)
        output_g_layout.addRow("Max Text Length:", self.max_len_input)
        settings_layout.addWidget(output_group)

        # Moderation Panel
        mod_group = QGroupBox("Moderation & Filters")
        mod_g_layout = QVBoxLayout(mod_group)
        mod_g_layout.addWidget(QLabel("NG Word List (comma-separated):"))
        self.ng_words_input = QTextEdit()
        self.ng_words_input.setMaximumHeight(60)
        mod_g_layout.addWidget(self.ng_words_input)
        
        mod_g_layout.addWidget(QLabel("NG User List (comma-separated):"))
        self.ng_users_input = QTextEdit()
        self.ng_users_input.setMaximumHeight(60)
        mod_g_layout.addWidget(self.ng_users_input)
        settings_layout.addWidget(mod_group)

        # OBS WebSocket Sync settings
        obs_group = QGroupBox("OBS Streaming Sync")
        obs_g_layout = QFormLayout(obs_group)
        self.chk_obs_sync = QCheckBox("Sync comment acquisition with OBS Stream")
        self.obs_port_input = QLineEdit()
        self.obs_port_input.setPlaceholderText("4455")
        self.obs_pw_input = QLineEdit()
        self.obs_pw_input.setEchoMode(QLineEdit.Password)
        self.lbl_obs_conn_status = QLabel("OBS Connection: Disconnected ⚪")
        
        obs_g_layout.addRow(self.chk_obs_sync)
        obs_g_layout.addRow("OBS WebSocket Port:", self.obs_port_input)
        obs_g_layout.addRow("OBS WebSocket Password:", self.obs_pw_input)
        obs_g_layout.addRow(self.lbl_obs_conn_status)
        settings_layout.addWidget(obs_group)

        btn_save_settings = QPushButton("Save Settings")
        btn_save_settings.clicked.connect(self.save_settings)
        settings_layout.addWidget(btn_save_settings)
        tabs.addTab(settings_tab, "Output & Filter")

        # TAB 3: Developer Settings (Credentials Editor)
        dev_tab = QWidget()
        dev_layout = QVBoxLayout(dev_tab)
        dev_group = QGroupBox("Developer API Credentials")
        dev_g_layout = QFormLayout(dev_group)
        
        self.twitch_client_id = QLineEdit()
        self.twitch_client_secret = QLineEdit()
        self.twitch_client_secret.setEchoMode(QLineEdit.Password)
        self.youtube_client_id = QLineEdit()
        self.youtube_client_secret = QLineEdit()
        self.youtube_client_secret.setEchoMode(QLineEdit.Password)
        
        dev_g_layout.addRow("Twitch Client ID:", self.twitch_client_id)
        dev_g_layout.addRow("Twitch Client Secret:", self.twitch_client_secret)
        dev_g_layout.addRow("Google Client ID:", self.youtube_client_id)
        dev_g_layout.addRow("Google Client Secret:", self.youtube_client_secret)
        dev_layout.addWidget(dev_group)
        
        btn_save_credentials = QPushButton("Save Developer Keys")
        btn_save_credentials.clicked.connect(self.save_credentials)
        dev_layout.addWidget(btn_save_credentials)
        dev_layout.addStretch()
        tabs.addTab(dev_tab, "Developer Credentials")

        splitter.addWidget(left_widget)

        # ------------------ RIGHT PANEL: Console & Telemetry ------------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Telemetry Labels
        stats_layout = QHBoxLayout()
        self.lbl_file_size = QLabel("File Size: 0.00 KB")
        self.lbl_comment_count = QLabel("Total Bridged Comments: 0")
        stats_layout.addWidget(self.lbl_file_size)
        stats_layout.addStretch()
        stats_layout.addWidget(self.lbl_comment_count)
        right_layout.addLayout(stats_layout)

        # Tabs for Live Console vs Logs Console
        console_tabs = QTabWidget()
        right_layout.addWidget(console_tabs)

        # Live Chat Console
        self.chat_console = QTextEdit()
        self.chat_console.setReadOnly(True)
        self.chat_console.setFont(QFont("Consolas", 10))
        console_tabs.addTab(self.chat_console, "Live Comments Stream")

        # System Logs Console
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFont(QFont("Consolas", 9))
        console_tabs.addTab(self.log_console, "System Logs")

        # Execution Controls
        control_layout = QHBoxLayout()
        self.btn_start = QPushButton("START ACQUISITION")
        self.btn_start.clicked.connect(self.start_acquisition)
        self.btn_stop = QPushButton("STOP ACQUISITION")
        self.btn_stop.clicked.connect(self.stop_acquisition)
        self.btn_stop.setEnabled(False)
        
        control_layout.addWidget(self.btn_start)
        control_layout.addWidget(self.btn_stop)
        right_layout.addLayout(control_layout)

        splitter.addWidget(right_widget)
        
        # Set panel proportions (35% left, 65% right)
        splitter.setSizes([380, 720])

    def _apply_dark_theme(self):
        stylesheet = """
        QMainWindow {
            background-color: #0d0d13;
        }
        QWidget {
            color: #efeff1;
            font-size: 13px;
        }
        QGroupBox {
            font-weight: bold;
            border: 1px solid #2d2d3a;
            border-radius: 8px;
            margin-top: 12px;
            padding-top: 12px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0px 5px 0px 5px;
        }
        QTabWidget::pane {
            border: 1px solid #2d2d3a;
            border-radius: 8px;
            background: #15151e;
        }
        QTabBar::tab {
            background: #20202b;
            border: 1px solid #2d2d3a;
            padding: 8px 16px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }
        QTabBar::tab:selected {
            background: #15151e;
            border-bottom-color: #15151e;
        }
        QLineEdit, QTextEdit {
            background-color: #20202b;
            border: 1px solid #2d2d3a;
            border-radius: 6px;
            padding: 5px;
            color: #f3f4f6;
        }
        QLineEdit:focus, QTextEdit:focus {
            border: 1px solid #5f5feb;
        }
        QComboBox {
            background-color: #20202b;
            border: 1px solid #2d2d3a;
            border-radius: 6px;
            padding: 5px;
            min-width: 80px;
        }
        QPushButton {
            background-color: #3b3b4f;
            border: 1px solid #4f4f6f;
            border-radius: 6px;
            padding: 6px 12px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #4a4a68;
        }
        QPushButton:pressed {
            background-color: #2b2b3f;
        }
        QPushButton#btn_start {
            background-color: #1b8a1b;
            border-color: #2cba2c;
            color: white;
        }
        QPushButton#btn_start:hover {
            background-color: #25aa25;
        }
        QPushButton#btn_stop {
            background-color: #aa1b1b;
            border-color: #ba2c2c;
            color: white;
        }
        QPushButton#btn_stop:hover {
            background-color: #ca2525;
        }
        """
        self.setStyleSheet(stylesheet)
        self.btn_start.setObjectName("btn_start")
        self.btn_stop.setObjectName("btn_stop")

    def save_credentials(self):
        # Save Twitch
        self.oauth_core.update_provider_credentials(
            "twitch",
            self.twitch_client_id.text(),
            self.twitch_client_secret.text()
        )
        # Save YouTube
        self.oauth_core.update_provider_credentials(
            "youtube",
            self.youtube_client_id.text(),
            self.youtube_client_secret.text()
        )
        QMessageBox.information(self, "Settings Saved", "Developer credentials have been securely saved.")

    def update_account_display(self):
        # Check Twitch
        t_tokens = self.token_store.get_tokens("twitch")
        if t_tokens:
            self.lbl_twitch_status.setText(f"Connected: {t_tokens.get('account_name')} 🟢")
            self.btn_twitch_login.setText("Re-Login")
        else:
            self.lbl_twitch_status.setText("State: Disconnected ⚪")
            self.btn_twitch_login.setText("Login Twitch")

        # Check YouTube
        y_tokens = self.token_store.get_tokens("youtube")
        if y_tokens:
            self.lbl_youtube_status.setText(f"Connected: {y_tokens.get('account_name')} 🟢")
            self.btn_youtube_login.setText("Re-Login")
        else:
            self.lbl_youtube_status.setText("State: Disconnected ⚪")
            self.btn_youtube_login.setText("Login YouTube")

    def on_oauth_login_click(self, provider: str):
        # Trigger OAuth flow in a background thread to prevent GUI lockup
        def run_auth():
            logger.info(f"Initiating login sequence for: {provider}...")
            try:
                success, msg = self.oauth_core.authorize_provider(provider)
                if success:
                    logger.info(f"Authorization successful: {msg}")
                else:
                    logger.error(f"Authorization failed: {msg}")
            except Exception as e:
                logger.error(f"Authorization failed with unhandled exception: {e}", exc_info=True)
                success, msg = False, str(e)
            
            # Request UI updates on the main Qt Thread
            self.signals.status_changed.emit(provider, success)

        threading.Thread(target=run_auth, daemon=True).start()

    def on_oauth_logout_click(self, provider: str):
        try:
            self.token_store.delete_tokens(provider)
            logger.info(f"Disconnected and removed access keys for {provider}.")
            self.update_account_display()
        except Exception as e:
            logger.error(f"Failed to clear credentials for {provider}: {e}")

    @Slot(str, bool)
    def on_status_changed(self, provider: str, success: bool):
        self.update_account_display()
        if success:
            QMessageBox.information(self, "Authorization Successful", f"Authenticated successfully with {provider.upper()}!")
        else:
            QMessageBox.critical(self, "Authorization Failed", f"OAuth authentication flow failed for {provider.upper()}. Check Developer Credentials or timeout constraints.")

    # --- Comment Bridging Execution Logic ---
    def start_acquisition(self):
        # Save GUI inputs first
        self.save_settings()
        
        # Reset counters
        self.total_comments_received = 0
        self.lbl_comment_count.setText("Total Bridged Comments: 0")
        self.chat_console.clear()
        
        # Clear/truncate target files before starting stream session
        self.text_writer.clear()
        self.jsonl_writer.clear()

        # Instantiate adapters passing loop and core
        self.twitch_adapter = TwitchAdapter(self.oauth_core, on_comment_cb=self._async_comment_callback)
        self.youtube_adapter = YouTubeAdapter(self.oauth_core, on_comment_cb=self._async_comment_callback)

        logger.info("Initializing adapters...")
        
        # Start adapters inside the background asyncio loop thread
        twitch_enabled = self.settings.get("providers", {}).get("twitch", {}).get("enabled", True)
        youtube_enabled = self.settings.get("providers", {}).get("youtube", {}).get("enabled", True)
        
        any_started = False
        
        if twitch_enabled:
            try:
                self.async_thread.run_coro(self.twitch_adapter.start())
                any_started = True
            except Exception as e:
                logger.error(f"Failed to start Twitch adapter: {e}")

        if youtube_enabled:
            try:
                self.async_thread.run_coro(self.youtube_adapter.start())
                any_started = True
            except Exception as e:
                logger.error(f"Failed to start YouTube adapter: {e}")

        if any_started:
            self.is_capturing = True
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            logger.info("Comment Acquisition Started.")
        else:
            logger.error("No active adapters were started. Ensure platforms are authenticated.")

    def stop_acquisition(self):
        # Stop adapters in background loop thread
        if self.twitch_adapter:
            self.async_thread.run_coro(self.twitch_adapter.stop())
            self.twitch_adapter = None
        if self.youtube_adapter:
            self.async_thread.run_coro(self.youtube_adapter.stop())
            self.youtube_adapter = None

        self.is_capturing = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        logger.info("Comment Acquisition Stopped.")

    def _async_comment_callback(self, comment: UnifiedComment):
        """
        Thread-safe callback triggered on the Asyncio background thread.
        Sends the comment to Qt event pipeline.
        """
        self.signals.comment_received.emit(comment)

    @Slot(object)
    def on_comment_received(self, comment: UnifiedComment):
        # Run comment through moderation pipeline
        if not self.filter_pipeline.process(comment):
            return

        # Write to files depending on configuration
        mode = self.settings.get("output", {}).get("mode", "text")
        if mode == "text":
            self.text_writer.write_comment(comment)
        elif mode == "jsonl":
            self.jsonl_writer.write_comment(comment)

        # Update stats
        self.total_comments_received += 1
        
        # Calculate file size of target file
        file_size_kb = 0.0
        path = self.settings["output"]["text_path"] if mode == "text" else self.settings["output"]["jsonl_path"]
        if os.path.exists(path):
            try:
                file_size_kb = os.path.getsize(path) / 1024.0
            except Exception:
                pass
                
        self.signals.stats_updated.emit(file_size_kb, self.total_comments_received)

        # Show in chat console
        self._append_to_chat_console(comment)

    def _append_to_chat_console(self, comment: UnifiedComment):
        # Format HTML message based on provider with colors
        color_hex = "#9146ff" if comment.platform == "twitch" else "#ff0033"
        platform_text = "Twitch" if comment.platform == "twitch" else "YouTube"
        
        html = f"""
        <div style="margin-bottom: 4px;">
            <span style="background-color: {color_hex}; color: white; padding: 2px 4px; border-radius: 4px; font-weight: bold; font-size: 11px;">
                {platform_text}
            </span>
            <span style="font-weight: bold; color: #f3f4f6;">{comment.user_name}: </span>
            <span style="color: #d1d5db;">{comment.text}</span>
        </div>
        """
        
        self.chat_console.append(html)
        # Auto-scroll to bottom
        self.chat_console.moveCursor(QTextCursor.End)

    @Slot(float, int)
    def on_stats_updated(self, size_kb: float, count: int):
        self.lbl_file_size.setText(f"File Size: {size_kb:.2f} KB")
        self.lbl_comment_count.setText(f"Total Bridged Comments: {count}")

    @Slot(str, str)
    def on_log_emitted(self, message: str, level: str):
        # Format logs with color codes depending on logging severity level
        color = "#e5e7eb" # Gray
        if level == "WARNING":
            color = "#f59e0b" # Orange
        elif level == "ERROR":
            color = "#ef4444" # Red
        elif level == "DEBUG":
            color = "#9ca3af" # Dark Gray
            
        html = f'<div style="color: {color}; margin-bottom: 2px;">{message}</div>'
        self.log_console.append(html)
        self.log_console.moveCursor(QTextCursor.End)

    def _async_obs_stream_callback(self, active: bool):
        self.signals.obs_stream_active.emit(active)
        
    def _async_obs_conn_callback(self, connected: bool):
        self.signals.obs_connection_changed.emit(connected)

    @Slot(bool)
    def on_obs_stream_active(self, active: bool):
        self.obs_stream_active_state = active
        sync_enabled = self.settings.get("obs_link", {}).get("enabled", False)
        if not sync_enabled:
            return
            
        if active and not self.is_capturing:
            logger.info("OBS stream started. Automatically starting comment acquisition...")
            self.start_acquisition()
        elif not active and self.is_capturing:
            logger.info("OBS stream stopped. Automatically stopping comment acquisition...")
            self.stop_acquisition()
            
    @Slot(bool)
    def on_obs_connection_changed(self, connected: bool):
        if connected:
            self.lbl_obs_conn_status.setText("OBS Connection: Connected 🟢")
            logger.info("Successfully connected to OBS Studio WebSocket server.")
        else:
            self.lbl_obs_conn_status.setText("OBS Connection: Disconnected ⚪")
            logger.info("Disconnected from OBS Studio WebSocket server.")

    def closeEvent(self, event):
        # Ensure loops and background threads terminate cleanly on close
        self.stop_acquisition()
        if hasattr(self, 'obs_listener'):
            # Run stop cleanly inside async thread loop before shutdown
            self.async_thread.run_coro(self.obs_listener.stop())
        self.async_thread.stop()
        self.async_thread.join(timeout=5)
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
