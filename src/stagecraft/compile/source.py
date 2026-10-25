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

TASK = "source"
PATH = "/sources/main"
ENV = "STAGECRAFT_SOURCE_MAIN"
_ARTIFACT = "stagecraft-source"
_SHA_PARAMETER = "stagecraft-source-sha"
_SHA_FILE = "/tmp/stagecraft-sha"
SHA = f"{{{{workflow.outputs.parameters.{_SHA_PARAMETER}}}}}"
_CHECKOUT = f"{{{{workflow.outputs.artifacts.{_ARTIFACT}}}}}"
# True once the source step has published a commit; false (not an error) if it failed, when the
# global parameter stays unresolved (verified on 4.1.6).
HAS_COMMIT = f"'{SHA}' =~ '^[0-9a-f]{{40}}$'"

# Credentials are optional: the Secret may not exist, for public repositories. They go in an
# HTTP header for this one command, never into the checkout's .git/config.
_SCRIPT = f"""\
set -eu
git init -q {PATH}
cd {PATH}
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


def source_step(url: str, revision: str, ctx: CompileContext) -> Container:
    """The source step's template. `url` and `revision` are already lowered."""
    secret = ctx.framework_value("stagecraft.sources.secret", str)
    return Container(
        name=TASK,
        image=ctx.framework_value("stagecraft.sources.image", str),
        command=["sh", "-c"],
        args=[_SCRIPT],
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
            Parameter(name="sha", global_name=_SHA_PARAMETER, value_from=ValueFrom(path=_SHA_FILE)),
            Artifact(name="checkout", global_name=_ARTIFACT, path=PATH),
        ],
    )


def argument() -> Artifact:
    """The task argument passing the checkout to a step."""
    return Artifact(name=_ARTIFACT, from_=_CHECKOUT)


def inject(template: Container | Script, where: str) -> Container | Script:
    """`template` with the checkout at PATH, ENV pointing to it, and PATH as the working
    directory unless the action set its own."""
    for field in ("inputs", "env"):
        value = getattr(template, field)
        if value is not None and not isinstance(value, list):
            raise CompileError(
                f"{where}: with a pipeline source, an action's {field} must be a list"
            )
    return dataclasses.replace(
        template,
        inputs=[*(template.inputs or []), Artifact(name=_ARTIFACT, path=PATH)],
        env=[*(template.env or []), Env(name=ENV, value=PATH)],
        working_dir=template.working_dir or PATH,
    )
