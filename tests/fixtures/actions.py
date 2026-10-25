"""Minimal actions for tests. Stand-ins for whatever actions a consumer defines."""

from hera.workflows import Container, Env

from stagecraft.context import CompileContext
from stagecraft.spec import Action


class Shell(Action):
    """Runs a shell script. The image comes from the chart's `images.shell` value."""

    script: str

    def compile(self, ctx: CompileContext) -> Container:
        image = ctx.value("images.shell", str)
        return Container(image=image, command=["sh", "-c"], args=[self.script])


class Typed(Action):
    """Echoes chart values used as numbers and booleans, which Helm renders through toJson: inside
    a script, and as a whole env var value."""

    def compile(self, ctx: CompileContext) -> Container:
        retries = ctx.value("strings.retries", int)
        debug = ctx.value("strings.debug", bool)
        big = ctx.value("strings.big", int)
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=[f'echo "retries={retries} debug={debug} big={big} {{{{workflow.name}}}}"'],
            env=[Env(name="BIG", value=big)],
        )
