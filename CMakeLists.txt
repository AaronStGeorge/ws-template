# Contributor-facing build and test orchestrator for the imp tools: builds
# build/bin/{impd,impctl,impwatch} from tools/ and registers the end-to-end
# chain check. The Go targets always run — go's own incrementality makes
# re-running cheap, so no source-globbing games.
cmake_minimum_required(VERSION 3.28)
project(imp NONE)

find_program(GO_EXECUTABLE go REQUIRED)
find_program(PYTHON3_EXECUTABLE python3 REQUIRED)

set(IMP_BIN_DIR ${CMAKE_BINARY_DIR}/bin)

add_custom_target(impd ALL
  COMMAND ${GO_EXECUTABLE} build -o ${IMP_BIN_DIR}/impd ./tools/impd
  WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
  VERBATIM)
add_custom_target(impctl ALL
  COMMAND ${GO_EXECUTABLE} build -o ${IMP_BIN_DIR}/impctl ./tools/impctl
  WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
  VERBATIM)
add_custom_target(impwatch ALL
  COMMAND ${GO_EXECUTABLE} build -o ${IMP_BIN_DIR}/impwatch ./tools/impwatch
  WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
  VERBATIM)

enable_testing()
add_test(NAME e2e_chain
  COMMAND ${PYTHON3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/test/e2e_chain_test.py
          --bin-dir ${IMP_BIN_DIR} --repo-root ${CMAKE_SOURCE_DIR})

add_custom_target(check
  COMMAND ${CMAKE_CTEST_COMMAND} --test-dir ${CMAKE_BINARY_DIR} --output-on-failure
  DEPENDS impd impctl impwatch
  VERBATIM)
