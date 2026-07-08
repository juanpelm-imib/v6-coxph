"""
Example: submit a coxph task to a real vantage6 v5 deployment.

This mirrors the actual steps used to test this algorithm's v5 migration
end-to-end (session creation, data extraction into the session, then task
submission), with deployment-specific values (URLs, collaboration/store
ids, organization/session names, dataset columns) replaced by placeholders
you need to fill in for your own vantage6 instance.

Before running this:
  1. Generate (or otherwise obtain) a CSV with the expected columns and make
     it available as a database on every node that should participate - see
     generate_synthetic_data.py in this same folder for a quick way to get
     started. Each node reads its data from its own local configuration; a
     script cannot upload/place local data files onto remote nodes for you.
  2. Push the coxph image built from this repo to a registry your nodes can
     pull from, and register it with your Algorithm Store (see this
     repository's deployment docs).
  3. Fill in the placeholders below (marked with <...>).
"""
import json

from vantage6.client import UserClient as Client

# --- Connection settings - replace with your own vantage6 deployment -------
HQ_URL = "<https://your-vantage6-hq-url>"
AUTH_URL = "<https://your-vantage6-auth-url>"
KEYCLOAK_REALM = "<your-keycloak-realm>"
KEYCLOAK_CLIENT = "<your-keycloak-public-client>"

# --- Study settings - replace with your own collaboration/store ids --------
COLLABORATION_ID = 1
ALGORITHM_STORE_ID = 1
COORDINATING_ORGANIZATION_NAME = "<name of the organization that should run central()>"
DATASET_LABEL = "coxph-example-data"
SESSION_NAME = "coxph_example"

# --- Dataset columns - matches generate_synthetic_data.py's output ---------
TIME_COLUMN = "time"
OUTCOME_COLUMN = "event"
EXPLANATORY_VARIABLES = ["age", "sex", "treatment_arm", "biomarker_level"]

# --- Algorithm image - point at wherever you pushed the coxph:5.0.2 image --
ALGORITHM_IMAGE = "<registry>/coxph:<tag>"


def main() -> None:
    client = Client(
        hq_url=HQ_URL,
        auth_url=AUTH_URL,
        auth_realm=KEYCLOAK_REALM,
        auth_client=KEYCLOAK_CLIENT,
        log_level="info",
    )
    # Opens a browser window to authenticate against Keycloak.
    client.authenticate()

    # Only needed if your collaboration has encryption enabled - point this
    # at your own organization's private key file.
    # client.setup_encryption("path/to/your_organization_private_key.pem")

    org_ids = [
        org["id"]
        for org in client.organization.list(fields=["id", "name"])
        if org["name"] != "root"
    ]

    # A session groups the dataframes and tasks of one analysis together.
    client.session.create(
        scope="collaboration",
        collaboration=COLLABORATION_ID,
        name=SESSION_NAME,
        display=True,
    )
    session_id = next(
        s["id"]
        for s in client.session.list(fields=["id", "name"])["data"]
        if s["name"] == SESSION_NAME
    )

    # Load the CSV each node has locally into the session as a dataframe,
    # using the community extract-basics algorithm. Replace the image tag
    # with whatever extract-basics version your Algorithm Store has.
    client.dataframe.create(
        label=DATASET_LABEL,
        image="ghcr.io/vantage6/algorithm/extract-basics:1.0.1-v6-5.0.1",
        method="read_csv",
        arguments={},
        name=DATASET_LABEL,
        session=session_id,
        store=ALGORITHM_STORE_ID,
        display=True,
    )
    dataframes = client.dataframe.list(session=session_id)
    dataframe_id = next(
        df["id"] for df in dataframes["data"] if df["name"] == DATASET_LABEL
    )

    coordinating_org = next(
        node["id"]
        for node in client.node.list(fields=["id", "name"])
        if node["name"] == COORDINATING_ORGANIZATION_NAME
    )

    # NOTE: `organizations` is the single organization that runs the
    # `central` orchestrator function - it must be exactly one organization,
    # not the full collaboration. The full set of organizations that should
    # participate in the federated subtasks goes in `organization_ids`
    # below instead. Dispatching the central task itself to multiple
    # organizations triggers a real vantage6 v5 bug where the whole task
    # gets marked "failed" as soon as any one of them fails for any reason -
    # see MIGRATION.md, section 6, for the full explanation.
    task = client.task.create(
        collaboration=COLLABORATION_ID,
        organizations=[coordinating_org],
        name="coxph-example",
        image=ALGORITHM_IMAGE,
        description="coxph example run on synthetic data",
        method="central",
        arguments={
            "time_col": TIME_COLUMN,
            "outcome_col": OUTCOME_COLUMN,
            "expl_vars": EXPLANATORY_VARIABLES,
            "organization_ids": org_ids,
        },
        session=session_id,
        store=ALGORITHM_STORE_ID,
        databases=[{"dataframe_id": dataframe_id}],
        action="central_compute",
    )

    print(f"Task {task['id']} submitted, waiting for results...")
    results = client.wait_for_results(task["id"])

    model = json.loads(results[0]["model"])
    print("\nFitted Cox model coefficients:")
    print(json.dumps(model, indent=2))
    print(f"\nOverall p-value: {results[0]['overall_p_value']}")
    print(f"AIC: {results[0]['aic']}")
    print(f"Included organizations: {results[0]['included_organizations']}")
    print(f"Excluded organizations: {results[0]['excluded_organizations']}")


if __name__ == "__main__":
    main()
