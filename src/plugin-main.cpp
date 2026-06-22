#include <obs-module.h>
#include <windows.h>
#include <gdiplus.h>

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("obs-niconico-danmaku", "en-US")

extern void RegisterDanmakuSource(void);

static ULONG_PTR gdiplusToken;

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
    blog(LOG_INFO, "[obs-niconico-danmaku] Plugin loaded successfully.");
    return true;
}

void obs_module_unload(void)
{
    // Shutdown GDI+
    Gdiplus::GdiplusShutdown(gdiplusToken);
    blog(LOG_INFO, "[obs-niconico-danmaku] Plugin unloaded.");
}
