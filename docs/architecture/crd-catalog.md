# Custom Resource Definition Catalog

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
