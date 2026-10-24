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

## Commands

```sh
uv sync                                  # install
uv run pytest                            # unit, contract, snapshot tests
uv run pytest -m integration             # behavior tests; needs a kind cluster with Argo installed
uv run pytest --snapshot-update          # accept snapshot changes (review the diff)
uv run ruff check . && uv run ruff format --check .

# Static validation of rendered output (fixture chart lives in tests/fixtures/chart)
helm template fixture tests/fixtures/chart > out/rendered.yaml
argo lint --offline out/rendered.yaml
kubeconform -strict -summary \
  -schema-location default \
  -schema-location 'tests/fixtures/crd-schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  out/rendered.yaml
```

## Rules

- The compile layer stays pure: no I/O, no env reads, no globals.
- Blocks get values only through the `CompileContext`, declared with path and type. Secrets are
  references, never contents.
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
