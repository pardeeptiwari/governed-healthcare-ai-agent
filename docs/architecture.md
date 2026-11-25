# Architecture diagram

```mermaid
flowchart LR
    Rep[Member-services rep] --> Identity[Identity and authorized member selection]
    Identity --> API[Conversation API]
    API --> Graph[Bounded LangGraph workflow]
    Graph --> Model[Optional intent classifier]
    Graph --> Policy[Tool gateway and payload validation]
    Policy --> KB[Tenant-scoped policy index]
    Policy --> APIs[Scoped member, claims, and grievance APIs]
    Policy --> Directory[Provider directory]
    APIs -. production claim updates .-> Projection[Freshness-bounded claims read model]
    Projection -. authorized reads .-> Policy
    Graph --> Approval[Match explicit rep approval to draft]
    Approval --> State[Tenant state and durable write intent]
    State -. approved intent only .-> Policy
    Policy --> Audit[Tenant audit archive]
    Graph --> Audit
```
