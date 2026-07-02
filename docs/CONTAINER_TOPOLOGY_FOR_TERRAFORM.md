# Container Topology For Terraform

This document summarizes what ports need to be open and which containers talk
to each other for the IntoData KB / Nexus deployment.

## Simple Diagram

```text
                           PUBLIC INTERNET
                                  |
                                  | open: 80 / 443 only
                                  v
+------------------------------------------------------------------------+
| CLIENT VPS                                                              |
|                                                                        |
|  Public / host layer                                                    |
|  +------------------+        +-----------------------------+            |
|  | nginx + certbot  |        | SSH :22                     |            |
|  | public gateway   |        | approved IPs / VPN only     |            |
|  +--------+---------+        +-----------------------------+            |
|           |                                                            |
|           | routes to private container ports                           |
|           v                                                            |
|  +------------------------------------------------------------------+  |
|  | PRIVATE DOCKER NETWORK                                            |  |
|  |                                                                  |  |
|  |  App layer                                                        |  |
|  |  +------------+     calls      +------------+                    |  |
|  |  | frontend   |--------------->| backend    |                    |  |
|  |  | :3000      |                | :8000      |                    |  |
|  |  +-----+------+                +-----+------+                    |  |
|  |        |                             |                           |  |
|  |        | calls                       | uses                      |  |
|  |        v                             v                           |  |
|  |  +------------+                +------------+   +------------+   |  |
|  |  | mcp-server |--------------->| postgres   |   | qdrant     |   |  |
|  |  | :8002      |                | :5432      |   | :6333/6334 |   |  |
|  |  +-----+------+                +------------+   +------------+   |  |
|  |        |                             ^              ^            |  |
|  |        | also uses                   |              |            |  |
|  |        +-----------------------------+              |            |  |
|  |                                      backend uses ---+            |  |
|  |                                                                  |  |
|  |  Memory / model layer                                             |  |
|  |  +------------+                +-----------------+               |  |
|  |  | hindsight  |--------------->| host Ollama     |               |  |
|  |  | :8888      |                | :11434          |               |  |
|  |  +------------+                +-----------------+               |  |
|  |        ^                             ^                           |  |
|  |        |                             |                           |  |
|  |        | backend + mcp-server use both services                  |  |
|  |                                                                  |  |
|  |  Ops layer                                                        |  |
|  |  +------------+     uses       +-----------------+               |  |
|  |  | dashboard  |--------------->| mission-control |               |  |
|  |  | :8001      |                | :9100           |               |  |
|  |  +-----+------+                +-----------------+               |  |
|  |        |                                                         |  |
|  |        +---- also calls backend and mcp-server                   |  |
|  +------------------------------------------------------------------+  |
|                                                                        |
+------------------------------------------------------------------------+
```

## Ports To Open Publicly

Only these ports should be open from the internet:

| Port | Purpose | Rule |
| --- | --- | --- |
| 80/tcp | HTTP, nginx, certbot challenge/redirect | Public |
| 443/tcp | HTTPS app traffic | Public |
| 22/tcp | SSH admin access | Restricted to approved IPs/VPN |

## Ports To Keep Private

These are application/internal ports. Do not expose them publicly.

| Port | Service |
| --- | --- |
| 3000 | `frontend` |
| 8000 | `backend` |
| 8001 | `dashboard` |
| 8002 | `mcp-server` |
| 5432 | `postgres` |
| 6333 | `qdrant` HTTP |
| 6334 | `qdrant` gRPC |
| 8888 | `hindsight` API |
| 9999 | `hindsight` internal |
| 9100 | `mission-control` |
| 11434 | host `ollama` |

## Container Communication

```text
nginx
  -> frontend:3000
  -> backend:8000
  -> mcp-server:8002

frontend
  -> backend:8000
  -> mcp-server:8002

backend
  -> postgres:5432
  -> qdrant:6333
  -> hindsight:8888
  -> host Ollama:11434

mcp-server
  -> postgres:5432
  -> backend:8000
  -> hindsight:8888
  -> host Ollama:11434

dashboard
  -> mcp-server:8002
  -> backend:8000
  -> mission-control:9100

hindsight
  -> host Ollama:11434
```

## Startup Order

```text
postgres, qdrant, hindsight, mission-control
  -> backend
  -> mcp-server
  -> frontend
  -> dashboard
```

## Terraform Summary

Terraform should create a private container cluster where nginx is the only
public entry point. Public traffic enters through `80/443`. Nginx forwards web
traffic to `frontend`, API traffic to `backend`, and webhook traffic to
`mcp-server`. All other ports should remain private or bound to localhost.
