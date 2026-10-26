# Stagecraft

Python framework and CLI that compiles CI/CD pipelines (ordered stages of parallel steps) into a
generated Helm chart of Argo Workflows `WorkflowTemplate` and `CronWorkflow` manifests, using Hera
as the compiler. `DESIGN.md` is the spec; read the relevant section before changing behavior.
`README.md` is the overview and `docs/` the step-by-step guide (`docs/reference.md` lists every
field); keep both in step with behavior changes. `tests/unit/test_readme.py` and
`tests/snapshots/test_docs.py` compile their examples (fences tagged `file=<path>` in `docs/`).

## Pinned versions

| Component | Version | Notes |
|---|---|---|
| Hera | 7.1.0 | Pinned exactly in `pyproject.toml`. Its models are generated from Argo **4.0.5**. |
| Argo Workflows | 4.1.6 | Target controller, CLI (`argo lint`), kind install, and kubeconform CRD schemas. |
| Helm | 4.x | The chart's CI uses Helm 4. Check `values.schema.json` behavior against Helm 4, not Helm 3. |
| Python | 3.14 (`.python-version`), `>=3.12` supported | |
| kind / kubeconform | 0.33.0 / 0.8.0 | |

Known gap: Hera's models trail the controller by a minor version (4.0.5 vs 4.1.6). A field added
in Argo 4.1 is not available in Hera's models. `argo lint` and kubeconform run against 4.1.6
and are the source of truth for output validity.

Hera 6+ core classes are dataclasses (not Pydantic), the decorator API is gone, and Hera 7 changed
how wrapped Argo expressions are written in YAML output. Read the docs and source for the pinned
release (https://github.com/argoproj-labs/hera/tree/7.1.0) instead of relying on older examples.

Hera pitfalls found so far:
- Passing `None` explicitly to a model field can serialize as the string `"null"` (seen with
  `Parameter(value=None)`). Omit unset fields instead.
- The compiler builds Hera objects explicitly (`DAG(tasks=[...])`, `WorkflowTemplate(templates=[...])`)
  and never uses Hera's `with` contexts, which keep module-level state.

## Verified Argo behavior (4.1.6, on kind)

Facts confirmed by experiment or by `tests/integration`. Re-check them when bumping Argo.

- Every DAG task field except `depends`/`dependencies` also exists on steps. Stagecraft uses DAGs
  because steps add unlabelled `[0]` group nodes to the UI graph and rule out non-barrier edges.
- A failing `onExit` task turns a Succeeded workflow into Failed. Stagecraft keeps that default
  on purpose: handler failures are unexpected and should fail the build. (`continueOn` would
  keep the result while still showing the handler as Failed.)
- Task hooks: an expression hook on a **sub-DAG** task never fires if another task depends on
  it (pod tasks are fine). A task's `exit` hook always fires, and its arguments can read
  `{{tasks.<name>.status}}`, which expression-hook arguments can't for sub-DAG tasks. Hence
  stage and step handlers run from one `exit` hook calling a `when`-guarded handler DAG.
  `{{tasks.<name>.exitCode}}` doesn't resolve in hook arguments.
- A failing task hook (`exit` or expression) does **not** fail the workflow. Accepted for stage
  and step handlers; pipeline handlers (onExit) do fail it.
- A task skipped by `when` fires none of its handlers.
- `when` parses bare words: `true == 'true'` is false (boolean vs string). Stagecraft quotes
  param placeholders inside `when`.
- `running` hooks are unreliable: one on an 8-second step never fired (it did on a 60-second
  one). Don't use them for anything that must happen.
- `exit` hooks don't fire for a task skipped by `when`.
- `continueOn` is rejected by `argo lint` in any DAG template that uses `depends`.
- A failed task counts as handled (the DAG can still succeed) only if a task depending on its
  failure actually runs.
- `argo stop` doesn't run the `exit` hook of a step that was running (the stage's still runs).
  A step-level commit status stays pending after a stop until the workflow is re-run.
- `argo stop` runs exit handlers with `{{workflow.status}} == Failed`.
- Helm applies a release's `CronWorkflow`s before its `WorkflowTemplate`s (Helm 4.1.0 sorts unknown
  kinds by name and creates one kind at a time; seen on kind). A new CronWorkflow
  whose template doesn't exist yet is marked invalid ("WorkflowTemplate Informer cannot find
  WorkflowTemplate") and isn't scheduled until the controller re-processes it (about 3 minutes on
  kind; at most the 20-minute informer resync). Accepted. Tests install the template first.
- CronWorkflow names are limited to 52 characters (`argo lint` and the controller reject longer).
- Argo's git input artifact with `depth: 1` can't check out a commit that isn't a branch tip
  ("reference not found"); only a full clone can. So the source step runs
  `git fetch --depth 1 <url> <revision>` itself, which reaches any commit on GitHub.
- Global outputs (`globalName`) are readable from sub-DAG tasks, exit hooks, and `onExit`. If the
  producing step failed, the global parameter has no value: it reads as `''` when the step's main
  container ran, or stays literal (`{{workflow.outputs.parameters.x}}`) when it never started.
  Neither is an error, so guards test its shape (`=~ '^[0-9a-f]{40}$'`).
- A failed step's output artifacts are still uploaded when its main container ran.
- Global outputs of a pod inside a stage's sub-DAG reach later stages' steps and `onExit`. An
  input artifact can be placed inside another (an artifact in the checkout at
  `/sources/main/dist/app`), whichever is listed first.
- A `steps` template's outputs with a `globalName` become global outputs; a `dag` template's
  never do (the node gets them, the workflow doesn't). So the setup wrapper is a `steps` template.
- `argo lint --offline` resolves a `templateRef` only against the files it's given; lint rendered
  output together with the referenced WorkflowTemplate (`tests/fixtures/templates/`).
- Output artifacts can come from the main container's own filesystem (no volume needed).
- On the kind quick-start, the default artifact repository is the namespace's MinIO
  (`artifact-repositories` configmap), not the controller config's plugin entry.
- `argo terminate` runs **no** exit handlers, so a terminated run can leave commit statuses
  pending. Accepted: builds are rarely terminated, and re-running the workflow updates the
  statuses. Don't add machinery for this case.

## Commands

```sh
uv sync                                  # install
uv run pytest                            # unit, snapshot, and static validation (helm/argo/kubeconform)
uv run pytest -m integration             # behavior tests on kind (run scripts/kind-up.sh first)
uv run pytest --snapshot-update          # accept snapshot changes (review the diff)
uv run ruff check . && uv run ruff format --check .

uv run stagecraft render <pipeline files or dirs> --out <chart dir> [--chart-name <name>]
uv run stagecraft check <pipeline files or dirs>
uv run stagecraft graph <pipeline files or dirs> [--pipeline <name>]   # Mermaid
uv run stagecraft contexts <pipeline files or dirs>

./scripts/kind-up.sh                     # kind cluster "stagecraft" + Argo (version from ARGO_VERSION)
./scripts/kind-demo.sh [--clean]         # install every fixture pipeline into kind (shares the tests' release)
./scripts/kind-ui.sh                     # Argo UI at https://localhost:2746 (port-forward)
kind delete cluster --name stagecraft

# Regenerate kubeconform schemas when bumping Argo (also updates ARGO_VERSION)
uv run python scripts/gen_crd_schemas.py <argo-version> tests/fixtures/crd-schemas
```

`tests/snapshots/test_rendered.py` renders each fixture pipeline as a generated chart and runs
`helm template` (with the test-only `tests/fixtures/values.yaml`), `argo lint --offline`, and
kubeconform on the result. By hand:

```sh
helm template fixture <chart> -f tests/fixtures/values.yaml > out/rendered.yaml
argo lint --offline out/rendered.yaml tests/fixtures/templates/*.yaml
kubeconform -strict -summary -schema-location default \
  -schema-location 'tests/fixtures/crd-schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  out/rendered.yaml
```

Integration tests pass `--context kind-stagecraft` to every kubectl, helm, and argo call. Keep it
that way so tests never touch the user's current kube context. Fixtures with a source fetch the
public `octocat/Hello-World` repository from GitHub, so the kind cluster needs network access.

## Rules

- The compile layer stays pure: no I/O, no env reads, no globals.
- Actions get values only through `ctx.value(path, type)` in `Action.compile(ctx)`, or through
  `{{ values.a.b }}` placeholders in their fields. Both return a marker string that the serializer
  turns into `{{ .Values.a.b | quote }}` (strings) or `{{ .Values.a.b | toJson | quote }}`
  (numbers and booleans: Helm's `quote` prints 1000000 as "1e+06"). Secrets are value-referenced names (for example
  `SecretEnv(secret_name=ctx.value(...))`), never contents.
- Every value reference is recorded in `ctx.references`, and the generated `values.schema.json`
  requires each with its type. Only single-typed scalars can be referenced (decided 2026-10-23);
  placeholders are strings.
  Structured values (objects, arrays) wait until an action needs them (decided 2026-10-24); typed
  action inputs and outputs come with artifacts (`DESIGN.md` section 16).
- Actions never emit Helm syntax. Only the serializer does, and it escapes all Argo expressions in
  one pass.
- A path used with two types, as both a value and an object, with non-identifier keys, or under
  the reserved `stagecraft` key must fail rendering. Never add defaults or optional values.
- Authors never write `depends`, `onExit`, or `hooks`. If a primitive cannot be expressed, fix the
  compiler.
- Stagecraft writes only `Chart.yaml`, `values.schema.json`, and `templates/` of the chart it
  generates (decided 2026-10-25): never `values.yaml`, and never the service accounts, RBAC, or
  Secrets pipelines refer to. It writes only into a missing or empty directory, or a chart it generated.
  `tests/fixtures/values.yaml` is test-only and never published.
- Actions hold only their own fields and `compile`; a `Step` places one in a stage with a name,
  `when`, `status`, and handlers (decided 2026-10-25). Don't add step features to `Action`.
- Statuses need a pipeline source: the reporter reads `{{ source.sha }}`, the commit the run
  resolved. Handlers and the reporter get the `{{ source.* }}` placeholders, never the checkout.
- Handlers see Stagecraft outcomes (`{{ step.outcome }}`: `succeeded`, `failed`), never Argo
  phases. The status reporter keeps host-style states (`success`, `failure`, `error`).
- There is no shared filesystem: each step runs in its own pod, and files will cross step
  boundaries only as artifacts (decided 2026-10-25). Don't add volumes shared between steps.
- `DESIGN.md` section 16 is the target model for the next slices (stage sources, conditions).
- Step outputs and artifacts are declared the Hera way on the action's template; Stagecraft adds
  `globalName`s and binds inputs. It emits no artifact GC or TTL: those are controller defaults. Read it before changing the authoring model.
- Hera's API changes between versions, so check the pinned version's docs rather than guessing.
- Don't generalize before two or three real pipelines exist. `DESIGN.md` section 15 lists the
  deferred features and what would justify each; update it when one is built or newly deferred.

## Workflow

- Write a failing compile or snapshot test first, then the implementation.
- Build in thin vertical slices, following `DESIGN.md` section 11.
- When a behavior depends on Argo semantics listed in `DESIGN.md` section 13, prove it with an
  integration test on kind rather than assuming.

## Layout

```
src/stagecraft/   spec.py, context.py, chart.py, compile/, serialize/, graph.py, cli.py
tests/unit/         plain functions, escaping, chart and schema generation
tests/contract/     value references: recording, conflicts, rendering
tests/snapshots/    compile + rendered-template snapshots
tests/integration/  kind + Argo behavior tests (marked `integration`)
tests/fixtures/     minimal actions, fixture pipelines, test-only values and WorkflowTemplates, CRD schemas
docs/               user guide: numbered chapters (one cumulative example project) + reference
```

Modules are added when a slice needs them, not ahead of time.

## External integrations

- Commit statuses go to GitHub (Commit Status API).
