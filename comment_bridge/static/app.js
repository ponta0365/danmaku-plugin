// app.js

let ws = null;
let activePlatforms = {
    twitch: false,
    youtube: false,
    kick: false
};

// UI Elements
const commentsFilePathInput = document.getElementById("comments_file_path");
const twitchChannelInput = document.getElementById("twitch_channel");
const twitchTokenInput = document.getElementById("twitch_token");
const twitchNicknameInput = document.getElementById("twitch_nickname");
const youtubeModeSelect = document.getElementById("youtube_mode");
const youtubeVideoIdInput = document.getElementById("youtube_video_id");
const youtubeTokenInput = document.getElementById("youtube_token");
const kickChannelInput = document.getElementById("kick_channel");
const kickChatroomIdInput = document.getElementById("kick_chatroom_id");

const btnTwitchLogin = document.getElementById("btn-twitch-login");
const btnToggleTwitch = document.getElementById("btn-toggle-twitch");
const btnToggleYoutube = document.getElementById("btn-toggle-youtube");
const btnToggleKick = document.getElementById("btn-toggle-kick");

const btnStartBridge = document.getElementById("btn-start-bridge");
const btnStopBridge = document.getElementById("btn-stop-bridge");

const consoleMonitor = document.getElementById("console-monitor");
const bridgeStatusDot = document.getElementById("bridge-status-dot");
const bridgeStatusText = document.getElementById("bridge-status-text");
const statFileSize = document.getElementById("stat-file-size");
const statWsClients = document.getElementById("stat-ws-clients");

// Load config from server on start
async function loadConfig() {
    try {
        const response = await fetch("/api/config");
        if (response.ok) {
            const config = await response.json();
            commentsFilePathInput.value = config.comments_file_path || "";
            twitchChannelInput.value = config.twitch_channel || "";
            twitchTokenInput.value = config.twitch_token || "";
            twitchNicknameInput.value = config.twitch_nickname || "";
            youtubeModeSelect.value = config.youtube_mode || "scraper";
            youtubeVideoIdInput.value = config.youtube_video_id || "";
            youtubeTokenInput.value = config.youtube_token || "";
            kickChannelInput.value = config.kick_channel || "";
            kickChatroomIdInput.value = config.kick_chatroom_id || "";
            
            toggleYoutubeSection();
        }
    } catch (err) {
        console.error("Failed to load config:", err);
        logToConsole("System", "Failed to retrieve configuration from backend.", "system");
    }
}

// Save config to server
async function saveConfig() {
    const config = {
        comments_file_path: commentsFilePathInput.value,
        twitch_channel: twitchChannelInput.value,
        twitch_token: twitchTokenInput.value,
        twitch_nickname: twitchNicknameInput.value,
        youtube_mode: youtubeModeSelect.value,
        youtube_video_id: youtubeVideoIdInput.value,
        youtube_token: youtubeTokenInput.value,
        kick_channel: kickChannelInput.value,
        kick_chatroom_id: kickChatroomIdInput.value
    };

    try {
        await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(config)
        });
    } catch (err) {
        console.error("Failed to save config:", err);
        logToConsole("System", "Failed to save configuration to backend.", "system");
    }
}

// Toggle YouTube inputs based on mode selection
function toggleYoutubeSection() {
    const oauthSection = document.getElementById("youtube-oauth-section");
    const scraperTip = document.getElementById("youtube-scraper-tip");
    if (youtubeModeSelect.value === "oauth") {
        oauthSection.style.display = "block";
        scraperTip.style.display = "none";
    } else {
        oauthSection.style.display = "none";
        scraperTip.style.display = "block";
    }
}
youtubeModeSelect.addEventListener("change", toggleYoutubeSection);

// Twitch Login OAuth Redirection
btnTwitchLogin.addEventListener("click", () => {
    // Client ID for twitch Chat OAuth (using a common helper client or your own registered client)
    const clientId = "gp762nuuoqcoxypm4t5665g5lumhz1"; 
    const redirectUri = window.location.origin + "/callback/twitch";
    const scopes = "chat:read";
    const authUrl = `https://id.twitch.tv/oauth2/authorize?client_id=${clientId}&redirect_uri=${redirectUri}&response_type=token&scope=${scopes}`;
    
    // Save current config before redirecting so settings aren't lost
    saveConfig().then(() => {
        window.open(authUrl, "_blank", "width=500,height=600");
    });
});

// Platform connection toggles in UI
function setupPlatformToggle(btn, key) {
    btn.addEventListener("click", () => {
        activePlatforms[key] = !activePlatforms[key];
        if (activePlatforms[key]) {
            btn.classList.add("active");
            btn.innerText = "CONNECTED";
            logToConsole("System", `${key.toUpperCase()} channel marked for bridge connection.`, "system");
        } else {
            btn.classList.remove("active");
            btn.innerText = "CONNECT";
            logToConsole("System", `${key.toUpperCase()} channel disconnected.`, "system");
        }
    });
}
setupPlatformToggle(btnToggleTwitch, "twitch");
setupPlatformToggle(btnToggleYoutube, "youtube");
setupPlatformToggle(btnToggleKick, "kick");

// Start Acquisition
btnStartBridge.addEventListener("click", async () => {
    const selectedPlatforms = Object.keys(activePlatforms).filter(k => activePlatforms[k]);
    
    if (selectedPlatforms.length === 0) {
        alert("Please click 'CONNECT' on at least one platform before starting acquisition.");
        return;
    }

    // Save inputs first
    await saveConfig();
    logToConsole("System", "Starting comment acquisition...", "system");

    try {
        const response = await fetch("/api/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(selectedPlatforms)
        });
        
        if (response.ok) {
            const data = await response.json();
            if (data.status === "success" || data.status === "partial") {
                logToConsole("System", `Successfully started: ${data.started.join(", ") || "none"}`, "system");
                if (data.failed.length > 0) {
                    logToConsole("System", `Failed to start: ${data.failed.join(", ")}`, "system");
                }
                updateStatusUI(true);
            }
        }
    } catch (err) {
        console.error("Start error:", err);
        logToConsole("System", "Failed to send start signal to backend.", "system");
    }
});

// Stop Acquisition
btnStopBridge.addEventListener("click", async () => {
    const runningPlatforms = Object.keys(activePlatforms).filter(k => activePlatforms[k]);
    logToConsole("System", "Stopping comment acquisition...", "system");

    try {
        const response = await fetch("/api/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(runningPlatforms)
        });
        
        if (response.ok) {
            logToConsole("System", "Comment acquisition stopped.", "system");
            updateStatusUI(false);
        }
    } catch (err) {
        console.error("Stop error:", err);
        logToConsole("System", "Failed to send stop signal to backend.", "system");
    }
});

// Status UI updates
function updateStatusUI(running) {
    if (running) {
        bridgeStatusDot.classList.add("active");
        bridgeStatusText.innerText = "ACTIVE BRIDGING";
        btnStartBridge.style.opacity = 0.5;
        btnStartBridge.disabled = true;
    } else {
        bridgeStatusDot.classList.remove("active");
        bridgeStatusText.innerText = "DISCONNECTED";
        btnStartBridge.style.opacity = 1;
        btnStartBridge.disabled = false;
    }
}

// Log formatting to Terminal Monitor
function logToConsole(author, text, platform) {
    const placeholder = consoleMonitor.querySelector(".console-placeholder");
    if (placeholder) {
        placeholder.remove();
    }

    const msgElement = document.createElement("div");
    msgElement.className = `console-msg ${platform.toLowerCase()}`;
    
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.innerText = platform;
    msgElement.appendChild(badge);

    const authorSpan = document.createElement("span");
    authorSpan.className = "author";
    authorSpan.innerText = author + ": ";
    msgElement.appendChild(authorSpan);

    const textSpan = document.createElement("span");
    textSpan.className = "text";
    textSpan.innerText = text;
    msgElement.appendChild(textSpan);

    consoleMonitor.appendChild(msgElement);
    consoleMonitor.scrollTop = consoleMonitor.scrollHeight;
    
    // Limit console length to 100 items
    while (consoleMonitor.children.length > 100) {
        consoleMonitor.removeChild(consoleMonitor.firstChild);
    }
}

// Setup WebSocket telemetry stream
function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/comments`;
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
        statWsClients.innerText = "1";
        logToConsole("System", "Connected to local server telemetry feed.", "system");
    };
    
    ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        logToConsole(payload.author, payload.text, payload.platform);
    };
    
    ws.onclose = () => {
        statWsClients.innerText = "0";
        logToConsole("System", "Connection to telemetry feed lost. Reconnecting in 3s...", "system");
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (err) => {
        console.error("WS error:", err);
    };
}

// Periodically fetch client status & statistics from server
async function fetchStatusUpdate() {
    try {
        const response = await fetch("/api/status");
        if (response.ok) {
            const data = await response.json();
            
            // Check if any platform is running on the backend
            const isAnyRunning = data.twitch || data.youtube || data.kick;
            updateStatusUI(isAnyRunning);
            
            // Set buttons active state based on backend state
            updateToggleButtonState(btnToggleTwitch, "twitch", data.twitch);
            updateToggleButtonState(btnToggleYoutube, "youtube", data.youtube);
            updateToggleButtonState(btnToggleKick, "kick", data.kick);
            
            // Update comments file size display
            if (data.file_size_kb !== undefined) {
                statFileSize.innerText = data.file_size_kb.toFixed(2) + " KB";
            }
        }
    } catch (err) {
        console.error("Status fetch error:", err);
    }
}

function updateToggleButtonState(btn, key, isActiveOnBackend) {
    if (isActiveOnBackend) {
        activePlatforms[key] = true;
        btn.classList.add("active");
        btn.innerText = "CONNECTED";
    }
}

// Start polling status
setInterval(fetchStatusUpdate, 2000);

// Init
window.addEventListener("DOMContentLoaded", () => {
    loadConfig().then(() => {
        connectWebSocket();
        fetchStatusUpdate();
    });
});
