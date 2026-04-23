from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    courses_dir: Path
    default_port: int
    dotenv_file: Path
    h5p_runtime_dir: Path
    h5p_runtime_content_dir: Path
    h5p_runtime_libraries_dir: Path
    h5p_runtime_downloads_dir: Path
    h5p_runtime_port: int
    runtime_proxy_prefix: str
    h5p_library_release_repo: str
    h5p_library_release_tag: str
    h5p_library_asset_prefixes: dict[str, str]
    custom_h5p_library_short_names: dict[str, str]
    python_question_machine_name: str
    placeholder_template: str
    sync_metadata_file: str
    h5p_sidecar_dirname: str
    tag_re: re.Pattern[str]
    fence_re: re.Pattern[str]
    html_tag_re: re.Pattern[str]
    whitespace_re: re.Pattern[str]
    h5p_embed_iframe_re: re.Pattern[str]
    mbz_link_re: re.Pattern[str]
