#!/usr/bin/env python3
"""Generate Kubernetes architecture documentation for Cerebral and Unsigned.

This script generates:
- docs/architecture/README.md
- docs/architecture/substrate-conscious-boundary.md
- docs/architecture/kubernetes-rust-type-system.md
- docs/architecture/crd-catalog.md

Prose follows ASD-STE100 (Simplified Technical English):
- Procedural sentences <= 20 words.
- Descriptive sentences <= 25 words.
- Paragraphs <= 6 sentences on one topic.
- Simple verb forms (imperative, present, past).
- Noun clusters <= 3 words.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


DOCS_README = """# Architecture Overview

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
"""

DOCS_BOUNDARY = """# Substrate and Conscious Boundary

This document defines the architectural boundary between Unsigned and Cerebral.

## Boundary Principles

The estate splits Kubernetes responsibilities into two distinct domains:

1. **Unsigned** owns compute, scheduling, and kernel primitives.
2. **Cerebral** owns cognitive loops, sagas, reflex targets, and deliberative states.

This separation prevents circular dependencies.
It also isolates conscious decision systems from hardware execution.

```mermaid
flowchart LR
    subgraph Conscious ["Cerebral Domain"]
        direction TB
        Saga["Sagas & Cognitive Loops"]
        Reflex["Reflex Pipelines"]
        AgentState["Agent Mental States"]
    end

    subgraph Substrate ["Unsigned Domain"]
        direction TB
        Pods["Pod & Sandbox Lifecycle"]
        GPU["Hardware & GPU Scheduling"]
        Network["Zero-Trust Policies"]
        Storage["Storage & Volumes"]
    end

    Conscious -->|Requests Execution| Substrate
```

## Unsigned: Compute and Kernel Substrate

Unsigned components manage direct execution resources.
They do not make strategic or cognitive decisions.

### Crates and Locations

- **`unsigned-k8s-core`** (`projects/domain/schedule/k8s-core`)
  Foundational crate providing raw bindings.
  It re-exports `kube` 4.2.0 and `k8s-openapi` 0.28.0 with the `latest` feature.
- **`unsigned-k8s-api`** (`projects/domain/schedule/k8s-api`)
  Public API crate for Unsigned cluster services.
  Consumes `unsigned-k8s-core`.
- **`k8` Workspace** (`projects/domain/schedule/k8`)
  - **`k8-api`**: Zero-I/O leaf domain crate. Defines quantities, selectors, lifecycle conditions, and `AgentWorkload`.
  - **`k8-core`**: Core domain logic and scheduling models.
  - **`k8-orchestrator`**: Controller runtime and reconcile loops for hardware scheduling.
  - **`k8`**: Command-line interface for workload lifecycle.

### Responsibilities

- Container execution engines and pod sandboxes.
- GPU and CPU hardware placement.
- Slurm cluster integration and batch queues.
- Zero-trust network policy enforcement.
- Restricted pod security contexts (`restricted` profile).

## Cerebral: The Conscious Layer

Cerebral components manage cognition and multi-agent coordination.
They express intent through high-level custom resources.

### Crates and Locations

- **`cerebral-k8s-api`** (`projects/domain/schedule/cerebral-work-k8s-api`)
  Private crate for Cerebral Custom Resource Definitions (`cerebral.work/v1alpha1`).
  Defines `SagaFlow` and `SagaRun`.
- **`cerebral-k8s-domain`** (`projects/domain/schedule/cerebral-k8s/crates/cerebral-k8s-domain`)
  Zero-I/O algebraic state models and operator configurations.
- **`cerebral-k8s`** (`projects/domain/schedule/cerebral-k8s/crates/cerebral-k8s`)
  Systems operator runtime and crate re-exports.
- **`vespertin`** (`projects/platform/runtime/vespertin`)
  Conscious runtime operator.
  Manages `Orchestrator`, `WorkSource`, and `FunctionAgent` under `vespertin.cerebral.work/v1alpha1`.

### Responsibilities

- Multi-agent coordination and task delegation.
- Long-running cognitive workflows and sagas.
- Memory consolidation and observation capture.
- Source ingestion from trackers (for example, Linear).
- Agent execution sandboxing with Landlock.

## Dependency Invariant

Substrate crates never depend on conscious crates.
Conscious crates depend on substrate types or declare private schemas.
This rule protects the compute layer from cognitive state changes.
"""

DOCS_TYPE_SYSTEM = """# Kubernetes Rust Type System

This guide explains how Kubernetes types work in Rust across the estate.
It describes design patterns, schema derivation, and patch semantics.

## The Dual-Plane Architecture

Rust operators in the estate interact with two distinct type layers:

1. **Upstream Schema Types** (`k8s-openapi`)
   Generated from standard Kubernetes OpenAPI specifications.
   These types represent standard resources such as `Pod`, `Service`, and `Node`.
2. **Custom Resource Types** (`kube::CustomResource`)
   Domain-specific custom resources generated via procedural derive macros.

```mermaid
flowchart TD
    subgraph DeriveFlow ["Custom Resource Derivation"]
        RUST["Rust Struct: FooSpec"] -->|kube::CustomResource| CRD["CRD Foo Definition"]
        RUST -->|schemars::JsonSchema| SCHEMA["JSON Schema v3"]
        RUST -->|serde::Serialize| JSON["JSON Payload"]
    end

    subgraph ClusterSync ["Cluster Operations"]
        JSON -->|POST / PUT| API["K8s API Server"]
        PATCH["Status Merge Patch"] -->|PATCH status| API
    end
```

## Derive Macro: `kube::CustomResource`

Custom resources use the `CustomResource` derive macro from `kube`.
This macro generates the top-level resource struct and Kubernetes metadata.

```rust
use kube::CustomResource;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

#[derive(CustomResource, Serialize, Deserialize, Clone, Debug, JsonSchema)]
#[kube(
    group = "cerebral.work",
    version = "v1alpha1",
    kind = "SagaFlow",
    namespaced,
    status = "SagaFlowStatus"
)]
#[serde(rename_all = "camelCase")]
pub struct SagaFlowSpec {
    pub description: Option<String>,
}
```

### Key Attributes

- `group`: The API group name (for example, `cerebral.work`).
- `version`: The API version string (for example, `v1alpha1`).
- `kind`: The PascalCase resource kind.
- `namespaced`: Declares that the resource lives inside namespaces.
- `status`: Connects a separate status struct to the resource.

## Zero-I/O Domain Crates and Schema Derivation

Leaf domain crates (such as `k8-api` and `cerebral-k8s-domain`) enforce zero I/O.
They avoid network calls, file systems, and heavy runtimes.

### The `k8s-openapi` Schema Footprint

Upstream `k8s-openapi` structs do not implement `schemars::JsonSchema` by default.
Enabling the `schemars` feature in `k8s-openapi` inflates build times significantly.

To avoid this cost, our domain crates apply two patterns:

1. **Lightweight Wire Types**:
   Define dedicated structs with `#[derive(JsonSchema)]` matching Kubernetes wire formats.
2. **Feature Isolation**:
   Keep `schemars` dependencies on leaf API crates.
   Never pull heavy schema compilers into runtime kernels.

## JSON Merge Patch and Null Serialization

Kubernetes updates custom resource status using JSON merge patches.
A JSON merge patch deletes a field only when the key has an explicit `null` value.

### The Stale Status Bug (EST-138)

Using `#[serde(skip_serializing_if = "Option::is_none")]` on status fields causes defects.
When a failure condition clears, Rust sets the field to `None`.
If serialized without the key, Kubernetes retains the previous error message.

```rust
// Correct status struct pattern:
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct OrchestratorStatus {
    // None serializes as null on purpose to clear prior errors
    pub healthy: Option<String>,
    pub message: Option<String>,
    pub observed_generation: Option<i64>,
    #[serde(default)]
    pub conditions: Vec<Condition>,
}
```

WARNING: Never use `skip_serializing_if = "Option::is_none"` on nullable status fields.
The field will fail to delete during status recovery.

## Status Conditions and Wire Alignment

Status conditions must match the standard Kubernetes `metav1.Condition` wire format.

### Field Rules

- Use `camelCase` field serialization with `#[serde(rename_all = "camelCase")]`.
- Format timestamps using RFC3339 strings (`lastTransitionTime`).
- Use single uppercase words for condition reasons (`Reason`).
- Express status values as strings: `"True"`, `"False"`, or `"Unknown"`.

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct Condition {
    pub type_: String,
    pub status: String,
    pub reason: String,
    pub message: Option<String>,
    pub last_transition_time: String,
    pub observed_generation: Option<i64>,
}
```

## Summary Checklist

1. Derive `CustomResource`, `Serialize`, `Deserialize`, and `JsonSchema`.
2. Apply `#[serde(rename_all = "camelCase")]` to specs and statuses.
3. Emit explicit `null` for `Option::None` in status merge patches.
4. Keep leaf domain crates free of network I/O and async runtimes.
"""

DOCS_CRD_CATALOG = """# Custom Resource Definition Catalog

This catalog documents the Custom Resource Definitions across the estate.

## Catalog Summary

| Kind | API Group | Version | Scope | Owner | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SagaFlow** | `cerebral.work` | `v1alpha1` | Namespaced | Cerebral | Multi-step agent workflow graph definition |
| **SagaRun** | `cerebral.work` | `v1alpha1` | Namespaced | Cerebral | Execution instance of a SagaFlow |
| **Orchestrator** | `vespertin.cerebral.work` | `v1alpha1` | Namespaced | Vespertin | Subsystem health probe and discovery endpoint |
| **WorkSource** | `vespertin.cerebral.work` | `v1alpha1` | Namespaced | Vespertin | External event intake (for example, Linear issues) |
| **FunctionAgent** | `vespertin.cerebral.work` | `v1alpha1` | Namespaced | Vespertin | Agent runtime execution spec and Landlock jail |
| **AgentWorkload** | `unsigned.gg` | `v1alpha1` | Namespaced | Unsigned | Low-level compute workload and hardware profile |

---

## 1. SagaFlow

- **Group**: `cerebral.work`
- **Version**: `v1alpha1`
- **Kind**: `SagaFlow`
- **Scope**: Namespaced
- **Crate**: `cerebral-k8s-api`

### Specification (`SagaFlowSpec`)

```yaml
apiVersion: cerebral.work/v1alpha1
kind: SagaFlow
metadata:
  name: codebase-ratchet
  namespace: default
spec:
  description: "Autonomous code improvement loop"
```

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `description` | `string` | No | Human-readable explanation of the workflow graph |

---

## 2. SagaRun

- **Group**: `cerebral.work`
- **Version**: `v1alpha1`
- **Kind**: `SagaRun`
- **Scope**: Namespaced
- **Crate**: `cerebral-k8s-api`

### Specification (`SagaRunSpec`)

```yaml
apiVersion: cerebral.work/v1alpha1
kind: SagaRun
metadata:
  name: codebase-ratchet-run-42
  namespace: default
spec:
  flowRef: "codebase-ratchet"
```

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `flowRef` | `string` | Yes | Name of the referenced SagaFlow resource |

---

## 3. Orchestrator

- **Group**: `vespertin.cerebral.work`
- **Version**: `v1alpha1`
- **Kind**: `Orchestrator`
- **Scope**: Namespaced
- **Crate**: `vespertin` (`operator/src/main.rs`)

### Specification (`OrchestratorSpec`)

```yaml
apiVersion: vespertin.cerebral.work/v1alpha1
kind: Orchestrator
metadata:
  name: mesh-gateway
  namespace: default
spec:
  subsystem: "mesh"
  endpoint: "https://mesh.cerebral.work"
  healthPath: "/healthz"
  probeIntervalSeconds: 60
  authSecretRef:
    name: "mesh-token"
    key: "token"
```

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `subsystem` | `string` | Required | Target subsystem name |
| `endpoint` | `string` | Required | Base URL for probes and requests |
| `healthPath` | `string` | `/healthz` | HTTP probe path |
| `probeIntervalSeconds` | `integer` | `60` | Probe polling frequency |
| `authSecretRef` | `SecretKeyRef` | `null` | Kubernetes secret reference for authentication |

### Status (`OrchestratorStatus`)

- `healthy`: String (`"True"` or `"False"`).
- `version`: Detected subsystem version string.
- `lastProbe`: RFC3339 timestamp of the last health probe.
- `message`: Diagnostic message or error description.
- `observedGeneration`: Last observed resource generation.
- `conditions`: List of standard `metav1.Condition` objects (`Ready`, `Degraded`, `Reconciling`).

---

## 4. WorkSource

- **Group**: `vespertin.cerebral.work`
- **Version**: `v1alpha1`
- **Kind**: `WorkSource`
- **Scope**: Namespaced
- **Crate**: `vespertin` (`operator/src/main.rs`)

### Specification (`WorkSourceSpec`)

```yaml
apiVersion: vespertin.cerebral.work/v1alpha1
kind: WorkSource
metadata:
  name: linear-intake
  namespace: default
spec:
  type: "linear"
  pollIntervalSeconds: 300
  linear:
    team: "CER"
    states: ["Todo", "Triage"]
    credentialSecretRef:
      name: "linear-api-key"
      key: "token"
  reflex:
    target: "dispatch-lane"
```

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `type` | `string` | Required | Work source adapter type (`linear`, `github`) |
| `pollIntervalSeconds` | `integer` | `300` | Ingestion poll frequency in seconds |
| `linear` | `LinearSource` | `null` | Linear adapter configuration |
| `reflex` | `ReflexTarget` | Required | Downstream reflex dispatch destination |

---

## 5. FunctionAgent

- **Group**: `vespertin.cerebral.work`
- **Version**: `v1alpha1`
- **Kind**: `FunctionAgent`
- **Scope**: Namespaced
- **Crate**: `vespertin` (`operator/src/main.rs`)

### Specification (`FunctionAgentSpec`)

```yaml
apiVersion: vespertin.cerebral.work/v1alpha1
kind: FunctionAgent
metadata:
  name: code-reviewer
  namespace: default
spec:
  function: "jury-review"
  timeoutSeconds: 900
```

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `function` | `string` | Required | Cognitive function name |
| `timeoutSeconds` | `integer` | `600` | Maximum execution timeout |

---

## 6. AgentWorkload

- **Group**: `unsigned.gg`
- **Version**: `v1alpha1`
- **Kind**: `AgentWorkload`
- **Scope**: Namespaced
- **Crate**: `k8-api` (`crates/k8-api/src/lib.rs`)

### Specification (`AgentWorkloadSpec`)

```yaml
apiVersion: unsigned.gg/v1alpha1
kind: AgentWorkload
metadata:
  name: inference-worker-0
  namespace: default
spec:
  image: "ghcr.io/unsigned-gg/runner:latest"
  resources:
    cpu: "2000m"
    memory: "4Gi"
    gpu: "1"
```

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `image` | `string` | Yes | Container image repository and tag |
| `resources` | `ResourceProfile` | Yes | Compute resource requests and limits |
"""


TARGETS = {
    "docs/architecture/README.md": DOCS_README,
    "docs/architecture/substrate-conscious-boundary.md": DOCS_BOUNDARY,
    "docs/architecture/kubernetes-rust-type-system.md": DOCS_TYPE_SYSTEM,
    "docs/architecture/crd-catalog.md": DOCS_CRD_CATALOG,
}


def generate(root: Path, check: bool = False) -> bool:
    """Generate or verify architecture documentation files."""
    mismatches = []
    for rel_path, content in TARGETS.items():
        dest = root / rel_path
        if check:
            if not dest.exists():
                mismatches.append(f"Missing file: {rel_path}")
            elif dest.read_text(encoding="utf-8") != content:
                mismatches.append(f"Content drift: {rel_path}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            print(f"Wrote {rel_path}")

    if check:
        if mismatches:
            print("Documentation parity check failed:")
            for m in mismatches:
                print(f"  - {m}")
            return False
        print("Documentation is up to date.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Kubernetes architecture documentation.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether documentation matches generated content without writing.",
    )
    args = parser.parse_args()
    root = get_repo_root()
    success = generate(root, check=args.check)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
