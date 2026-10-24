"""Minimal blocks for tests. Stand-ins for whatever blocks a consumer defines."""

from hera.workflows import Container

from stagecraft.context import CompileContext
from stagecraft.spec import Block


class Shell(Block):
    """Runs a shell script. The image comes from the chart's `images.shell` value."""

    script: str

    def compile(self, ctx: CompileContext) -> Container:
        image = ctx.value("images.shell", str)
        return Container(image=image, command=["sh", "-c"], args=[self.script])
