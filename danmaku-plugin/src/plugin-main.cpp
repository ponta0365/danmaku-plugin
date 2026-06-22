#include <obs-module.h>
#include <obs-frontend-api.h>
#include <windows.h>
#include <gdiplus.h>
#include <filesystem>
#include <string>
#include <shellapi.h>
#include <fstream>

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("obs-niconico-danmaku", "en-US")

extern void RegisterDanmakuSource(void);

static ULONG_PTR gdiplusToken;

static void LaunchBridgeCallback(void *private_data)
{
    UNUSED_PARAMETER(private_data);
    
    // Attempt to launch run_hidden.vbs inside danmaku-bridge-tray folder
    std::wstring vbs_path = L"D:\\AI\\OBSニコニコ弾幕再現配信サイトからコメント取得版\\danmaku-bridge-tray\\run_hidden.vbs";
    
    if (!std::filesystem::exists(vbs_path)) {
        blog(LOG_WARNING, "[obs-niconico-danmaku] Bridge launcher not found at: %S", vbs_path.c_str());
        return;
    }
    
    // Launch using ShellExecuteW
    HINSTANCE res = ShellExecuteW(NULL, L"open", L"wscript.exe", (L"\"" + vbs_path + L"\"").c_str(), NULL, SW_SHOWDEFAULT);
    if ((intptr_t)res <= 32) {
        blog(LOG_ERROR, "[obs-niconico-danmaku] Failed to launch bridge GUI (ErrorCode: %d)", (int)(intptr_t)res);
    } else {
        blog(LOG_INFO, "[obs-niconico-danmaku] Successfully launched bridge GUI via wscript.");
    }
}

static void OBSFrontendEventCallback(enum obs_frontend_event event, void *private_data)
{
    UNUSED_PARAMETER(private_data);
    
    std::wstring state_path = L"D:\\AI\\OBSニコニコ弾幕再現配信サイトからコメント取得版\\danmaku-bridge-tray\\obs_state.txt";
    
    if (event == OBS_FRONTEND_EVENT_STREAMING_STARTED) {
        std::ofstream file(state_path);
        if (file.is_open()) {
            file << "1";
            file.close();
            blog(LOG_INFO, "[obs-niconico-danmaku] Written streaming state: 1 (started)");
        }
    } else if (event == OBS_FRONTEND_EVENT_STREAMING_STOPPED) {
        std::ofstream file(state_path);
        if (file.is_open()) {
            file << "0";
            file.close();
            blog(LOG_INFO, "[obs-niconico-danmaku] Written streaming state: 0 (stopped)");
        }
    }
}
static bool IsAutoLaunchEnabled()
{
    std::wstring config_path = L"D:\\AI\\OBSニコニコ弾幕再現配信サイトからコメント取得版\\danmaku-bridge-tray\\config\\settings.json";
    std::ifstream file(config_path);
    if (!file.is_open()) return false;
    
    std::string content((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    file.close();
    
    obs_data_t *data = obs_data_create_from_json(content.c_str());
    if (!data) return false;
    
    bool enabled = obs_data_get_bool(data, "auto_launch_on_obs_start");
    obs_data_release(data);
    return enabled;
}

bool obs_module_load(void)
{
    // Initialize GDI+ for text rendering
    Gdiplus::GdiplusStartupInput gdiplusStartupInput;
    Gdiplus::Status status = Gdiplus::GdiplusStartup(&gdiplusToken, &gdiplusStartupInput, NULL);
    if (status != Gdiplus::Ok) {
        blog(LOG_ERROR, "[obs-niconico-danmaku] Failed to initialize GDI+");
        return false;
    }

    // Register our custom source plugin
    RegisterDanmakuSource();
    
    // Register tools menu item in OBS Studio
    obs_frontend_add_tools_menu_item("OBS Danmaku Bridge", LaunchBridgeCallback, nullptr);
    
    // Write initial streaming state
    std::wstring state_path = L"D:\\AI\\OBSニコニコ弾幕再現配信サイトからコメント取得版\\danmaku-bridge-tray\\obs_state.txt";
    std::ofstream file(state_path);
    if (file.is_open()) {
        file << (obs_frontend_streaming_active() ? "1" : "0");
        file.close();
    }
    
    // Register frontend event callback
    obs_frontend_add_event_callback(OBSFrontendEventCallback, nullptr);
    
    // Check and auto-launch bridge GUI if enabled
    if (IsAutoLaunchEnabled()) {
        blog(LOG_INFO, "[obs-niconico-danmaku] Auto-launch is enabled, launching bridge...");
        LaunchBridgeCallback(nullptr);
    }
    
    blog(LOG_INFO, "[obs-niconico-danmaku] Plugin loaded successfully.");
    return true;
}

void obs_module_unload(void)
{
    // Remove frontend event callback
    obs_frontend_remove_event_callback(OBSFrontendEventCallback, nullptr);

    // Shutdown GDI+
    Gdiplus::GdiplusShutdown(gdiplusToken);
    blog(LOG_INFO, "[obs-niconico-danmaku] Plugin unloaded.");
}
