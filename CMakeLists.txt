# =================================================================================================
# Game module of this project
# =================================================================================================
# Builds the C++ in game/ into build/<config>/bin/test_project.so (<config>: debug, release, release_with_debug_info),
# which the engine loads when it opens this project.
#
#   cmake --preset debug   && cmake --build --preset debug        (or in the editor: Tools > Game module > Build and load)
#   cmake --preset release && cmake --build --preset release
#
# GLUTTONY_DIR: the engine's source directory. Default: ../Gluttony next to this project, or the GLUTTONY_DIR
# environment variable. The engine passes it itself when it builds the module.
# =================================================================================================

cmake_minimum_required(VERSION 3.25)

set(GLUTTONY_DIR "$ENV{GLUTTONY_DIR}" CACHE PATH "Gluttony engine source directory")
if(NOT GLUTTONY_DIR)
    set(GLUTTONY_DIR "${CMAKE_CURRENT_LIST_DIR}/../Gluttony" CACHE PATH "Gluttony engine source directory" FORCE)
endif()
include("${GLUTTONY_DIR}/cmake/gluttony_game_module.cmake")            # before project(): selects the engine's compiler

project(test_project LANGUAGES CXX)

# Everything below game/, registered from namespace test_project (scripts, construct packs, on_module_load)
gluttony_add_game_module(test_project)
