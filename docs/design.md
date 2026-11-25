# Production design

## Page 1 · GCP deployment and objectives

Deploy one regional Cloud Run entry point with separate tenant workloads and identities behind corporate OIDC. Derive tenant and rep from validated claims and member access from the rep workflow. Route to private tenant APIs with distinct credentials. Keep a PostgreSQL database and role per tenant in regional HA Cloud SQL. Run the container with warm capacity and tune concurrency with a 250-rep load test. Use Vertex AI through the classifier adapter after the provider and endpoint are approved for the data projection and selected region. Use regional Secret Manager and restricted egress. Store structured audit records in tenant-specific retention-locked Cloud Storage with six-year retention. Keep operational metrics separate from audit records.

Measure monthly successful workflow availability against 99.99%, about 4.3 minutes of monthly downtime in 30 days. Alert on error-budget burn, p95 by workflow, unresolved writes, and dependency saturation. Model, upstream, and regional failures remain part of the availability risk. Validate zonal failover and backup restore before launch. A regional outage can exhaust the error budget because year-one hosting is confined to one region.

A cold claims call takes about 1.5 seconds and a second serial page exhausts the three-second target before model overhead. A quota response can add a one-second wait. Use a freshness-bounded claims read model populated from authorized change events for indexed status, date, and aggregate reads. Require a freshness SLA and show the snapshot time. Outside the freshness window, use a budgeted live read or report unavailable. Measure degraded outcomes separately under peak load. Compare warm and cold distributions before claiming target compliance.

At 20,000 conversations/day the spending ceiling is $600/day. Measure input/output token use, database and container usage, network and audit storage, and warm capacity. Price measured workloads in the deployment region and load-test Monday demand separately from average spend. The local report measures the slice and does not establish production cost or SLO compliance.

## Page 2 · Threat model and operations

| Boundary or failure | Control and verification |
|---|---|
| Malicious rep input or forged tenant/member scope | Bind identity and selected member server-side. Check claim ownership and member access before rendering. Test cross-tenant reads, writes, state replay, and cache isolation. |
| Broken tenant authorization in the supplied mock | Gateway chooses both route and credential. Use a private test network. Require a downstream scope check in production as a second boundary. |
| Injection from policy documents, tool metadata, and tool responses | Accept only reviewed tools and approved fields. Validate document provenance and metadata before indexing. Omit raw claim notes and directory descriptions. Keep model output as a typed intent with no authority over destinations or writes. |
| PHI exfiltration through directory inputs, model, or observability | Directory sends normalized specialty, network, and ZIP only. Verify SSN suffix in the trusted gateway. Redact audit data at emission and suppress raw transcripts, headers, and upstream response bodies. Encrypt protected state and audit storage and test retention and access controls. |
| Duplicate or lost filing after ambiguous acknowledgment | Persist intent and immutable payload before dispatch. Use the same key during bounded retry. Require production downstream lookup and durable deduplication. Resume unresolved intents only once deduplication is verified. |
| Malicious continuations, upstream outages, and cost abuse | Fixed destinations and credentials, disabled redirects, validated pagination, request budgets, timeout handling, and tenant-level quotas. Alert on degraded answers and drift in usage. |

Roll out through shadow reads, then a rep pilot with supervised writes. Review audit completeness, data access, accuracy, latency, and pending write age before expanding. Roll back by disabling new writes and reconciling in-flight intents before changing the service revision.

## Page 3 · Architecture decision records

| ADR | Context and decision | Alternatives and consequences |
|---|---|---|
| 1. Tenant boundary | Reps serve distinct health plans and the mock is permissive. Partition retrieval, credentials, and production state. Bind the tenant in code at entry and at every gateway operation. | A shared tenant-keyed database lowers provisioning work but enlarges the impact of query mistakes. Dedicated tenant resources increase provisioning and monitoring effort. |
| 2. Orchestration and retrieval | Data is small and structured. Use BM25 over scoped documents, a bounded LangGraph workflow, optional model classification, and deterministic calculations and replies. | An open-ended tool agent can handle more paraphrases but increases leakage risk and latency. Semantic retrieval could improve recall and adds an embedding service and another data boundary. |
| 3. Confirmed writes | A grievance can be committed before a 503 response. Persist a single immutable intent after explicit confirmation and reuse the same request bytes and key. | Client-side request retries alone can duplicate writes. An outbox with durable downstream lookup is preferred for production recovery and adds operational state. |
| 4. Claims SLO and regional availability | Use regional managed services and zonal failover within the approved region. Introduce a tenant-scoped claims projection only with an upstream freshness contract. | Multi-region failover improves disaster resilience and requires year-one cost approval. Live-only pagination is simpler but leaves a measured claims latency gap. |

## Page 4 · Azure mapping for Prairie Sky

| GCP | Azure |
|---|---|
| Regional Cloud Run | Azure Container Apps in a zone-redundant environment, with warm replicas |
| Regional HA Cloud SQL for PostgreSQL | Azure Database for PostgreSQL Flexible Server with zone-redundant HA and private access |
| Vertex AI | Azure OpenAI deployment through the classifier adapter |
| Workload identity and regional Secret Manager | Managed identities and Azure Key Vault |
| Regional audit bucket and metrics | Immutable Azure Blob Storage and Azure Monitor |
| Container registry and private connectivity | Azure Container Registry, VNet integration, and private endpoints |

Provision in the customer's approved Azure tenancy and region. Deploy the same container and identity claims mapping, then validate tenant-only KB and credentials. Export tenant state through a verified, encrypted migration. Validate row counts and hashes, run read shadowing against Prairie Sky, then drain writes and reconcile pending operation IDs. Enable one Azure writer at cutover. Roll back by stopping Azure writes, reconciling the operation ledger, and restoring the GCP writer only after deduplication is verified. Maintain access to the six-year audit trail through the migration. Validate quotas, compliance approvals, residency, and model behavior before enabling traffic.
