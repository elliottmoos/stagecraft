"""Minimal blocks for tests. Stand-ins for whatever blocks a consumer defines."""

from hera.workflows import Container

from stagecraft.spec import Block


class Shell(Block):
    """Runs a shell script in a small image."""

    script: str
    image: str = "alpine:3.22"

    def compile(self) -> Container:
        return Container(image=self.image, command=["sh", "-c"], args=[self.script])
