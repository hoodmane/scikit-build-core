from __future__ import annotations

import os
import shutil
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest

from scikit_build_core.build import (
    cross_build_wheel,
    get_build_requires_for_cross_build_wheel,
    get_host_requires_for_cross_build_wheel,
)
from scikit_build_core.builder.cross import CrossBuild
from scikit_build_core.builder.wheel_tag import WheelTag

DIR = Path(__file__).parent.resolve()
SIMPLEST = DIR / "packages/simplest_c"


def build_details(
    platform: str = "linux-aarch64",
    *,
    version: tuple[int, int, int] = (3, 14, 8),
    flags: tuple[str, ...] = (),
    multiarch: str | None = "aarch64-linux-gnu",
    extension_suffix: str = ".cpython-314-aarch64-linux-gnu.so",
    implementation: str = "cpython",
    base_prefix: str = "/opt/host-python",
) -> dict[str, object]:
    major, minor, micro = version
    details: dict[str, object] = {
        "schema_version": "1.0",
        "base_prefix": base_prefix,
        "base_interpreter": f"./bin/python{major}.{minor}",
        "platform": platform,
        "language": {
            "version": f"{major}.{minor}",
            "version_info": {
                "major": major,
                "minor": minor,
                "micro": micro,
                "releaselevel": "final",
                "serial": 0,
            },
        },
        "implementation": {
            "name": implementation,
            "cache_tag": f"{implementation}-{major}{minor}",
        },
        "abi": {
            "flags": list(flags),
            "extension_suffix": extension_suffix,
            "stable_abi_suffix": ".abi3.so",
        },
        "libpython": {
            "dynamic": f"./lib/libpython{major}.{minor}.so",
            "link_extensions": False,
        },
        "c_api": {
            "headers": f"./include/python{major}.{minor}",
            "pkgconfig_path": "./lib/pkgconfig",
        },
    }
    if multiarch is not None:
        details["implementation"]["_multiarch"] = multiarch  # type: ignore[index]
    return details


@pytest.mark.parametrize(
    ("details", "system", "processor", "tag"),
    [
        (build_details(), "Linux", "aarch64", "cp314-cp314-linux_aarch64"),
        # python-build-standalone ships an empty platform key
        (build_details(""), "Linux", "aarch64", "cp314-cp314-linux_aarch64"),
        (
            build_details(
                "linux-aarch64",
                flags=("t",),
                extension_suffix=".cpython-314t-aarch64-linux-gnu.so",
            ),
            "Linux",
            "aarch64",
            "cp314-cp314t-linux_aarch64",
        ),
        (
            build_details(
                "macosx-11.0-arm64",
                multiarch="darwin",
                extension_suffix=".cpython-314-darwin.so",
            ),
            "Darwin",
            "arm64",
            "cp314-cp314-macosx_11_0_arm64",
        ),
        (
            build_details(
                "ios-13.0-arm64-iphoneos",
                multiarch="arm64-iphoneos",
                extension_suffix=".cpython-314-arm64-iphoneos.so",
            ),
            "iOS",
            "arm64",
            "cp314-cp314-ios_13_0_arm64_iphoneos",
        ),
        (
            build_details(
                "android-24-arm64_v8a",
                multiarch="aarch64-linux-android",
                extension_suffix=".cpython-314-aarch64-linux-android.so",
            ),
            "Android",
            "aarch64",
            "cp314-cp314-android_24_arm64_v8a",
        ),
        (
            build_details(
                "win-arm64", multiarch=None, extension_suffix=".cp314-win_arm64.pyd"
            ),
            "Windows",
            "ARM64",
            "cp314-cp314-win_arm64",
        ),
        (
            build_details(
                "linux-x86_64",
                version=(3, 11, 13),
                implementation="pypy",
                multiarch="x86_64-linux-gnu",
                extension_suffix=".pypy311-pp73-x86_64-linux-gnu.so",
            ),
            "Linux",
            "x86_64",
            "pp311-pypy311_pp73-linux_x86_64",
        ),
    ],
)
def test_cross_build_properties(details, system, processor, tag):
    cross = CrossBuild.from_hook_args(details, None, None)
    assert cross.system_name == system
    assert cross.processor == processor
    assert f"{cross.interpreter_tag}-{cross.abi_tag}-{cross.platform_tag}" == tag


def test_cross_build_pyemscripten_tag():
    details = build_details(
        "emscripten-5.0.3-wasm32",
        multiarch="wasm32-emscripten",
        extension_suffix=".cpython-314-wasm32-emscripten.so",
    )
    cross = CrossBuild.from_hook_args(details, None, None)
    assert cross.platform_tag == "emscripten_5_0_3_wasm32"
    assert cross.system_name == "Emscripten"
    assert cross.processor == "wasm32"
    details["pyemscripten_platform_version"] = "2026_0"
    cross = CrossBuild.from_hook_args(details, None, None)
    assert cross.platform_tag == "pyemscripten_2026_0_wasm32"


def test_cross_build_python_properties():
    cross = CrossBuild.from_hook_args(build_details(), None, None)
    assert cross.version_info == (3, 14)
    assert cross.python_version == "3.14.8"
    assert cross.is_cpython
    assert not cross.is_free_threaded
    assert cross.soabi() == "cpython-314-aarch64-linux-gnu"
    assert cross.soabi(abi3=True) == "abi3"
    assert cross.sosabi() == "abi3"
    assert cross.include_dir == Path("/opt/host-python/include/python3.14")
    assert cross.pkgconfig_path == Path("/opt/host-python/lib/pkgconfig")
    assert cross.libpython == Path("/opt/host-python/lib/libpython3.14.so")


def test_cross_build_invalid():
    with pytest.raises(ValueError, match="requires the build_details"):
        CrossBuild.from_hook_args(None, None, None)
    with pytest.raises(ValueError, match="missing the 'abi'"):
        CrossBuild.from_hook_args({"language": {}, "implementation": {}}, None, None)
    with pytest.raises(ValueError, match='empty "platform"'):
        CrossBuild.from_hook_args(
            build_details("", multiarch=None, extension_suffix=".so"), None, None
        )


def test_cross_build_host_requires(tmp_path):
    host = tmp_path / "host-requires"
    (host / "numpy" / "_core" / "include" / "numpy").mkdir(parents=True)
    (host / "numpy" / "_core" / "lib" / "pkgconfig").mkdir(parents=True)
    (host / "numpy" / "_core" / "lib" / "pkgconfig" / "numpy.pc").write_text("")
    cross = CrossBuild.from_hook_args(build_details(), os.fspath(host), None)
    assert cross.numpy_include_dir == host / "numpy" / "_core" / "include"
    assert cross.host_pkgconfig_dirs() == [
        host / "numpy" / "_core" / "lib" / "pkgconfig"
    ]

    env: dict[str, str] = {"PKG_CONFIG_PATH": "/build-machine/pkgconfig"}
    cross.apply_environment(env)
    assert env["PKG_CONFIG_LIBDIR"] == os.fspath(
        host / "numpy" / "_core" / "lib" / "pkgconfig"
    )
    assert "PKG_CONFIG_PATH" not in env


def test_cross_build_toolchain_file(tmp_path):
    toolchain = {
        "CC": "aarch64-linux-gnu-gcc",
        "CXX": "clang++ --target=aarch64-linux-gnu",
        "AR": "aarch64-linux-gnu-ar",
        "CFLAGS": "-O2 -g",
        "LDFLAGS": "-Wl,-z,relro",
        "PKG_CONFIG_SYSROOT_DIR": "/sysroot",
    }
    cross = CrossBuild.from_hook_args(
        build_details(), os.fspath(tmp_path / "host"), toolchain
    )
    text = cross.toolchain_file_contents()
    assert "set(CMAKE_SYSTEM_NAME Linux)" in text
    assert "set(CMAKE_SYSTEM_PROCESSOR aarch64)" in text
    assert 'set(CMAKE_C_COMPILER "aarch64-linux-gnu-gcc")' in text
    assert 'set(CMAKE_CXX_COMPILER "clang++")' in text
    assert 'string(APPEND CMAKE_CXX_FLAGS_INIT " --target=aarch64-linux-gnu")' in text
    assert 'set(CMAKE_AR "aarch64-linux-gnu-ar")' in text
    assert 'string(APPEND CMAKE_C_FLAGS_INIT " -O2 -g")' in text
    assert 'string(APPEND CMAKE_MODULE_LINKER_FLAGS_INIT " -Wl,-z,relro")' in text
    assert 'set(CMAKE_SYSROOT "/sysroot")' in text
    assert f'"{tmp_path / "host"}" "/opt/host-python"' in text
    assert "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)" in text

    env: dict[str, str] = {}
    cross.apply_environment(env)
    assert env["CC"] == "aarch64-linux-gnu-gcc"
    assert env["CFLAGS"] == "-O2 -g"

    path = cross.write_toolchain_file(tmp_path / "build")
    assert path.read_text() == text


def test_wheel_tag_cross():
    cross = CrossBuild.from_hook_args(build_details(), None, None)
    tags = WheelTag.compute_best([], cross=cross)
    assert str(tags) == "cp314-cp314-linux_aarch64"

    tags = WheelTag.compute_best([], py_api="cp312", cross=cross)
    assert str(tags) == "cp312-abi3-linux_aarch64"

    # py-api newer than the host interpreter is ignored
    tags = WheelTag.compute_best([], py_api="cp315", cross=cross)
    assert str(tags) == "cp314-cp314-linux_aarch64"

    tags = WheelTag.compute_best([], py_api="py3", cross=cross)
    assert str(tags) == "py3-none-linux_aarch64"

    tags = WheelTag.compute_best([], root_is_purelib=True, cross=cross)
    assert str(tags) == "py3-none-any"


def test_wheel_tag_cross_free_threaded():
    details = build_details(
        version=(3, 15, 0),
        flags=("t",),
        extension_suffix=".cpython-315t-aarch64-linux-gnu.so",
    )
    cross = CrossBuild.from_hook_args(details, None, None)
    assert str(WheelTag.compute_best([], cross=cross)) == "cp315-cp315t-linux_aarch64"
    assert (
        str(WheelTag.compute_best([], py_api="cp315t", cross=cross))
        == "cp315-abi3t-linux_aarch64"
    )
    # classic abi3 is not available on free-threaded builds
    assert (
        str(WheelTag.compute_best([], py_api="cp312", cross=cross))
        == "cp315-cp315t-linux_aarch64"
    )


def test_wheel_tag_cross_ignores_environment(monkeypatch):
    monkeypatch.setenv("_PYTHON_HOST_PLATFORM", "macosx-11.0-arm64")
    cross = CrossBuild.from_hook_args(build_details(), None, None)
    assert (
        str(WheelTag.compute_best(["x86_64"], cross=cross))
        == "cp314-cp314-linux_aarch64"
    )


def test_get_requires_hooks(monkeypatch):
    monkeypatch.chdir(SIMPLEST)
    assert get_host_requires_for_cross_build_wheel({}, build_details()) == []
    # same build machine requirements as a native build
    from scikit_build_core.build import get_requires_for_build_wheel

    assert get_build_requires_for_cross_build_wheel(
        {}, build_details()
    ) == get_requires_for_build_wheel({})


def test_cross_build_wheel_requires_build_details(tmp_path, monkeypatch):
    monkeypatch.chdir(SIMPLEST)
    with pytest.raises(SystemExit):
        cross_build_wheel(str(tmp_path), build_details=None)


def running_interpreter_build_details() -> dict[str, object]:
    """Describe the running interpreter as a build-details.json document."""
    version = sys.version_info
    multiarch = getattr(sys.implementation, "_multiarch", None)
    libdir = sysconfig.get_config_var("LIBDIR") or ""
    return {
        "schema_version": "1.0",
        "base_prefix": sys.base_prefix,
        "base_interpreter": sys.executable,
        "platform": sysconfig.get_platform(),
        "language": {
            "version": f"{version.major}.{version.minor}",
            "version_info": {
                "major": version.major,
                "minor": version.minor,
                "micro": version.micro,
                "releaselevel": version.releaselevel,
                "serial": version.serial,
            },
        },
        "implementation": {
            "name": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag,
            **({"_multiarch": multiarch} if multiarch else {}),
        },
        "abi": {
            "flags": list(sys.abiflags),
            "extension_suffix": sysconfig.get_config_var("EXT_SUFFIX"),
            "stable_abi_suffix": ".abi3.so",
        },
        "libpython": {
            "dynamic": str(
                Path(libdir) / (sysconfig.get_config_var("LDLIBRARY") or "")
            ),
            "link_extensions": False,
        },
        "c_api": {
            "headers": sysconfig.get_path("include"),
            "pkgconfig_path": str(Path(libdir) / "pkgconfig"),
        },
    }


# A "cross" build whose host is the machine we are running on: exercises the
# whole code path (generated toolchain file, FindPython wrapper, tags from
# build-details.json) without needing a cross compiler.
@pytest.mark.compile
@pytest.mark.configure
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython only")
@pytest.mark.skipif(shutil.which("cc") is None, reason="requires a C compiler")
def test_cross_build_wheel_same_machine(tmp_path, monkeypatch):
    dist = tmp_path.resolve() / "dist"
    monkeypatch.chdir(SIMPLEST)
    monkeypatch.delenv("CMAKE_TOOLCHAIN_FILE", raising=False)

    out = cross_build_wheel(
        str(dist),
        {"build-dir": str(tmp_path / "build")},
        build_details=running_interpreter_build_details(),
        toolchain={"CC": os.environ.get("CC", "cc")},
    )

    native = str(WheelTag.compute_best([]))
    assert out == f"simplest-0.0.1-{native}.whl"
    with zipfile.ZipFile(dist / out) as zf:
        names = zf.namelist()
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    assert f"simplest/_module{ext_suffix}" in names
    assert (tmp_path / "build" / "scikit-build-core-toolchain.cmake").exists()
    cache = (tmp_path / "build" / "CMakeCache.txt").read_text()
    assert "scikit-build-core-toolchain.cmake" in cache
