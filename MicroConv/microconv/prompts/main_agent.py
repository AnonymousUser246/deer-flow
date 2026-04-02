"""Prompt templates for Main Agent graph nodes."""

ANALYZE_PROMPT = """\
You are an expert software architect specialising in monolith-to-microservice migration.

Analyse the monolith project at the path below and produce a structured JSON output.

## Project path
{monolith_path}

## Instructions
1. Use the `ls` tool to explore the top-level directory structure.
2. Use `read_file` to examine key files: pom.xml (or build.gradle), application.yml/properties, \
and the main Java source directories.
3. Identify logical modules/packages that could become independent services.
4. For each candidate service determine:
   - Which classes belong to it
   - Which other services it depends on (calls)
   - Which interfaces it exposes (from @RestController / @RequestMapping)
   - Which external interfaces it calls (from RestTemplate / FeignClient / WebClient)
5. Identify shared entity classes used across multiple modules.

## Output format
Return **only** a JSON object (no markdown fences) with two top-level keys:

```
{{
  "service_topology": {{
    "services": {{
      "<service-name>": {{
        "name": "<service-name>",
        "description": "...",
        "classes": ["com.example.Foo", ...],
        "depends_on": ["other-service", ...],
        "used_interfaces": {{"other-service": ["getXxx", ...]}}
      }}
    }},
    "shared_entities": ["com.example.shared.BaseEntity", ...],
    "output_base_path": ""
  }},
  "service_interfaces": {{
    "<service-name>": [
      {{
        "name": "getOrderById",
        "type": "REST",
        "method": "GET",
        "path": "/api/orders/{{id}}",
        "request": {{"params": {{"id": "Long"}}}},
        "response": {{"body": "OrderDTO"}}
      }}
    ]
  }}
}}
```
"""

PLAN_PROMPT = """\
You are a migration planner. Based on the service topology and interface contracts below, \
produce a list of microservice specs that will be dispatched to individual sub-agents.

## Service topology
{service_topology}

## Service interfaces
{service_interfaces}

## User-specified target services (null = auto-detect all)
{target_services}

## Instructions
- If target_services is null, plan migration for every service in the topology.
- Otherwise plan only for the listed services.
- For each service output a spec object.

## Output format
Return **only** a JSON array (no markdown fences):
```
[
  {{
    "name": "order-service",
    "classes": ["com.example.order.OrderService", ...],
    "target_package": "com.example.orderservice",
    "target_path": "/mnt/user-data/workspace/output/order-service",
    "description": "..."
  }}
]
```
"""
