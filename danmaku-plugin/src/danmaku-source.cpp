#include <obs-module.h>
#include <util/platform.h>
#include <graphics/vec4.h>
#include <string>
#include <vector>
#include <queue>
#include <mutex>
#include <filesystem>
#include <fstream>
#include <algorithm>
#include <ctime>
#include <cstdlib>

#include <windows.h>
#include <gdiplus.h>

// Struct definition for individual comments
struct comment_info {
    std::wstring text;
    float x;
    float y;
    float speed;
    int width;
    int height;
    gs_texture_t *texture;
    int lane;
};

// Struct definition for lane tracking
struct lane_status {
    float last_x;
    float last_width;
    float last_speed;
    bool active;
};

// Main context struct for the source
struct danmaku_source {
    obs_source_t *source;
    
    // Properties
    std::wstring file_path;
    std::wstring font_name;
    int font_size;
    float scroll_speed;
    uint32_t text_color;
    uint32_t outline_color;
    float outline_size_percent;
    int max_comments;
    int min_push_interval_ms;
    int nigayaka_prob;
    std::wstring direction; // L"left_to_right" or L"right_to_left"
    bool loop_play;
    bool enable_nigayaka;
    int check_interval_ms;
    
    // Expanded JSONL settings
    std::wstring color_mode; // L"fixed", L"platform", L"user"
    bool show_username;
    bool show_platform;
    
    int canvas_width;
    int canvas_height;
    
    // Runtime state
    std::vector<comment_info> active_comments;
    std::queue<std::wstring> comment_queue;
    std::vector<std::wstring> loaded_comments;
    std::filesystem::file_time_type last_file_time;
    size_t last_read_line_count;
    
    uint64_t last_check_time_ms;
    uint64_t last_push_time_ms;
    
    std::mutex data_mutex;
    
    int num_lanes;
    std::vector<lane_status> lanes;
};

// Static Emoji/Symbol/AA List for extra fluff (Nigayaka list)
static const std::wstring NIGAYAKA_LIST[] = {
    L"☆.｡.:*･ﾟ☆.｡.:*･ﾟ☆",
    L"★★★★★★★★★★",
    L"キタ━━━━(゜∀゜)━━━━ッ!!",
    L"ｷﾀ━━━━━━(ﾟ∀ﾟ)━━━━━━ !!!!",
    L"（・∀・）ｲｲ!!",
    L"（；´Д｀）ハァハァ",
    L"＼(＾o＾)／",
    L"(ﾟдﾟ)ｳﾏｰ",
    L"( ﾟ∀ﾟ)o彡°おっぱい！おっぱい！",
    L"88888888888888888888",
    L"wwwwwwwwwwwwwwwwwwww",
    L"☆彡 ★彡 ☆彡 ★彡",
    L"┌(。Д。)┐",
    L"(((( ；ﾟДﾟ)))ｶﾞﾀｶﾞﾀ",
    L"おつおつ",
    L"初見です",
    L"神回決定"
};
static const size_t NIGAYAKA_COUNT = sizeof(NIGAYAKA_LIST) / sizeof(NIGAYAKA_LIST[0]);

struct TextSegment {
    std::wstring text;
    bool is_emoji;
};

static bool IsEmojiOrSymbol(wchar_t high, wchar_t low = 0) {
    if (high >= 0xD800 && high <= 0xDBFF) {
        return true;
    }
    if (high >= 0x2000 && high <= 0x2BFF) {
        return true;
    }
    if (high >= 0x2700 && high <= 0x27BF) {
        return true;
    }
    if (high >= 0xFE00 && high <= 0xFE0F) {
        return true;
    }
    return false;
}

static std::vector<TextSegment> SegmentText(const std::wstring& text) {
    std::vector<TextSegment> segments;
    if (text.empty()) return segments;
    
    std::wstring current_seg = L"";
    bool current_is_emoji = false;
    
    for (size_t i = 0; i < text.length(); ) {
        wchar_t ch = text[i];
        bool is_emoji = false;
        size_t char_len = 1;
        
        if (ch >= 0xD800 && ch <= 0xDBFF && i + 1 < text.length()) {
            wchar_t low = text[i + 1];
            if (low >= 0xDC00 && low <= 0xDFFF) {
                is_emoji = true;
                char_len = 2;
            } else {
                is_emoji = IsEmojiOrSymbol(ch);
            }
        } else {
            is_emoji = IsEmojiOrSymbol(ch);
        }
        
        if (current_seg.empty()) {
            current_is_emoji = is_emoji;
        }
        
        if (is_emoji == current_is_emoji) {
            current_seg.append(text.substr(i, char_len));
        } else {
            segments.push_back({current_seg, current_is_emoji});
            current_seg = text.substr(i, char_len);
            current_is_emoji = is_emoji;
        }
        
        i += char_len;
    }
    
    if (!current_seg.empty()) {
        segments.push_back({current_seg, current_is_emoji});
    }
    
    return segments;
}

// Helper to render text to an OBS texture via GDI+ memory mapping (no temp files!)
static gs_texture_t* CreateTextTexture(const std::wstring& text, const std::wstring& font_name, float font_size,
                                       uint32_t text_color, uint32_t outline_color, float outline_size_percent,
                                       int& out_width, int& out_height)
{
    using namespace Gdiplus;
    
    std::wstring actual_font_name = font_name;
    {
        FontFamily test_family(actual_font_name.c_str());
        if (test_family.GetLastStatus() != Ok) {
            actual_font_name = L"MS UI Gothic";
        }
    }
    FontFamily family(actual_font_name.c_str());
    Font font(&family, font_size, FontStyleBold, UnitPixel);
    
    std::wstring emoji_font_family_name = L"Segoe UI Emoji";
    {
        FontFamily test_emoji(emoji_font_family_name.c_str());
        if (test_emoji.GetLastStatus() != Ok) {
            emoji_font_family_name = L"Segoe UI Symbol";
            FontFamily test_symbol(emoji_font_family_name.c_str());
            if (test_symbol.GetLastStatus() != Ok) {
                emoji_font_family_name = actual_font_name;
            }
        }
    }
    FontFamily emoji_family(emoji_font_family_name.c_str());
    Font emoji_font(&emoji_family, font_size, FontStyleRegular, UnitPixel);
    
    StringFormat format;
    format.SetAlignment(StringAlignmentNear);
    format.SetLineAlignment(StringAlignmentNear);
    format.SetFormatFlags(StringFormatFlagsNoWrap | StringFormatFlagsMeasureTrailingSpaces);
    
    std::vector<TextSegment> segments = SegmentText(text);
    
    // Measure bounding box of all segments
    HDC hdc = GetDC(NULL);
    Graphics g_measure(hdc);
    RectF layout_rect(0.f, 0.f, 10000.f, 1000.f);
    
    float total_width = 0.f;
    float max_height = 0.f;
    
    for (const auto& seg : segments) {
        Font* active_font = seg.is_emoji ? &emoji_font : &font;
        RectF bound_rect;
        g_measure.MeasureString(seg.text.c_str(), -1, active_font, layout_rect, &format, &bound_rect);
        total_width += bound_rect.Width;
        if (bound_rect.Height > max_height) {
            max_height = bound_rect.Height;
        }
    }
    ReleaseDC(NULL, hdc);
    
    float outline_thickness = font_size * (outline_size_percent / 100.0f);
    if (outline_thickness < 1.0f) outline_thickness = 1.0f;
    
    int width = (int)ceil(total_width) + (int)ceil(outline_thickness * 2.0f) + 4;
    int height = (int)ceil(max_height) + (int)ceil(outline_thickness * 2.0f) + 4;
    
    if (width <= 0) width = 1;
    if (height <= 0) height = 1;
    
    // Draw string to a memory bitmap
    Bitmap bitmap(width, height, PixelFormat32bppARGB);
    Graphics g(&bitmap);
    g.SetSmoothingMode(SmoothingModeAntiAlias);
    g.SetTextRenderingHint(TextRenderingHintAntiAlias);
    g.Clear(Color(0, 0, 0, 0)); // Transparent background
    
    GraphicsPath path;
    float current_x = outline_thickness + 2.f;
    
    HDC hdc2 = GetDC(NULL);
    Graphics g_measure2(hdc2);
    
    for (const auto& seg : segments) {
        FontFamily* active_family = seg.is_emoji ? &emoji_family : &family;
        FontStyle active_style = seg.is_emoji ? FontStyleRegular : FontStyleBold;
        Font* active_font = seg.is_emoji ? &emoji_font : &font;
        
        path.AddString(seg.text.c_str(), -1, active_family, active_style, font_size, PointF(current_x, outline_thickness + 2.f), &format);
        
        RectF bound_rect;
        g_measure2.MeasureString(seg.text.c_str(), -1, active_font, layout_rect, &format, &bound_rect);
        current_x += bound_rect.Width;
    }
    ReleaseDC(NULL, hdc2);
    
    // 1. Draw outline path
    Color pen_color(outline_color);
    Pen pen(pen_color, outline_thickness);
    pen.SetLineJoin(LineJoinRound);
    g.DrawPath(&pen, &path);
    
    // 2. Fill interior path
    Color brush_color(text_color);
    SolidBrush brush(brush_color);
    g.FillPath(&brush, &path);
    
    // Lock bitmap and copy pixels directly to VRAM texture
    BitmapData bmp_data;
    Rect rect(0, 0, width, height);
    bitmap.LockBits(&rect, ImageLockModeRead, PixelFormat32bppARGB, &bmp_data);
    
    obs_enter_graphics();
    // Use GS_BGRA as GDI+ PixelFormat32bppARGB is stored in BGRA layout in system memory
    gs_texture_t *texture = gs_texture_create(width, height, GS_BGRA, 1, (const uint8_t**)&bmp_data.Scan0, GS_DYNAMIC);
    obs_leave_graphics();
    
    bitmap.UnlockBits(&bmp_data);
    
    out_width = width;
    out_height = height;
    return texture;
}

static const char *danmaku_get_name(void *unused)
{
    UNUSED_PARAMETER(unused);
    return obs_module_text("NicoNicoDanmaku");
}

static void LoadCommentFile(struct danmaku_source *context)
{
    if (context->file_path.empty()) return;
    
    std::ifstream file(context->file_path);
    if (!file.is_open()) {
        blog(LOG_WARNING, "[obs-niconico-danmaku] Failed to open comment file: %S", context->file_path.c_str());
        return;
    }
    
    std::vector<std::wstring> new_comments;
    std::string line;
    while (std::getline(file, line)) {
        // Strip BOM if present
        if (line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB && (unsigned char)line[2] == 0xBF) {
            line = line.substr(3);
        }
        
        // Trim spaces
        line.erase(line.begin(), std::find_if(line.begin(), line.end(), [](unsigned char ch) { return !std::isspace(ch); }));
        line.erase(std::find_if(line.rbegin(), line.rend(), [](unsigned char ch) { return !std::isspace(ch); }).base(), line.end());
        
        if (line.empty()) continue;
        
        // Convert to wide string
        int size = MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, nullptr, 0);
        if (size > 0) {
            std::wstring wline(size - 1, 0);
            MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, &wline[0], size);
            new_comments.push_back(wline);
        }
    }
    
    context->loaded_comments = std::move(new_comments);
    blog(LOG_INFO, "[obs-niconico-danmaku] Loaded %d comments from file.", (int)context->loaded_comments.size());
}

static void CheckAndReloadFile(struct danmaku_source *context)
{
    if (context->file_path.empty()) return;
    
    try {
        if (!std::filesystem::exists(context->file_path)) return;
        
        auto current_time = std::filesystem::last_write_time(context->file_path);
        if (current_time != context->last_file_time) {
            context->last_file_time = current_time;
            
            std::ifstream file(context->file_path);
            if (!file.is_open()) return;
            
            std::vector<std::wstring> all_lines;
            std::string line;
            while (std::getline(file, line)) {
                if (line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB && (unsigned char)line[2] == 0xBF) {
                    line = line.substr(3);
                }
                line.erase(line.begin(), std::find_if(line.begin(), line.end(), [](unsigned char ch) { return !std::isspace(ch); }));
                line.erase(std::find_if(line.rbegin(), line.rend(), [](unsigned char ch) { return !std::isspace(ch); }).base(), line.end());
                if (line.empty()) continue;
                
                int size = MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, nullptr, 0);
                if (size > 0) {
                    std::wstring wline(size - 1, 0);
                    MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, &wline[0], size);
                    all_lines.push_back(wline);
                }
            }
            
            if (all_lines.empty()) {
                for (auto& comment : context->active_comments) {
                    if (comment.texture) {
                        obs_enter_graphics();
                        gs_texture_destroy(comment.texture);
                        obs_leave_graphics();
                    }
                }
                context->active_comments.clear();
                
                std::queue<std::wstring> empty_queue;
                std::swap(context->comment_queue, empty_queue);
                
                context->loaded_comments.clear();
                context->last_read_line_count = 0;
            } else {
                if (context->loop_play) {
                    context->loaded_comments = std::move(all_lines);
                } else {
                    // Watch for appended lines
                    if (all_lines.size() > context->last_read_line_count) {
                        for (size_t i = context->last_read_line_count; i < all_lines.size(); ++i) {
                            context->comment_queue.push(all_lines[i]);
                        }
                    } else if (all_lines.size() < context->last_read_line_count) {
                        // Reset if file was cleared/shrunk
                        context->last_read_line_count = all_lines.size();
                    }
                    context->last_read_line_count = all_lines.size();
                    context->loaded_comments = std::move(all_lines);
                }
            }
        }
    } catch (...) {
        // Safe check ignore
    }
}

static std::string WideToUtf8(const std::wstring& wstr)
{
    if (wstr.empty()) return "";
    int size_needed = WideCharToMultiByte(CP_UTF8, 0, &wstr[0], (int)wstr.size(), NULL, 0, NULL, NULL);
    std::string strTo(size_needed, 0);
    WideCharToMultiByte(CP_UTF8, 0, &wstr[0], (int)wstr.size(), &strTo[0], size_needed, NULL, NULL);
    return strTo;
}

static std::wstring Utf8ToWide(const std::string& str)
{
    if (str.empty()) return L"";
    int size_needed = MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), NULL, 0);
    std::wstring wstrTo(size_needed, 0);
    MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), &wstrTo[0], size_needed);
    return wstrTo;
}

static uint32_t ParseHexColor(const char *hex_str, uint32_t fallback_color)
{
    if (!hex_str || *hex_str == '\0') return fallback_color;
    
    std::string s = hex_str;
    if (s[0] == '#') {
        s = s.substr(1);
    }
    
    if (s.length() != 6) return fallback_color;
    
    try {
        unsigned int val = std::stoul(s, nullptr, 16);
        uint8_t r = (val >> 16) & 0xFF;
        uint8_t g = (val >> 8) & 0xFF;
        uint8_t b = val & 0xFF;
        // GDI+/OBS color is stored as AARRGGBB
        return 0xFF000000 | (r << 16) | (g << 8) | b;
    } catch (...) {
        return fallback_color;
    }
}

static void PushComment(struct danmaku_source *context, const std::wstring& raw_line)
{
    if ((int)context->active_comments.size() >= context->max_comments) return;
    
    std::wstring display_text = raw_line;
    uint32_t display_color = context->text_color;
    
    // Auto-detect JSONL line
    std::string utf8_line = WideToUtf8(raw_line);
    bool is_json = (!utf8_line.empty() && utf8_line[0] == '{' && utf8_line.back() == '}');
    
    if (is_json) {
        obs_data_t *json_data = obs_data_create_from_json(utf8_line.c_str());
        if (json_data) {
            const char *text_str = obs_data_get_string(json_data, "text");
            const char *user_name_str = obs_data_get_string(json_data, "user_name");
            const char *platform_str = obs_data_get_string(json_data, "platform");
            const char *user_color_str = obs_data_get_string(json_data, "user_color");
            
            std::wstring comment_text = Utf8ToWide(text_str ? text_str : "");
            std::wstring user_name = Utf8ToWide(user_name_str ? user_name_str : "");
            std::wstring platform = Utf8ToWide(platform_str ? platform_str : "");
            
            // 1. Build prefix based on properties
            std::wstring prefix = L"";
            if (context->show_platform && !platform.empty()) {
                prefix += L"[" + platform + L"] ";
            }
            if (context->show_username && !user_name.empty()) {
                prefix += user_name + L": ";
            }
            display_text = prefix + comment_text;
            
            // 2. Select color based on mode
            if (context->color_mode == L"user") {
                display_color = ParseHexColor(user_color_str, context->text_color);
            } else if (context->color_mode == L"platform") {
                if (strcmp(platform_str, "twitch") == 0) {
                    display_color = ParseHexColor("#9146ff", context->text_color);
                } else if (strcmp(platform_str, "youtube") == 0) {
                    display_color = ParseHexColor("#ff0000", context->text_color);
                }
            }
            
            obs_data_release(json_data);
        }
    }
    
    // Dynamic lanes recalculation based on font size
    int lane_height = (int)(context->font_size * 1.4f);
    if (lane_height <= 0) lane_height = 10;
    context->num_lanes = context->canvas_height / lane_height;
    if (context->num_lanes <= 0) context->num_lanes = 1;
    
    if (context->lanes.size() != (size_t)context->num_lanes) {
        context->lanes.resize(context->num_lanes);
        for (auto& lane : context->lanes) {
            lane.active = false;
            lane.last_x = 0.f;
            lane.last_width = 0.f;
            lane.last_speed = 0.f;
        }
    }
    
    // Clear and build current active lane states
    for (auto& lane : context->lanes) {
        lane.active = false;
    }
    for (const auto& c : context->active_comments) {
        if (c.lane >= 0 && c.lane < context->num_lanes) {
            context->lanes[c.lane].active = true;
            context->lanes[c.lane].last_x = c.x;
            context->lanes[c.lane].last_width = (float)c.width;
            context->lanes[c.lane].last_speed = c.speed;
        }
    }
    
    // Find eligible lanes
    std::vector<int> valid_lanes;
    bool is_l2r = (context->direction == L"left_to_right");
    
    // Introduce random minor variance in speed for authentic feel (85% to 115%)
    float speed_modifier = 0.85f + (float)(rand() % 31) / 100.f;
    float final_speed = context->scroll_speed * speed_modifier;
    if (final_speed < 10.f) final_speed = 10.f;
    
    int comment_width = 0;
    int comment_height = 0;
    
    gs_texture_t *tex = CreateTextTexture(display_text, context->font_name, (float)context->font_size,
                                           display_color, context->outline_color,
                                           context->outline_size_percent, comment_width, comment_height);
    if (!tex) return;
    
    for (int i = 0; i < context->num_lanes; ++i) {
        auto& lane = context->lanes[i];
        if (!lane.active) {
            valid_lanes.push_back(i);
            continue;
        }
        
        if (is_l2r) {
            // Left to Right:
            // Ensure previous comment is entirely on screen (left edge > 0)
            if (lane.last_x >= 0.f) {
                if (final_speed <= lane.last_speed) {
                    // Safe because new comment is slower or equal speed
                    valid_lanes.push_back(i);
                } else {
                    // Fast comment: check if it overlaps before old comment leaves screen
                    float t_catch = lane.last_x / (final_speed - lane.last_speed);
                    float t_out = ((float)context->canvas_width - lane.last_x) / lane.last_speed;
                    if (t_catch > t_out) {
                        valid_lanes.push_back(i);
                    }
                }
            }
        } else {
            // Right to Left:
            // Ensure previous comment is entirely on screen (right edge < canvas_width)
            float right_edge = lane.last_x + lane.last_width;
            if (right_edge <= (float)context->canvas_width) {
                if (final_speed <= lane.last_speed) {
                    valid_lanes.push_back(i);
                } else {
                    float t_catch = ((float)context->canvas_width - right_edge) / (final_speed - lane.last_speed);
                    float t_out = right_edge / lane.last_speed;
                    if (t_catch > t_out) {
                        valid_lanes.push_back(i);
                    }
                }
            }
        }
    }
    
    if (valid_lanes.empty()) {
        obs_enter_graphics();
        gs_texture_destroy(tex);
        obs_leave_graphics();
        return;
    }
    
    int chosen_lane = valid_lanes[rand() % valid_lanes.size()];
    
    comment_info new_comment;
    new_comment.text = display_text;
    new_comment.width = comment_width;
    new_comment.height = comment_height;
    new_comment.speed = final_speed;
    new_comment.texture = tex;
    new_comment.lane = chosen_lane;
    new_comment.y = (float)(chosen_lane * lane_height);
    
    if (is_l2r) {
        new_comment.x = -(float)comment_width;
    } else {
        new_comment.x = (float)context->canvas_width;
    }
    
    context->active_comments.push_back(new_comment);
    
    // Update lane state
    context->lanes[chosen_lane].active = true;
    context->lanes[chosen_lane].last_x = new_comment.x;
    context->lanes[chosen_lane].last_width = (float)comment_width;
    context->lanes[chosen_lane].last_speed = final_speed;
}

static void *danmaku_create(obs_data_t *settings, obs_source_t *source)
{
    struct danmaku_source *context = new danmaku_source();
    context->source = source;
    context->canvas_width = 1920;
    context->canvas_height = 1080;
    context->last_check_time_ms = 0;
    context->last_push_time_ms = 0;
    context->last_read_line_count = 0;
    
    srand((unsigned int)time(NULL));
    
    obs_source_update(source, settings);
    return context;
}

static void danmaku_destroy(void *data)
{
    struct danmaku_source *context = (struct danmaku_source*)data;
    if (context) {
        obs_enter_graphics();
        for (auto& c : context->active_comments) {
            if (c.texture) {
                gs_texture_destroy(c.texture);
            }
        }
        obs_leave_graphics();
        delete context;
    }
}

static void danmaku_update(void *data, obs_data_t *settings)
{
    struct danmaku_source *context = (struct danmaku_source*)data;
    std::lock_guard<std::mutex> lock(context->data_mutex);
    
    const char *path = obs_data_get_string(settings, "file_path");
    std::wstring new_path = L"";
    if (path) {
        int size_w = MultiByteToWideChar(CP_UTF8, 0, path, -1, nullptr, 0);
        if (size_w > 0) {
            std::wstring wpath(size_w - 1, 0);
            MultiByteToWideChar(CP_UTF8, 0, path, -1, &wpath[0], size_w);
            new_path = wpath;
        }
    }
    
    bool path_changed = (context->file_path != new_path);
    context->file_path = new_path;
    
    obs_data_t *font_obj = obs_data_get_obj(settings, "font");
    if (font_obj) {
        const char *face = obs_data_get_string(font_obj, "face");
        if (face && *face) {
            int size_w = MultiByteToWideChar(CP_UTF8, 0, face, -1, nullptr, 0);
            if (size_w > 0) {
                std::wstring wface(size_w - 1, 0);
                MultiByteToWideChar(CP_UTF8, 0, face, -1, &wface[0], size_w);
                context->font_name = wface;
            }
        }
        obs_data_release(font_obj);
    } else {
        context->font_name = L"MS UI Gothic";
    }
    
    context->font_size = (int)obs_data_get_int(settings, "font_size");
    if (context->font_size <= 0) context->font_size = 36;
    
    context->scroll_speed = (float)obs_data_get_double(settings, "speed");
    
    // Parse color values (convert from windows BGR structure)
    uint32_t color_val = (uint32_t)obs_data_get_int(settings, "color");
    uint8_t r = color_val & 0xFF;
    uint8_t g = (color_val >> 8) & 0xFF;
    uint8_t b = (color_val >> 16) & 0xFF;
    context->text_color = 0xFF000000 | (r << 16) | (g << 8) | b;
    
    uint32_t outline_val = (uint32_t)obs_data_get_int(settings, "outline_color");
    uint8_t or_ = outline_val & 0xFF;
    uint8_t og = (outline_val >> 8) & 0xFF;
    uint8_t ob = (outline_val >> 16) & 0xFF;
    context->outline_color = 0xFF000000 | (or_ << 16) | (og << 8) | ob;
    
    context->outline_size_percent = (float)obs_data_get_double(settings, "outline_size");
    context->max_comments = (int)obs_data_get_int(settings, "max_comments");
    context->min_push_interval_ms = (int)obs_data_get_int(settings, "interval");
    context->nigayaka_prob = (int)obs_data_get_int(settings, "nigayaka_prob");
    
    const char *dir = obs_data_get_string(settings, "direction");
    if (dir && strcmp(dir, "left_to_right") == 0) {
        context->direction = L"left_to_right";
    } else {
        context->direction = L"right_to_left";
    }
    
    context->loop_play = obs_data_get_bool(settings, "loop_play");
    context->enable_nigayaka = obs_data_get_bool(settings, "enable_nigayaka");
    context->check_interval_ms = (int)obs_data_get_int(settings, "check_interval");
    if (context->check_interval_ms < 100) context->check_interval_ms = 100;
    
    // Parse new color mode and display settings
    const char *color_m = obs_data_get_string(settings, "color_mode");
    if (color_m && strcmp(color_m, "platform") == 0) {
        context->color_mode = L"platform";
    } else if (color_m && strcmp(color_m, "user") == 0) {
        context->color_mode = L"user";
    } else {
        context->color_mode = L"fixed";
    }
    
    context->show_username = obs_data_get_bool(settings, "show_username");
    context->show_platform = obs_data_get_bool(settings, "show_platform");
    
    if (path_changed || context->loaded_comments.empty()) {
        try {
            if (std::filesystem::exists(context->file_path)) {
                context->last_file_time = std::filesystem::last_write_time(context->file_path);
            }
        } catch(...) {}
        
        LoadCommentFile(context);
        context->last_read_line_count = context->loaded_comments.size();
        
        while (!context->comment_queue.empty()) {
            context->comment_queue.pop();
        }
    }
}

static void danmaku_defaults(obs_data_t *settings)
{
    obs_data_set_default_string(settings, "file_path", "");
    obs_data_set_default_int(settings, "font_size", 36);
    obs_data_set_default_double(settings, "speed", 250.0);
    obs_data_set_default_int(settings, "color", 0xFFFFFFFF);
    obs_data_set_default_int(settings, "outline_color", 0xFF000000);
    obs_data_set_default_double(settings, "outline_size", 10.0);
    obs_data_set_default_int(settings, "max_comments", 100);
    obs_data_set_default_int(settings, "interval", 300);
    obs_data_set_default_int(settings, "nigayaka_prob", 10);
    obs_data_set_default_string(settings, "direction", "left_to_right");
    obs_data_set_default_bool(settings, "loop_play", true);
    obs_data_set_default_bool(settings, "enable_nigayaka", true);
    obs_data_set_default_int(settings, "check_interval", 1000);
    
    // Default values for expanded settings
    obs_data_set_default_string(settings, "color_mode", "fixed");
    obs_data_set_default_bool(settings, "show_username", true);
    obs_data_set_default_bool(settings, "show_platform", false);
}

static obs_properties_t *danmaku_get_properties(void *data)
{
    obs_properties_t *props = obs_properties_create();

    obs_properties_add_path(props, "file_path", obs_module_text("FilePath"), OBS_PATH_FILE, "*.txt", NULL);
    obs_properties_add_font(props, "font", obs_module_text("Font"));
    obs_properties_add_int(props, "font_size", obs_module_text("FontSize"), 10, 200, 2);
    obs_properties_add_float_slider(props, "speed", obs_module_text("Speed"), 50.0, 1000.0, 10.0);
    obs_properties_add_color(props, "color", obs_module_text("Color"));
    obs_properties_add_color(props, "outline_color", obs_module_text("OutlineColor"));
    obs_properties_add_float_slider(props, "outline_size", obs_module_text("OutlineSize"), 0.0, 50.0, 1.0);
    obs_properties_add_int(props, "max_comments", obs_module_text("MaxComments"), 1, 500, 5);
    obs_properties_add_int(props, "interval", obs_module_text("Interval"), 50, 5000, 50);
    obs_properties_add_int(props, "nigayaka_prob", obs_module_text("NigayakaProb"), 0, 100, 5);
    
    obs_property_t *dir_list = obs_properties_add_list(props, "direction", obs_module_text("Direction"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
    obs_property_list_add_string(dir_list, obs_module_text("Direction.LeftToRight"), "left_to_right");
    obs_property_list_add_string(dir_list, obs_module_text("Direction.RightToLeft"), "right_to_left");
    
    obs_properties_add_bool(props, "loop_play", obs_module_text("LoopPlay"));
    obs_properties_add_bool(props, "enable_nigayaka", obs_module_text("EnableNigayaka"));
    obs_properties_add_int(props, "check_interval", obs_module_text("CheckInterval"), 100, 10000, 100);

    // Color mode list selector
    obs_property_t *color_mode_list = obs_properties_add_list(props, "color_mode", obs_module_text("ColorMode"), OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
    obs_property_list_add_string(color_mode_list, obs_module_text("ColorMode.Fixed"), "fixed");
    obs_property_list_add_string(color_mode_list, obs_module_text("ColorMode.Platform"), "platform");
    obs_property_list_add_string(color_mode_list, obs_module_text("ColorMode.User"), "user");

    // Display prefixes checkboxes
    obs_properties_add_bool(props, "show_username", obs_module_text("ShowUsername"));
    obs_properties_add_bool(props, "show_platform", obs_module_text("ShowPlatform"));

    return props;
}

static uint32_t danmaku_get_width(void *data)
{
    struct danmaku_source *context = (struct danmaku_source*)data;
    return context->canvas_width > 0 ? (uint32_t)context->canvas_width : 1920;
}

static uint32_t danmaku_get_height(void *data)
{
    struct danmaku_source *context = (struct danmaku_source*)data;
    return context->canvas_height > 0 ? (uint32_t)context->canvas_height : 1080;
}

static void danmaku_video_tick(void *data, float seconds)
{
    struct danmaku_source *context = (struct danmaku_source*)data;
    std::lock_guard<std::mutex> lock(context->data_mutex);
    
    uint64_t now_ms = os_gettime_ns() / 1000000;
    
    // 1. Check and poll comment file changes
    if (now_ms - context->last_check_time_ms >= (uint64_t)context->check_interval_ms) {
        context->last_check_time_ms = now_ms;
        CheckAndReloadFile(context);
    }
    
    // 2. Update comments position & clean up out-of-screen textures
    bool is_l2r = (context->direction == L"left_to_right");
    for (auto it = context->active_comments.begin(); it != context->active_comments.end(); ) {
        if (is_l2r) {
            it->x += it->speed * seconds;
            if (it->x > (float)context->canvas_width) {
                if (it->texture) {
                    obs_enter_graphics();
                    gs_texture_destroy(it->texture);
                    obs_leave_graphics();
                }
                it = context->active_comments.erase(it);
                continue;
            }
        } else {
            it->x -= it->speed * seconds;
            if (it->x < -(float)it->width) {
                if (it->texture) {
                    obs_enter_graphics();
                    gs_texture_destroy(it->texture);
                    obs_leave_graphics();
                }
                it = context->active_comments.erase(it);
                continue;
            }
        }
        ++it;
    }
    
    // Update lane active status for collision checks
    for (auto& lane : context->lanes) {
        lane.active = false;
    }
    for (const auto& c : context->active_comments) {
        if (c.lane >= 0 && c.lane < context->num_lanes) {
            context->lanes[c.lane].active = true;
            context->lanes[c.lane].last_x = c.x;
            context->lanes[c.lane].last_width = (float)c.width;
            context->lanes[c.lane].last_speed = c.speed;
        }
    }
    
    // 3. Push new comment based on interval settings
    if (now_ms - context->last_push_time_ms >= (uint64_t)context->min_push_interval_ms) {
        std::wstring text_to_push = L"";
        bool has_comment = false;
        
        if (context->enable_nigayaka && context->nigayaka_prob > 0 && (rand() % 100) < context->nigayaka_prob) {
            text_to_push = NIGAYAKA_LIST[rand() % NIGAYAKA_COUNT];
            has_comment = true;
        }
        else if (!context->comment_queue.empty()) {
            text_to_push = context->comment_queue.front();
            context->comment_queue.pop();
            has_comment = true;
        }
        else if (context->loop_play && !context->loaded_comments.empty()) {
            text_to_push = context->loaded_comments[rand() % context->loaded_comments.size()];
            has_comment = true;
        }
        
        if (has_comment && !text_to_push.empty()) {
            PushComment(context, text_to_push);
            context->last_push_time_ms = now_ms;
        }
    }
}

static void danmaku_video_render(void *data, gs_effect_t *effect)
{
    struct danmaku_source *context = (struct danmaku_source*)data;
    
    obs_enter_graphics();
    
    // Draw each active comment using OBS helper API
    for (const auto& comment : context->active_comments) {
        if (comment.texture) {
            obs_source_draw(comment.texture, (int)comment.x, (int)comment.y, comment.width, comment.height, false);
        }
    }
    
    obs_leave_graphics();
    
    UNUSED_PARAMETER(effect);
}

struct obs_source_info danmaku_source_info = {};

void RegisterDanmakuSource(void)
{
    danmaku_source_info.id             = "niconico_danmaku_source";
    danmaku_source_info.type           = OBS_SOURCE_TYPE_INPUT;
    danmaku_source_info.output_flags   = OBS_SOURCE_VIDEO;
    danmaku_source_info.get_name       = danmaku_get_name;
    danmaku_source_info.create         = danmaku_create;
    danmaku_source_info.destroy        = danmaku_destroy;
    danmaku_source_info.update         = danmaku_update;
    danmaku_source_info.get_properties = danmaku_get_properties;
    danmaku_source_info.get_defaults   = danmaku_defaults;
    danmaku_source_info.get_width      = danmaku_get_width;
    danmaku_source_info.get_height     = danmaku_get_height;
    danmaku_source_info.video_tick     = danmaku_video_tick;
    danmaku_source_info.video_render   = danmaku_video_render;

    obs_register_source(&danmaku_source_info);
}
