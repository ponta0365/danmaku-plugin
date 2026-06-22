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
from PySide6.QtCore import Qt, QObject, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QTextEdit, QCheckBox, QComboBox,
    QGroupBox, QFormLayout, QSplitter, QMessageBox, QTabWidget,
    QSystemTrayIcon, QMenu, QStyle, QScrollArea, QFrame
)
from PySide6.QtGui import QFont, QColor, QTextCursor, QIcon, QAction

# Import local modules
import win32com.client
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
    account_display_updated = Signal()

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
        self.signals.account_display_updated.connect(self.update_account_display)
        
        # Load details into GUI
        self.load_settings_to_ui()
        self.update_account_display()
        
        # No system tray icon setup - standard desktop window behavior

        # Automatically clean up legacy Windows Startup shortcut if present
        try:
            shortcut_path = self._get_startup_shortcut_path()
            if os.path.exists(shortcut_path):
                os.remove(shortcut_path)
                logger.info("Automatically cleaned up legacy Windows Startup shortcut.")
        except Exception as e:
            logger.warning(f"Could not remove legacy startup shortcut: {e}")

        # Start file-based OBS sync polling timer
        self.obs_file_sync_timer = QTimer(self)
        self.obs_file_sync_timer.timeout.connect(self.check_obs_file_state)
        self.obs_file_sync_timer.start(1000) # Check every 1 second

        # Start diagnostics check timer
        self.diag_timer = QTimer(self)
        self.diag_timer.timeout.connect(self.run_diagnostics)
        self.diag_timer.start(2000) # Check every 2 seconds
        self.run_diagnostics() # Run initial check immediately

        # State tracking for OBS process auto-close
        self.obs_was_seen_running = False

        # Verify stored sessions and auto-refresh expired tokens on startup
        self.async_thread.run_coro(self.verify_tokens_on_startup())

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
            },
            "auto_launch_on_obs_start": False
        }

    def run_diagnostics(self):
        # 1. Twitch Auth Check
        t_tokens = self.token_store.get_tokens("twitch")
        if t_tokens:
            self.lbl_diag_twitch.setText(f"Twitch Auth: Connected ({t_tokens.get('account_name')}) 🟢")
            twitch_ok = True
        else:
            self.lbl_diag_twitch.setText("Twitch Auth: Not Connected 🟡")
            twitch_ok = False

        # 2. YouTube Auth Check
        y_tokens = self.token_store.get_tokens("youtube")
        if y_tokens:
            self.lbl_diag_youtube.setText(f"YouTube Auth: Connected ({y_tokens.get('account_name')}) 🟢")
            youtube_ok = True
        else:
            self.lbl_diag_youtube.setText("YouTube Auth: Not Connected 🟡")
            youtube_ok = False

        # At least one platform must be connected
        platform_ok = twitch_ok or youtube_ok

        # 3. OBS Connection Check
        obs_ws_sync = self.chk_obs_sync.isChecked()
        obs_file_sync = self.chk_obs_sync_file.isChecked()
        obs_ok = False

        if obs_ws_sync:
            ws_conn = "Connected" in self.lbl_obs_conn_status.text()
            if ws_conn:
                self.lbl_diag_obs.setText("OBS Studio Link: Connected (WebSocket) 🟢")
                obs_ok = True
            else:
                self.lbl_diag_obs.setText("OBS Studio Link: Disconnected (WebSocket) 🔴")
                obs_ok = False
        elif obs_file_sync:
            self.lbl_diag_obs.setText("OBS Studio Link: Enabled (File-based Sync) 🟢")
            obs_ok = True
        else:
            self.lbl_diag_obs.setText("OBS Studio Link: Disabled (Manual Mode) 🟡")
            obs_ok = True

        # 4. Output Path Check
        mode = self.mode_select.currentText().lower()
        path = self.text_path_input.text().strip() if mode == "text" else self.jsonl_path_input.text().strip()
        write_ok = False

        if path:
            try:
                parent_dir = os.path.dirname(path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    pass
                self.lbl_diag_write.setText(f"Output Path: Writable ({os.path.basename(path)}) 🟢")
                write_ok = True
            except Exception as e:
                self.lbl_diag_write.setText(f"Output Path: Unwritable 🔴")
                write_ok = False
        else:
            self.lbl_diag_write.setText("Output Path: Not Specified 🔴")
            write_ok = False

        # Determine overall result
        if platform_ok and obs_ok and write_ok:
            if not hasattr(self, '_diag_last_all_ok') or not self._diag_last_all_ok:
                self._diag_last_all_ok = True
                logger.info("System Health Diagnostics: ALL OK!")
            self.lbl_diag_result.setText("Inspection Result: ALL OK 🟢")
            self.lbl_diag_result.setStyleSheet("font-weight: bold; font-size: 14px; color: #2cba2c;")
        else:
            self._diag_last_all_ok = False
            reasons = []
            if not platform_ok:
                reasons.append("No Accounts Logged In")
            if not obs_ok:
                reasons.append("OBS Disconnected")
            if not write_ok:
                reasons.append("Output Unwritable")
            
            reason_str = ", ".join(reasons) if reasons else "Pending Verification"
            self.lbl_diag_result.setText(f"Inspection Result: Pending ({reason_str}) 🟡")
            self.lbl_diag_result.setStyleSheet("font-weight: bold; font-size: 12px; color: #f59e0b;")

    def save_settings(self, *args):
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
            "password": self.obs_pw_input.text(),
            "file_sync_enabled": self.chk_obs_sync_file.isChecked()
        }
        self.settings["auto_launch_on_obs_start"] = self.chk_auto_launch.isChecked()

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
            self.run_diagnostics()
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
        self.chk_obs_sync_file.setChecked(obs_cfg.get("file_sync_enabled", False))
        self.chk_auto_launch.setChecked(self.settings.get("auto_launch_on_obs_start", False))

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

        # Always-visible Sync settings at the bottom of the left panel
        sync_group = QGroupBox("OBS Streaming Sync (OBS配信連動)")
        sync_layout = QVBoxLayout(sync_group)
        self.chk_obs_sync_file = QCheckBox("Sync comment acquisition with OBS Stream\n(OBSの配信開始・停止と自動連動する)")
        self.chk_obs_sync_file.stateChanged.connect(lambda: self.save_settings())
        sync_layout.addWidget(self.chk_obs_sync_file)
        
        self.chk_auto_launch = QCheckBox("Launch program when OBS Studio starts\n(OBS起動時に自動で常駐アプリを起動する)")
        self.chk_auto_launch.stateChanged.connect(lambda: self.save_settings())
        sync_layout.addWidget(self.chk_auto_launch)
        
        left_layout.addWidget(sync_group)

        # Always-visible System Diagnostics Group
        diag_group = QGroupBox("System Diagnostics (動作検査)")
        diag_layout = QVBoxLayout(diag_group)
        self.lbl_diag_twitch = QLabel("Twitch Auth: Check Pending ⚪")
        self.lbl_diag_youtube = QLabel("YouTube Auth: Check Pending ⚪")
        self.lbl_diag_obs = QLabel("OBS Studio Link: Check Pending ⚪")
        self.lbl_diag_write = QLabel("Output Path: Check Pending ⚪")
        self.lbl_diag_result = QLabel("Status: Checking...")
        self.lbl_diag_result.setStyleSheet("font-weight: bold; font-size: 14px; color: #f59e0b;")
        
        diag_layout.addWidget(self.lbl_diag_twitch)
        diag_layout.addWidget(self.lbl_diag_youtube)
        diag_layout.addWidget(self.lbl_diag_obs)
        diag_layout.addWidget(self.lbl_diag_write)
        diag_layout.addWidget(self.lbl_diag_result)
        left_layout.addWidget(diag_group)
        
        # TAB 1: OAuth & Account Manager (Scrollable)
        account_tab = QScrollArea()
        account_tab.setWidgetResizable(True)
        account_tab.setFrameShape(QFrame.NoFrame)
        account_scroll_widget = QWidget()
        account_scroll_widget.setObjectName("account_scroll_widget")
        account_layout = QVBoxLayout(account_scroll_widget)
        
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
        account_tab.setWidget(account_scroll_widget)
        tabs.addTab(account_tab, "Accounts")

        # TAB 2: Settings Panel (Scrollable)
        settings_tab = QScrollArea()
        settings_tab.setWidgetResizable(True)
        settings_tab.setFrameShape(QFrame.NoFrame)
        settings_scroll_widget = QWidget()
        settings_scroll_widget.setObjectName("settings_scroll_widget")
        settings_layout = QVBoxLayout(settings_scroll_widget)
        
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
        settings_tab.setWidget(settings_scroll_widget)
        tabs.addTab(settings_tab, "Output & Filter")

        # TAB 3: Developer Settings (Scrollable)
        dev_tab = QScrollArea()
        dev_tab.setWidgetResizable(True)
        dev_tab.setFrameShape(QFrame.NoFrame)
        dev_scroll_widget = QWidget()
        dev_scroll_widget.setObjectName("dev_scroll_widget")
        dev_layout = QVBoxLayout(dev_scroll_widget)
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
        dev_tab.setWidget(dev_scroll_widget)
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
        console_tabs.setCurrentIndex(1) # Default to System Logs tab

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
        QDialog, QMessageBox {
            background-color: #15151e;
            color: #efeff1;
        }
        QDialog QLabel, QMessageBox QLabel {
            color: #efeff1;
            background-color: transparent;
        }
        QDialog QPushButton, QMessageBox QPushButton {
            color: #efeff1;
            background-color: #3b3b4f;
            border: 1px solid #4f4f6f;
            border-radius: 6px;
            padding: 6px 12px;
        }
        QDialog QPushButton:hover, QMessageBox QPushButton:hover {
            background-color: #4a4a68;
        }
        QDialog QPushButton:pressed, QMessageBox QPushButton:pressed {
            background-color: #2b2b3f;
        }
        QScrollArea {
            background-color: #15151e;
            border: none;
        }
        #account_scroll_widget, #settings_scroll_widget, #dev_scroll_widget {
            background-color: #15151e;
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
        QScrollBar:vertical {
            border: none;
            background: #15151e;
            width: 10px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:vertical {
            background: #3b3b4f;
            min-height: 20px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: #4a4a68;
        }
        QScrollBar::add-line:vertical {
            border: none;
            background: none;
            height: 0px;
        }
        QScrollBar::sub-line:vertical {
            border: none;
            background: none;
            height: 0px;
        }
        QScrollBar:horizontal {
            border: none;
            background: #15151e;
            height: 10px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:horizontal {
            background: #3b3b4f;
            min-width: 20px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #4a4a68;
        }
        QScrollBar::add-line:horizontal {
            border: none;
            background: none;
            width: 0px;
        }
        QScrollBar::sub-line:horizontal {
            border: none;
            background: none;
            width: 0px;
        }
        """
        self.setStyleSheet(stylesheet)
        self.btn_start.setObjectName("btn_start")
        self.btn_stop.setObjectName("btn_stop")

    def show_foreground(self):
        """Force the window to the foreground on top of other applications."""
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.showNormal()
        self.raise_()
        self.activateWindow()

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

    @Slot()
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

    async def verify_tokens_on_startup(self):
        logger.info("Checking stored login sessions and refreshing tokens if necessary on startup...")
        for provider in ["twitch", "youtube"]:
            tokens = self.token_store.get_tokens(provider)
            if tokens:
                try:
                    loop = asyncio.get_running_loop()
                    # get_valid_token will verify and auto-refresh the token if close to expiry / expired
                    token = await loop.run_in_executor(None, self.oauth_core.get_valid_token, provider)
                    if token:
                        logger.info(f"Session verified for {provider.upper()} (User: {tokens.get('account_name')})")
                    else:
                        logger.warning(f"Session expired or invalid for {provider.upper()}. Please re-authenticate.")
                except Exception as e:
                    logger.error(f"Error validating token for {provider.upper()} on startup: {e}")
            else:
                logger.info(f"No stored session found for {provider.upper()}.")
        
        self.signals.account_display_updated.emit()

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
                fut = self.async_thread.run_coro(self.twitch_adapter.start())
                fut.result(timeout=5)
                any_started = True
            except Exception as e:
                logger.error(f"Failed to start Twitch adapter: {e}")

        if youtube_enabled:
            try:
                fut = self.async_thread.run_coro(self.youtube_adapter.start())
                fut.result(timeout=5)
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

        # Clear target files when acquisition stops to keep sync clean
        self.text_writer.clear()
        self.jsonl_writer.clear()

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

    def check_obs_file_state(self):
        # 1. Auto-close application if OBS Studio process is terminated
        if not hasattr(self, '_obs_check_counter'):
            self._obs_check_counter = 0
        self._obs_check_counter += 1
        
        if self._obs_check_counter >= 3:
            self._obs_check_counter = 0
            is_running = self.is_obs_process_running()
            if is_running:
                self.obs_was_seen_running = True
            elif hasattr(self, 'obs_was_seen_running') and self.obs_was_seen_running:
                logger.info("OBS Studio process close detected. Exiting application...")
                self.close()
                return

        # 2. File-based streaming sync logic (if enabled)
        if not hasattr(self, 'chk_obs_sync_file') or not self.chk_obs_sync_file.isChecked():
            return
        
        state_path = os.path.join(self.base_dir, "obs_state.txt")
        if not os.path.exists(state_path):
            return
            
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = f.read().strip()
                
            if state == "1":
                if not self.is_capturing:
                    logger.info("OBS stream start detected via state file. Starting comment acquisition...")
                    self.start_acquisition()
            elif state == "0":
                if self.is_capturing:
                    logger.info("OBS stream stop detected via state file. Stopping comment acquisition...")
                    self.stop_acquisition()
        except Exception as e:
            logger.debug(f"Failed to read obs_state.txt: {e}")

    def is_obs_process_running(self) -> bool:
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and proc.info['name'].lower() in ("obs64.exe", "obs32.exe"):
                    return True
        except Exception as e:
            logger.debug(f"Error checking OBS process: {e}")
            return True
        return False
            
    @Slot(bool)
    def on_obs_connection_changed(self, connected: bool):
        if connected:
            self.lbl_obs_conn_status.setText("OBS Connection: Connected 🟢")
            logger.info("Successfully connected to OBS Studio WebSocket server.")
        else:
            self.lbl_obs_conn_status.setText("OBS Connection: Disconnected ⚪")
            logger.info("Disconnected from OBS Studio WebSocket server.")

    def _get_startup_shortcut_path(self) -> str:
        startup_dir = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        return os.path.join(startup_dir, "OBS Danmaku Bridge.lnk")

    def closeEvent(self, event):
        # Clean exit (Close button on main window now exits app completely)
        self.stop_acquisition()
        if hasattr(self, 'obs_listener'):
            # Run stop cleanly inside async thread loop before shutdown
            self.async_thread.run_coro(self.obs_listener.stop())
        self.async_thread.stop()
        self.async_thread.join(timeout=5)
            
        super().closeEvent(event)
        QApplication.quit()

from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress

class SingleInstanceApp:
    def __init__(self, port=27845):
        self.port = port
        self.server = None
        self.window = None
        
    def check(self) -> bool:
        # Try to connect to existing instance on local port
        socket = QTcpSocket()
        socket.connectToHost("127.0.0.1", self.port)
        if socket.waitForConnected(500):
            # Already running. Send "activate" message and exit.
            socket.write(b"activate")
            socket.waitForBytesWritten(500)
            socket.disconnectFromHost()
            return False
        
        # Not running. Start server.
        self.server = QTcpServer()
        if not self.server.listen(QHostAddress.LocalHost, self.port):
            return False
            
        self.server.newConnection.connect(self.handle_connection)
        return True

    def handle_connection(self):
        client = self.server.nextPendingConnection()
        client.readyRead.connect(lambda: self.handle_read(client))

    def handle_read(self, client):
        data = client.readAll().data()
        if data == b"activate":
            # Bring existing window to the foreground
            if self.window:
                self.window.show_foreground()
        client.disconnectFromHost()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Prevent multiple instances of the bridge app
    single_instance = SingleInstanceApp(port=27845)
    if not single_instance.check():
        sys.exit(0)
        
    window = MainWindow()
    single_instance.window = window
    
    window.show_foreground()
    sys.exit(app.exec())
