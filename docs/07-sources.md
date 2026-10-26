# 7. Sources

Most steps need the code they're building. Rather than have every action fetch it, a pipeline
declares its **source**, and Stagecraft checks it out once and hands every step the same commit.

## Declaring a source

```python
Pipeline(
    ...,
    params=[Param("revision", default="main", description="Branch, tag, or commit to build")],
    source=Source(url="https://github.com/example/service.git", revision="{{ param.revision }}"),
)
```

- **`url`** is the repository, as `git fetch` takes it.
- **`revision`** is a commit SHA, a branch, or a tag. Both may use `{{ param.x }}` and
  `{{ values.a.b }}`, so a run can say what to build.

A pipeline has at most one source.

## What you get

**One commit for the whole run.** A generated step named `source` runs before the first stage. It
fetches the revision and resolves it to a commit: a branch is resolved once, at the start of the
run, so every step builds the same commit even if the branch moves meanwhile.

**The checkout in every step.** Each step starts with the checkout at `/sources/main`, which is
also its working directory (unless the action sets its own), and the environment variable
`STAGECRAFT_SOURCE_MAIN` holds the path. So `make lint` just works. Each step gets its own copy:
steps share no files, and changes one step makes are invisible to the others.

**The commit, by placeholder.** Every action, handlers included, can use:

| Placeholder | Value |
|---|---|
| `{{ source.url }}` | the source's `url` |
| `{{ source.revision }}` | the revision as given, such as `main` |
| `{{ source.sha }}` | the commit it resolved to |

Things to know:

- **The checkout is shallow** (one commit deep). It includes `.git`, so `git rev-parse HEAD`
  works, but there's no history: `git log` shows one commit and `git describe` finds no tags.
- **Handlers and the status reporter get no checkout**, only the placeholders. They also run when
  the source step itself failed, when there's nothing to check out.
- **If the source step fails** (a revision that doesn't exist, bad credentials), the run fails
  before any stage, and `{{ source.sha }}` has no value.
- **`source` is a reserved stage name.**

## When the revision comes from a webhook: the setup step

Runs started by Argo Events often receive the whole GitHub webhook payload as one param, and the
repository and commit have to be parsed out of it first. A **setup step** runs before everything
else, the source included, and its output parameters work everywhere as `{{ setup.<output> }}`:

```python
Pipeline(
    ...,
    params=[Param("payload", required=True, description="The GitHub webhook payload")],
    setup=TemplateRef(
        "parse-webhook",                          # an existing WorkflowTemplate
        template="parse",
        arguments={"payload": "{{ param.payload }}"},
        outputs=["repo_url", "sha", "event"],
    ),
    source=Source(url="{{ setup.repo_url }}", revision="{{ setup.sha }}"),
    stages=[..., Stage("deploy", [...], when="{{ setup.event }} == 'push'")],
)
```

- **Two forms.** `TemplateRef(name, template=, arguments=, outputs=)` calls a template of a
  WorkflowTemplate that's already installed, as is. Or the setup is an ordinary action, bare or in
  a `Step`, like `setup=ParseWebhook(payload="{{ param.payload }}")`.
- **Outputs.** An action's outputs are the output parameters its `compile` declares
  (`Container(..., outputs=[Parameter(name="sha", value_from=ValueFrom(path="/tmp/sha"))])`). A
  `TemplateRef` lists them in `outputs`, since Stagecraft can't look inside the template.
  Referring to any other name fails the render.
- **Everywhere.** `{{ setup.<output> }}` works in the source's fields, every step, `when` (quoted,
  like params), handlers, and the status reporter.
- **A plain step.** The setup can't set `when`, `status`, or handlers, and gets no checkout (it
  runs before the source). If it fails, the run fails before anything else runs.
- **Linting.** `argo lint` checks a `TemplateRef` against the files it's given, so lint the
  rendered chart together with the referenced WorkflowTemplate's manifest (chapter 11).
- **`setup` is a reserved stage name.**

## Settings: the git image and credentials

The source step needs an image with `git` and `sh`, and credentials for private repositories.
Both are Helm values under the `stagecraft` key, which is Stagecraft's own:

| Value | Meaning |
|---|---|
| `stagecraft.sources.image` | An image with `git` and `sh`, such as `alpine/git:2.47.2` |
| `stagecraft.sources.secret` | A Secret with `username` and `password` keys, for HTTPS. For GitHub, use a token as the password. |

The Secret is optional at run time: for a public repository, name one that doesn't exist and
the fetch runs without credentials. The credentials go in an HTTP header for the fetch only;
they never land in the checkout.

## Growing the example

The `prepare` stage was a stand-in; the source replaces it, and the `repo` and `sha` params give
way to one `revision`:

```python file=pipelines/service_ci.py
from stagecraft.spec import Param, Pipeline, Source, Stage, Step

from actions import Make, Notify

pipeline = Pipeline(
    name="service-ci",
    params=[
        Param("revision", default="main", description="Branch, tag, or commit to build"),
        Param("push", default="false", enum=["true", "false"], description="Push the image"),
        Param("deploy", default="false", enum=["true", "false"], description="Deploy to staging"),
    ],
    source=Source(url="https://github.com/example/service.git", revision="{{ param.revision }}"),
    stages=[
        Stage(
            "verify",
            [
                Step("lint", Make(target="lint")),
                Step("unit-tests", Make(target="test")),
            ],
            on_failure=[Notify(message="{{ step.name }} failed at {{ source.sha }}")],
        ),
        Stage(
            "package",
            [Step("image", Make(target="image", variables={"PUSH": "{{ param.push }}"}))],
        ),
        Stage(
            "deploy",
            [Step("staging", Make(target="deploy-staging"))],
            when="{{ param.deploy }} == 'true'",
        ),
    ],
    on_failure=[Notify(message="{{ step.name }} {{ step.outcome }}")],
)
```

The installation names the git image and the credentials Secret:

```yaml file=values/staging.yaml
images:
  shell: alpine:3.22
  build: golang:1.25
build:
  cpu: "1"
  memory: 2Gi
notify:
  channel: "#ci"
stagecraft:
  sources:
    image: alpine/git:2.47.2
    secret: github-checkout
```

`github-checkout` is created like any other Secret, outside Stagecraft:

```sh
kubectl create secret generic github-checkout -n argo \
  --from-literal=username=x-access-token --from-literal=password=<token>
```

## Recap

- `Pipeline(source=Source(url=..., revision=...))` checks the code out once, before the first
  stage, resolving a branch or tag to one commit for the whole run.
- Every step starts in the checkout (`/sources/main`, `$STAGECRAFT_SOURCE_MAIN`); handlers and the
  reporter get only `{{ source.url }}`, `{{ source.revision }}`, and `{{ source.sha }}`.
- The installation supplies `stagecraft.sources.image` and `stagecraft.sources.secret`.
- A setup step (`TemplateRef` or an action) runs first; its outputs work everywhere as
  `{{ setup.<output> }}`, the source's fields included.

Next: [8. Passing results between steps](08-passing-results.md).
