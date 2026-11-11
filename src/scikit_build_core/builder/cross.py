"""
Support for cross builds driven by a build frontend through the
``cross_build_wheel`` hook: the host (target) Python is described by a PEP 739
``build-details.json`` document, host requirements are installed in a plain
directory, and the toolchain is described by a mapping of the conventional
``CC``/``CXX``/... variables.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shlex
from pathlib import Path

from .._logging import logger

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from .._compat.typing import Self

__all__ = ["CrossBuild"]


def __dir__() -> list[str]:
    return __all__


# sysconfig platform prefix -> CMAKE_SYSTEM_NAME
_CMAKE_SYSTEM_NAMES = {
    "linux": "Linux",
    "macosx": "Darwin",
    "ios": "iOS",
    "android": "Android",
    "win": "Windows",
    "win32": "Windows",
    "emscripten": "Emscripten",
}

# toolchain variable -> CMake variable set in the generated toolchain file
_CMAKE_TOOLS = {
    "CC": "CMAKE_C_COMPILER",
    "CXX": "CMAKE_CXX_COMPILER",
    "FC": "CMAKE_Fortran_COMPILER",
    "AR": "CMAKE_AR",
    "RANLIB": "CMAKE_RANLIB",
    "STRIP": "CMAKE_STRIP",
    "NM": "CMAKE_NM",
    "OBJCOPY": "CMAKE_OBJCOPY",
    "OBJDUMP": "CMAKE_OBJDUMP",
    "READELF": "CMAKE_READELF",
    "LD": "CMAKE_LINKER",
    "PKG_CONFIG": "PKG_CONFIG_EXECUTABLE",
}

_CMAKE_FLAGS = {
    "CFLAGS": ["CMAKE_C_FLAGS_INIT"],
    "CXXFLAGS": ["CMAKE_CXX_FLAGS_INIT"],
    "LDFLAGS": [
        "CMAKE_EXE_LINKER_FLAGS_INIT",
        "CMAKE_SHARED_LINKER_FLAGS_INIT",
        "CMAKE_MODULE_LINKER_FLAGS_INIT",
    ],
}

# Environment variables forwarded verbatim to the CMake subprocess.
_ENV_PASSTHROUGH = (
    "CC",
    "CXX",
    "FC",
    "AR",
    "RANLIB",
    "STRIP",
    "CFLAGS",
    "CXXFLAGS",
    "LDFLAGS",
    "PKG_CONFIG",
    "PKG_CONFIG_PATH",
    "PKG_CONFIG_LIBDIR",
    "PKG_CONFIG_SYSROOT_DIR",
    "MACOSX_DEPLOYMENT_TARGET",
    "IPHONEOS_DEPLOYMENT_TARGET",
)


def _cmake_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _multiarch(build_details: Mapping[str, Any]) -> str | None:
    multiarch = build_details.get("implementation", {}).get("_multiarch")
    if isinstance(multiarch, str) and multiarch:
        return multiarch
    ext_suffix = build_details.get("abi", {}).get("extension_suffix", "")
    match = re.match(r"\.cpython-\d+[a-z]*-(.+)\.(so|pyd|dylib)$", ext_suffix)
    if match and "-" in match.group(1):
        return match.group(1)
    return None


@dataclasses.dataclass(frozen=True)
class CrossBuild:
    """
    The host Python and toolchain for a cross build, as given to the
    ``cross_build_wheel`` hook.
    """

    build_details: Mapping[str, Any]
    host_requires_directory: Path | None = None
    toolchain: Mapping[str, str] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_hook_args(
        cls,
        build_details: Mapping[str, Any] | None,
        host_requires_directory: str | None,
        toolchain: Mapping[str, str] | None,
    ) -> Self:
        if build_details is None:
            msg = "cross_build_wheel() requires the build_details argument"
            raise ValueError(msg)
        for key in ("language", "implementation", "abi"):
            if key not in build_details:
                msg = f"build-details.json is missing the {key!r} key"
                raise ValueError(msg)
        self = cls(
            build_details,
            Path(host_requires_directory) if host_requires_directory else None,
            dict(toolchain or {}),
        )
        # Validate early that we understand the platform.
        _ = self.sysconfig_platform
        return self

    # -- Python properties --------------------------------------------------

    @property
    def implementation_name(self) -> str:
        return str(self.build_details["implementation"]["name"])

    @property
    def is_cpython(self) -> bool:
        return self.implementation_name == "cpython"

    @property
    def version_info(self) -> tuple[int, int]:
        info = self.build_details["language"].get("version_info")
        if info:
            return (int(info["major"]), int(info["minor"]))
        major, minor = str(self.build_details["language"]["version"]).split(".")[:2]
        return (int(major), int(minor))

    @property
    def python_version(self) -> str:
        """Full version string, e.g. ``3.14.8``."""
        info = self.build_details["language"].get("version_info")
        if info:
            version = f"{info['major']}.{info['minor']}.{info.get('micro', 0)}"
            level = info.get("releaselevel", "final")
            if level != "final":
                version += f"{level[0]}{info.get('serial', 0)}"
            return version
        return str(self.build_details["language"]["version"])

    @property
    def abiflags(self) -> str:
        return "".join(self.build_details["abi"].get("flags", []))

    @property
    def is_free_threaded(self) -> bool:
        return "t" in self.abiflags

    @property
    def extension_suffix(self) -> str:
        return str(self.build_details["abi"]["extension_suffix"])

    @property
    def stable_abi_suffix(self) -> str | None:
        value = self.build_details["abi"].get("stable_abi_suffix")
        return str(value) if value else None

    def soabi(self, *, abi3: bool = False, abi3t: bool = False) -> str:
        """The SOABI, i.e. the extension suffix without leading dot and final extension."""
        if abi3t:
            return "abi3t"
        if abi3:
            return "abi3"
        suffix = self.extension_suffix.lstrip(".")
        return suffix.rsplit(".", 1)[0] if "." in suffix else ""

    def sosabi(self) -> str:
        """The stable ABI SOABI (e.g. ``abi3``), empty if the host has none."""
        suffix = self.stable_abi_suffix
        if not suffix:
            return ""
        suffix = suffix.lstrip(".")
        return suffix.rsplit(".", 1)[0] if "." in suffix else ""

    # -- Paths from build-details.json -------------------------------------

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return Path(os.path.normpath(Path(self.base_prefix or "") / candidate))

    @property
    def base_prefix(self) -> str | None:
        value = self.build_details.get("base_prefix")
        return str(value) if value else None

    @property
    def include_dir(self) -> Path | None:
        headers = self.build_details.get("c_api", {}).get("headers")
        return self._resolve(headers) if headers else None

    @property
    def pkgconfig_path(self) -> Path | None:
        path = self.build_details.get("c_api", {}).get("pkgconfig_path")
        return self._resolve(path) if path else None

    @property
    def libpython(self) -> Path | None:
        path = self.build_details.get("libpython", {}).get("dynamic")
        return self._resolve(path) if path else None

    @property
    def numpy_include_dir(self) -> Path | None:
        """NumPy headers from the host requirements directory, if installed there."""
        if self.host_requires_directory is None:
            return None
        for candidate in ("numpy/_core/include", "numpy/core/include"):
            path = self.host_requires_directory / candidate
            if path.is_dir():
                return path
        return None

    def host_pkgconfig_dirs(self) -> list[Path]:
        """``pkgconfig`` directories in the host requirements directory and host Python."""
        dirs: list[Path] = []
        if (
            self.host_requires_directory is not None
            and self.host_requires_directory.is_dir()
        ):
            found = {
                p.parent
                for p in self.host_requires_directory.rglob("*.pc")
                if p.parent.name == "pkgconfig"
            }
            dirs.extend(sorted(found))
        if self.pkgconfig_path is not None and self.pkgconfig_path.is_dir():
            dirs.append(self.pkgconfig_path)
        return dirs

    # -- Platform properties ------------------------------------------------

    @property
    def sysconfig_platform(self) -> str:
        platform = self.build_details.get("platform") or ""
        if platform:
            return str(platform)
        # python-build-standalone ships an empty platform key; reconstruct it
        # from the multiarch tuple.
        multiarch = _multiarch(self.build_details)
        if multiarch is None:
            msg = 'build-details.json has an empty "platform" and no "implementation._multiarch"'
            raise ValueError(msg)
        cpu, os_, *_ = [*multiarch.split("-"), ""]
        if os_.startswith("linux"):
            return f"linux-{cpu}"
        if os_.startswith("emscripten"):
            return f"emscripten-{cpu}"
        msg = f"cannot determine host platform from multiarch {multiarch!r}"
        raise ValueError(msg)

    @property
    def system_name(self) -> str:
        """``CMAKE_SYSTEM_NAME`` for the host."""
        prefix = self.sysconfig_platform.split("-", 1)[0]
        return _CMAKE_SYSTEM_NAMES.get(prefix, prefix.capitalize())

    @property
    def processor(self) -> str:
        """``CMAKE_SYSTEM_PROCESSOR`` for the host."""
        platform = self.sysconfig_platform
        if platform == "win32":
            return "x86"
        if platform.startswith("win-"):
            return platform.split("-", 1)[1].upper()
        if platform.startswith("linux-"):
            return platform.split("-", 1)[1]
        if platform.startswith("android-"):
            abi = platform.rsplit("-", 1)[1]
            return {
                "arm64_v8a": "aarch64",
                "armeabi_v7a": "armv7-a",
                "x86": "i686",
            }.get(abi, abi)
        if platform.startswith("ios-"):
            return platform.split("-")[2]
        # macosx-11.0-arm64, emscripten-4.0.9-wasm32
        return platform.rsplit("-", 1)[1]

    @property
    def is_windows(self) -> bool:
        return self.system_name == "Windows"

    @property
    def is_darwin(self) -> bool:
        return self.system_name in {"Darwin", "iOS"}

    # -- Wheel tags ---------------------------------------------------------

    @property
    def interpreter_tag(self) -> str:
        name = {
            "cpython": "cp",
            "pypy": "pp",
            "ironpython": "ip",
            "jython": "jy",
        }.get(self.implementation_name, self.implementation_name)
        major, minor = self.version_info
        return f"{name}{major}{minor}"

    @property
    def abi_tag(self) -> str:
        parts = self.extension_suffix.split(".")
        if len(parts) >= 3:
            soabi = parts[1]
            if soabi.startswith("cpython"):
                abi = "cp" + soabi.split("-")[1]
            elif soabi.startswith("cp"):
                abi = soabi.split("-")[0]
            elif soabi.startswith("pypy"):
                abi = "_".join(soabi.split("-")[:2])
            elif soabi.startswith("graalpy"):
                abi = "_".join(soabi.split("-")[:3])
            else:
                abi = soabi
            return abi.replace(".", "_").replace("-", "_")
        major, minor = self.version_info
        return f"cp{major}{minor}{self.abiflags}"

    @property
    def platform_tag(self) -> str:
        platform = self.sysconfig_platform
        if platform.startswith("macosx-"):
            _, version, arch = platform.split("-", 2)
            major, minor = (*map(int, version.split(".")), 0, 0)[:2]
            if arch == "arm64" and (major, minor) < (11, 0):
                major, minor = 11, 0
            if major >= 11:
                minor = 0
            return f"macosx_{major}_{minor}_{arch}"
        return platform.replace("-", "_").replace(".", "_").lower()

    # -- CMake integration --------------------------------------------------

    def apply_environment(self, env: dict[str, str]) -> None:
        """Make the toolchain visible to CMake and the tools it runs."""
        for name in _ENV_PASSTHROUGH:
            value = self.toolchain.get(name)
            if value is not None:
                env[name] = value
        # Point pkg-config at host artifacts only: the host requirements, the
        # host Python, and whatever the toolchain declares.
        pkgconfig_dirs = [os.fspath(p) for p in self.host_pkgconfig_dirs()]
        for name in ("PKG_CONFIG_LIBDIR", "PKG_CONFIG_PATH"):
            value = self.toolchain.get(name)
            if value:
                pkgconfig_dirs.extend(p for p in value.split(os.pathsep) if p)
        if pkgconfig_dirs:
            env["PKG_CONFIG_LIBDIR"] = os.pathsep.join(dict.fromkeys(pkgconfig_dirs))
            env.pop("PKG_CONFIG_PATH", None)

    def toolchain_file_contents(self) -> str:
        """Generate a CMake toolchain file from the host platform and toolchain."""
        lines = [
            "# Generated by scikit-build-core for a cross build",
            f"set(CMAKE_SYSTEM_NAME {self.system_name})",
            f"set(CMAKE_SYSTEM_PROCESSOR {self.processor})",
        ]
        for var, cmake_var in _CMAKE_TOOLS.items():
            value = self.toolchain.get(var)
            if value:
                # The value may carry arguments, such as a clang target triple.
                argv = shlex.split(value)
                lines.append(f"set({cmake_var} {_cmake_quote(argv[0])})")
                lang = {"CC": "C", "CXX": "CXX", "FC": "Fortran"}.get(var)
                if lang and len(argv) > 1:
                    flags = " ".join(argv[1:])
                    lines.append(
                        f"string(APPEND CMAKE_{lang}_FLAGS_INIT {_cmake_quote(' ' + flags)})"
                    )
        for var, cmake_vars in _CMAKE_FLAGS.items():
            value = self.toolchain.get(var)
            if value:
                lines.extend(
                    f"string(APPEND {cmake_var} {_cmake_quote(' ' + value)})"
                    for cmake_var in cmake_vars
                )
        sysroot = self.toolchain.get("PKG_CONFIG_SYSROOT_DIR") or self.toolchain.get(
            "SYSROOT"
        )
        if sysroot:
            lines.append(f"set(CMAKE_SYSROOT {_cmake_quote(sysroot)})")
        find_roots = []
        if self.host_requires_directory is not None:
            find_roots.append(os.fspath(self.host_requires_directory))
        if self.base_prefix:
            find_roots.append(self.base_prefix)
        if find_roots:
            lines.append(
                "list(APPEND CMAKE_FIND_ROOT_PATH "
                + " ".join(_cmake_quote(p) for p in find_roots)
                + ")"
            )
        lines += [
            # Programs run on the build machine; libraries, headers and packages
            # may come from the host roots or from the build machine's prefixes
            # (header-only packages such as pybind11 are build-system requirements).
            "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY BOTH)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)",
        ]
        return "\n".join(lines) + "\n"

    def write_toolchain_file(self, build_dir: Path) -> Path:
        path = build_dir / "scikit-build-core-toolchain.cmake"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.toolchain_file_contents(), encoding="utf-8")
        logger.debug("Generated cross toolchain file {}", path)
        return path
