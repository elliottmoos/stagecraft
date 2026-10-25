# Reference

Everything in one place. The [guide](README.md) explains each item in context.

## Imports

| Import | What |
|---|---|
| `from stagecraft.spec import Pipeline, Stage, Step, Action, Param, Trigger` | The authoring models |
| `from stagecraft.context import CompileContext` | The `ctx` passed to `Action.compile` |
| `from stagecraft.contract import ValuesContract` | A values contract, for action tests |
| `from stagecraft.compile import compile_pipeline, compile_triggers` | Compile in tests |
| `from stagecraft.errors import CompileError` | Raised for contract and composition errors at compile time |
| `from hera.workflows import Container, Script, Env, SecretEnv, ...` | What `compile` returns (Hera 7.1.0) |

## `Pipeline`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required | DNS label; unique among pipelines; the `WorkflowTemplate`'s name |
| `params` | list[Param] | `[]` | Unique names |
| `stages` | list[Stage] | required | At least one; unique names; run in order |
| `on_success`, `on_failure` | list[Step \| Action] | `[]` | Run after the run ends; a failure here fails the run |
| `status` | str | None | Commit-status context for the whole run |
| `status_reporter` | Action | None | Required if anything has a status; a bare action, not a `Step` |
| `triggers` | list[Trigger] | `[]` | Unique names |

Statuses also require params `repo` and `sha`, both `required=True`. Status contexts are unique
across the pipeline.

## `Stage(name, steps, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required (positional) | DNS label |
| `steps` | list[Step \| Action] | required (positional) | At least one; unique step names; run in parallel |
| `when` | str | None | Argo condition; `{{ param.x }}` is quoted inside it |
| `status` | str | None | Commit-status context |
| `on_success`, `on_failure`, `finally_` | list[Step \| Action] | `[]` | Handlers |

Wherever a list takes `Step | Action`, a bare action is a step named after its class in kebab case
(`GitHubStatus` is `git-hub-status`).

## `Step(name, action, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required (positional) | DNS label; unique within its stage or handler list |
| `action` | Action | required (positional) | What runs |
| `when` | str | None | As for stages |
| `status` | str | None | As for stages |
| `on_success`, `on_failure`, `finally_` | list[Step \| Action] | `[]` | Handlers |

Handler steps can't set `when`, `status`, or handlers. Each step runs in its own pod; no files
are shared between steps.

## `Action`

Subclass it, add fields, and implement `compile(self, ctx: CompileContext) -> Container | Script`.
It has no fields of its own.

## `Param(name, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required (positional) | `[A-Za-z_][A-Za-z0-9_-]*` |
| `required` | bool | False | Exactly one of `required=True` or `default` |
| `default` | str | None | Must be in `enum` if both are set |
| `enum` | list[str] | None | Drop-down in the submit form |
| `description` | str | None | Shown in the submit form |

Param fields are literal: placeholders aren't replaced in them.

## `Trigger.cron(schedule, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `schedule` | str | required (positional) | Five cron fields, or `@hourly`, `@daily`, `@midnight`, `@weekly`, `@monthly`, `@yearly`, `@annually`, `@every <duration>` |
| `name` | str | `"cron"` | DNS label; `<pipeline>-<name>` at most 52 characters |
| `params` | dict[str, str] | `{}` | Declared params only; must set every required param; values in `enum` unless they use `{{ values.a.b }}` |
| `timezone` | str | None | IANA name; the controller's time zone if unset |

## `CompileContext.value(path, expected=None)`

Returns a reference to the Helm value at `path` (dotted, relative to `--values-prefix`).
`expected` is `str`, `int`, `float`, or `bool`; `None` accepts any scalar. Raises `CompileError`
unless the schema guarantees the value: declared and required at every level, non-null, a single
scalar type matching `expected`, reached through `properties` and local `$ref`s only, with
identifier keys.

## Placeholders

| Placeholder | Allowed in | Becomes |
|---|---|---|
| `{{ param.x }}` | action fields, `when` | `{{workflow.parameters.x}}`; quoted in `when` |
| `{{ values.a.b }}` | action fields, `when`, trigger params | A Helm expression for `.Values.a.b` |
| `{{ step.name }}`, `{{ step.outcome }}` | handler action fields | The path (`stage` or `stage/step`), or the run's name in pipeline handlers; and `succeeded` or `failed` |
| `{{ status.context }}`, `{{ status.state }}`, `{{ status.description }}` | `status_reporter` fields | The report being posted |

"Action fields" means every string an action's fields contain, including inside lists, dicts, and
nested models, but not strings created in `compile`. Names, `status` contexts, param fields, and
trigger schedules and time zones are literal. Any other `{{ ... }}` is passed to Argo unchanged;
a lone `{{` or `}}` fails the render.

## How values render

| Reference | Renders as |
|---|---|
| string, whole field | `{{ .Values.a.b \| quote }}` |
| number or boolean, whole field | `{{ .Values.a.b \| toJson \| quote }}` |
| inside other text | spliced into a double-quoted string |

Values always render as strings.

## Generated names

| Template | Name |
|---|---|
| Entrypoint DAG | `main` |
| A stage's DAG | `<stage>` |
| A step | `<stage>-<step>` |
| A step's handler DAG / a handler | `<stage>[-<step>]-on-exit` / `<stage>[-<step>]-<on-success\|on-failure\|finally>-<handler>[-succeeded\|-failed]` |
| Pipeline handlers | `on-exit`, `on-success-<handler>`, `on-failure-<handler>` |
| Status reporting | `status-report`, `status-reporter` |
| A trigger's `CronWorkflow` | `<pipeline>-<trigger>` |

Two generated names colliding fails the render.

## CLI

| Command | Does |
|---|---|
| `stagecraft render <paths> --values-schema <file> --out <dir> [--values-prefix <key>]` | Compile, validate, and write `<pipeline>.yaml` per pipeline; writes nothing if any fails |
| `stagecraft check <paths> --values-schema <file> [--values-prefix <key>]` | The same, without writing |
| `stagecraft graph <paths> [--pipeline <name>]` | Mermaid diagram of a pipeline |
| `stagecraft contexts <paths>` | `<pipeline><TAB><context>` per status context |
| `stagecraft version` | Stagecraft and Hera versions |

`<paths>` are pipeline files or directories (their `*.py` files, not recursive, skipping names
starting with `_`). Pipelines are the module-level `Pipeline` objects; their names must be unique.
Run from the project root: pipeline files import actions relative to the working directory.

## Targets

Hera 7.1.0 (pinned by Stagecraft), Argo Workflows 4.1.6, Helm 4, Python 3.12+.
