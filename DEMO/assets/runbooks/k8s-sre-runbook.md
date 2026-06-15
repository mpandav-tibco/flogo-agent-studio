# Kubernetes & SRE Runbook — Payments Platform

**Owner**: Platform SRE Team  
**Last reviewed**: 2025-01-10  
**Scope**: Kubernetes workloads on the Payments Platform cluster  

---

## Table of Contents

1. [CrashLoopBackOff](#1-crashloopbackoff)
2. [OOMKilled — Container Out-of-Memory](#2-oomkilled--container-out-of-memory)
3. [ImagePullBackOff / ErrImagePull](#3-imagepullbackoff--errimagepull)
4. [Readiness Probe Failing](#4-readiness-probe-failing)
5. [Pod Stuck in Pending](#5-pod-stuck-in-pending)
6. [Service Unreachable / Connection Refused](#6-service-unreachable--connection-refused)
7. [High Restart Count (not CrashLoop)](#7-high-restart-count-not-crashloop)
8. [Escalation Matrix](#8-escalation-matrix)

---

## 1. CrashLoopBackOff

**What it means**: The container keeps crashing and Kubernetes is backing off the restart timer exponentially. The application is failing to start or exits immediately after start.

**Immediate triage (do first)**:
```bash
kubectl describe pod <pod-name> -n <namespace>
# Look for: "Last State", "Exit Code", "Reason"

kubectl logs <pod-name> -n <namespace> --previous
# Shows the logs from the last crash

kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -20
```

**Common causes and fixes**:

| Exit Code | Cause | Fix |
|-----------|-------|-----|
| 1 | Application error / unhandled exception | Check `kubectl logs --previous` for stack trace |
| 137 | OOMKilled (see section 2) | Increase memory limit |
| 139 | Segmentation fault | Update image; contact service team |
| 143 | SIGTERM not handled within grace period | Tune `terminationGracePeriodSeconds` |
| 255 | Missing required env var or config | Check for missing `ConfigMap` or `Secret` references |

**Environment variable missing (very common)**:
```bash
# Confirm which env vars the pod tried to use
kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Environment:"

# Check if ConfigMap/Secret exists
kubectl get configmap <name> -n <namespace>
kubectl get secret <name> -n <namespace>
```

**Resolution checklist**:
- [ ] Exit code identified
- [ ] Stack trace from `--previous` logs reviewed
- [ ] All referenced ConfigMaps and Secrets verified to exist
- [ ] Image pinned to a specific version (never `:latest` in production)
- [ ] Resource limits are set and appropriate (see section 2)

**Escalation trigger**: If restart count > 5 within 10 minutes and root cause is not identified, escalate to on-call service team. SLA: P2 High.

---

## 2. OOMKilled — Container Out-of-Memory

**What it means**: The Linux kernel OOM killer terminated the container because it exceeded its configured memory limit. Exit code will be **137**.

**Immediate triage**:
```bash
kubectl describe pod <pod-name> -n <namespace>
# Look for: "OOMKilled" in Last State -> Reason

kubectl top pod <pod-name> -n <namespace>
# Shows current CPU/memory usage (requires metrics-server)

kubectl top pod -n <namespace> --sort-by=memory
# Shows all pods sorted by memory usage
```

**How to determine the right memory limit**:
```bash
# Watch memory over time during load
watch -n 2 kubectl top pod -n <namespace> -l app=<service>

# For Java services — check heap vs non-heap
kubectl exec -it <pod-name> -n <namespace> -- jcmd 1 GC.heap_info
```

**Short-term fix** (increase limit):
```yaml
resources:
  requests:
    memory: "256Mi"
  limits:
    memory: "512Mi"   # increase from previous value
```

**Long-term fix options**:
1. Profile heap usage and identify unbounded caches or memory leaks
2. For Java: add `-XX:MaxRAMPercentage=75.0` to align heap with container limit
3. Enable VPA (Vertical Pod Autoscaler) for automatic right-sizing

**Resolution checklist**:
- [ ] OOMKilled confirmed via `kubectl describe`
- [ ] Memory limit raised to 2× measured peak usage
- [ ] `kubectl top` confirmed new usage is within limit
- [ ] Service team notified if a memory leak is suspected

**Escalation trigger**: Two OOMKilled events within 1 hour despite increased limits → P2 High, escalate to service team.

---

## 3. ImagePullBackOff / ErrImagePull

**What it means**: Kubernetes cannot pull the container image. The pod will not start until the image is available.

**Immediate triage**:
```bash
kubectl describe pod <pod-name> -n <namespace>
# Look for: "Failed to pull image" event with error detail

kubectl get events -n <namespace> | grep -i "pull\|image"
```

**Common causes**:

| Error message | Cause | Fix |
|---------------|-------|-----|
| `repository does not exist` | Wrong image name or tag | Correct the image reference |
| `unauthorized: authentication required` | Missing or expired pull secret | Rotate `imagePullSecret` |
| `manifest unknown` | Tag does not exist in registry | Build/push the image tag |
| `i/o timeout` | Network issue reaching registry | Check cluster egress; try `kubectl run test --image=busybox` |

**Check and rotate pull secret**:
```bash
# Verify secret exists
kubectl get secret <registry-secret-name> -n <namespace>

# Recreate if expired
kubectl create secret docker-registry <name> \
  --docker-server=<registry> \
  --docker-username=<user> \
  --docker-password=<token> \
  -n <namespace>
```

**Resolution checklist**:
- [ ] Image name and tag verified in registry UI
- [ ] Pull secret exists and is not expired
- [ ] `imagePullSecrets` is referenced correctly in the pod spec
- [ ] Network connectivity to registry confirmed

---

## 4. Readiness Probe Failing

**What it means**: The application is running but not ready to serve traffic. Kubernetes removes the pod from Service endpoints until the probe passes. During a rollout this will pause the deployment.

**Immediate triage**:
```bash
kubectl describe pod <pod-name> -n <namespace>
# Look for: "Readiness probe failed" events

kubectl get endpoints <service-name> -n <namespace>
# Shows which pods are currently in the endpoint list

# Manually test the probe endpoint from inside the cluster
kubectl exec -it <any-healthy-pod> -n <namespace> -- \
  curl -s -o /dev/null -w "%{http_code}" http://<pod-ip>:8080/api/health
```

**Common causes**:
- Application startup takes longer than `initialDelaySeconds` — increase the delay
- Health endpoint returns non-2xx during DB connection setup — check DB connectivity
- Application crashed internally but the process didn't exit (zombie state) — check app logs

**Resolution checklist**:
- [ ] Probe path and port verified against the running application
- [ ] `initialDelaySeconds` is long enough for the app to start
- [ ] DB/dependency connectivity confirmed from within the pod
- [ ] Application logs checked for internal errors not causing process exit

---

## 5. Pod Stuck in Pending

**What it means**: The scheduler cannot place the pod on any node.

**Immediate triage**:
```bash
kubectl describe pod <pod-name> -n <namespace>
# Look for: "Insufficient cpu", "Insufficient memory", "no nodes available",
#           "node selector did not match", "taint not tolerated"

kubectl describe nodes | grep -A5 "Allocated resources"
# Shows actual vs allocatable resources on each node
```

**Common causes**:

| Reason | Fix |
|--------|-----|
| Insufficient CPU/memory | Scale cluster nodes or reduce resource requests |
| Node selector / affinity mismatch | Check pod's `nodeSelector` / `affinity` settings |
| Taint not tolerated | Add the required `tolerations` to the pod spec |
| PVC pending | Check `kubectl describe pvc <name>` for storage provisioner issues |

---

## 6. Service Unreachable / Connection Refused

**What it means**: Requests to the Service are failing — either the service is not routing to healthy pods or the pod's port mapping is wrong.

**Triage steps**:
```bash
# 1. Confirm pods are running and ready
kubectl get pods -n <namespace> -l app=<service> -o wide

# 2. Confirm service endpoints are populated
kubectl get endpoints <service-name> -n <namespace>
# If endpoints list is empty → readiness probe is failing

# 3. Test from inside the cluster
kubectl run curl-test --image=curlimages/curl --rm -it --restart=Never -- \
  curl -v http://<service-name>.<namespace>.svc.cluster.local

# 4. Check if the target port matches what the app listens on
kubectl describe service <service-name> -n <namespace>
```

**Resolution checklist**:
- [ ] At least one pod is in `Running` + `1/1 Ready` state
- [ ] Service `selector` matches pod labels exactly (case-sensitive)
- [ ] `targetPort` matches the actual container listening port
- [ ] NetworkPolicy (if present) allows inbound traffic from the caller's namespace

---

## 7. High Restart Count (not CrashLoop)

**What it means**: A pod has restarted more than expected but is currently running. Often indicates intermittent OOM or liveness probe killing a temporarily slow pod.

```bash
kubectl get pods -n <namespace> | sort -k4 -t' ' -rn
# Sort by restart count to find the worst offenders

kubectl describe pod <pod-name> -n <namespace>
# Look for: Last State exit code and reason
```

Treat the exit code as per section 1. If the liveness probe is killing healthy pods, tune `failureThreshold` and `periodSeconds` rather than disabling the probe.

---

## 8. Escalation Matrix

| Severity | Condition | Escalation target | Response SLA |
|----------|-----------|-------------------|--------------|
| P1 Critical | Payment processing pods all down | Payments on-call + Platform SRE | 15 min |
| P2 High | Single payment pod CrashLoop > 5 restarts | Platform SRE on-call | 30 min |
| P2 High | OOMKilled twice within 1 hour | Platform SRE + service team | 30 min |
| P3 Medium | ImagePullBackOff blocking deployment | DevOps | 2 hours |
| P3 Medium | Readiness probe blocking rollout | Service team | 2 hours |
| P4 Low | High restart count, pod currently healthy | Service team next business day | 24 hours |

---

*This runbook is maintained by the Platform SRE team. Submit corrections via PR to the `platform/runbooks` repository.*
