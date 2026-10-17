# Architecture Overview

This directory documents the Kubernetes architecture for Cerebral and Unsigned.
It defines the system boundary and the Rust type system.

## Documents

- [Substrate and Conscious Boundary](substrate-conscious-boundary.md)
  Explains the separation between Unsigned compute substrates and the Cerebral conscious layer.
- [Kubernetes Rust Type System](kubernetes-rust-type-system.md)
  Details `kube-rs`, `k8s-openapi`, schema derivation, and JSON merge patch rules.
- [CRD Catalog](crd-catalog.md)
  Catalogs all custom resource definitions across the estate.

## System Model

```mermaid
flowchart TD
    subgraph Cerebral ["Cerebral Conscious Layer"]
        CFG["SagaFlow / SagaRun (cerebral.work)"]
        VES["Orchestrator / WorkSource (vespertin.cerebral.work)"]
        FA["FunctionAgent (vespertin.cerebral.work)"]
    end

    subgraph Unsigned ["Unsigned Compute & Kernel Substrate"]
        K8["k8-api / k8-core (unsigned.gg)"]
        AW["AgentWorkload Runtime"]
        KUBE["unsigned-k8s-core / unsigned-k8s-api"]
    end

    CFG --> K8
    VES --> K8
    FA --> AW
    K8 --> KUBE
```

## Maintenance

Run the generator script to update these files:

```bash
python3 scripts/generate_k8s_docs.py
```

Verify documentation parity with tests:

```bash
python3 -m unittest discover -s tests -v
```
