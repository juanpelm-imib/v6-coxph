"""
Generate a synthetic survival dataset to try out the coxph algorithm without
needing real patient data.

Simulates event times from a Cox proportional hazards model with a constant
baseline hazard (i.e. exponentially distributed survival times), so the
generated data has a known, recoverable structure: fitting coxph against it
should recover coefficients reasonably close to `TRUE_BETA` below (given
enough patients and event rate).

Usage:

    python generate_synthetic_data.py [n_patients] [output.csv]

Defaults to 200 patients, written to synthetic_coxph_data.csv in the current
directory.

In a real (non-mocked) vantage6 deployment, each participating node reads
its data from a local file registered in its own node configuration (see
this repo's deployment docs for the `databases.fileBased` pattern). To try
the federated flow for real, generate one CSV per organization (they can be
identical or have different patient counts/distributions to see how the
algorithm behaves with heterogeneous data stations), and register each one
as a database on its corresponding node.
"""
import sys

import numpy as np
import pandas as pd

# True coefficients used to simulate the data - a real fit should recover
# values in the same ballpark (sign and rough magnitude) as these.
TRUE_BETA = {
    "age": 0.03,  # older patients have higher hazard
    "sex": 0.4,  # sex=1 has higher hazard than sex=0
    "treatment_arm": -0.6,  # treatment_arm=1 (treated) has lower hazard
    "biomarker_level": 0.25,
}
BASELINE_HAZARD = 0.02
MAX_FOLLOW_UP = 60  # administrative censoring time, e.g. months


def generate(n_patients: int, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    age = rng.normal(loc=60, scale=10, size=n_patients).clip(18, 95)
    sex = rng.integers(0, 2, size=n_patients)
    treatment_arm = rng.integers(0, 2, size=n_patients)
    biomarker_level = rng.normal(loc=1.0, scale=0.5, size=n_patients).clip(0, None)

    linear_predictor = (
        TRUE_BETA["age"] * (age - age.mean())
        + TRUE_BETA["sex"] * sex
        + TRUE_BETA["treatment_arm"] * treatment_arm
        + TRUE_BETA["biomarker_level"] * biomarker_level
    )
    hazard = BASELINE_HAZARD * np.exp(linear_predictor)

    # Inverse transform sampling for an exponential distribution with the
    # per-patient hazard above.
    event_time = -np.log(rng.uniform(size=n_patients)) / hazard
    censor_time = rng.uniform(0, MAX_FOLLOW_UP, size=n_patients)

    time = np.minimum(event_time, censor_time)
    event = (event_time <= censor_time).astype(int)

    return pd.DataFrame(
        {
            "time": np.round(time, 1),
            "event": event,
            "age": np.round(age, 1),
            "sex": sex,
            "treatment_arm": treatment_arm,
            "biomarker_level": np.round(biomarker_level, 3),
        }
    )


if __name__ == "__main__":
    n_patients = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    output_path = sys.argv[2] if len(sys.argv) > 2 else "synthetic_coxph_data.csv"

    df = generate(n_patients)
    df.to_csv(output_path, index=False)

    print(f"Wrote {len(df)} rows to {output_path}")
    print(f"Event rate: {df['event'].mean():.1%}")
    print("\nFirst rows:")
    print(df.head())
