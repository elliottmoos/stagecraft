"""Minimal actions for tests. Stand-ins for whatever actions a consumer defines."""

from hera.workflows import Container

from stagecraft.context import CompileContext
from stagecraft.spec import Action


class Shell(Action):
    """Runs a shell script. The image comes from the chart's `images.shell` value."""

    script: str

    def compile(self, ctx: CompileContext) -> Container:
        image = ctx.value("images.shell", str)
        return Container(image=image, command=["sh", "-c"], args=[self.script])
