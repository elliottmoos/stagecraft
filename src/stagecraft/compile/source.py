"""The pipeline source: a generated step that resolves the source's revision to a commit and
shares the checkout with every step. See DESIGN.md section 16.

The step fetches the revision shallowly with git (Argo's git artifact can only reach a commit that
isn't a branch tip with a full clone, verified on 4.1.6), writes the commit to a global output
parameter, and publishes the checkout as a global output artifact. Steps receive the checkout
through an input artifact; handlers and the status reporter only get `{{ source.* }}`.
"""

import dataclasses

from hera.workflows import Artifact, Container, Env, Parameter, Script, SecretEnv
from hera.workflows.models import ValueFrom

from stagecraft.context import CompileContext
from stagecraft.errors import CompileError


@dataclasses.dataclass(frozen=True)
class Checkout:
    """Where a source's step runs, puts its checkout, and publishes its commit and checkout."""

    task: str
    path: str
    env: str
    artifact: str  # the global output artifact, and the input artifact steps receive
    sha_parameter: str  # the global output parameter

    @property
    def sha(self) -> str:
        return f"{{{{workflow.outputs.parameters.{self.sha_parameter}}}}}"

    @property
    def checkout(self) -> str:
        return f"{{{{workflow.outputs.artifacts.{self.artifact}}}}}"


MAIN = Checkout(
    task="source",
    path="/sources/main",
    env="STAGECRAFT_SOURCE_MAIN",
    artifact="stagecraft-source",
    sha_parameter="stagecraft-source-sha",
)
TASK = MAIN.task
PATH = MAIN.path
SHA = MAIN.sha
_SHA_FILE = "/tmp/stagecraft-sha"
# True once the source step has published a commit; false (not an error) if it failed, when the
# global parameter stays unresolved (verified on 4.1.6).
HAS_COMMIT = f"'{SHA}' =~ '^[0-9a-f]{{40}}$'"


def for_stage(stage: str, name: str) -> Checkout:
    """The checkout of stage source `name`: a task at the start of the stage's DAG."""
    return Checkout(
        task=f"source-{name}",
        path=f"/sources/{name}",
        env=f"STAGECRAFT_SOURCE_{name.upper().replace('-', '_')}",
        artifact=f"stagecraft-source-{stage}-{name}",
        sha_parameter=f"stagecraft-source-{stage}-{name}-sha",
    )


def _script(path: str) -> str:
    # Credentials are optional: the Secret may not exist, for public repositories. They go in an
    # HTTP header for this one command, never into the checkout's .git/config.
    return f"""\
set -eu
git init -q {path}
cd {path}
if [ -n "${{GIT_PASSWORD:-}}" ]; then
  credentials="$(printf '%s:%s' "${{GIT_USERNAME:-}}" "$GIT_PASSWORD" | base64 | tr -d '\\n')"
  header="Authorization: Basic $credentials"
  git -c "http.extraHeader=$header" fetch -q --depth 1 "$URL" "$REVISION"
else
  git fetch -q --depth 1 "$URL" "$REVISION"
fi
git checkout -q --detach FETCH_HEAD
git rev-parse HEAD | tee {_SHA_FILE}
"""


def source_step(
    checkout: Checkout, name: str, url: str, revision: str, ctx: CompileContext
) -> Container:
    """The template, named `name`, of the step fetching a source. `url` and `revision` are
    already lowered."""
    secret = ctx.framework_value("stagecraft.sources.secret", str)
    return Container(
        name=name,
        image=ctx.framework_value("stagecraft.sources.image", str),
        command=["sh", "-c"],
        args=[_script(checkout.path)],
        env=[
            Env(name="URL", value=url),
            Env(name="REVISION", value=revision),
            *(
                SecretEnv(
                    name=f"GIT_{key.upper()}", secret_name=secret, secret_key=key, optional=True
                )
                for key in ("username", "password")
            ),
        ],
        outputs=[
            Parameter(
                name="sha", global_name=checkout.sha_parameter, value_from=ValueFrom(path=_SHA_FILE)
            ),
            Artifact(name="checkout", global_name=checkout.artifact, path=checkout.path),
        ],
    )


def argument(checkout: Checkout) -> Artifact:
    """The task argument passing a checkout to a step."""
    return Artifact(name=checkout.artifact, from_=checkout.checkout)


def inject(
    template: Container | Script, checkouts: list[Checkout], where: str
) -> Container | Script:
    """`template` with each checkout at its path and its env var pointing to it. The main
    source's path is the working directory unless the action set its own."""
    for field in ("inputs", "env"):
        value = getattr(template, field)
        if value is not None and not isinstance(value, list):
            raise CompileError(f"{where}: with a source, an action's {field} must be a list")
    return dataclasses.replace(
        template,
        inputs=[
            *(template.inputs or []),
            *(Artifact(name=c.artifact, path=c.path) for c in checkouts),
        ],
        env=[*(template.env or []), *(Env(name=c.env, value=c.path) for c in checkouts)],
        working_dir=template.working_dir or (PATH if MAIN in checkouts else None),
    )
