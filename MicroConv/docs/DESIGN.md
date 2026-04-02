# MicroConv: Monolith-to-Microservice Agent — Implementation Design

## 1. 概览

MicroConv 是一个基于 LangGraph 显式 StateGraph 构建的 agent harness，用于将单体应用代码自动转化为微服务架构。它复用 DeerFlow 的基础设施（LLM 工厂、Sandbox、Config），自己实现领域相关的 graph 拓扑、多层 memory 和验证逻辑。

### 1.1 核心设计原则

- **显式 graph 控制**：Main graph + Microservice subgraph 都用 `StateGraph` 手写，保证阶段顺序和回退逻辑由代码强制执行
- **Memory 分层**：Main Agent 持有宏观视图（服务拓扑 + 接口契约），Sub-agent 持有微观视图（服务内部类依赖）；dispatch 时按需过滤，只传和该服务相关的上下文
- **DeerFlow 复用不修改**：通过 `import deerflow.*` 复用基础设施，不 fork、不修改 DeerFlow 源码
- **MVP 先行**：先跑通单个微服务的端到端迁移，再扩展到并行多服务

### 1.2 Memory 分层总览

```
Main Agent（城市地图）                Sub-agent（一栋楼的施工图）
┌───────────────────────┐           ┌───────────────────────────┐
│ service_topology      │ ──过滤──▶ │ related_service_deps      │
│  服务间依赖关系       │           │  和我相关的服务依赖        │
│                       │           │                           │
│ service_interfaces    │ ──过滤──▶ │ required_interfaces       │
│  每个服务的对外接口   │           │  我要调用的外部接口        │
│                       │           │                           │
│                       │ ──过滤──▶ │ my_exposed_interfaces     │
│                       │           │  我要暴露的接口            │
│                       │           │                           │
│ edit_list             │           │ class_dependency_graph     │
│  全局编辑日志         │           │  我内部的类依赖（自己构建） │
└───────────────────────┘           └───────────────────────────┘
```

---

## 2. 项目结构

```
MicroConv/
├── pyproject.toml                  # 项目依赖（langgraph, deerflow-harness 等）
├── config.yaml                     # 复用 DeerFlow config 格式（models, sandbox, tools）
├── docs/
│   └── DESIGN.md                   # 本文档
├── microconv/
│   ├── __init__.py
│   │
│   ├── state/                      # ========== 状态定义 ==========
│   │   ├── __init__.py
│   │   ├── global_state.py         # GlobalState — Main Agent 的完整状态
│   │   └── service_state.py        # MicroserviceState — 每个微服务 subgraph 的状态
│   │
│   ├── memory/                     # ========== 多层记忆 ==========
│   │   ├── __init__.py
│   │   ├── topology.py             # ServiceTopology — 服务间依赖拓扑
│   │   ├── interfaces.py           # ServiceInterfaces — 服务接口契约
│   │   ├── edit_list.py            # EditList — 编辑操作日志
│   │   └── class_graph.py          # ClassDependencyGraph — 类级依赖图（sub-agent 用）
│   │
│   ├── graph/                      # ========== LangGraph 图定义 ==========
│   │   ├── __init__.py
│   │   ├── main_graph.py           # Main Agent graph（analyze → plan → dispatch → merge）
│   │   ├── microservice_graph.py   # Microservice subgraph（explore → edit → verify）
│   │   └── nodes/                  # 各节点的实现
│   │       ├── __init__.py
│   │       ├── analyze.py          # 单体代码分析（输出 topology + interfaces）
│   │       ├── plan.py             # 微服务分解规划
│   │       ├── explore.py          # 微服务内部探索（构建 class_dependency_graph）
│   │       ├── edit.py             # 执行代码迁移（原子操作集）
│   │       ├── verify.py           # 编译验证 + 接口一致性验证
│   │       └── merge.py            # 结果合并（edits + 接口修正 → global memory）
│   │
│   ├── tools/                      # ========== 领域工具 ==========
│   │   ├── __init__.py
│   │   ├── java_analyzer.py        # Java 源码 + POM 依赖分析
│   │   └── arch_validator.py       # 架构约束检查
│   │
│   ├── prompts/                    # ========== Prompt 模板 ==========
│   │   ├── __init__.py
│   │   ├── main_agent.py           # Main Agent 各节点的 system prompt
│   │   └── service_agent.py        # Microservice subgraph 各节点的 system prompt
│   │
│   └── config/                     # ========== 配置 ==========
│       ├── __init__.py
│       └── settings.py             # MicroConv 专有配置（在 DeerFlow config 之上）
│
├── tests/
│   ├── __init__.py
│   ├── test_state.py
│   ├── test_memory.py
│   ├── test_graph.py
│   └── test_tools.py
│
└── README.md
```

---

## 3. 状态定义

### 3.1 GlobalState（Main Agent 状态）

```python
from typing import TypedDict, Annotated
from langgraph.graph import add_messages
import operator

class GlobalState(TypedDict):
    # === LangGraph 标准字段 ===
    messages: Annotated[list, add_messages]

    # === 输入 ===
    monolith_path: str                      # 单体项目的根目录路径
    target_services: list[str] | None       # 用户指定要拆分的服务（None = 自动识别）

    # === Main Agent Memory ===
    service_topology: dict                  # 服务间依赖拓扑（ServiceTopology 序列化）
    service_interfaces: dict                # 各服务的对外接口契约（ServiceInterfaces 序列化）
    edit_list: list[dict]                   # 全局编辑操作日志

    # === 任务调度 ===
    microservice_specs: list[dict]          # plan 节点输出的微服务规格列表
    results: Annotated[list, operator.add]  # subgraph 返回的结果（通过 reducer 自动合并）

    # === 控制 ===
    phase: str                              # "analyze" | "plan" | "dispatch" | "merge" | "done"
    iteration: int                          # 当前迭代轮次（用于 multi-round dispatch）
    max_iterations: int                     # 最大迭代次数
```

**字段说明**：

| 字段 | 谁写 | 谁读 | 说明 |
|------|------|------|------|
| `monolith_path` | 用户输入 | analyze | 初始化后不变 |
| `service_topology` | analyze 初始化，merge 更新 | plan, dispatch（过滤后传给 subagent） | 服务级依赖关系，不含类级细节 |
| `service_interfaces` | analyze 初始化，merge 更新 | plan, dispatch（过滤后传给 subagent） | 每个服务的对外接口定义 |
| `edit_list` | merge 节点 append | merge（冲突检测） | 全局编辑日志，单调递增 |
| `microservice_specs` | plan 输出 | dispatch 使用 | 每轮迭代可能重新生成 |
| `results` | subgraph 通过 reducer append | merge 消费 | 每轮迭代重置 |

### 3.2 MicroserviceState（Subgraph 状态）

```python
class MicroserviceState(TypedDict):
    # === LangGraph 标准字段 ===
    messages: Annotated[list, add_messages]

    # === 身份 ===
    service_name: str                       # 微服务名称
    spec: dict                              # 该微服务的规格（要迁移哪些类、目标包名等）

    # === 从 parent 过滤后传入（只读） ===
    related_service_deps: dict              # 和我相关的服务间依赖
    required_interfaces: dict               # 我需要调用的外部服务的接口定义
    my_exposed_interfaces: dict             # 我需要对外暴露的接口定义

    # === Sub-agent 自建的内部 Memory ===
    class_dependency_graph: dict            # 服务内部类级依赖（explore 阶段构建）

    # === 阶段控制 ===
    phase: str                              # "explore" | "edit" | "verify" | "done"
    plan: dict | None                       # explore 节点输出的迁移计划
    retry_count: int                        # 当前重试次数
    max_retries: int                        # 最大重试次数（默认 3）

    # === 输出（最终返回给 parent） ===
    local_edits: list[dict]                 # 本次迁移执行的所有 AtomicActionSet
    interface_corrections: dict | None      # 对接口定义的修正建议（merge 时更新全局）
    verification_result: dict | None        # 最终验证结果
    status: str                             # "success" | "failed" | "max_retries_exceeded"
    error: str | None                       # 失败原因
```

**从 parent 传入的三个字段详解**：

`related_service_deps` — 和我相关的服务间依赖（**过滤后的**，不是全量拓扑）：
```python
{
    "my_service": "order-service",
    "i_depend_on": {                       # 我调用了哪些服务的哪些接口
        "user-service": ["getUserById", "validateUser"],
        "payment-service": ["createPayment"]
    },
    "depend_on_me": {                      # 谁调用了我的哪些接口
        "api-gateway": ["getOrderById", "createOrder"]
    }
}
```

`required_interfaces` — 我需要调用的外部服务的**具体接口定义**：
```python
{
    "user-service": [
        {
            "name": "getUserById",
            "type": "REST",
            "method": "GET",
            "path": "/api/users/{id}",
            "request": {"params": {"id": "Long"}},
            "response": {"body": "UserDTO", "fields": {"id": "Long", "name": "String", "email": "String"}}
        }
    ],
    "payment-service": [
        {
            "name": "createPayment",
            "type": "REST",
            "method": "POST",
            "path": "/api/payments",
            "request": {"body": "PaymentRequest", "fields": {"orderId": "Long", "amount": "BigDecimal"}},
            "response": {"body": "PaymentResult"}
        }
    ]
}
```

`my_exposed_interfaces` — 我需要对外暴露的接口定义（其他服务依赖我的这些接口）：
```python
{
    "exposed": [
        {
            "name": "getOrderById",
            "type": "REST",
            "method": "GET",
            "path": "/api/orders/{id}",
            "request": {"params": {"id": "Long"}},
            "response": {"body": "OrderDTO"}
        },
        {
            "name": "createOrder",
            "type": "REST",
            "method": "POST",
            "path": "/api/orders",
            "request": {"body": "CreateOrderRequest"},
            "response": {"body": "OrderDTO"}
        }
    ]
}
```

**Sub-agent 自建的 `class_dependency_graph`**（explore 阶段构建）：
```python
{
    "nodes": {
        "OrderService": {"type": "service", "package": "com.example.order"},
        "OrderRepository": {"type": "repository", "package": "com.example.order"},
        "UserClient": {"type": "client", "package": "com.example.order.client",
                        "note": "调用外部 user-service"},
        "Order": {"type": "entity", "package": "com.example.order.model"}
    },
    "edges": [
        {"source": "OrderService", "target": "OrderRepository", "type": "injection"},
        {"source": "OrderService", "target": "UserClient", "type": "method_call"},
        {"source": "OrderRepository", "target": "Order", "type": "generic_param"}
    ]
}
```

---

## 4. 多层 Memory 详细设计

### 4.1 ServiceTopology（服务间依赖拓扑）— Main Agent 持有

```python
from dataclasses import dataclass, field

@dataclass
class ServiceInfo:
    """一个微服务的宏观信息"""
    name: str                               # 服务名
    description: str                        # 功能描述
    classes: list[str]                      # 包含的类列表（FQCN）
    depends_on: list[str]                   # 依赖的其他服务名列表
    used_interfaces: dict[str, list[str]]   # {dep_service: [interface_names]} 我调了对方哪些接口

@dataclass
class ServiceTopology:
    """服务间依赖拓扑 — Main Agent 的宏观视图"""
    services: dict[str, ServiceInfo] = field(default_factory=dict)
    shared_entities: list[str] = field(default_factory=list)
    output_base_path: str = ""

    def get_dependents(self, service_name: str) -> dict[str, list[str]]:
        """返回依赖 service_name 的所有服务及其调用的接口"""
        result = {}
        for svc_name, svc_info in self.services.items():
            if service_name in svc_info.depends_on:
                result[svc_name] = svc_info.used_interfaces.get(service_name, [])
        return result

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ServiceTopology": ...
```

**谁读谁写**：
- **写入**：`analyze` 节点初始化，`merge` 节点在多轮迭代时更新
- **读取**：`plan` 节点（规划拆分），`dispatch` 节点（过滤后传给 subagent）

### 4.2 ServiceInterfaces（服务接口契约）— Main Agent 持有

```python
@dataclass
class InterfaceEndpoint:
    """一个服务对外暴露的接口端点"""
    name: str                               # 接口名（如 getUserById）
    type: str                               # "REST" | "RPC" | "MQ"
    method: str = ""                        # HTTP method（REST 时）
    path: str = ""                          # URL 路径（REST 时）
    request: dict = field(default_factory=dict)   # 请求格式
    response: dict = field(default_factory=dict)  # 响应格式

@dataclass
class ServiceInterfaces:
    """所有服务的对外接口契约"""
    interfaces: dict[str, list[InterfaceEndpoint]] = field(default_factory=dict)
    # key = service_name, value = 该服务暴露的接口列表

    def get_interfaces_for(self, service_name: str) -> list[InterfaceEndpoint]:
        return self.interfaces.get(service_name, [])

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ServiceInterfaces": ...
```

**谁读谁写**：
- **写入**：`analyze` 节点初始化（从 Controller/RestTemplate 推断），`merge` 节点根据 subagent 的 `interface_corrections` 更新
- **读取**：`dispatch` 节点（按服务过滤后传给 subagent）

### 4.3 EditList（编辑操作日志）— Main Agent 持有

```python
@dataclass
class FileAction:
    """单个文件操作"""
    file: str                               # 文件路径
    action_type: str                        # "create" | "modify" | "delete" | "move"
    description: str                        # 人类可读描述

@dataclass
class AtomicActionSet:
    """一组原子操作（事务）"""
    transaction_id: str                     # 唯一 ID
    service_name: str                       # 所属微服务
    target: str                             # 人类可读的目标
    actions: list[FileAction] = field(default_factory=list)
    status: str = "committed"               # "committed" | "rolled_back"

@dataclass
class EditList:
    """编辑操作日志"""
    entries: list[AtomicActionSet] = field(default_factory=list)

    def append(self, action_set: AtomicActionSet) -> None: ...
    def get_by_service(self, service_name: str) -> list[AtomicActionSet]: ...
    def to_list(self) -> list[dict]: ...
    @classmethod
    def from_list(cls, data: list[dict]) -> "EditList": ...
```

**谁读谁写**：
- **写入**：`merge` 节点（汇总 subagent 的 `local_edits` 后 append）
- **读取**：`merge` 节点（冲突检测），用户查看进度
- Sub-agent **不直接访问**全局 EditList

### 4.4 ClassDependencyGraph（类级依赖图）— Sub-agent 自建

```python
@dataclass
class ClassNode:
    """一个 Java 类的元信息"""
    fqcn: str                               # 全限定类名
    type: str                               # "service" | "repository" | "entity" | "client" | "config" | "util"
    package: str                            # 所属包
    note: str = ""                          # 补充说明（如 "调用外部 user-service"）

@dataclass
class ClassEdge:
    """两个类之间的依赖关系"""
    source: str                             # 调用方
    target: str                             # 被调用方
    type: str                               # "injection" | "method_call" | "inheritance" | "generic_param"

@dataclass
class ClassDependencyGraph:
    """服务内部的类级依赖图"""
    nodes: dict[str, ClassNode] = field(default_factory=dict)
    edges: list[ClassEdge] = field(default_factory=list)

    def get_external_clients(self) -> list[ClassNode]:
        """返回所有调用外部服务的 client 类"""
        return [n for n in self.nodes.values() if n.type == "client"]

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ClassDependencyGraph": ...
```

**谁读谁写**：
- **写入**：Sub-agent 的 `explore` 节点（通过 read_file + LLM 分析源码构建）
- **读取**：Sub-agent 的 `edit` 节点（决定迁移顺序和依赖处理方式）、`verify` 节点（检查内部一致性）
- Main Agent **不持有也不读取** class 级依赖图

---

## 5. Graph 拓扑

### 5.1 Main Graph

```
┌─────────┐     ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│  START   │────▶│   analyze    │────▶│      plan        │────▶│   dispatch   │
│          │     │  monolith    │     │  decomposition   │     │  (Send × N)  │
└─────────┘     └──────────────┘     └──────────────────┘     └──────┬───────┘
                                                                      │
                                                                      │ N 个 subgraph
                                                                      │ 并行执行
                                                                      ▼
                ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
                │     END      │◀────│  check_complete  │◀────│    merge     │
                │              │     │  (还有未处理的?)   │     │   results    │
                └──────────────┘     └────────┬─────────┘     └──────────────┘
                                              │
                                              │ 有 → 下一轮迭代
                                              ▼
                                       回到 plan
```

**节点职责**：

| 节点 | 输入 | 输出 | LLM? |
|------|------|------|------|
| `analyze` | `monolith_path` | `service_topology`, `service_interfaces` | 是 |
| `plan` | `service_topology`, `service_interfaces`, `target_services` | `microservice_specs` | 是 |
| `dispatch` | `microservice_specs`, `service_topology`, `service_interfaces` | 触发 N 个 `Send()`（传过滤后的上下文） | 否 |
| `merge` | `results` | 更新 `edit_list`, `service_topology`, `service_interfaces` | 否 |
| `check_complete` | `results`, `iteration` | 路由到 END 或 plan | 否 |

**条件边**：
- `check_complete`:
  - 所有 subgraph 都成功 → `END`
  - 有失败的 + 未超过 max_iterations → `plan`（重新规划失败的部分）
  - 超过 max_iterations → `END`（部分完成）

### 5.2 Microservice Subgraph

```
┌─────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  START   │────▶│   explore    │────▶│    edit       │────▶│   verify     │
│          │     │  & plan      │     │  (execute     │     │  (compile +  │
│          │     │              │     │   actions)    │     │   interface  │
│          │     │  构建 class  │     │              │     │   check)    │
│          │     │  dep graph   │     │              │     │              │
└─────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                       ▲                                          │
                       │              ┌───────────────────────────┤
                       │              │                           │
                       │         fail_arch                   pass │
                       │         (retry_count                     │
                       │          < max?)                         ▼
                       │              │                    ┌──────────────┐
                       └──────────────┘                    │     END      │
                                                           │  (return     │
                       fail_compile ──▶ edit (直接重试编辑)  │   results)   │
                                                           └──────────────┘
```

**节点职责**：

| 节点 | 输入 | 输出 | LLM? |
|------|------|------|------|
| `explore` | `spec`, `related_service_deps`, `required_interfaces`, `my_exposed_interfaces` | `class_dependency_graph`, `plan` | 是 |
| `edit` | `plan`, `class_dependency_graph`, `required_interfaces`（生成 client 代码时需要接口格式） | `local_edits` | 是 |
| `verify` | `service_name`, `my_exposed_interfaces`（验证是否都实现了）, `required_interfaces`（验证调用参数匹配） | `verification_result`, `interface_corrections` | 部分 |

**条件边**：
- `verify` 之后：
  - `pass` → `END`（`status = "success"`）
  - `fail_compile` + `retry_count < max_retries` → `edit`（`retry_count += 1`）
  - `fail_arch` + `retry_count < max_retries` → `explore`（`retry_count += 1`）
  - 超过 max_retries → `END`（`status = "max_retries_exceeded"`）

---

## 6. Memory 读写流与共享机制

### 6.1 完整数据流

```
                         Main Agent (GlobalState)
                    ┌─────────────────────────────────────────┐
                    │                                         │
                    │  service_topology  ←── analyze 写入      │
                    │       │               merge 更新        │
                    │       │                                 │
                    │       ▼ 按服务过滤                       │
     dispatch ─────►│  related_service_deps (只传相关的)       │
                    │                                         │
                    │  service_interfaces  ←── analyze 写入    │
                    │       │                  merge 更新      │
                    │       │                                 │
                    │       ▼ 按服务过滤                       │
     dispatch ─────►│  required_interfaces (只传我要调的)      │
                    │  my_exposed_interfaces (只传我要暴露的)   │
                    │                                         │
                    │  edit_list ←── merge 节点 append          │
                    │     (subagent 不访问全局 edit_list)       │
                    │                                         │
                    │  results ←── subgraph 返回值              │
                    │     (自动通过 operator.add reducer)       │
                    └─────────────────────────────────────────┘

                         Subagent (MicroserviceState)
                    ┌─────────────────────────────────────────┐
                    │                                         │
                    │  related_service_deps       [只读,过滤后] │
                    │  required_interfaces        [只读,过滤后] │
                    │  my_exposed_interfaces      [只读,过滤后] │
                    │                                         │
                    │  class_dependency_graph ←── explore 构建  │
                    │     (服务内部类依赖，sub-agent 独有)       │
                    │                                         │
                    │  local_edits ←── edit 节点 append         │
                    │  interface_corrections ←── verify 输出    │
                    │  verification_result ←── verify 写入      │
                    │                                         │
                    │  返回值 = {                               │
                    │    service_name,                         │
                    │    local_edits,                          │
                    │    interface_corrections,                │
                    │    verification_result,                  │
                    │    status, error                         │
                    │  }                                       │
                    └─────────────────────────────────────────┘
```

### 6.2 Memory 共享总结

| Memory 层 | 所有者 | Main Agent | Sub-agent | 同步机制 |
|-----------|--------|-----------|----------|---------|
| **ServiceTopology** | Main | 读写 | 只读（过滤后的 `related_service_deps`） | dispatch 时按服务过滤传入 |
| **ServiceInterfaces** | Main | 读写 | 只读（过滤后的 `required_interfaces` + `my_exposed_interfaces`） | dispatch 时按服务过滤传入 |
| **EditList** | Main | 读写 | 不访问 | sub-agent 写 `local_edits`，merge 汇总 |
| **ClassDependencyGraph** | Sub-agent | 不持有 | 读写（explore 构建，edit/verify 读取） | 不回传给 Main（Main 不需要类级细节） |

### 6.3 Dispatch 时的过滤逻辑

```python
def dispatch(state: GlobalState) -> list[Send]:
    topology = ServiceTopology.from_dict(state["service_topology"])
    interfaces = ServiceInterfaces.from_dict(state["service_interfaces"])

    sends = []
    for spec in state["microservice_specs"]:
        svc = spec["name"]
        svc_info = topology.services.get(svc)

        # 1. 过滤服务依赖：只传和该服务相关的
        i_depend_on = svc_info.used_interfaces if svc_info else {}
        depend_on_me = topology.get_dependents(svc)

        # 2. 过滤接口：只传该服务需要调用的外部接口定义
        required = {}
        for dep_svc in (svc_info.depends_on if svc_info else []):
            required[dep_svc] = [ep.to_dict() for ep in interfaces.get_interfaces_for(dep_svc)]

        # 3. 过滤接口：只传该服务需要暴露的接口定义
        my_exposed = {
            "exposed": [ep.to_dict() for ep in interfaces.get_interfaces_for(svc)]
        }

        sends.append(Send("process_microservice", {
            "messages": [],
            "service_name": svc,
            "spec": spec,
            "related_service_deps": {
                "my_service": svc,
                "i_depend_on": i_depend_on,
                "depend_on_me": depend_on_me,
            },
            "required_interfaces": required,
            "my_exposed_interfaces": my_exposed,
            "class_dependency_graph": {},    # explore 阶段自己构建
            "phase": "explore",
            "plan": None,
            "retry_count": 0,
            "max_retries": 3,
            "local_edits": [],
            "interface_corrections": None,
            "verification_result": None,
            "status": "running",
            "error": None,
        }))
    return sends
```

### 6.4 多轮迭代时的 Memory 更新

1. `merge` 汇总第一批成功的结果：
   - `local_edits` → 追加到全局 `edit_list`
   - `interface_corrections` → 更新全局 `service_interfaces`（下一批 subagent 能拿到修正后的接口定义）
   - 更新 `service_topology` 中完成的服务状态
2. `check_complete` 发现有失败的 → 路由到 `plan`
3. `plan` 基于更新后的 topology + interfaces 重新规划
4. `dispatch` 使用最新的 memory 做新一轮过滤

---

## 7. 各节点实现规格

### 7.1 analyze（单体分析）

**职责**：扫描单体代码，构建 ServiceTopology 和 ServiceInterfaces。

**实现**（MVP）：
1. `ls` 获取项目目录结构
2. `read_file` 读取 `pom.xml`、Controller 类、Service 类、`application.yml`
3. LLM 分析并输出：
   - 哪些模块可以成为独立服务
   - 服务间的调用关系（谁调谁）
   - 每个服务对外暴露的接口（从 Controller 的 `@RequestMapping` 推断）
   - 每个服务调用外部的接口（从 `RestTemplate`/`FeignClient` 推断）

**输入**：`monolith_path`
**输出**：`service_topology`, `service_interfaces`, `phase = "plan"`

### 7.2 plan（微服务分解规划）

**职责**：基于拓扑和接口信息，决定每个微服务包含哪些类。

**输入**：`service_topology`, `service_interfaces`, `target_services`
**输出**：`microservice_specs`, `phase = "dispatch"`

**microservice_spec 格式**：
```python
{
    "name": "order-service",
    "classes": ["com.example.order.OrderService", "com.example.order.OrderRepository", ...],
    "target_package": "com.example.orderservice",
    "target_path": "/output/order-service",
    "description": "处理订单相关业务逻辑"
}
```

### 7.3 explore（微服务内探索）

**职责**：分析该服务内部的类结构，构建 `class_dependency_graph`，生成迁移计划。

**实现**：
1. 从 `spec.classes` 知道要迁移哪些类
2. 从 `related_service_deps` 知道我和哪些服务有关系
3. 从 `required_interfaces` 知道我要调的接口长什么样 → 需要生成对应的 Client 类
4. 从 `my_exposed_interfaces` 知道我要暴露的接口 → Controller 必须实现这些端点
5. `read_file` 读源码 → LLM 构建 `class_dependency_graph`
6. LLM 生成迁移计划

**输入**：`spec`, `related_service_deps`, `required_interfaces`, `my_exposed_interfaces`
**输出**：`class_dependency_graph`, `plan`, `phase = "edit"`

### 7.4 edit（执行代码迁移）

**职责**：按 plan 逐步执行文件操作。

**实现**：
1. 遍历 plan 中的每个 step，调用 sandbox tools
2. 生成调用外部服务的 Client 代码时，参考 `required_interfaces` 获取准确的 URL、参数、返回类型
3. 每完成一个 step，append 到 `local_edits`

**输入**：`plan`, `class_dependency_graph`, `required_interfaces`
**输出**：`local_edits`, `phase = "verify"`

### 7.5 verify（验证）

**编译验证**：
```bash
cd {service_path} && mvn compile -q
```

**接口一致性验证**：
- 检查 `my_exposed_interfaces` 中定义的每个接口是否都在 Controller 中实现了
- 检查调用外部服务的 Client 代码的参数/返回类型是否和 `required_interfaces` 一致
- 如果发现接口定义有误，输出 `interface_corrections`

**输入**：`service_name`, `my_exposed_interfaces`, `required_interfaces`
**输出**：`verification_result`, `interface_corrections`, `phase`, `status`

**verification_result 格式**：
```python
{
    "compile_success": True | False,
    "compile_errors": [...],
    "interface_issues": [
        {"type": "missing_endpoint", "interface": "getOrderById", "detail": "Controller 未实现"},
        {"type": "param_mismatch", "interface": "createPayment", "detail": "请求体字段不匹配"}
    ],
    "verdict": "pass" | "fail_compile" | "fail_arch"
}
```

### 7.6 merge（结果合并）

**职责**：收集 subgraph 结果，更新全局 memory。

**实现**：
1. 遍历 `results`
2. 对成功的 subagent：
   - `local_edits` → append 到全局 `edit_list`
   - `interface_corrections` → 更新全局 `service_interfaces`（修正接口定义）
   - 更新 `service_topology` 中该服务的状态
3. 对失败的 subagent：记录错误，标记需要重试

**输入**：`results`, `edit_list`, `service_topology`, `service_interfaces`
**输出**：更新后的 `edit_list`, `service_topology`, `service_interfaces`

---

## 8. DeerFlow 复用清单

### 8.1 直接复用（import 即用）

```python
from deerflow.models import create_chat_model           # LLM 调用
from deerflow.sandbox.tools import bash_tool, read_file_tool, write_file_tool, str_replace_tool, ls_tool
from deerflow.config import get_app_config               # 配置系统
```

### 8.2 参考但不直接复用

| DeerFlow 模块 | 参考什么 | 自己实现什么 |
|--------------|---------|------------|
| `agents/memory/storage.py` | `MemoryStorage` 抽象类模式 | memory 的 `to_dict()` / `from_dict()` 序列化 |
| `subagents/config.py` | `SubagentConfig` 数据结构 | `microservice_spec` 数据结构 |
| `skills/types.py` | `Skill` dataclass 设计 | memory 的 dataclass 设计 |

### 8.3 不复用

| DeerFlow 模块 | 原因 |
|--------------|------|
| `agents/lead_agent/` | 用自己的 StateGraph |
| `agents/middlewares/` | 显式 graph 不需要 middleware 链 |
| `subagents/executor.py` | 用 LangGraph `Send()` 替代 |
| `tools/builtins/task_tool.py` | 用 graph 拓扑替代 |
| 前端 / Gateway | MVP 不需要 UI |

---

## 9. Prompt 设计

### 9.1 analyze 节点

```
你是一个代码架构分析专家。请分析以下单体应用的代码结构。

项目路径: {monolith_path}

请你：
1. 使用 ls 和 read_file 工具扫描项目结构
2. 识别主要模块/包及其职责
3. 分析模块间的调用关系（哪个模块调用了哪个模块的什么接口）
4. 从 Controller 的 @RequestMapping 推断每个模块对外暴露的接口
5. 从 RestTemplate/FeignClient 推断每个模块调用的外部接口

输出 JSON：
- service_topology: 服务间依赖关系
- service_interfaces: 每个服务的接口定义（name, type, method, path, request, response）
```

### 9.2 explore 节点

```
你是一个微服务迁移专家。你正在将 {service_name} 从单体应用中提取为独立微服务。

## 我和其他服务的关系
{related_service_deps}

## 我需要调用的外部接口
{required_interfaces}

## 我需要对外暴露的接口
{my_exposed_interfaces}

## 要迁移的类
{spec.classes}

请你：
1. 读取每个类的源码（使用 read_file）
2. 分析这些类的内部依赖关系，输出 class_dependency_graph
3. 制定迁移计划，注意：
   - 需要为调用外部服务的接口生成 Client 类（参考 required_interfaces 的格式）
   - 需要确保 Controller 实现了 my_exposed_interfaces 中定义的所有端点
   - 需要处理共享实体类的引用

输出 JSON：
- class_dependency_graph: 类级依赖图
- plan: 迁移步骤列表
```

### 9.3 edit 节点

```
你是一个代码迁移执行者。请按照以下计划逐步执行文件操作。

## 迁移计划
{plan}

## 我需要调用的外部接口（用于生成 Client 代码）
{required_interfaces}

## 服务内部类依赖
{class_dependency_graph}

请严格按顺序执行每个 step。生成调用外部服务的 Client 类时，
请确保 URL、HTTP method、请求参数和返回类型与 required_interfaces 中的定义完全一致。
```

### 9.4 verify 节点

```
验证 {service_name} 微服务的迁移结果。

## 第一步：编译验证
执行 mvn compile -q，检查编译是否通过。

## 第二步：接口一致性验证
检查以下接口是否都在 Controller 中正确实现：
{my_exposed_interfaces}

检查调用外部服务的 Client 代码是否与以下接口定义匹配：
{required_interfaces}

输出 JSON verification_result。如果发现接口定义有误，
同时输出 interface_corrections 建议修正。
```

---

## 10. MVP 范围

### 10.1 包含

- [x] GlobalState + MicroserviceState（新 memory 分层）
- [x] ServiceTopology + ServiceInterfaces + EditList + ClassDependencyGraph
- [x] Main graph（analyze → plan → dispatch → merge → check_complete）
- [x] Microservice subgraph（explore → edit → verify + retry）
- [x] dispatch 时的服务级过滤逻辑
- [x] 各节点的 LLM prompt + tool 调用
- [x] 单个微服务的端到端迁移（先不做并行）
- [x] 编译验证 + 接口一致性验证

### 10.2 不包含（Phase 2+）

- [ ] 多服务并行 `Send()`
- [ ] AST 级别的依赖分析
- [ ] 事务回滚
- [ ] Memory 持久化到数据库
- [ ] 循环依赖检测
- [ ] 前端 UI
- [ ] 多轮迭代重试
