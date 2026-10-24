# Stagecraft

Python framework and CLI that compiles CI/CD pipelines (ordered stages of parallel blocks) into
Helm-compatible Argo Workflows `WorkflowTemplate` and `CronWorkflow` manifests, using Hera as the
compiler. `DESIGN.md` is the spec; read the relevant section before changing behavior.

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
  stage and block handlers run from one `exit` hook calling a `when`-guarded handler DAG.
  `{{tasks.<name>.exitCode}}` doesn't resolve in hook arguments.
- A failing task hook (`exit` or expression) does **not** fail the workflow. Accepted for stage
  and block handlers; pipeline handlers (onExit) do fail it.
- A task skipped by `when` fires none of its handlers.
- `when` parses bare words: `true == 'true'` is false (boolean vs string). Stagecraft quotes
  param placeholders inside `when`.
- `running` hooks are unreliable: one on an 8-second block never fired (it did on a 60-second
  one). Don't use them for anything that must happen.
- `exit` hooks don't fire for a task skipped by `when`.
- `continueOn` is rejected by `argo lint` in any DAG template that uses `depends`.
- A failed task counts as handled (the DAG can still succeed) only if a task depending on its
  failure actually runs.
- `argo stop` doesn't run the `exit` hook of a block that was running (the stage's still runs).
  A block-level commit status stays pending after a stop until the workflow is re-run.
- `argo stop` runs exit handlers with `{{workflow.status}} == Failed`.
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

uv run stagecraft render <pipeline files or dirs> --values-schema <chart>/values.schema.json --out <dir>

./scripts/kind-up.sh                     # kind cluster "stagecraft" + Argo (version from ARGO_VERSION)
kind delete cluster --name stagecraft

# Regenerate kubeconform schemas when bumping Argo (also updates ARGO_VERSION)
uv run python scripts/gen_crd_schemas.py <argo-version> tests/fixtures/crd-schemas
```

`tests/snapshots/test_rendered.py` renders each fixture pipeline into a temporary copy of the
fixture chart and runs `helm template`, `argo lint --offline`, and kubeconform on the result. By
hand:

```sh
helm template fixture <chart> > out/rendered.yaml
argo lint --offline out/rendered.yaml
kubeconform -strict -summary -schema-location default \
  -schema-location 'tests/fixtures/crd-schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  out/rendered.yaml
```

Integration tests pass `--context kind-stagecraft` to every kubectl, helm, and argo call. Keep it
that way so tests never touch the user's current kube context.

## Rules

- The compile layer stays pure: no I/O, no env reads, no globals.
- Blocks get values only through `ctx.value(path, type)` in `Block.compile(ctx)`, or through
  `{{ values.a.b }}` placeholders in their fields. Both return a marker string that the serializer
  turns into `{{ .Values.a.b | quote }}`. Secrets are value-referenced names (for example
  `SecretEnv(secret_name=ctx.value(...))`), never contents.
- Only required, non-null, scalar values can be referenced (decided 2026-10-23). Structured values
  (objects, arrays) are slice 8; typed block inputs/outputs wait until a pipeline needs them.
- Blocks never emit Helm syntax. Only the serializer does, and it escapes all Argo expressions in
  one pass.
- A reference to a value missing from the contract, or of the wrong type, must fail rendering.
  Never silently default.
- Authors never write `depends`, `onExit`, or `hooks`. If a primitive cannot be expressed, fix the
  compiler.
- Stagecraft never generates chart files or the values contract. The fixture chart under
  `tests/fixtures/` is test-only and never published.
- Workspace strategy is a compile-time pipeline choice and reads no Helm values or
  `CompileContext` data.
- Hera's API changes between versions, so check the pinned version's docs rather than guessing.
- Don't generalize before two or three real pipelines exist.

## Workflow

- Write a failing compile or snapshot test first, then the implementation.
- Build in thin vertical slices, following `DESIGN.md` section 11.
- When a behavior depends on Argo semantics listed in `DESIGN.md` section 13, prove it with an
  integration test on kind rather than assuming.

## Layout

```
src/stagecraft/   spec.py, context.py, contract.py, compile/, serialize/, graph.py, cli.py
tests/unit/         plain functions, escaping, contract-validation logic
tests/contract/     block contract and values-contract validation
tests/snapshots/    compile + rendered-template snapshots
tests/integration/  kind + Argo behavior tests (marked `integration`)
tests/fixtures/     minimal blocks, fixture schema, test-only fixture chart, CRD schemas
```

Modules are added when a slice needs them, not ahead of time.

## External integrations

- Commit statuses go to GitHub (Commit Status API).
