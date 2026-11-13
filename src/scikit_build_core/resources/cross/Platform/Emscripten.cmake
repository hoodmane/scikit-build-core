# Platform module used by scikit-build-core for Emscripten cross builds.
#
# CMake loads Platform/<CMAKE_SYSTEM_NAME>.cmake after the toolchain file; the
# generated toolchain file prepends this directory to CMAKE_MODULE_PATH so that
# this module is used instead of CMake's built-in one (which names shared
# libraries ".wasm") or Emscripten's (which declares that shared libraries are
# unsupported). Python extension modules are Emscripten side modules, so both
# need adjusting.
if(SKBUILD_EMSCRIPTEN_TOOLCHAIN_FILE)
  include("${SKBUILD_EMSCRIPTEN_TOOLCHAIN_FILE}")
endif()
set_property(GLOBAL PROPERTY TARGET_SUPPORTS_SHARED_LIBS TRUE)
set(CMAKE_SHARED_LIBRARY_SUFFIX ".so")
set(CMAKE_SHARED_MODULE_SUFFIX ".so")
# Pyodide does not use versioned sonames for side modules.
if(NOT DEFINED CMAKE_PLATFORM_NO_VERSIONED_SONAME)
  set(CMAKE_PLATFORM_NO_VERSIONED_SONAME 1)
endif()
