"""Typed conditions, combined, and on Helm values. Stages that must be skipped fail if they run;
the test checks the others ran. notify.channel is "#ci-builds" in the fixture values."""

from stagecraft.conditions import param, value
from stagecraft.spec import Param, Pipeline, Stage

from fixtures.actions import Shell

RUNS, MUST_SKIP = Shell(script="true"), Shell(script="false")

pipeline = Pipeline(
    name="conditions-ci",
    params=[Param("deploy", default="true"), Param("env", default="staging")],
    stages=[
        Stage("deploy", [RUNS], when=(param("deploy") == "true") & ~(param("env") == "prod")),
        Stage("prod-only", [MUST_SKIP], when=param("env") == "prod"),
        Stage("either", [RUNS], when=(param("env") == "qa") | (param("env") == "staging")),
        Stage("by-value", [RUNS], when=value("notify.channel") == "#ci-builds"),
        Stage("not-value", [MUST_SKIP], when=value("notify.channel") != "#ci-builds"),
    ],
)
