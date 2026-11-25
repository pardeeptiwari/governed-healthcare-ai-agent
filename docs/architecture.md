# Proposed production architecture

```mermaid
flowchart LR
    Rep[Member-services rep] --> Identity[Corporate OIDC and member entitlements]
    subgraph Region[Single GCP region]
      Entry[Regional Cloud Run ingress] --> A[Plan A Cloud Run workload]
      Entry --> B[Plan B Cloud Run workload]
      A --> DB[Regional HA Cloud SQL with database and role per tenant]
      B --> DB
      A --> Model[Vertex AI classifier]
      B --> Model
      A --> Secrets[Secret Manager with tenant-scoped access]
      B --> Secrets
      A --> Audit[Cloud Storage with retention-locked bucket per tenant]
      B --> Audit
      A --> Monitor[Cloud Monitoring for workflow SLOs]
      B --> Monitor
    end
    Identity --> Entry
    A --> ApiA[Plan A authorized APIs]
    B --> ApiB[Plan B authorized APIs]
    ApiA -. authorized claim changes .-> DB
    ApiB -. authorized claim changes .-> DB
    A --> Vendor[Allowlisted directory]
    B --> Vendor
```

Each workload contains the workflow, tenant-scoped knowledge index, tool gateway and approval checks. The database stores approved filing intents and recovery state. The grievance systems of record must provide durable operation lookup and deduplication. The claims projection is conditional on an authorized feed and freshness contract.
