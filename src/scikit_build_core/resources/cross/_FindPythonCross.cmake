# Wrapper around FindPython / FindPython3 used by scikit-build-core for cross
# builds. ``_skbuild_cross_prefix`` is set by the including module.
#
# When ``CMAKE_CROSSCOMPILING`` is set and the ``Interpreter`` component is
# requested, FindPython computes ``<prefix>_SOABI`` during the interpreter
# phase, before it has located the host ``python3.X-config`` tool, so the value
# comes out empty and ``python_add_library(... WITH_SOABI)`` produces a module
# without an ABI tag. Fill it in from the SOABI scikit-build-core computed from
# the host ``build-details.json`` instead.

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

if(CMAKE_CROSSCOMPILING)
  if(SKBUILD_SOABI AND NOT ${_skbuild_cross_prefix}_SOABI AND NOT SKBUILD_SABI_COMPONENT)
    set(${_skbuild_cross_prefix}_SOABI "${SKBUILD_SOABI}")
  endif()
  if(SKBUILD_SOSABI AND NOT ${_skbuild_cross_prefix}_SOSABI)
    set(${_skbuild_cross_prefix}_SOSABI "${SKBUILD_SOSABI}")
  endif()
endif()
