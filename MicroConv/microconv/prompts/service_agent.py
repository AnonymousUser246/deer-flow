"""Prompt templates for Microservice subgraph nodes."""

EXPLORE_PROMPT = """\
You are a microservice migration expert. You are extracting **{service_name}** from a monolith.

## Service dependency context
{related_service_deps}

## External interfaces I need to call
{required_interfaces}

## Interfaces I must expose
{my_exposed_interfaces}

## Classes to migrate
{classes}

## Instructions
1. Use `read_file` to read the source code of each class listed above.
2. Analyse internal dependencies among these classes (who injects / calls whom).
3. Produce a `class_dependency_graph` capturing nodes and edges.
4. Create a migration plan — an ordered list of steps. Each step is an AtomicActionSet \
with concrete file operations.

Key considerations:
- For each external service I depend on, I need a Client/Feign class. \
  Use the exact endpoint definitions from "required_interfaces".
- My Controller must implement every endpoint listed in "my_exposed_interfaces".
- Handle shared entities by creating local DTOs or referencing a shared module.

## Output format
Return **only** a JSON object (no markdown fences):
```
{{
  "class_dependency_graph": {{
    "nodes": {{
      "OrderService": {{"fqcn": "com.example.order.OrderService", "type": "service", "package": "com.example.order"}},
      ...
    }},
    "edges": [
      {{"source": "OrderService", "target": "OrderRepository", "type": "injection"}},
      ...
    ]
  }},
  "plan": {{
    "steps": [
      {{
        "transaction_id": "tx_001",
        "target": "Create order-service POM and directory structure",
        "actions": [
          {{"file": "order-service/pom.xml", "action_type": "create", "description": "..."}},
          ...
        ]
      }}
    ]
  }}
}}
```
"""

EDIT_PROMPT = """\
You are a code migration executor. Execute the migration plan step by step.

## Service: {service_name}

## Migration plan
{plan}

## External interfaces I call (use for generating Client code)
{required_interfaces}

## Internal class dependency graph
{class_dependency_graph}

## Instructions
Execute each step in order:
- For "create" actions: use `write_file` to create the file with proper content.
- For "modify" actions: use `read_file` to get current content, then `str_replace` to apply changes.
- For "move" actions: `read_file` the source, `write_file` to the destination, update package declarations and imports.

When generating Client/Feign classes for external services, ensure the URL paths, \
HTTP methods, request parameters and return types **exactly match** the definitions \
in required_interfaces.

After executing all steps, report the list of completed actions.

## Output format
Return **only** a JSON object (no markdown fences):
```
{{
  "completed_actions": [
    {{
      "transaction_id": "tx_001",
      "service_name": "{service_name}",
      "target": "...",
      "actions": [
        {{"file": "...", "action_type": "create", "description": "..."}}
      ],
      "status": "committed"
    }}
  ]
}}
```
"""

VERIFY_PROMPT = """\
Verify the migration result for **{service_name}**.

## Step 1 — Compilation
Run `bash` with:
```
cd {service_path} && mvn compile -q 2>&1
```
Record whether compilation succeeds or fails, and capture error messages.

## Step 2 — Interface consistency
Check the following exposed interfaces are all implemented in the Controller:
{my_exposed_interfaces}

Check that Client/Feign code for external calls matches these contracts:
{required_interfaces}

## Output format
Return **only** a JSON object (no markdown fences):
```
{{
  "verification_result": {{
    "compile_success": true,
    "compile_errors": [],
    "interface_issues": [],
    "verdict": "pass"
  }},
  "interface_corrections": null
}}
```

If you discover that the interface definitions from the parent are inaccurate, \
populate `interface_corrections` with corrected endpoint definitions:
```
{{
  "interface_corrections": {{
    "exposed": [
      {{"name": "...", "type": "REST", "method": "GET", "path": "...", "request": {{}}, "response": {{}}}}
    ]
  }}
}}
```
"""
