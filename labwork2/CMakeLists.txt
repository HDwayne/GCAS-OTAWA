cmake_minimum_required (VERSION 2.6)

project (LabWork)

# C++ 11
if(CMAKE_VERSION LESS "3.1")
	add_compile_options(--std=c++11)
	message(STATUS "C++11 set using cflags")
else()
	set(CMAKE_CXX_STANDARD 11)
	message(STATUS "C++ set using CMAKE_CXX_STANDARD")
endif()

# look for OTAWA
if(NOT OTAWA_CONFIG)
	find_program(OTAWA_CONFIG otawa-config DOC "path to otawa-config")
	if(NOT OTAWA_CONFIG)
		message(FATAL_ERROR "ERROR: otawa-config is required !")
	endif()
endif()
message(STATUS "otawa-config at ${OTAWA_CONFIG}")
execute_process(COMMAND "${OTAWA_CONFIG}" --cflags ${MODULES} OUTPUT_VARIABLE OTAWA_CFLAGS OUTPUT_STRIP_TRAILING_WHITESPACE)
execute_process(COMMAND "${OTAWA_CONFIG}" --libs -r ${MODULES}  OUTPUT_VARIABLE OTAWA_LDFLAGS OUTPUT_STRIP_TRAILING_WHITESPACE)
execute_process(COMMAND "${OTAWA_CONFIG}" --prefix OUTPUT_VARIABLE OTAWA_PREFIX OUTPUT_STRIP_TRAILING_WHITESPACE)

# program building
add_executable(labwork labwork.cpp)
set_property(TARGET labwork PROPERTY PREFIX "")
set_property(TARGET labwork PROPERTY COMPILE_FLAGS "${OTAWA_CFLAGS}")
target_link_libraries(labwork "${OTAWA_LDFLAGS}")


# packaging
execute_process(COMMAND "date +%y%m%d" OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE DATE)

add_custom_target("dist"
	COMMAND "mkdir" "-p" "labwork2"
	COMMAND "cp" "CMakeLists.txt" "labwork2"
	COMMAND "./hide.py" "labwork.cpp" "labwork2/labwork.cpp"
	COMMAND "mkdir" "-p" "labwork2/test"
	COMMAND "cp" "test/*.elf" "labwork2/test"
	COMMAND "cp" "test/*.c" "labwork2/test"
	COMMAND "cp" "test/*.ff" "labwork2/test"
	COMMAND "tar" "cvfz" "labwork2.tgz" "labwork2")

add_custom_target("otawa-dist"
	COMMAND "tar" "cvfz" "otawa.tgz" otawa/bin otawa/lib otawa/share otawa/include)
