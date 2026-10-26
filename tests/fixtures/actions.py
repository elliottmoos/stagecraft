"""Minimal actions for tests. Stand-ins for whatever actions a consumer defines."""

from hera.workflows import Artifact, Container, Env, Parameter
from hera.workflows.models import ValueFrom

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


class Emit(Action):
    """Publishes each of `values` as an output parameter, the way a setup step parses a payload."""

    values: dict[str, str]

    def compile(self, ctx: CompileContext) -> Container:
        writes = [f'printf "%s" "${name.upper()}" > /tmp/{name}' for name in self.values]
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=["; ".join(writes)],
            env=[Env(name=name.upper(), value=value) for name, value in self.values.items()],
            outputs=[
                Parameter(name=n, value_from=ValueFrom(path=f"/tmp/{n}")) for n in self.values
            ],
        )


class Build(Action):
    """Writes a binary and its digest: the output artifact `bin` (a path in the checkout) and the
    output parameter `digest`."""

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=["mkdir -p dist && echo binary > dist/app && printf sha256:abc > /tmp/digest"],
            outputs=[
                Artifact(name="bin", path="dist/app"),
                Parameter(name="digest", value_from=ValueFrom(path="/tmp/digest")),
            ],
        )


class Package(Action):
    """Reads the input artifact `bin` (a path in the checkout) and fails unless it holds what
    Build wrote and `digest` is Build's digest."""

    digest: str = ""

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=[
                'test "$(cat dist/app)" = binary && test -f README && test "$DIGEST" = sha256:abc'
            ],
            env=[Env(name="DIGEST", value=self.digest)],
            inputs=[Artifact(name="bin", path="dist/app")],
        )
