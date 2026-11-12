# Wrapper around FindPython / FindPython3 used by scikit-build-core for cross
# builds. ``_skbuild_cross_prefix`` is set by the including module.
#
# When ``CMAKE_CROSSCOMPILING`` is set and the ``Interpreter`` component is
# requested, FindPython derives ``<prefix>_SOABI`` from the interpreter rather
# than from the host development artifacts: with CMake < 4.1 the value comes out
# empty (the interpreter is not queried and the host ``python3.X-config`` tool
# has not been located yet), with CMake >= 4.1 and policy CMP0190 OLD it is the
# *build* machine's SOABI. Either way ``python_add_library(... WITH_SOABI)``
# would name the module wrongly, so always use the SOABI scikit-build-core
# computed from the host ``build-details.json``.

# Find the next FindPython module in the search path, skipping this directory.
unset(_skbuild_cross_real_module)
foreach(_skbuild_cross_dir IN LISTS CMAKE_MODULE_PATH)
  if(NOT _skbuild_cross_dir STREQUAL CMAKE_CURRENT_LIST_DIR
     AND EXISTS "${_skbuild_cross_dir}/Find${_skbuild_cross_prefix}.cmake")
    set(_skbuild_cross_real_module "${_skbuild_cross_dir}/Find${_skbuild_cross_prefix}.cmake")
    break()
  endif()
endforeach()
if(NOT _skbuild_cross_real_module)
  set(_skbuild_cross_real_module "${CMAKE_ROOT}/Modules/Find${_skbuild_cross_prefix}.cmake")
endif()
unset(_skbuild_cross_dir)

include("${_skbuild_cross_real_module}")
unset(_skbuild_cross_real_module)

if(CMAKE_CROSSCOMPILING AND SKBUILD_CROSS_BUILD)
  if(SKBUILD_SOABI AND NOT SKBUILD_SABI_COMPONENT)
    set(${_skbuild_cross_prefix}_SOABI "${SKBUILD_SOABI}")
  endif()
  if(SKBUILD_SOSABI)
    set(${_skbuild_cross_prefix}_SOSABI "${SKBUILD_SOSABI}")
  endif()
endif()
