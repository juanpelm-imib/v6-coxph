# Migrating coxph from vantage6 v4 to v5.0.2

This document describes the full process followed to migrate the
[`v6-coxph`](https://github.com/MaastrichtU-CDS/v6-coxph) algorithm
(originally designed for vantage6 v4, Docker-based) to version 5.0.2
(Kubernetes-based), and the version history of real bugs found while
deploying it to an actual production environment, not just local tests.

## 1. Background

vantage6 v4 and earlier were Docker-based; starting with v5.0.0 the project
moved to a Kubernetes-based architecture, and alongside that infrastructure
change, the API algorithms use to communicate with the framework
(`vantage6-algorithm-tools`) also changed in an incompatible way. The
vantage6 team migrated the official "community algorithms"
(`vantage6-community-algorithms`) to the new version, but third-party
algorithms such as `coxph` were left on the old API.

The mathematical/statistical logic of the algorithm (the part that actually
fits the Cox model) **did not change at all** during this migration - every
change is about packaging and the vantage6 communication layer.

## 2. How the migration pattern was identified

Rather than guessing at the required changes, an official vantage6 algorithm
was compared in its pre-v5 version and its current version:

- [`v6-glm-py` at tag `1.2.0`](https://github.com/vantage6/v6-glm-py/tree/1.2.0) (v4-compatible)
- [`v6-glm-py` at tag `1.3.1`](https://github.com/vantage6/v6-glm-py/tree/1.3.1) (v5-compatible)

Diffing both versions produced the exact migration pattern described below.

### 2.1. Packaging (`Dockerfile`, `pyproject.toml`)

| | v4 | v5 |
|---|---|---|
| Base image | `ghcr.io/vantage6/infrastructure/algorithm-base:4.15` | `ghcr.io/vantage6/infrastructure/algorithm-base:5.0` |
| Package install | `pip install /app` | `uv pip install --system -e /app` |
| Package definition | `setup.py` + `requirements.txt` | `pyproject.toml` (build backend `hatchling`) |
| Key dependency | `vantage6-algorithm-tools>4.0,<5.0` | `vantage6-algorithm-tools>=5.0.1` |

### 2.2. Decorators and imports (`vantage6-algorithm-tools`)

| | v4 | v5 |
|---|---|---|
| Client in the central function | `from vantage6.algorithm.tools.decorators import algorithm_client` | `from vantage6.algorithm.decorator.algorithm_client import algorithm_client` |
| Mark a central function | (implicit) | `from vantage6.algorithm.decorator.action import central`, `@central` decorator (outermost) |
| Mark a federated function | (implicit) | `from vantage6.algorithm.decorator.action import federated`, `@federated` decorator (outermost) |
| Inject data | `from vantage6.algorithm.tools.decorators import data`, `@data(1)` | `from vantage6.algorithm.decorator.data import dataframe`, `@dataframe(1)` |
| Logging | `from vantage6.algorithm.tools.util import info, warn, error` | `from vantage6.common import info, warning, error` (`warn` is renamed to `warning`) |

### 2.3. `client.task.create()` API

```python
# v4
client.task.create(
    input_={"method": "my_method", "kwargs": {"arg1": ...}},
    organizations=[...],
)

# v5
client.task.create(
    method="my_method",
    arguments={"arg1": ...},
    organizations=[...],
)
```

### 2.4. `algorithm_store.json`

- Each function's `"type": "central"` field is replaced by two fields:
  `"step_type": "central_compute"` / `"federated_compute"` and
  `"standalone": true`.
- `"ui_visualizations": []` (or populated) is added to every function.
- Arguments gain fields such as `display_name`, `has_default_value`,
  `is_frontend_only`, and support for conditionals
  (`conditional_on`/`conditional_operator`/`conditional_value`).

## 3. Changes applied to `coxph`

Applying the pattern above:

- **`coxph/central.py`**: added `@central` above `@algorithm_client`;
  replaced the 3 `client.task.create(input_={...})` calls with the
  `method=`/`arguments=` form; switched logging imports to
  `vantage6.common`.
- **`coxph/partial.py`**: see section 5 - a "direct" migration was applied
  first (same pattern as `central.py`), but it had to be revisited from
  first principles after real-deployment testing (this file needed more
  than an import-path change).
- **`Dockerfile`**: base `algorithm-base:5.0`, install via `uv`.
- **`pyproject.toml`**: replaces `setup.py`/`requirements.txt`. The
  `scikit-learn`, `requests` and `scikit-survival` dependencies from the
  original `setup.py` were dropped: none of them are actually used by the
  code (`central.py`/`partial.py` only import `numpy`, `pandas` and
  `scipy`) - they were unused dependencies inherited from the algorithm
  template, and `scikit-survival` in particular is heavy and adds nothing
  here.
- **`algorithm_store.json`**: rewritten from scratch following the v5
  schema (the original v4 one was also incomplete - it was missing 2 of the
  3 federated functions).

## 4. Local verification with `MockNetwork`

Before building the Docker image, the migration was verified by running the
algorithm locally against `vantage6.algorithm.mock.network.MockNetwork` (see
`test/test.py`), using the same `vantage6-algorithm-tools` version
(`5.0.2`) as the one deployed in production. This exercises the full
central + federated-subtask flow without needing Docker or Kubernetes.

**Important - limits of this verification**: `MockNetwork` in
`vantage6-algorithm-tools==5.0.2` **does not faithfully reproduce real v5
node behavior** in several respects, and this is exactly why v1.0.0 passed
local tests but failed in the real deployment:

- It does not enforce the real restriction that `federated_compute`
  containers never receive a client token (see section 5.1) - the mock
  always hands back a working client, regardless of action type.
- `MockBaseClient.Result.get()` has its own bug (it reads `self.network...`
  instead of `self.parent.network...`), unrelated to the real
  `vantage6-node` bug described in section 5.2, and had to be patched just
  for testing (`test/test.py` includes these patches, with comments
  explaining why they're needed and that they do not affect production
  code).
- It does not implement `_multi_page_request` at all.

In short: `MockNetwork` is a useful tool for catching logic/syntax errors
quickly, but it is **not a substitute for a real test against a deployed v5
node** for algorithms that use `algorithm_client` in federated steps, or the
less common client methods (`run.from_task`, `result.get`, etc.).

## 5. Version history: real bugs found in production

### v1.0.0 - initial migration (direct, per the pattern in section 2)

Passed local `MockNetwork` tests, but failed on the first real deployment
with:

```
error > Token not found. Is the method you called started as a compute container? Exiting...
```

**Root cause**: in vantage6 v5, the node
(`vantage6-node/vantage6/node/k8s/container_manager.py`) only injects the
`CONTAINER_TOKEN` environment variable when the task action is
`central_compute`:

```python
if action == AlgorithmStepType.CENTRAL_COMPUTE:
    secrets[ContainerEnvNames.CONTAINER_TOKEN.value] = token
```

For any other action (including `federated_compute`) the token is simply
never injected - this is a hard architectural rule, not something
configurable. The original v4 `coxph` used `client.organization_id` inside
`get_unique_event_times` (a federated function) to report which
organization failed the minimum sample-size threshold - a pattern that is
no longer possible in v5. None of the officially migrated algorithms (e.g.
`v6-glm-py`) use `@algorithm_client` in their federated functions, which
should have been the hint to revisit this from the start.

### v1.0.1 - remove `@algorithm_client` from federated functions

- `partial.py`: removed `@algorithm_client` (and the unused `client`
  parameter, unused in 2 of the 3 functions) from `get_unique_event_times`,
  `compute_summed_z` and `perform_iteration`. The low-sample-size sentinel
  value changed from `{"N-Threshold not met": client.organization_id}` to
  `{"N-Threshold not met": True}`, since the function can no longer know
  its own organization.
- `central.py`: since the central function does have a client, each
  `get_unique_event_times` result's organization is now resolved via
  `client.run.from_task(task_id)` (which does include `organization.id` per
  run) instead of relying on the payload to carry it.

### v1.0.2 - `client.result.get()` hits a node proxy bug

Testing v1.0.1 for real produced:

```
TypeError: proxy_results() got an unexpected keyword argument 'id'. Did you mean 'id_'?
```

**Root cause**: `client.result.get(id_)` makes a request to the
`/result/<id>` endpoint through the node's local proxy. In
`vantage6-node:5.0.2`, the Flask view function handling that route
(`proxy_results`) has a parameter-name mismatch between the route and the
function signature - this is a genuine bug in `vantage6-node` itself, not
specific to this algorithm, and will affect any algorithm that calls
`client.result.get()` directly.

- `central.py`: replaced the `client.result.get(id)` call (broken
  single-item endpoint) with a call to the list endpoint
  `result?task_id=...` (`client._multi_page_request("result", params=...)`,
  the same internal mechanism already used successfully by
  `wait_for_results()`/`Result.from_task()`), manually decoding the result
  (`base64s_to_bytes(...)` + `json.loads(...)`) and correlating each result
  with its organization by `id` - `Run` and `Result` are the same
  underlying row in the HQ schema (`RunSchema`/`ResultSchema` both use
  `model = db.Run`), so they share the same `id`.

## 6. Other vantage6 v5 bugs discovered along the way

Not specific to `coxph`, but relevant to any future deployment/algorithm on
this cluster:

- **Fail-fast on multi-organization tasks**: if a `central_compute` task is
  submitted with `organizations=[org1, org2, org3]` (instead of a single
  coordinating organization) and **any** of those runs fails (e.g. because
  it cannot pull the image), HQ marks the whole task `failed` immediately,
  even while the other runs are still legitimately in progress. When the
  "good" run then tries to create its first subtask, HQ rejects it
  (`"Container from node=X attempts to start sub-task for a completed
  task"`), which in turn makes `client.task.create()` return an error dict
  with no `id`, and `client.wait_for_results(task_id=None)` retries forever
  against `task/None/status` (404 in a loop) - and since the container never
  crashes, it becomes a zombie job that Kubernetes never cleans up on its
  own. Because of this: **a `central_compute` task must be submitted to a
  single coordinating organization** (`organizations=[org_id]`), not to all
  of them - the full list of organizations belongs in the `organization_ids`
  argument that the algorithm itself uses to fan out federated subtasks.
- **The private Docker registry needs to be reachable from every host
  running a participating node**, not just from wherever the image is
  built/pushed.

## 7. Deployment

Building the image, publishing it to a private registry, and registering it
with the Algorithm Store (private Docker registry, TLS, trusting the
certificate across Docker/containerd/the Algorithm Store, and registering
the algorithm via the Python client) is deployment-specific and documented
separately in the internal deployment repository this algorithm was tested
against, not in this file - this document covers only the migration of the
algorithm's own code.
