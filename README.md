# Stagecraft

Stagecraft compiles CI/CD pipelines, written in Python as ordered **stages** of parallel
**blocks**, into Argo Workflows `WorkflowTemplate` (and, for schedules, `CronWorkflow`)
manifests that a Helm chart can ship. You keep
triggering and watching runs in the Argo Workflows UI; Stagecraft only writes templates.

```
pipelines (Python) ──stagecraft render──▶ chart/templates/*.yaml ──helm──▶ Argo Workflows
                       ▲
       chart's values.schema.json (the values contract)
```

Pinned targets: Hera 7.1.0, Argo Workflows 4.1.6, Helm 4, Python 3.12+. The design rationale is in
[DESIGN.md](DESIGN.md).

**New to Stagecraft? Start with the guide in [docs/](docs/README.md)**: eleven short chapters
that build one pipeline from scratch, layer by layer. This page is the quick overview.

## A pipelines repo

A pipelines repo is a plain Python project that depends on a pinned stagecraft release and holds
your blocks and pipeline files:

```
pipelines/
  pyproject.toml        # dependencies = ["stagecraft==0.0.1"]
  blocks.py             # your blocks
  pipelines/
    service_ci.py       # one or more Pipeline objects at module level
```

Render into the chart that installs them:

```sh
uv run stagecraft render pipelines --values-schema ../chart/values.schema.json \
  --out ../chart/templates/pipelines
```

Run the CLI from the repo root: pipeline files import your blocks relative to the working
directory.

## Blocks

A block is a Pydantic model with a `compile` method that returns one Hera `Container` (or
`Script`). Stagecraft names the template and wires it into the workflow.

```python
from hera.workflows import Container, Env, SecretEnv

from stagecraft.context import CompileContext
from stagecraft.spec import Block


class Shell(Block):
    """Runs a shell script in the chart's shell image."""

    script: str

    def compile(self, ctx: CompileContext) -> Container:
        return Container(
            image=ctx.value("images.shell", str),
            command=["sh", "-c"],
            args=[self.script],
        )


class GitHubStatus(Block):
    """Posts a commit status. Used as a pipeline's status_reporter."""

    repo: str = "{{ param.repo }}"
    sha: str = "{{ param.sha }}"
    context: str = "{{ status.context }}"
    state: str = "{{ status.state }}"
    description: str = "{{ status.description }}"

    def compile(self, ctx: CompileContext) -> Container:
        fields = ("repo", "sha", "context", "state", "description")
        return Container(
            image=ctx.value("images.curl", str),
            env=[
                *[Env(name=f.upper(), value=getattr(self, f)) for f in fields],
                SecretEnv(
                    name="GITHUB_TOKEN",
                    secret_name=ctx.value("github.tokenSecret", str),
                    secret_key="token",
                ),
            ],
            command=["sh", "-c"],
            args=[
                'curl -fsS -H "Authorization: Bearer $GITHUB_TOKEN"'
                ' "https://api.github.com/repos/$REPO/statuses/$SHA"'
                ' -d "{\\"context\\": \\"$CONTEXT\\", \\"state\\": \\"$STATE\\",'
                ' \\"description\\": \\"$DESCRIPTION\\"}"'
            ],
        )
```

Rules for blocks:

- **Helm values only through `ctx.value(path, type)`**, where `type` is `str`, `int`, `float`, or
  `bool`. It returns a placeholder string that renders as a Helm expression, and fails the render
  if the chart's schema doesn't guarantee that value. Never write Helm syntax yourself.
- **Placeholders are lowered in a block's fields, before `compile` runs**, not in what `compile`
  returns. Give a block a field (with a default, as `GitHubStatus` does) for anything that needs
  `{{ param.x }}`, `{{ step.x }}`, or `{{ status.x }}`.
- **Secrets by name, never by content**: take the Secret's name from a value, as `SecretEnv` above
  does.
- A block's name defaults to its class name in kebab case (`GitHubStatus` → `git-hub-status`).
  Pass `name=` to tell apart two blocks of the same type in one stage.

## Pipelines

```python
from stagecraft.spec import Param, Pipeline, Stage, Trigger, Workspace

from blocks import GitHubStatus, Shell

pipeline = Pipeline(
    name="service-ci",
    params=[
        Param("repo", required=True, description="Repository, as owner/name"),
        Param("sha", required=True, description="Commit to build"),
        Param("deploy", default="false", enum=["true", "false"]),
    ],
    status="ci/pipeline",
    status_reporter=GitHubStatus(),
    workspace=Workspace(strategy="containerset", size="5Gi"),
    stages=[
        Stage("prepare", [
            Shell(
                name="clone",
                workspace="write",
                script="git init -q . && git fetch -q --depth 1 "
                "https://github.com/{{ param.repo }} {{ param.sha }} && git checkout -q FETCH_HEAD",
            ),
        ]),
        Stage(
            "verify",
            [
                Shell(name="lint", workspace="read", script="make lint"),
                Shell(name="unit-tests", workspace="read", script="make test"),
            ],
            status="ci/verify",
            on_failure=[
                Shell(name="alert", script="echo '{{ step.name }} failed' # to {{ values.notify.channel }}"),
            ],
        ),
        Stage(
            "deploy",
            [Shell(name="staging", script="make deploy")],
            when="{{ param.deploy }} == 'true'",
            status="ci/deploy",
        ),
    ],
    on_failure=[Shell(name="report", script="echo '{{ step.name }} ended {{ step.status }}'")],
    triggers=[
        Trigger.cron("0 2 * * *", name="nightly", params={"repo": "example/service", "sha": "main"}),
    ],
)
```

- **Stages run in order; the blocks in a stage run in parallel.** A failing block fails its stage
  and the pipeline, and later stages don't run.
- **Params** become the Argo UI's submit form (descriptions, enums, defaults).
- **`when`** (stage or block) skips the step when false. Inside it, `{{ param.x }}` is a quoted
  string, so compare with quoted values: `"{{ param.deploy }} == 'true'"`.
- **Handlers**: `on_success`, `on_failure`, and `finally_` blocks run after a stage, a block, or
  (`on_success`/`on_failure` only) the whole pipeline finishes. Handlers can't have their own
  `when`, status, handlers, or workspace access. A failing pipeline handler fails the pipeline; a
  failing stage or block handler shows as failed but doesn't (an Argo limitation). A step skipped
  by `when` runs no handlers.
- **Commit statuses**: `status="<context>"` on the pipeline, a stage, or a block reports pending,
  then success, failure, or error, through `status_reporter`. A step skipped by `when` reports
  success with the description "Skipped", so a required check doesn't hang. Reporting is
  best-effort and never fails the build. Statuses need required `repo` and `sha` params.
- **Workspace**: blocks declare `workspace="read"` or `"write"`; the pipeline then needs
  `workspace=Workspace(strategy="containerset", size=...)` (see below).

### Placeholders

| Placeholder | Where | Becomes |
|---|---|---|
| `{{ param.x }}` | any block field, `when` | the workflow parameter |
| `{{ values.a.b }}` | any block field, `when` | the chart value `.Values.a.b`, checked against the schema |
| `{{ step.name }}`, `{{ step.status }}` | handler blocks | the step's path (`stage` or `stage/block`) and `Succeeded` or `Failed`; the workflow's name and status in pipeline handlers |
| `{{ status.context }}`, `{{ status.state }}`, `{{ status.description }}` | the `status_reporter` | the report being posted; state is `pending`, `success`, `failure`, or `error` |

Other `{{ ... }}` text, such as native Argo expressions (`{{workflow.name}}`), passes through to
Argo unchanged.

### Triggers

`triggers` run a pipeline on a schedule. Each one becomes a CronWorkflow named
`<pipeline>-<name>`, written into the pipeline's file, that runs the pipeline's template:

```python
Trigger.cron(
    "0 2 * * *",                 # cron expression or macro (@daily, @weekly, ...)
    name="nightly",              # default "cron"; names are unique per pipeline
    timezone="Europe/Amsterdam", # default: the workflow controller's time zone
    params={"repo": "example/service", "sha": "main", "deploy": "false"},
)
```

Trigger params must be declared and must set every required param. Their values may use
`{{ values.a.b }}`; schedules and time zones are literal. `<pipeline>-<name>` can be at most 52
characters.

### Workspace

The workspace is one ReadWriteOnce volume per run, from the cluster's default StorageClass,
mounted at `/workspace` (read-only for `read` blocks) and deleted when the run ends. It is the
working directory of blocks that use it. Blocks with no `workspace` declared don't mount it.

With the `containerset` strategy, a stage's workspace blocks, when there are two or more, run as
containers of a single pod, so the volume is never mounted by two pods at once. That has limits:

- A block that writes the workspace must be the only workspace block in its stage.
- Blocks sharing the pod must compile to a `Container` that sets only container fields (no
  retries, timeouts, or inputs), and can't have their own `when`, `status`, or handlers. Put those
  on the stage.
- The pod requests the sum of its containers' resources.

## The values contract

The chart's `values.schema.json` is the contract between pipelines and installations. Every value
reference (from `ctx.value` or a `{{ values.a.b }}` placeholder) must be:

- declared, and **required at every level** (give it a default in the chart's `values.yaml`);
- a non-null scalar of a single type: string, integer, number, or boolean;
- reachable through `properties` and local `$ref`s only (`oneOf`, `anyOf`, `allOf`, `if`, `not`,
  `patternProperties`, and `dependentSchemas` are rejected rather than guessed at);
- keyed by identifiers, so it works as `.Values.a.b`.

Anything else fails the render, naming the pipeline, stage, block, and path. If the chart nests
these values under a key, pass `--values-prefix <key>`. Values always render as strings in the
manifest.

## CLI

| Command | Does |
|---|---|
| `stagecraft render <paths> --values-schema <file> --out <dir>` | Compile and validate every pipeline, then write `<pipeline>.yaml` for each (its WorkflowTemplate, then a CronWorkflow per trigger). Writes nothing if any pipeline fails. |
| `stagecraft check <paths> --values-schema <file>` | The same validation, without writing. For CI. |
| `stagecraft graph <paths> [--pipeline <name>]` | Print a Mermaid diagram of a pipeline: stages, blocks, conditions, statuses, handlers. |
| `stagecraft contexts <paths>` | Print each status context as `<pipeline><TAB><context>`: the checks branch protection can require. |
| `stagecraft version` | Print the stagecraft and Hera versions. |

`<paths>` are pipeline files or directories of them (`*.py`, not recursive, skipping names that
start with `_`).

## Shipping templates in the chart

Stagecraft writes template files only; it never writes `Chart.yaml`, `values.yaml`, or the
schema. A chart's CI should run, for each environment's values:

```sh
stagecraft check pipelines --values-schema chart/values.schema.json
helm template ci chart -f <env values> > rendered.yaml
argo lint --offline rendered.yaml
kubeconform -strict rendered.yaml   # with schemas for the Argo CRDs
```

## Known gaps

- **Not built yet**: typed block inputs and outputs, structured (object or array) values, and
  the `node-affinity` and `artifact` workspace strategies.
- **Right after an install that creates a trigger**, its first ticks can be missed: Helm applies
  the CronWorkflow before the WorkflowTemplate it references, and Argo re-checks the cron only
  minutes later (at most 20).
- Scheduled runs need literal values for required params; a trigger can't resolve a branch's
  latest commit for `sha`, so a commit status for a scheduled run may fail to post (reporting is
  best-effort).
- A trigger can't be switched off per installation yet (no `suspend` from Helm values).
- `argo terminate` runs no exit handlers, and `argo stop` skips the exit hook of a running block,
  so either can leave a commit status pending. Re-running the workflow updates it.
- Stage and block handlers see Argo's Error as `Failed`, and get no exit code or message.
- A multi-line script containing a string value renders as one double-quoted line: correct, but
  harder to read in the chart's diffs.
