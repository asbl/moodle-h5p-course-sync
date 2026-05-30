from .automata_question import AutomataQuestion
from .base import H5PContentType
from .imported_question_factory import ImportedQuestionFactory
from .java_question import JavaQuestion
from .python_question import PythonQuestion
from .question_set import QuestionSet
from .raw_content import RawH5PContent
from .sql_question import SQLQuestion

__all__ = [
    "AutomataQuestion",
    "H5PContentType",
    "ImportedQuestionFactory",
    "JavaQuestion",
    "PythonQuestion",
    "QuestionSet",
    "RawH5PContent",
    "SQLQuestion",
    "block_to_content_type",
]


def block_to_content_type(block: object) -> H5PContentType:
    """Convert a ``PythonQuestionBlock`` (or compatible object) to the
    appropriate :class:`H5PContentType` subclass instance.

    Dispatch is based on the block's ``main_library`` attribute:
    * ``"H5P.PythonQuestion"`` → :class:`PythonQuestion`
    * ``"H5P.JavaQuestion"`` → :class:`JavaQuestion`
    * ``"H5P.SQLQuestion"`` → :class:`SQLQuestion`
    * ``"H5P.AutomataQuestion"`` → :class:`AutomataQuestion`
    * ``"H5P.QuestionSet"`` → :class:`QuestionSet`
    * anything else → :class:`RawH5PContent`
    """
    klass = H5PContentType.for_machine_name(getattr(block, "main_library", ""))
    return klass.from_block(block)
