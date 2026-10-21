cmake_minimum_required(VERSION 3.20)
project(Counterback VERSION 0.1.0 LANGUAGES CXX)

include(CTest)
option(COUNTERBACK_PYTHON_TESTS "Run Python/C++ historical replay differential tests" OFF)
add_subdirectory(engine)
