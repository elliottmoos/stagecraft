"""Cron triggers: a nightly run with overridden params, and a second trigger whose param comes
from a Helm value."""

from stagecraft.spec import Param, Pipeline, Stage, Trigger

from fixtures.blocks import Shell

pipeline = Pipeline(
    name="triggers-ci",
    params=[
        Param("repo", required=True),
        Param("mode", default="manual", enum=["manual", "nightly", "weekly"]),
        Param("channel", default="none"),
    ],
    stages=[
        Stage("build", [Shell(name="echo", script='echo "{{ param.repo }} {{ param.mode }}"')]),
    ],
    triggers=[
        Trigger.cron(
            "0 2 * * *", name="nightly", params={"repo": "example/repo", "mode": "nightly"}
        ),
        Trigger.cron(
            "@weekly",
            name="weekly",
            timezone="Europe/Amsterdam",
            params={
                "repo": "example/repo",
                "mode": "weekly",
                "channel": "{{ values.notify.channel }}",
            },
        ),
    ],
)
