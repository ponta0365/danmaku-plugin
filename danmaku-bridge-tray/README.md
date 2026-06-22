# OBS Danmaku OAuth Bridge (danmaku-bridge)

This is a local Python application with a PySide6 GUI that manages official OAuth connections for Twitch and YouTube. It fetches live chat messages in real time, normalizes them into a unified format, and writes them to a text file (preferably on a RAMDisk like `K:\`) so they can be rendered as danmaku on stream by the C++ OBS Danmaku Plugin.

## Features

- **OAuth 2.0 PKCE Core**: Secure browser-based authentication using PKCE and state protection, binding temporary callback servers on random ports.
- **Secure Token Storage**: Encrypts access and refresh tokens using Windows DPAPI, saving them to an SQLite database (`data/token_store.db`).
- **Twitch EventSub WebSocket**: Direct real-time stream subscription (`channel.chat.message`) over WebSockets.
- **YouTube API Polling**: Live chat messages polling with support for `liveChatMessages.list`, honoring google's polling interval requirements.
- **Thread-Safe File Output**: High-performance text and JSONL output pipelines with retry loops to avoid file locking collisions with OBS.
- **NG Words & NG Users Filters**: Moderation pipeline to suppress unwanted messages.
- **Local Settings & Provider configuration**: Easily editable templates.

## Directory Structure

```text
danmaku-bridge/
├── config/
│   ├── providers.json        # Developer App Client IDs and Secrets
│   └── settings.json         # Local outputs and moderation settings
├── data/
│   └── token_store.db        # SQLite database (DPAPI-encrypted tokens)
├── logs/
│   └── danmaku-bridge.log    # Application system logs
├── src/
│   ├── auth/
│   │   ├── oauth_core.py     # Authorization Code Flow engine
│   │   ├── pkce.py           # Cryptographic utilities
│   │   ├── callback_server.py # Temporary HTTP callback server
│   │   └── token_store.py    # DPAPI SQLite access wrapper
│   ├── providers/
│   │   ├── twitch_adapter.py # EventSub WebSocket chat reader
│   │   └── youtube_adapter.py # YouTube Live Chat messages poller
│   ├── normalize/
│   │   └── unified_comment.py # Unified Comment schema
│   ├── output/
│   │   ├── text_writer.py    # Safe text appending
│   │   └── jsonl_writer.py   # Safe JSONL logging
│   ├── moderation/
│   │   └── filter_pipeline.py # Word and spam filter
│   └── ui/
│       └── main_window.py    # PySide6 GUI Dashboard
├── run.bat                   # Double-click startup batch script
└── README.md                 # Document
```

## Setup Instructions

1. Register developer applications on:
   - **Twitch Developer Console** (Set redirect URI to `http://127.0.0.1/callback` or allow dynamic local ports if permitted, though you can use standard localhost redirects. Twitch allows registering redirect URIs with localhost).
   - **Google Cloud Console** (Select "Desktop App" under OAuth client types, which allows redirecting to localhost automatically without manually specifying ports!).
2. Double-click `run.bat` to launch the application.
3. Open **Developer Credentials** tab in the sidebar and input your Twitch and Google Client IDs & Secrets, then click **Save Developer Keys**.
4. Go to **Accounts** tab:
   - Click **Login Twitch** / **Login YouTube**. Your browser will open the respective login window.
   - Authorize, and the browser will display "Authentication Successful!". Close the tab and return to the app.
5. In **Output & Filter** tab:
   - Set **Text Path** to `K:\obs_comments.txt` (or any path in your RAMDisk).
6. Click **START ACQUISITION** to begin streaming live comments directly to your text file!
7. Point your C++ OBS Danmaku Plugin's path property to `K:\obs_comments.txt` to overlay the comments.
