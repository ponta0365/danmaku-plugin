cmake_minimum_required(VERSION 3.16)

project(obs-niconico-danmaku VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Configure output directories
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/output)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/output)

# ============================================================
# OBS SDK: Download headers only (no build)
# ============================================================
include(FetchContent)

set(OBS_VERSION "31.1.1")

FetchContent_Declare(obs-headers
    URL "https://github.com/obsproject/obs-studio/archive/refs/tags/${OBS_VERSION}.zip"
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)

FetchContent_GetProperties(obs-headers)
if(NOT obs-headers_POPULATED)
    FetchContent_Populate(obs-headers)
endif()

set(OBS_SRC_DIR "${obs-headers_SOURCE_DIR}")
message(STATUS "OBS headers dir: ${OBS_SRC_DIR}/libobs")

# Generate minimal obsconfig.h
file(WRITE "${CMAKE_BINARY_DIR}/generated/obsconfig.h"
    "#pragma once\n"
    "#define OBS_VERSION \"${OBS_VERSION}\"\n"
    "#define OBS_DATA_PATH \"../../data\"\n"
    "#define OBS_INSTALL_PREFIX \"\"\n"
    "#define OBS_PLUGIN_DESTINATION \"obs-plugins/64bit\"\n"
    "#define OBS_UNIX_STRUCTURE 0\n"
    "#define OBS_RELATIVE_PREFIX \"../../\"\n"
)

# obs.lib - auto-detect from obs-marker-plugin
set(libobs_LIB "" CACHE FILEPATH "Path to obs.lib import library")
if(NOT libobs_LIB OR libobs_LIB STREQUAL "")
    set(_auto_lib "${CMAKE_CURRENT_SOURCE_DIR}/../../OBS_字幕とチャプター作成プラグイン/.deps/lib/obs.lib")
    if(EXISTS "${_auto_lib}")
        set(libobs_LIB "${_auto_lib}" CACHE FILEPATH "" FORCE)
        message(STATUS "Auto-detected obs.lib: ${libobs_LIB}")
    else()
        message(WARNING "obs.lib not found. Set -Dlibobs_LIB=<path>")
    endif()
endif()

# obs-frontend-api.lib - auto-detect
set(obs_frontend_api_LIB "" CACHE FILEPATH "Path to obs-frontend-api.lib import library")
if(NOT obs_frontend_api_LIB OR obs_frontend_api_LIB STREQUAL "")
    set(_auto_frontend_lib "${CMAKE_CURRENT_SOURCE_DIR}/../../OBS_字幕とチャプター作成プラグイン/.deps/lib/obs-frontend-api.lib")
    if(EXISTS "${_auto_frontend_lib}")
        set(obs_frontend_api_LIB "${_auto_frontend_lib}" CACHE FILEPATH "" FORCE)
        message(STATUS "Auto-detected obs-frontend-api.lib: ${obs_frontend_api_LIB}")
    else()
        message(WARNING "obs-frontend-api.lib not found.")
    endif()
endif()

# Plugin target
add_library(obs-niconico-danmaku MODULE
    src/plugin-main.cpp
    src/danmaku-source.cpp
)

set_target_properties(obs-niconico-danmaku PROPERTIES
    PREFIX ""
    SUFFIX ".dll"
)

# Compiler flags
if(MSVC)
    target_compile_options(obs-niconico-danmaku PRIVATE /W4 /WX- /wd4100 /wd4456 /wd4457 /wd4458 /utf-8)
    target_compile_definitions(obs-niconico-danmaku PRIVATE _CRT_SECURE_NO_WARNINGS UNICODE _UNICODE)
else()
    target_compile_options(obs-niconico-danmaku PRIVATE -Wall -Wextra -Werror)
endif()

# Include directories - OBS source libobs + generated obsconfig.h
target_include_directories(obs-niconico-danmaku PRIVATE
    "${OBS_SRC_DIR}/libobs"
    "${OBS_SRC_DIR}/frontend/api"
    "${CMAKE_BINARY_DIR}/generated"
)

# Link Windows libraries and libobs
target_link_libraries(obs-niconico-danmaku PRIVATE gdiplus gdi32 user32)

if(libobs_LIB)
    target_link_libraries(obs-niconico-danmaku PRIVATE "${libobs_LIB}")
else()
    message(WARNING "No obs.lib - linking will fail.")
endif()

if(obs_frontend_api_LIB)
    target_link_libraries(obs-niconico-danmaku PRIVATE "${obs_frontend_api_LIB}")
endif()

# Post-build: copy locale files to build output (just to match structure)
add_custom_command(TARGET obs-niconico-danmaku POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory $<TARGET_FILE_DIR:obs-niconico-danmaku>/data/obs-plugins/obs-niconico-danmaku/locale
    COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_SOURCE_DIR}/data/locale $<TARGET_FILE_DIR:obs-niconico-danmaku>/data/obs-plugins/obs-niconico-danmaku/locale
    COMMENT "Copying localization files to build output directory"
)

# Install rules
install(TARGETS obs-niconico-danmaku
    LIBRARY DESTINATION obs-plugins/64bit
    RUNTIME DESTINATION obs-plugins/64bit
)
install(DIRECTORY data/locale/
    DESTINATION data/obs-plugins/obs-niconico-danmaku/locale
)
