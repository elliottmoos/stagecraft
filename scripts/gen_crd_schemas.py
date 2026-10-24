"""Generate kubeconform JSON schemas from the full Argo Workflows CRDs.

Usage: uv run python scripts/gen_crd_schemas.py <argo-version> <out-dir>

Writes <kind>_<version>.json (lowercase kind) for every CRD below, matching kubeconform's
'{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' schema location template. Descriptions are
stripped to keep the files small; objects with declared properties reject unknown fields.
"""

import json
import sys
import urllib.request
from pathlib import Path

import yaml

CRDS = ["workflowtemplates", "cronworkflows", "workflows"]
URL = "https://raw.githubusercontent.com/argoproj/argo-workflows/v{version}/manifests/base/crds/full/argoproj.io_{crd}.yaml"


def to_json_schema(node):
    """Convert one OpenAPI v3 schema node. Only schema nodes reach here, never property maps."""
    if isinstance(node, list):
        return [to_json_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "description":
            continue  # documentation of this schema node
        if key == "properties":
            # Keys here are field names (a field may be called "description"), values are schemas.
            out[key] = {name: to_json_schema(schema) for name, schema in value.items()}
        else:
            out[key] = to_json_schema(value)
    if out.pop("x-kubernetes-int-or-string", False):
        out.pop("type", None)
        out["oneOf"] = [{"type": "string"}, {"type": "integer"}]
    preserve = out.pop("x-kubernetes-preserve-unknown-fields", False)
    if "properties" in out and "additionalProperties" not in out and not preserve:
        out["additionalProperties"] = False
    is_open_object = out.get("type") == "object" and "properties" not in out
    if is_open_object and "additionalProperties" not in out:
        out["additionalProperties"] = True
    return {k: v for k, v in out.items() if not k.startswith("x-kubernetes-")}


def main(version: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for crd in CRDS:
        with urllib.request.urlopen(URL.format(version=version, crd=crd)) as resp:
            doc = yaml.safe_load(resp)
        kind = doc["spec"]["names"]["kind"].lower()
        for v in doc["spec"]["versions"]:
            schema = to_json_schema(v["schema"]["openAPIV3Schema"])
            path = out_dir / f"{kind}_{v['name']}.json"
            path.write_text(json.dumps(schema, separators=(",", ":"), sort_keys=True) + "\n")
            print(f"{path} ({path.stat().st_size // 1024} KiB)")
    (out_dir / "ARGO_VERSION").write_text(version + "\n")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
