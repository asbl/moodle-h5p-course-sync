from scripts.classes.python_runner_policy import (
    ensure_miniworlds_packages,
    packages_for_h5p_content,
    resolve_python_runner,
)


def test_miniworlds_extensions_select_pyodide_and_include_dependencies():
    source = "import miniworlds_robot\nfrom miniworlds_turtle import Turtle\n"

    packages = ensure_miniworlds_packages([], source=source)

    assert resolve_python_runner(source=source) == "pyodide"
    assert packages_for_h5p_content(packages) == [
        {"package": "miniworlds", "remote": False},
        "miniworlds-robot",
        "miniworlds-turtle",
    ]


def test_extension_module_names_are_normalized_to_distributions():
    packages = ensure_miniworlds_packages(["miniworlds_robot", "miniworlds_turtle"])

    assert packages_for_h5p_content(packages) == [
        "miniworlds-robot",
        "miniworlds-turtle",
        {"package": "miniworlds", "remote": False},
    ]
