class StagecraftError(Exception):
    """An error in a pipeline or its inputs, reported to the author without a traceback."""


class LoadError(StagecraftError):
    """A pipeline file could not be loaded."""


class CompileError(StagecraftError):
    """A pipeline cannot be compiled. The message names the pipeline, stage, and step."""


class SerializeError(StagecraftError):
    """Compiled output cannot be written as a Helm-compatible template."""
