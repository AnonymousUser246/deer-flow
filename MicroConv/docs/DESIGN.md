# MicroConv: Monolith-to-Microservice Agent — Implementation Design

## 1. 概览

MicroConv 是一个基于 LangGraph 显式 StateGraph 构建的 agent harness，用于将单体应用代码自动转化为微服务架构。它复用 DeerFlow 的基础设施（LLM 工厂、Sandbox、Config），自己实现领域相关的 graph 拓扑、多层 memory 和验证逻辑。

### 1.1 核心设计原则

- **显式 graph 控制**：Main graph + Microservice subgraph 都用 `StateGraph` 手写，保证阶段顺序和回退逻辑由代码强制执行
- **Snapshot 隔离**：Subagent 拿到 memory 的只读快照，避免并行写冲突；结果通过 reducer 合并回主 state
- **DeerFlow 复用不修改**：通过 `import deerflow.*` 复用基础设施，不 fork、不修改 DeerFlow 源码
- **MVP 先行**：先跑通单个微服务的端到端迁移，再扩展到并行多服务

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
│   ├── memory/                     # ========== 三层记忆 ==========
│   │   ├── __init__.py
│   │   ├── structural.py           # StructuralMemory — 全局系统结构
│   │   ├── edit_list.py            # EditList — 编辑操作日志
│   │   └── dependency.py           # ClassDependencyGraph — 类级依赖图
│   │
│   ├── graph/                      # ========== LangGraph 图定义 ==========
│   │   ├── __init__.py
│   │   ├── main_graph.py           # Main Agent graph（analyze → plan → dispatch → merge）
│   │   ├── microservice_graph.py   # Microservice subgraph（explore → edit → verify）
│   │   └── nodes/                  # 各节点的实现
│   │       ├── __init__.py
│   │       ├── analyze.py          # 单体代码分析
│   │       ├── plan.py             # 微服务分解规划
│   │       ├── explore.py          # 微服务内部探索和迁移规划
│   │       ├── edit.py             # 执行代码迁移（原子操作集）
│   │       ├── verify.py           # 编译验证 + 架构验证
│   │       └── merge.py            # 结果合并（edits → global memory）
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

    # === 三层 Memory ===
    structural_memory: dict                 # StructuralMemory 的序列化 dict
    edit_list: list[dict]                   # EditList: 所有已提交的 AtomicActionSet
    class_dependencies: dict                # ClassDependencyGraph 的序列化 dict

    # === 任务调度 ===
    microservice_specs: list[dict]          # plan 节点输出的微服务规格列表
    results: Annotated[list, operator.add]  # subgraph 返回的结果（通过 reducer 自动合并）

    # === 控制 ===
    phase: str                              # "analyze" | "plan" | "dispatch" | "merge" | "done"
    iteration: int                          # 当前迭代轮次（用于 multi-round dispatch）
    max_iterations: int                     # 最大迭代次数
```

**字段说明**：

| 字段 | 谁写 | 谁读 | 生命周期 |
|------|------|------|---------|
| `messages` | 所有节点（LLM 对话） | 所有节点 | 全程 |
| `monolith_path` | 用户输入 | analyze 节点 | 初始化后不变 |
| `structural_memory` | analyze 节点初始化，merge 节点更新 | plan / dispatch（传给 subagent） | 全程，每轮迭代可能更新 |
| `edit_list` | merge 节点 append | merge 节点（冲突检测） | 全程，单调递增 |
| `class_dependencies` | analyze 节点初始化，merge 节点更新 | plan / dispatch（传给 subagent） | 全程，每轮迭代可能更新 |
| `microservice_specs` | plan 节点输出 | dispatch 使用 | 每轮迭代重新生成 |
| `results` | subgraph 通过 reducer append | merge 节点消费 | 每轮迭代重置 |

### 3.2 MicroserviceState（Subgraph 状态）

```python
class MicroserviceState(TypedDict):
    # === LangGraph 标准字段 ===
    messages: Annotated[list, add_messages]

    # === 身份 ===
    service_name: str                       # 微服务名称
    spec: dict                              # 该微服务的规格（要迁移哪些类、目标包名等）

    # === 从 parent 传入的快照（只读） ===
    structural_memory_snapshot: dict         # dispatch 时刻的 StructuralMemory 快照
    class_dependencies_snapshot: dict        # dispatch 时刻的依赖图快照

    # === subgraph 内部阶段 ===
    phase: str                              # "explore" | "edit" | "verify" | "done"
    plan: dict | None                       # explore 节点输出的迁移计划
    retry_count: int                        # 当前重试次数
    max_retries: int                        # 最大重试次数（默认 3）

    # === subgraph 输出（最终返回给 parent） ===
    local_edits: list[dict]                 # 本次迁移执行的所有 AtomicActionSet
    local_dependency_changes: list[dict]    # 迁移导致的依赖关系变更
    verification_result: dict | None        # 最终验证结果
    status: str                             # "success" | "failed" | "max_retries_exceeded"
    error: str | None                       # 失败原因
```

**Snapshot 字段详解**：

`structural_memory_snapshot` 和 `class_dependencies_snapshot` 是 Main Agent dispatch 时传入的**深拷贝**。Subagent 在整个生命周期中只读这两个字段，不写回。这保证了即使多个 subagent 并行运行，它们之间不会相互干扰。

---

## 4. 三层 Memory 详细设计

### 4.1 StructuralMemory（全局系统结构）

```python
from dataclasses import dataclass, field

@dataclass
class ModuleInfo:
    """单体中的一个模块/包的信息"""
    name: str                               # 模块名（如 com.example.order）
    classes: list[str]                       # 包含的类列表
    entry_points: list[str]                  # 入口点（Controller、API endpoint）
    internal_dependencies: list[str]         # 依赖的其他模块
    description: str = ""                    # LLM 生成的模块功能描述

@dataclass
class StructuralMemory:
    """全局结构化记忆"""
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    service_mapping: dict[str, str] = field(default_factory=dict)   # class → service_name
    shared_entities: list[str] = field(default_factory=list)        # 跨服务共享的实体类
    output_base_path: str = ""              # 微服务输出的根目录

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "StructuralMemory": ...
```

**谁读谁写**：
- **写入**：`analyze` 节点初始化（扫描单体代码），`merge` 节点按需更新（如 service_mapping 修正）
- **读取**：`plan` 节点（决定如何拆分），`dispatch` 时传给 subagent 做快照

### 4.2 EditList（编辑操作日志）

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
    target: str                             # 人类可读的目标（如 "Move OrderService to order-service"）
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

**MVP 简化**：不实现回滚逻辑，所有 entry 的 status 都是 `"committed"`。

### 4.3 ClassDependencyGraph（类级依赖图）

```python
@dataclass
class ClassNode:
    """一个 Java 类的元信息"""
    fqcn: str                               # 全限定类名
    module: str                             # 所属模块
    target_service: str | None = None       # 规划的目标微服务
    is_shared: bool = False                 # 是否为共享实体

@dataclass
class DependencyEdge:
    """两个类之间的依赖关系"""
    source: str                             # 调用方 FQCN
    target: str                             # 被调用方 FQCN
    dep_type: str                           # "import" | "method_call" | "inheritance" | "annotation"

@dataclass
class ClassDependencyGraph:
    """类级别依赖图"""
    nodes: dict[str, ClassNode] = field(default_factory=dict)
    edges: list[DependencyEdge] = field(default_factory=list)

    def get_dependencies_of(self, fqcn: str) -> list[DependencyEdge]: ...
    def get_cross_service_deps(self) -> list[DependencyEdge]: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ClassDependencyGraph": ...
```

**谁读谁写**：
- **写入**：`analyze` 节点初始化，`merge` 节点根据 subagent 返回的 `local_dependency_changes` 更新
- **读取**：`plan` 节点（聚类分析），`dispatch` 时传给 subagent

**MVP 简化**：依赖分析由 LLM 驱动（通过 `read_file` 读源码 + LLM 理解），不做 AST 解析。

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

| 节点 | 输入 | 输出 | LLM 参与? |
|------|------|------|----------|
| `analyze` | `monolith_path` | `structural_memory`, `class_dependencies` | 是（分析代码结构） |
| `plan` | `structural_memory`, `class_dependencies`, `target_services` | `microservice_specs` | 是（规划拆分方案） |
| `dispatch` | `microservice_specs`, memory snapshots | 触发 N 个 `Send()` | 否（纯调度） |
| `merge` | `results`（subgraph 返回） | 更新 `edit_list`, `structural_memory`, `class_dependencies` | 否（纯数据处理） |
| `check_complete` | `results`, `iteration` | 路由到 END 或 plan | 否（纯条件判断） |

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
│          │     │              │     │   actions)    │     │   arch check)│
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

| 节点 | 输入 | 输出 | LLM 参与? |
|------|------|------|----------|
| `explore` | `spec`, memory snapshots | `plan`（迁移计划 JSON） | 是（分析 + 规划） |
| `edit` | `plan` | `local_edits`（已执行的修改） | 是（LLM 决定具体修改内容，调用 sandbox tools） |
| `verify` | `service_name`, `local_edits` | `verification_result` | 部分（编译用 bash，架构检查可 LLM 辅助） |

**条件边**：
- `verify` 之后：
  - `pass` → `END`（设置 `status = "success"`）
  - `fail_compile` + `retry_count < max_retries` → `edit`（增加 retry_count，LLM 根据编译错误修复）
  - `fail_arch` + `retry_count < max_retries` → `explore`（增加 retry_count，重新规划）
  - 超过 max_retries → `END`（设置 `status = "max_retries_exceeded"`）

---

## 6. Memory 读写流与共享机制

### 6.1 完整数据流

```
                         Main Agent (GlobalState)
                    ┌──────────────────────────────────────┐
                    │                                      │
                    │  structural_memory   ←── analyze 写入  │
                    │        │                merge 更新    │
    dispatch 时     │        │                              │
    深拷贝为        │        ▼ (snapshot)                    │
    snapshot ──────►│  subagent 只读                        │
                    │                                      │
                    │  class_dependencies  ←── analyze 写入  │
                    │        │                merge 更新    │
    dispatch 时     │        │                              │
    深拷贝为        │        ▼ (snapshot)                    │
    snapshot ──────►│  subagent 只读                        │
                    │                                      │
                    │  edit_list ←── merge 节点 append       │
                    │     (subagent 不读不写全局 edit_list)   │
                    │                                      │
                    │  results ←── subgraph 返回值           │
                    │     (自动通过 operator.add reducer)    │
                    └──────────────────────────────────────┘

                         Subagent (MicroserviceState)
                    ┌──────────────────────────────────────┐
                    │                                      │
                    │  structural_memory_snapshot  [只读]    │
                    │  class_dependencies_snapshot [只读]    │
                    │                                      │
                    │  local_edits ←── edit 节点 append      │
                    │  local_dependency_changes ←── edit     │
                    │  verification_result ←── verify 写入   │
                    │                                      │
                    │  返回值 = {                            │
                    │    service_name,                      │
                    │    local_edits,                       │
                    │    local_dependency_changes,          │
                    │    verification_result,               │
                    │    status, error                      │
                    │  }                                    │
                    └──────────────────────────────────────┘
```

### 6.2 各层 Memory 的共享总结

| Memory 层 | Main Agent | Subagent | 同步机制 |
|-----------|-----------|----------|---------|
| **StructuralMemory** | 读写 | **只读 snapshot** | dispatch 时深拷贝传入 |
| **EditList** | 读写（merge 时 append） | **不读不写全局** | subagent 写 `local_edits`，merge 汇总到全局 |
| **ClassDependencyGraph** | 读写 | **只读 snapshot** | subagent 写 `local_dependency_changes`，merge 更新全局 |

### 6.3 多轮迭代时的 Memory 更新

如果第一批 subagent 有失败的，Main Agent 进入第二轮迭代：

1. `merge` 节点汇总第一批的成功结果，更新 `structural_memory` 和 `class_dependencies`
2. `check_complete` 检测到有失败，路由到 `plan`
3. `plan` 基于更新后的 memory 重新规划（只规划失败的服务）
4. `dispatch` 使用最新的 memory 生成新 snapshot 给第二批 subagent

这样第二批 subagent 能看到第一批的迁移结果（通过更新后的 snapshot），避免冲突。

---

## 7. 各节点实现规格

### 7.1 analyze（单体分析）

**职责**：扫描单体代码，构建 StructuralMemory 和 ClassDependencyGraph 的初始版本。

**实现方式**（MVP）：
1. 用 `ls` tool 获取项目目录结构
2. 用 `read_file` tool 读取关键文件（`pom.xml`、主要 Java 文件、`application.yml`）
3. LLM 分析代码结构，输出 JSON 格式的 StructuralMemory 和 ClassDependencyGraph
4. 解析 LLM 输出，初始化 memory

**输入 state 字段**：`monolith_path`
**输出 state 字段**：`structural_memory`, `class_dependencies`, `phase = "plan"`

### 7.2 plan（微服务分解规划）

**职责**：基于 StructuralMemory 和 ClassDependencyGraph，决定拆分方案。

**实现方式**：
1. 如果 `target_services` 已指定，按用户指定的服务名和类映射生成 specs
2. 否则 LLM 基于 memory 自动规划服务边界
3. 输出 `microservice_specs` 列表

**microservice_spec 格式**：
```python
{
    "name": "order-service",
    "classes": ["com.example.order.OrderService", "com.example.order.OrderRepository", ...],
    "target_package": "com.example.orderservice",
    "target_path": "/output/order-service",
    "dependencies": ["shared-entities"],      # 依赖的其他微服务
    "description": "处理订单相关业务逻辑"
}
```

**输入 state 字段**：`structural_memory`, `class_dependencies`, `target_services`
**输出 state 字段**：`microservice_specs`, `phase = "dispatch"`

### 7.3 dispatch（并行调度）

**职责**：为每个 microservice_spec 创建一个 subgraph 实例（通过 `Send()`）。

**实现方式**：纯数据映射，不调用 LLM。

```python
def dispatch(state: GlobalState) -> list[Send]:
    return [
        Send("process_microservice", {
            "messages": [],
            "service_name": spec["name"],
            "spec": spec,
            "structural_memory_snapshot": copy.deepcopy(state["structural_memory"]),
            "class_dependencies_snapshot": copy.deepcopy(state["class_dependencies"]),
            "phase": "explore",
            "plan": None,
            "retry_count": 0,
            "max_retries": 3,
            "local_edits": [],
            "local_dependency_changes": [],
            "verification_result": None,
            "status": "running",
            "error": None,
        })
        for spec in state["microservice_specs"]
    ]
```

### 7.4 explore（微服务内探索）

**职责**：分析要迁移到该微服务的类，规划具体的迁移步骤。

**实现方式**：
1. 从 snapshot 中获取系统全景
2. 用 `read_file` 读取 spec 中列出的每个类的源码
3. LLM 分析类间依赖，生成迁移计划

**迁移计划格式**：
```python
{
    "steps": [
        {
            "transaction_id": "tx_order_001",
            "target": "Create order-service module with POM",
            "actions": [
                {"file": "order-service/pom.xml", "action_type": "create", "description": "创建 POM 文件"},
                {"file": "order-service/src/main/java/.../OrderService.java", "action_type": "create", "description": "迁移 OrderService 类"}
            ]
        },
        ...
    ]
}
```

**输入 state 字段**：`spec`, `structural_memory_snapshot`, `class_dependencies_snapshot`
**输出 state 字段**：`plan`, `phase = "edit"`

### 7.5 edit（执行代码迁移）

**职责**：按 plan 逐步执行文件操作。

**实现方式**：
1. 遍历 plan 中的每个 step
2. 对每个 action，调用对应的 sandbox tool：
   - `create` → `write_file`
   - `modify` → `read_file` + LLM 生成修改 + `str_replace` 或 `write_file`
   - `move` → `read_file` + `write_file`（新位置） + 更新 import
3. 每完成一个 step，append 到 `local_edits`
4. 记录依赖变化到 `local_dependency_changes`

**输入 state 字段**：`plan`, `spec`
**输出 state 字段**：`local_edits`, `local_dependency_changes`, `phase = "verify"`

### 7.6 verify（验证）

**职责**：验证迁移结果的正确性。

**编译验证**：
```bash
cd /mnt/user-data/workspace/{service_name} && mvn compile -q
```
如果编译失败，返回编译错误信息。

**架构验证**（MVP 简化版）：
- 检查 `pom.xml` 中声明的依赖是否都存在
- 检查是否有 import 指向已不存在的类
- LLM 辅助判断是否有明显的架构问题

**输入 state 字段**：`service_name`, `local_edits`, `spec`
**输出 state 字段**：`verification_result`, `phase`, `status`

**verification_result 格式**：
```python
{
    "compile_success": True | False,
    "compile_errors": [...],           # 编译错误列表
    "arch_issues": [...],              # 架构问题列表
    "verdict": "pass" | "fail_compile" | "fail_arch"
}
```

### 7.7 merge（结果合并）

**职责**：收集所有 subgraph 的结果，更新全局 memory。

**实现方式**：
1. 遍历 `results`（每个元素是一个 subgraph 的返回值）
2. 对成功的 subagent：
   - `local_edits` → append 到全局 `edit_list`
   - `local_dependency_changes` → 更新全局 `class_dependencies`
   - 更新 `structural_memory.service_mapping`
3. 对失败的 subagent：
   - 记录错误信息
   - 标记为"需要重试"（供 `check_complete` 判断）

**输入 state 字段**：`results`, `edit_list`, `structural_memory`, `class_dependencies`
**输出 state 字段**：更新后的 `edit_list`, `structural_memory`, `class_dependencies`

---

## 8. DeerFlow 复用清单

### 8.1 直接复用（import 即用）

```python
# LLM 调用
from deerflow.models import create_chat_model

# 文件操作 tools（用于 edit 节点）
from deerflow.sandbox.tools import bash_tool, read_file_tool, write_file_tool, str_replace_tool, ls_tool

# 配置系统
from deerflow.config import get_app_config
```

### 8.2 参考但不直接复用

| DeerFlow 模块 | 参考什么 | 自己实现什么 |
|--------------|---------|------------|
| `agents/memory/storage.py` | `MemoryStorage` 抽象类模式 | 三层 memory 的序列化/反序列化 |
| `subagents/config.py` | `SubagentConfig` 数据结构 | microservice_spec 数据结构 |
| `skills/types.py` | `Skill` dataclass 设计 | memory 的 dataclass 设计 |

### 8.3 不复用

| DeerFlow 模块 | 原因 |
|--------------|------|
| `agents/lead_agent/` | 用自己的 StateGraph，不用 `create_agent` |
| `agents/middlewares/` | 显式 graph 不需要 middleware 链 |
| `subagents/executor.py` | 用 LangGraph `Send()` 替代后台线程池 |
| `tools/builtins/task_tool.py` | 不需要 `task` tool，用 graph 拓扑替代 |
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
3. 分析类之间的依赖关系
4. 识别可能的微服务边界

请以 JSON 格式输出分析结果，包含：
- modules: 各模块信息
- dependencies: 类级别依赖关系
- shared_entities: 跨模块共享的实体类
```

### 9.2 explore 节点

```
你是一个微服务迁移专家。你正在将 {service_name} 从单体应用中提取为独立微服务。

## 系统全景
{structural_memory_snapshot}

## 依赖关系
{class_dependencies_snapshot}

## 你的任务
将以下类迁移到 {service_name} 微服务：
{spec.classes}

请你：
1. 阅读每个类的源码（使用 read_file）
2. 分析这些类的内部依赖和外部依赖
3. 制定详细的迁移计划

输出 JSON 格式的迁移计划，每个 step 包含：
- transaction_id: 唯一标识
- target: 人类可读的目标描述
- actions: 具体的文件操作列表
```

### 9.3 edit 节点

```
你是一个代码迁移执行者。请按照以下计划逐步执行文件操作。

## 迁移计划
{plan}

请严格按顺序执行每个 step：
- 使用 write_file 创建新文件
- 使用 str_replace 修改现有文件
- 使用 bash 执行必要的命令

每完成一个 step，确认操作成功后再继续下一个。
```

---

## 10. MVP 范围

### 10.1 包含

- [x] GlobalState + MicroserviceState 定义
- [x] StructuralMemory + EditList + ClassDependencyGraph 数据结构
- [x] Main graph（analyze → plan → dispatch → merge → check_complete）
- [x] Microservice subgraph（explore → edit → verify + retry）
- [x] 各节点的 LLM prompt + tool 调用
- [x] 单个微服务的端到端迁移（先不做并行）
- [x] 编译验证（mvn compile via bash）

### 10.2 不包含（Phase 2+）

- [ ] 多服务并行 `Send()`（先串行验证 subgraph 正确性）
- [ ] AST 级别的依赖分析（先用 LLM 驱动）
- [ ] 事务回滚
- [ ] Memory 持久化到数据库
- [ ] 架构验证的高级规则（循环依赖检测、API 一致性）
- [ ] 前端 UI
- [ ] 多轮迭代重试
