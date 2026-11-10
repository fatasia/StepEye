# Frozen valve-fusion research candidate

Source: [fatasia/mechanism-lab](https://github.com/fatasia/mechanism-lab),
revision `01d77002e960b93254f137a8dd7e618abb12c3ae`.
Source code: MIT, Copyright (c) 2026 fatasia; full notice in `LICENSE.weihu.txt`.
The source manifest does not separately declare weight release terms.
This registration is `evaluation_only`, not production authorization.

Training data attribution from upstream `code/THIRD_PARTY_NOTICES.md`:
Hydraulic Systems — Nikolai Helwig, Eliseo Pignanelli, Andreas Schütze,
DOI [10.24432/C5CW21](https://doi.org/10.24432/C5CW21), CC BY 4.0.
Processing: PS1/PS2 pressures in bar, 100 Hz, cycle window [8,12) seconds;
project-authored frozen autoencoder and calibrated timing fusion.
Neither the source authors nor the data authors endorse a StepEye deployment.

The ONNX embeds normalization, autoencoder, timing features and historical
branch thresholds. It does not attest a device clock, verify command provenance,
establish a current fault root cause, or authorize control/alarm/SOP actions.
