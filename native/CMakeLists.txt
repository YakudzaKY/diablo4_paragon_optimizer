cmake_minimum_required(VERSION 3.20)
project(paragon_optimizer_native LANGUAGES CXX)

include(FetchContent)

FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(nlohmann_json)

add_executable(paragon_optimize optimizer_cli.cpp)
target_compile_features(paragon_optimize PRIVATE cxx_std_17)
target_link_libraries(paragon_optimize PRIVATE nlohmann_json::nlohmann_json)

set_target_properties(paragon_optimize PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/../bin"
)

foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(paragon_optimize PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/../bin"
    )
endforeach()
