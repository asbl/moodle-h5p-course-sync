from __future__ import annotations

import re


class RuntimeHtmlRewriter:
    """Applies display-mode specific overrides to proxied runtime HTML."""

    def __init__(self, *, runtime_port: int, runtime_proxy_prefix: str) -> None:
        self._runtime_port = runtime_port
        self._runtime_proxy_prefix = runtime_proxy_prefix

    def rewrite(self, document: str, runtime_path: str, query: str = "") -> str:
        runtime_origin_pattern = rf"https?://(?:localhost|127\.0\.0\.1):{self._runtime_port}"
        document = re.sub(runtime_origin_pattern + r"(?=/|[\"'])", self._runtime_proxy_prefix, document)
        document = re.sub(
            r'([\'"`])/(?!runtime(?:/|[\'"`]|$))',
            lambda match: f"{match.group(1)}{self._runtime_proxy_prefix}/",
            document,
        )

        if runtime_path.startswith("/view/"):
            view_override = """
<style>
    #sessions,
    #newSessionButton,
    #newSession,
    #resetSessionButton,
    .submenu {
        display: none !important;
    }
    a[href*="/split/"],
    a[href*="/remove/"],
    form[action*="/remove/"],
    button[formaction*="/remove/"] {
        display: none !important;
    }
</style>
<script>
window.addEventListener('load', () => {
    const sessionSelect = document.getElementById('sessions');
    const sessionContainer = sessionSelect?.closest('.menu-holder');
    if (sessionContainer) {
        sessionContainer.remove();
    }
    document.querySelectorAll('a[href*="/split/"], a[href*="/remove/"], form[action*="/remove/"], button[formaction*="/remove/"]').forEach((element) => {
        element.remove();
    });
});
</script>
""".strip()
            document = document.replace("</head>", f"{view_override}\n</head>", 1)

        if runtime_path.startswith("/edit/"):
            edit_override = """
<style>
  #menu {
    display: none !important;
  }
</style>
""".strip()
            document = document.replace("</head>", f"{edit_override}\n</head>", 1)

        if runtime_path.startswith("/split/"):
            split_override = """
<style>
  .h5p-cli-view > .col50 {
    display: none !important;
  }
</style>
""".strip()
            document = document.replace("</head>", f"{split_override}\n</head>", 1)

        if runtime_path.startswith("/view/") and "simple=1" in query:
            chrome_override = """
<style>
    html, body {
        margin: 0 !important;
        padding: 0 !important;
        background: transparent !important;
    }
    #status,
    .menu-holder,
    .theme-controls {
        display: none !important;
    }
    .holder,
    .h5p-cli-iframe-wrapper {
        margin: 0 !important;
        padding: 0 !important;
    }
    .h5p-cli-iframe-wrapper {
        border: 0 !important;
        box-shadow: none !important;
    }
    .h5p-iframe {
        display: block;
        width: 100%;
        min-height: 540px;
    }
</style>
""".strip()
            document = document.replace("</head>", f"{chrome_override}\n</head>", 1)

        return document
