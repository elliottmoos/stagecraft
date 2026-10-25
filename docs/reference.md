# Reference

Everything in one place. The [guide](README.md) explains each item in context.

## Imports

| Import | What |
|---|---|
| `from stagecraft.spec import Pipeline, Source, Stage, Step, Action, Param, TemplateRef, Trigger` | The authoring models |
| `from stagecraft.context import CompileContext` | The `ctx` passed to `Action.compile` |
| `from stagecraft.compile import compile_pipeline, compile_triggers` | Compile in tests |
| `from stagecraft.chart import render_chart` | The generated chart's files, by path |
| `from stagecraft.errors import CompileError` | Raised for value-reference and composition errors at compile time |
| `from hera.workflows import Container, Script, Env, SecretEnv, ...` | What `compile` returns (Hera 7.1.0) |

## `Pipeline`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required | DNS label; unique among pipelines; the `WorkflowTemplate`'s name |
| `params` | list[Param] | `[]` | Unique names |
| `setup` | Step \| Action \| TemplateRef | None | Runs first; its outputs work everywhere as `{{ setup.<output> }}`; no `when`, `status`, or handlers |
| `source` | Source | None | Checked out once, after the setup and before the first stage, for every step |
| `stages` | list[Stage] | required | At least one; unique names; run in order |
| `on_success`, `on_failure` | list[Step \| Action] | `[]` | Run after the run ends; a failure here fails the run |
| `status` | str | None | Commit-status context for the whole run |
| `status_reporter` | Action | None | Required if anything has a status; a bare action, not a `Step` |
| `triggers` | list[Trigger] | `[]` | Unique names |

Statuses also require a `source`, whose commit they report on. Status contexts are unique across
the pipeline. `setup` and `source` are reserved stage names.

## `TemplateRef(name, /, ...)`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | required (positional) | The WorkflowTemplate, already installed |
| `template` | str | required | The template in it to call |
| `arguments` | dict[str, str] | `{}` | Its input parameters; may use `{{ param.x }}` and `{{ values.a.b }}` |
| `outputs` | list[str] | `[]` | The output parameters it publishes, the only ones `{{ setup.<output> }}` can name |

Lint the rendered chart together with the referenced WorkflowTemplate's manifest.

## `Source(url=, revision=)`

| Field | Type | Notes |
|---|---|---|
| `url` | str | What `git fetch` takes; may use `{{ param.x }}` and `{{ values.a.b }}` |
| `revision` | str | A commit SHA, branch, or tag, resolved to one commit at the start of the run; may use placeholders |

Every step gets the checkout (shallow, with `.git`) at `/sources/main`, its working directory
unless the action sets one, with `STAGECRAFT_SOURCE_MAIN` set to that path. Handlers and the
status reporter get no checkout. The source step needs two Helm values:

| Value | Type | Meaning |
|---|---|---|
| `stagecraft.sources.image` | string | An image with `git` and `sh` |
| `stagecraft.sources.secret` | string | A Secret with `username` and `password` keys for HTTPS; optional at run time, so a public repository can name one that doesn't exist |

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

## `CompileContext.value(path, expected=str)`

Returns a reference to the Helm value at `path` (dotted) and records it in `ctx.references`
(`{path: type}`), from which the chart's `values.schema.json` is generated. `expected` is `str`,
`int`, `float`, or `bool`; `{{ values.a.b }}` placeholders are `str`. Raises `CompileError` if
the path isn't dotted identifiers, is under the reserved `stagecraft` key, is already used with
another type, or is also used as an object (`a` and `a.b`). `CompileContext()` takes no
arguments.

## The generated chart

| File | Contents |
|---|---|
| `Chart.yaml` | `apiVersion: v2`, the chart name (`--chart-name`, default `pipelines`), version `0.0.0`, annotation `stagecraft/generated: "true"` |
| `values.schema.json` | Every referenced value, required at every level, typed `string`, `integer`, `number`, or `boolean`; `global` allowed |
| `templates/<pipeline>.yaml` | The `WorkflowTemplate`, then a `CronWorkflow` per trigger |

`render` replaces these and nothing else. It writes only into a missing or empty directory, or a
chart it generated (a `Chart.yaml` with the annotation).

## Placeholders

| Placeholder | Allowed in | Becomes |
|---|---|---|
| `{{ param.x }}` | action fields, `when` | `{{workflow.parameters.x}}`; quoted in `when` |
| `{{ values.a.b }}` | action fields, `when`, trigger params, source fields | A Helm expression for `.Values.a.b` |
| `{{ setup.<output> }}` | action fields, `when`, source fields, with a `setup` | The setup's output parameter, as a global output; quoted in `when` |
| `{{ source.url }}`, `{{ source.revision }}`, `{{ source.sha }}` | action fields, `when`, with a `source` | The source's URL and revision as given, and the commit it resolved to; quoted in `when` |
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
| The setup step and its action | `setup`, `setup-<step>` |
| The source step | `source` |
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
| `stagecraft render <paths> --out <dir> [--chart-name <name>]` | Compile and write the chart; writes nothing if any pipeline fails |
| `stagecraft check <paths>` | Compile, without writing |
| `stagecraft graph <paths> [--pipeline <name>]` | Mermaid diagram of a pipeline |
| `stagecraft contexts <paths>` | `<pipeline><TAB><context>` per status context |
| `stagecraft version` | Stagecraft and Hera versions |

`<paths>` are pipeline files or directories (their `*.py` files, not recursive, skipping names
starting with `_`). Pipelines are the module-level `Pipeline` objects; their names must be unique.
Run from the project root: pipeline files import actions relative to the working directory.

## Targets

Hera 7.1.0 (pinned by Stagecraft), Argo Workflows 4.1.6, Helm 4, Python 3.12+.
