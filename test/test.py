"""
Run this script to test the migrated algorithm locally (without building a
Docker image) using vantage6's MockNetwork.

Run as (from the `test/` directory, with vantage6-algorithm-tools installed):

    python test.py
"""
import json
from pathlib import Path

import pandas as pd

from vantage6.common import bytes_to_base64s
from vantage6.algorithm.mock.network import MockNetwork
from vantage6.algorithm.mock.client import MockAlgorithmClient

# MockAlgorithmClient does not implement _multi_page_request at all (real
# AlgorithmClient uses it internally for paginated HQ requests; the mock
# short-circuits actual HTTP calls, so it's listed in _missing_attributes and
# resolves to None). central.py calls it directly to work around a real
# vantage6-node 5.0.2 bug in the /result/<id> proxy endpoint (see central.py
# comment). This patch only affects local MockNetwork testing - it re-encodes
# the mock's plain-JSON result strings as base64 to match what the real HQ
# /result endpoint returns.
def _fixed_multi_page_request(self, endpoint, params=None):
    if endpoint != "result":
        return []
    task_id = (params or {}).get("task_id")
    return [
        {
            "id": r["id"],
            "result": bytes_to_base64s(r["result"].encode()),
        }
        for r in self.network.hq.results
        if r.get("task", {}).get("id") == task_id
    ]


MockAlgorithmClient._multi_page_request = _fixed_multi_page_request

current_path = Path(__file__).parent
DATABASE_LABEL = "Database"

df = pd.read_csv(current_path / "HEAD-NECK-RADIOMICS-HN1.csv")

network = MockNetwork(
    datasets=[
        # Data for first organization
        {DATABASE_LABEL: {"database": df}},
        # Data for second organization
        {DATABASE_LABEL: {"database": df}},
    ],
    module_name="coxph",
)
client = network.user_client
databases = [{"type": "dataframe", "dataframe_id": network.hq.dataframes[0]["id"]}]

organizations = client.organization.list()
print(organizations)
org_ids = [organization["id"] for organization in organizations]

# Run the central method on 1 node and get the results
central_task = client.task.create(
    method="central",
    arguments={
        "time_col": "overall_survival_in_days",
        "outcome_col": "event_overall_survival",
        "expl_vars": ["clin_n_1", "index_tumour_location_oropharynx"],
        "organization_ids": org_ids,
    },
    organizations=[org_ids[0]],
    databases=databases,
)
results = client.wait_for_results(central_task.get("id"))
print(json.dumps(results, indent=2))

model = json.loads(results[0]["model"])
print("\nFitted coefficients:")
print(json.dumps(model, indent=2))
