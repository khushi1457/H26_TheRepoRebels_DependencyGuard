"""Match import names (what code writes) to distribution names (what pip installs).

They usually match after normalisation ('jinja2' -> 'Jinja2'), but many do not:
'import dateutil' comes from 'python-dateutil', 'import yaml' from 'PyYAML'.

KNOWN_ALIASES is general PyPI knowledge, not tied to any one repository.
It is deliberately small. Anything not covered is reported as 'unresolved'
instead of being guessed.

We do NOT use importlib.metadata.packages_distributions(): that describes
DependencyGuard's own environment, not the scanned repository's.
"""

from packaging.utils import canonicalize_name

# import top-level name -> candidate distributions (most common first)
KNOWN_ALIASES: dict[str, tuple[str, ...]] = {
    "attr": ("attrs",),
    "bs4": ("beautifulsoup4",),
    "cv2": ("opencv-python", "opencv-python-headless", "opencv-contrib-python"),
    "dateutil": ("python-dateutil",),
    "dotenv": ("python-dotenv",),
    "fitz": ("pymupdf",),
    "git": ("gitpython",),
    "google.protobuf": ("protobuf",),
    "jose": ("python-jose",),
    "jwt": ("pyjwt",),
    "magic": ("python-magic",),
    "multipart": ("python-multipart",),
    "OpenSSL": ("pyopenssl",),
    "PIL": ("pillow",),
    "pkg_resources": ("setuptools",),
    "psycopg2": ("psycopg2", "psycopg2-binary"),
    "serial": ("pyserial",),
    "setuptools": ("setuptools",),
    "skimage": ("scikit-image",),
    "sklearn": ("scikit-learn",),
    "slugify": ("python-slugify",),
    "socks": ("pysocks",),
    "telegram": ("python-telegram-bot",),
    "win32api": ("pywin32",),
    "yaml": ("pyyaml",),
    "zmq": ("pyzmq",),
}


def resolve(import_name: str, declared: set[str]) -> tuple[str, str]:
    """Return (distribution, method) for one top-level import name.

    `declared` holds normalized distribution names found in manifests.
    """
    exact = canonicalize_name(import_name)
    if exact in declared:
        return exact, "exact_name"

    candidates = [canonicalize_name(c) for c in KNOWN_ALIASES.get(import_name, ())]
    for candidate in candidates:
        if candidate in declared:
            return candidate, "known_alias"
    if candidates:
        return candidates[0], "known_alias"

    # Not declared and not a known alias: the import name is the best label we have.
    return exact, "unresolved"
