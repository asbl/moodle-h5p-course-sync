from __future__ import annotations

import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
COURSES_DIR = ROOT_DIR / "courses"
DEFAULT_PORT = 8765
DOTENV_FILE = ROOT_DIR / ".env"
H5P_RUNTIME_DIR = ROOT_DIR / ".h5p-runtime"
H5P_RUNTIME_CONTENT_DIR = H5P_RUNTIME_DIR / "content"
H5P_RUNTIME_LIBRARIES_DIR = H5P_RUNTIME_DIR / "libraries"
H5P_RUNTIME_DOWNLOADS_DIR = H5P_RUNTIME_DIR / "downloads"
H5P_RUNTIME_PORT = 8766
RUNTIME_PROXY_PREFIX = "/runtime"
H5P_LIBRARY_RELEASE_REPO = "asbl/h5p-content-python-question"
H5P_LIBRARY_RELEASE_TAG = "v6.73.0"
H5P_LIBRARY_ASSET_PREFIXES = {
    "H5P.PythonQuestion": "H5P.PythonQuestion-6.73_",
    "H5P.CodeQuestion": "H5P.CodeQuestion-6.73_",
    "H5P.LibCodeTools": "H5P.LibCodeTools-6.73_",
    "H5PEditor.CodeWidget": "H5PEditor.CodeWidget-6.73_",
}
CUSTOM_H5P_LIBRARY_SHORT_NAMES = {
    "H5P.PythonQuestion": "h5p-python-question",
    "H5P.CodeQuestion": "h5p-code-question",
    "H5P.LibCodeTools": "h5p-lib-code-tools",
    "H5PEditor.CodeWidget": "h5p-editor-code-widget",
    "H5P.MathDisplay": "h5p-math-display",
}
PYTHON_QUESTION_MACHINE_NAME = "H5P.PythonQuestion"
PLACEHOLDER_TEMPLATE = "[[[PYTHON_QUESTION:{identifier}]]]"
SYNC_METADATA_FILE = ".course-sync.json"
H5P_SIDECAR_DIRNAME = "h5p-imports"

TAG_RE = re.compile(r"<PythonQuestion(?P<attrs>.*?)\/>", re.DOTALL)
FENCE_RE = re.compile(r"```(?P<spec>[^\n`]*)\n(?P<body>.*?)\n```", re.DOTALL)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
H5P_EMBED_IFRAME_RE = re.compile(r'<iframe[^>]+src="(?P<src>[^"]+/h5p/embed\.php\?[^"]+)"', re.IGNORECASE)
MBZ_LINK_RE = re.compile(r'https?://[^"\']+\.mbz', re.IGNORECASE)



def build_app_config() -> "AppConfig":
    from scripts.config.app_config import AppConfig

    return AppConfig(
        root_dir=ROOT_DIR,
        courses_dir=COURSES_DIR,
        default_port=DEFAULT_PORT,
        dotenv_file=DOTENV_FILE,
        h5p_runtime_dir=H5P_RUNTIME_DIR,
        h5p_runtime_content_dir=H5P_RUNTIME_CONTENT_DIR,
        h5p_runtime_libraries_dir=H5P_RUNTIME_LIBRARIES_DIR,
        h5p_runtime_downloads_dir=H5P_RUNTIME_DOWNLOADS_DIR,
        h5p_runtime_port=H5P_RUNTIME_PORT,
        runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
        h5p_library_release_repo=H5P_LIBRARY_RELEASE_REPO,
        h5p_library_release_tag=H5P_LIBRARY_RELEASE_TAG,
        h5p_library_asset_prefixes=H5P_LIBRARY_ASSET_PREFIXES,
        custom_h5p_library_short_names=CUSTOM_H5P_LIBRARY_SHORT_NAMES,
        python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
        placeholder_template=PLACEHOLDER_TEMPLATE,
        sync_metadata_file=SYNC_METADATA_FILE,
        h5p_sidecar_dirname=H5P_SIDECAR_DIRNAME,
        tag_re=TAG_RE,
        fence_re=FENCE_RE,
        html_tag_re=HTML_TAG_RE,
        whitespace_re=WHITESPACE_RE,
        h5p_embed_iframe_re=H5P_EMBED_IFRAME_RE,
        mbz_link_re=MBZ_LINK_RE,
    )
