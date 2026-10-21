"""Source-only import guard for the separate Q03 consumer-observer test lane.

Mounted as /binding/sitecustomize.py, never installed into the native image.
Every fresh spawn interpreter runs this guard before importing worker code.
"""
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import stat
import sys


def require(value, message):
    if not value:
        raise RuntimeError(message)


def file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_tree(root):
    """Hash a regular-file Git subtree, including modes; retain source digests."""
    sources = {}

    def object_hash(kind, raw):
        return hashlib.sha1(kind + b" " + str(len(raw)).encode() + b"\0" + raw).digest()

    def walk(directory):
        entries = []
        for path in directory.iterdir():
            info = path.lstat()
            require(not path.is_symlink(), "test subtree contains an alias")
            if stat.S_ISDIR(info.st_mode):
                mode, value = b"40000", walk(path)
                key = path.name.encode() + b"/"
            else:
                require(stat.S_ISREG(info.st_mode), "test subtree is not regular")
                raw = path.read_bytes()
                mode = b"100755" if info.st_mode & 0o111 else b"100644"
                value, key = object_hash(b"blob", raw), path.name.encode()
                sources[str(path)] = hashlib.sha256(raw).hexdigest()
            entries.append((key, mode + b" " + path.name.encode() + b"\0" + value))
        return object_hash(b"tree", b"".join(row[1] for row in sorted(entries)))

    require(root.is_dir() and root.resolve() == root, "test root is not canonical")
    return walk(root).hex(), sources


class SourceLoader(importlib.machinery.SourceFileLoader):
    """Compile the verified bytes directly; never read or write a pyc."""

    def __init__(self, name, path, expected):
        super().__init__(name, path)
        self.expected = expected

    def get_code(self, fullname):
        path = Path(self.path)
        require(path.resolve() == path, "import path became an alias")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == self.expected,
                "import source changed: " + self.path)
        return compile(raw, self.path, "exec", dont_inherit=True)


class SourceGuard(importlib.abc.MetaPathFinder):
    def __init__(self, tests, runtime, sources, native_paths):
        self.tests, self.runtime, self.sources = tests, runtime, sources
        self.origins = dict(native_paths)
        for source in sources:
            file = Path(source)
            if file.suffix != ".py":
                continue
            if file.is_relative_to(runtime):
                parts = ["vllm", *file.relative_to(runtime).with_suffix("").parts]
            else:
                parts = ["tests", *file.relative_to(tests).with_suffix("").parts]
            if parts[-1] == "__init__":
                parts.pop()
            self.origins[".".join(parts)] = source
        for name in ("q03_use_owner_worker", "q03_use_observer", "q03_use_owner_fixture"):
            self.origins[name] = str(tests / "compile/fullgraph" / (name + ".py"))

    def find_spec(self, fullname, path=None, target=None):
        test = (fullname == "tests" or fullname.startswith("tests.")
                or fullname in ("q03_use_owner_worker", "q03_use_observer",
                                "q03_use_owner_fixture"))
        native = fullname == "vllm" or fullname.startswith("vllm.")
        if not (test or native):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        require(spec is not None and spec.origin is not None,
                "bound import has no source origin: " + fullname)
        origin = Path(spec.origin)
        root = self.tests if test else self.runtime
        require(origin.is_absolute() and origin.resolve() == origin
                and origin.is_relative_to(root), "bound import escaped source: " + fullname)
        if fullname in self.origins:
            require(str(origin) == self.origins[fullname],
                    "pinned module origin differs: " + fullname)
        if test:
            require(str(origin) in self.sources and origin.suffix == ".py",
                    "test import is not pinned Python source: " + fullname)
        if str(origin) in self.sources:
            spec.loader = SourceLoader(fullname, str(origin), self.sources[str(origin)])
        return spec


def activate(spec, *, validation, prefix, runtime_manifest):
    """Validate one interpreter before test or native worker imports."""
    require(os.environ.get("PYTHONPYCACHEPREFIX") == str(prefix)
            and sys.pycache_prefix == str(prefix) and sys.dont_write_bytecode
            and not prefix.exists() and not prefix.is_symlink(),
            "consumer imports require a fresh absent bytecode prefix")
    require(os.environ.get("PYTHONSAFEPATH") == "1" and sys.flags.safe_path,
            "consumer interpreter must not prepend an unsafe working directory")
    require(os.environ.get("VLLM_WORKER_MULTIPROC_METHOD") == "spawn",
            "consumer lane requires fresh spawn workers")
    require(os.environ.get("VLLM_ALLOW_INSECURE_SERIALIZATION", "0") == "0",
            "consumer lane requires native safe serialization")
    require(not any(name == "tests" or name.startswith("tests.")
                    or name == "vllm" or name.startswith("vllm.")
                    or name in ("q03_use_owner_worker", "q03_use_observer")
                    for name in sys.modules), "consumer guard installed after imports")
    require(validation.resolve() == validation and not (validation / "vllm").exists(),
            "consumer overlay must contain tests only")
    closure = spec["consumer_binding"]
    tree, sources = source_tree(validation / "tests")
    require(tree == closure["tests_subtree"], "mounted test subtree differs")
    for relative, expected in closure["test_sources"].items():
        require(sources.get(str(validation / relative)) == expected,
                "pinned test source differs: " + relative)
    raw = runtime_manifest.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == spec["runtime_manifest_sha256"],
            "runtime manifest changed")
    runtime = json.loads(raw)
    require(runtime["source_head"] == spec["source_head"]
            and runtime["installation"]["mode"] == "image-source",
            "runtime installation identity differs")
    runtime_root = Path(runtime["installation"]["package_root"])
    require(runtime_root.is_absolute() and runtime_root.resolve() == runtime_root
            and not runtime_root.is_relative_to(validation), "baked runtime path differs")
    for relative, expected in closure["runtime_sources"].items():
        path = runtime_root / relative
        require(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest()
                == expected, "runtime source differs: " + relative)
        sources[str(path)] = expected
    require(set(runtime["native_sha256"]) == set(runtime["native_paths"])
            == {"vllm._C_stable_libtorch", "vllm._moe_C_stable_libtorch"},
            "native manifest inventory differs")
    for name, expected in runtime["native_sha256"].items():
        path = Path(runtime["native_paths"][name])
        require(path.resolve() == path and path.is_relative_to(runtime_root)
                and file_sha256(path) == expected,
                "native library bytes differ: " + name)
    sys.path[:0] = [str(validation), str(validation / "tests/compile/fullgraph")]
    guard = SourceGuard(validation / "tests", runtime_root, sources,
                        runtime["native_paths"])
    sys.meta_path.insert(0, guard)
    return guard


def startup():
    path = Path("/binding/sitecustomize.py")
    require(Path(__file__) == path and path.resolve() == path
            and hashlib.sha256(path.read_bytes()).hexdigest()
            == os.environ["Q03_CONSUMER_SITE_SHA256"], "consumer guard source differs")
    require(os.environ.get("PYTHONPATH") == os.environ["Q03_EXPECTED_PYTHONPATH"],
            "consumer import path differs")
    raw = Path("/runtime/owner-spec.json").read_bytes()
    require(hashlib.sha256(raw).hexdigest() == os.environ["Q03_CONSUMER_SPEC_SHA256"],
            "consumer complete spec differs")
    activate(json.loads(raw), validation=Path("/validation"),
             prefix=Path("/cache/consumer-bytecode"),
             runtime_manifest=Path("/runtime/runtime-manifest.json"))
    wheel = Path("/pytest-dependency/tblib-3.1.0-py3-none-any.whl")
    require(hashlib.sha256(wheel.read_bytes()).hexdigest()
            == "670bb4582578134b3d81a84afa1b016128b429f3d48e6cbbaecc9d15675e984e",
            "tblib wheel bytes differ")
    sys.path.append(str(wheel))


if __name__ == "sitecustomize":
    try:
        startup()
    except BaseException as error:
        # Python normally logs and ignores sitecustomize failures. Never continue.
        sys.stderr.write("Q03 consumer import guard failed: " + str(error) + "\n")
        os._exit(78)
