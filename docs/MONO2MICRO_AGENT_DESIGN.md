# Monolith-to-Microservice Agent Harness: Architecture Design & Framework Evaluation

## 1. 需求分析

根据提供的架构图，目标系统包含以下核心组件：

### 1.1 Main Agent（主 Agent）
- 负责全局任务统筹和分解
- 维护全局结构化记忆（Global Structural Memory）
- 将每个微服务的转化任务派发给独立的 Subagent

### 1.2 Subagent（每个微服务一个）
三阶段流水线：
1. **Explore and Plan** — 分析单体代码，规划拆分方案
2. **Copy and Edit** — 执行代码迁移和修改
3. **Verify against Spec & run tests** — 编译级验证 + 架构级验证

### 1.3 Hierarchical Memory（多层记忆）
- **Global Structural Memory** — 全局系统结构信息
- **Edit List** — 已完成的编辑操作清单
- **Class-level Dependency** — 类级别依赖关系图

### 1.4 Atomic Action Set（原子操作集）
事务性的代码修改操作，例如：
```json
{
  "transaction_id": "tx_001",
  "target": "Move method A to class B in Service K",
  "actions": [
    {"file": "xxx.java", "action": "update package in class B"},
    {"file": "pom.xml", "action": "add dependency regarding xxx"}
  ]
}
```

### 1.5 验证层
- **Compilation Level** — 编译通过
- **Architecture Level** — 架构约束检查（循环依赖、API 一致性等）

---

## 2. 框架评估

### 2.1 方案 A：基于 DeerFlow 复用

**DeerFlow 已有的可复用能力：**

| 你需要的 | DeerFlow 已有的 | 匹配度 |
|---------|----------------|-------|
| Main Agent 统筹 | Lead Agent + middleware 链 | ★★★★☆ |
| 每个微服务一个 Subagent | `task` tool + `SubagentExecutor` 后台线程池 | ★★★☆☆ |
| 分阶段流水线（Explore → Edit → Verify） | 无内置阶段概念，靠 prompt 引导 | ★★☆☆☆ |
| 多层 Memory | 单层 Memory（facts + context），可扩展 storage | ★★☆☆☆ |
| Atomic Action Set（事务性编辑） | Sandbox tools（bash/read/write/str_replace） | ★★☆☆☆ |
| 编译验证 | Sandbox bash 可执行 mvn compile | ★★★★☆ |
| 架构验证 | 无内置，需自定义 tool | ★☆☆☆☆ |
| 并行 Subagent | `MAX_CONCURRENT_SUBAGENTS = 3`，可调 | ★★★★☆ |

**需要改动的核心模块：**

1. **Subagent 系统**（改动量：大）
   - 当前 `SubagentConfig` 是静态的 `general-purpose` / `bash` 两种，需要改为动态创建"微服务专用 subagent"
   - 当前 subagent 不支持多阶段流水线（Explore → Edit → Verify），需要在 executor 中加入阶段控制或用 StateGraph 替代
   - 并发数上限 3 需要调高（微服务可能有 10+ 个）

2. **Memory 系统**（改动量：大）
   - 当前 memory 是单层的 facts/context JSON，需要改为三层结构
   - 需要新增 `StructuralMemoryStorage`（全局 AST/依赖图）和 `EditListStorage`
   - 当前 memory 是 per-agent 隔离的，需要增加跨 subagent 的共享 memory 读写

3. **ThreadState**（改动量：中）
   - 需要扩展状态来追踪 transaction、阶段进度、依赖图

4. **新增 Tools**（改动量：中）
   - 架构验证 tool
   - 依赖分析 tool（解析 Java/POM 依赖）
   - 事务管理 tool（原子操作集的提交/回滚）

5. **Lead Agent Prompt**（改动量：中）
   - 需要重写 system prompt 来适配"微服务拆分统筹"场景

**优点：**
- 开箱即有 sandbox（bash/文件操作）、model factory、config 系统、streaming
- 有成熟的 harness/app 分层，可以把你的逻辑放在 app 层
- 前端 UI 可以复用来可视化进度

**缺点：**
- DeerFlow 的 subagent 是"扁平工具调用"模式（lead agent 调用 `task` tool 启动 subagent），不是真正的 graph subnode。改成多阶段有状态的 subgraph 需要重构 executor
- Memory 的数据模型和你需要的差距很大，基本需要重写
- DeerFlow 的 graph 不是手写 `StateGraph`，而是 `langchain.agents.create_agent` 生成的，想加自定义节点/边很困难
- 总改动量可能相当于重写 30-40% 的 harness 层

### 2.2 方案 B：纯 LangGraph 从头搭建

```
直接使用 LangGraph 的 StateGraph API 构建完全定制的工作流。
```

**优点：**
- 完全控制 graph 拓扑：可以精确建模 Main → Subagent、Subagent 内部的 Explore → Edit → Verify 流水线
- 状态管理灵活：`TypedDict` + `Annotated` reducers 可以直接建模 hierarchical memory
- LangGraph 原生支持 subgraph（`add_node(subgraph)` 实现嵌套），比 DeerFlow 的 tool-based subagent 更适合多阶段子任务
- `Send()` API 可以实现 map-reduce 式并行：主 agent 一次性 dispatch N 个微服务 subgraph
- 没有不需要的 UI/channel/upload 等代码拖累

**缺点：**
- 需要自己实现：sandbox、model factory、config、streaming bridge、checkpoint persistence
- 估算核心代码量：2000-3000 行 Python（graph 定义 + state + memory + tools + verification）
- 没有现成的 Web UI 做可视化

**关键代码结构示意：**

```python
from langgraph.graph import StateGraph, Send, END
from typing import TypedDict, Annotated
import operator

class GlobalState(TypedDict):
    # Hierarchical Memory
    structural_memory: dict          # AST、模块边界、API 列表
    edit_list: list[dict]            # 已提交的 atomic action sets
    class_dependencies: dict         # 类级别依赖图

    # Task tracking
    microservice_specs: list[dict]   # 待转化的微服务规格
    results: Annotated[list, operator.add]  # 各 subagent 结果

class MicroserviceState(TypedDict):
    service_name: str
    spec: dict
    phase: str                       # "explore" | "edit" | "verify"
    plan: dict | None
    actions: list[dict]              # atomic action set
    verification_result: dict | None
    # 从 parent 继承的 shared memory (read-only snapshot)
    structural_memory_snapshot: dict
    class_dependencies_snapshot: dict

# Subagent: 三阶段流水线
def build_microservice_subgraph():
    sg = StateGraph(MicroserviceState)
    sg.add_node("explore", explore_and_plan)
    sg.add_node("edit", copy_and_edit)
    sg.add_node("verify", verify_against_spec)

    sg.set_entry_point("explore")
    sg.add_edge("explore", "edit")
    sg.add_edge("edit", "verify")
    sg.add_conditional_edges("verify", check_verification, {
        "pass": END,
        "fail_compile": "edit",      # 编译失败 → 回到编辑
        "fail_arch": "explore",      # 架构问题 → 重新规划
    })
    return sg.compile()

# Main graph: map-reduce 并行
def build_main_graph():
    mg = StateGraph(GlobalState)
    mg.add_node("analyze_monolith", analyze_monolith)
    mg.add_node("plan_decomposition", plan_decomposition)
    mg.add_node("process_microservice", build_microservice_subgraph())
    mg.add_node("merge_results", merge_results)

    mg.set_entry_point("analyze_monolith")
    mg.add_edge("analyze_monolith", "plan_decomposition")
    mg.add_conditional_edges("plan_decomposition", dispatch_services,
        # 使用 Send() 并行启动多个 subgraph
        lambda state: [
            Send("process_microservice", {
                "service_name": spec["name"],
                "spec": spec,
                "phase": "explore",
                "structural_memory_snapshot": state["structural_memory"],
                "class_dependencies_snapshot": state["class_dependencies"],
            })
            for spec in state["microservice_specs"]
        ]
    )
    mg.add_edge("process_microservice", "merge_results")
    mg.add_edge("merge_results", END)

    return mg.compile(checkpointer=MemorySaver())
```

### 2.3 方案 C：LangGraph 核心 + DeerFlow 组件选择性复用

**推荐方案。** 取两者之长：

| 层次 | 用什么 | 理由 |
|------|--------|------|
| **Graph 拓扑** | LangGraph `StateGraph` 手写 | 需要精确控制 Main → Subgraph、三阶段流水线、条件回退 |
| **LLM Factory** | DeerFlow `deerflow.models.create_chat_model` | 开箱即用的 multi-provider 支持、thinking/vision toggle |
| **Sandbox** | DeerFlow `deerflow.sandbox` | 成熟的 bash/file 操作、虚拟路径映射、Docker 隔离 |
| **Config** | DeerFlow `deerflow.config` | YAML config + env var 替换 + 热重载 |
| **Memory** | 自己写（放在新模块） | DeerFlow 的 memory 模型不匹配，但可以参考其 storage 抽象 |
| **Tools** | 混合：复用 sandbox tools + 新增领域 tools | `bash`、`read_file`、`write_file`、`str_replace` 直接可用 |
| **Checkpoint** | DeerFlow `deerflow.agents.checkpointer` | 已有 SQLite/Postgres/Memory 实现 |
| **前端/Gateway** | 不复用（或后期接入） | 先专注 backend pipeline |

### 2.4 方案 D：其他框架对比

| 框架 | 适合度 | 理由 |
|------|--------|------|
| **CrewAI** | ★★☆☆☆ | 角色扮演式，不支持精确的 graph 拓扑控制和条件回退 |
| **AutoGen** | ★★★☆☆ | 多 agent 对话适合头脑风暴，但"代码转化"需要精确的状态机，不适合松散对话 |
| **Semantic Kernel** | ★★☆☆☆ | 微软生态，Python 支持弱于 C#，graph 能力不如 LangGraph |
| **Prefect / Temporal** | ★★★☆☆ | 适合做外层编排（重试、超时、持久化），但不适合做 LLM agent 的内部推理循环 |
| **自研 Python** | ★★★☆☆ | 完全控制，但要自己实现 checkpoint、streaming、state merge 等 LangGraph 已解决的问题 |

---

## 3. 推荐方案：方案 C 详细设计

### 3.1 项目结构

```
mono2micro/
├── pyproject.toml
├── config.yaml                    # 复用 DeerFlow 格式
├── mono2micro/
│   ├── __init__.py
│   ├── graph/                     # LangGraph 图定义
│   │   ├── __init__.py
│   │   ├── main_graph.py          # Main Agent graph
│   │   ├── microservice_graph.py  # Per-service subgraph
│   │   └── nodes/                 # 节点实现
│   │       ├── analyze.py         # 单体分析
│   │       ├── plan.py            # 分解规划
│   │       ├── explore.py         # 微服务内探索
│   │       ├── edit.py            # 代码迁移
│   │       ├── verify.py          # 编译 + 架构验证
│   │       └── merge.py           # 结果合并
│   ├── state/                     # 状态定义
│   │   ├── __init__.py
│   │   ├── global_state.py        # 全局状态
│   │   └── service_state.py       # 微服务子状态
│   ├── memory/                    # 多层记忆
│   │   ├── __init__.py
│   │   ├── structural.py          # 全局结构记忆
│   │   ├── edit_list.py           # 编辑操作列表
│   │   ├── dependency.py          # 类级依赖图
│   │   └── storage.py             # 持久化（可参考 DeerFlow MemoryStorage）
│   ├── tools/                     # 领域工具
│   │   ├── __init__.py
│   │   ├── java_analyzer.py       # Java 依赖分析
│   │   ├── pom_editor.py          # POM 文件编辑
│   │   ├── arch_validator.py      # 架构约束检查
│   │   └── transaction.py         # 原子操作集管理
│   └── config/                    # 配置
│       ├── __init__.py
│       └── settings.py
├── tests/
└── README.md
```

### 3.2 多层记忆详细设计

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class StructuralMemory:
    """全局结构化记忆 — 维护整个系统的宏观视图"""
    modules: dict[str, ModuleInfo]           # 模块 → {类列表, 入口, 依赖}
    api_boundaries: list[APIBoundary]        # 已确定的 API 边界
    service_mapping: dict[str, str]          # class → target_service
    shared_entities: list[str]               # 跨服务共享的实体类

@dataclass
class EditList:
    """编辑操作列表 — 追踪所有已完成和待执行的原子操作"""
    committed: list[AtomicActionSet]         # 已提交的事务
    pending: list[AtomicActionSet]           # 待提交的事务
    rolled_back: list[AtomicActionSet]       # 已回滚的事务

@dataclass
class ClassDependencyGraph:
    """类级别依赖图 — 细粒度的依赖关系"""
    nodes: dict[str, ClassNode]              # 类名 → 元信息
    edges: list[DependencyEdge]              # 依赖关系
    clusters: dict[str, list[str]]           # 聚类结果（哪些类应该归到同一微服务）

@dataclass
class AtomicActionSet:
    """原子操作集 — 事务性的代码修改"""
    transaction_id: str
    target: str                              # 人类可读的目标描述
    service_name: str
    actions: list[FileAction]
    status: str = "pending"                  # pending | committed | rolled_back
    verification: VerificationResult | None = None

@dataclass
class FileAction:
    file: str
    action_type: str                         # create | modify | delete | move
    description: str
    content: str | None = None               # 新增/修改的内容
    old_content: str | None = None           # 用于回滚
```

### 3.3 Subagent 三阶段流水线设计

```
┌─────────────────────────────────────────────────┐
│              Microservice Subgraph               │
│                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐ │
│  │ Explore  │──▶│  Edit    │──▶│   Verify     │ │
│  │ & Plan   │   │ (Copy &  │   │ (Compile +   │ │
│  │          │   │  Modify) │   │  Arch Check) │ │
│  └──────────┘   └──────────┘   └──────┬───────┘ │
│       ▲                               │         │
│       │          fail_arch            │         │
│       └───────────────────────────────┤         │
│                                       │         │
│                  fail_compile         │         │
│              ┌────────────────────────┘         │
│              ▼                                   │
│         ┌──────────┐                             │
│         │  Fix &   │──── retry ──▶ Verify       │
│         │  Retry   │                             │
│         └──────────┘                             │
│                                                  │
│              pass ──▶ END (merge to parent)      │
└─────────────────────────────────────────────────┘
```

每个阶段的核心逻辑：

**Explore & Plan：**
- 读取目标微服务的规格（哪些类要迁移）
- 从 `StructuralMemory` 获取当前系统全景
- 从 `ClassDependencyGraph` 分析涉及的依赖
- LLM 生成迁移计划（哪些文件要改、改什么、顺序）
- 输出：一组 `AtomicActionSet`

**Copy & Edit：**
- 按计划逐个执行 `AtomicActionSet`
- 每个 action 通过 sandbox tools 执行（`write_file`、`str_replace`、`bash`）
- 更新 `EditList`（append committed transactions）
- 更新 `ClassDependencyGraph`（反映迁移后的依赖变化）

**Verify：**
- **编译级**：在 sandbox 中执行 `mvn compile` / `gradle build`
- **架构级**：
  - 检查循环依赖
  - 验证 API 边界一致性
  - 检查共享实体是否正确处理
  - 验证 pom.xml 依赖声明完整性
- 失败处理：根据错误类型回退到 Edit（编译错误）或 Explore（架构错误）

### 3.4 Main Agent ↔ Subagent 通信

```
Main Agent (GlobalState)
    │
    ├── structural_memory ──── READ ────┐
    ├── class_dependencies ─── READ ────┤
    │                                    ▼
    │                          Subagent (snapshot)
    │                                    │
    │                          执行迁移...
    │                                    │
    ├── edit_list ◀──────── WRITE ──────┘
    ├── structural_memory ◀─ UPDATE ────┘
    └── results ◀─────────── APPEND ────┘
```

关键设计决策：
- Subagent 收到 memory 的 **snapshot**（只读），避免并发写冲突
- Subagent 完成后，结果通过 `Annotated[list, operator.add]` reducer 合并回主状态
- Main Agent 在 `merge_results` 阶段统一更新 `structural_memory` 和 `class_dependencies`
- 如果需要多轮迭代（服务间有依赖），Main Agent 可以再次 dispatch

### 3.5 从 DeerFlow 复用的具体代码

```python
# 复用 DeerFlow 的 LLM 工厂
from deerflow.models import create_chat_model

# 复用 DeerFlow 的 sandbox tools
from deerflow.sandbox.tools import bash, read_file, write_file, str_replace, ls

# 复用 DeerFlow 的 config 系统
from deerflow.config import get_app_config

# 复用 DeerFlow 的 checkpointer
from deerflow.agents.checkpointer.async_provider import make_checkpointer

# 可选：复用 DeerFlow 的 community tools（web search for API docs etc.）
from deerflow.community.tavily import tavily_search
```

---

## 4. 实施建议

### 4.1 Phase 1：核心 Pipeline（MVP）

**目标**：端到端跑通一个微服务的迁移

- [ ] 搭建项目骨架，配置 DeerFlow 为依赖
- [ ] 实现 `GlobalState` 和 `MicroserviceState`
- [ ] 实现 `microservice_graph`（三阶段 subgraph）
- [ ] 实现 `StructuralMemory` 基础版（手工初始化）
- [ ] 实现 `AtomicActionSet` 的 sandbox 执行
- [ ] 实现编译验证（`mvn compile` via bash tool）
- [ ] 测试：单个微服务迁移 E2E

### 4.2 Phase 2：多服务并行 + Memory 完善

- [ ] 实现 `main_graph` 的 `Send()` 并行 dispatch
- [ ] 实现 `ClassDependencyGraph` 自动构建（AST 分析）
- [ ] 实现 `EditList` 的事务回滚
- [ ] 实现 `merge_results` 的冲突检测
- [ ] 架构验证 tool
- [ ] 测试：3-5 个微服务并行迁移

### 4.3 Phase 3：闭环优化

- [ ] 服务间依赖的迭代处理（multi-round dispatch）
- [ ] Memory 持久化到数据库
- [ ] Web UI 集成（可选：接入 DeerFlow 前端）
- [ ] 错误恢复和 checkpoint resume

---

## 5. 隐式 Graph vs 显式 Graph：如何选择

### 5.1 什么是隐式 Graph

DeerFlow 使用 `langchain.agents.create_agent` 创建 agent，这个 API 内部自动生成一个 ReAct loop 拓扑的 StateGraph：

```
START → LLM → 有 tool call? → 是 → 执行 tool → LLM → ... → 否 → END
```

开发者不需要写 `add_node`、`add_edge`，graph 是框架自动生成的，因此称为"隐式 graph"。
所有领域特定的逻辑（阶段控制、回退策略等）都放在 **system prompt** 和 **tools** 中，由 LLM 自主决定执行顺序。

### 5.2 显式 Graph

使用 LangGraph 的 `StateGraph` API 手动定义节点和边：

```python
sg = StateGraph(MyState)
sg.add_node("explore", explore_fn)
sg.add_node("edit", edit_fn)
sg.add_node("verify", verify_fn)
sg.add_edge("explore", "edit")
sg.add_conditional_edges("verify", check_result, {"pass": END, "fail": "edit"})
```

阶段顺序和回退条件由代码硬编码，LLM 只在每个节点内部有自由度。

### 5.3 选择建议

| 维度 | 隐式 graph (create_agent) | 显式 graph (StateGraph) |
|------|--------------------------|----------------------|
| 阶段顺序的**软保证**（靠 prompt） | 可以 | 可以 |
| 阶段顺序的**硬保证**（代码强制） | 做不到 | 可以 |
| 阶段之间的**条件回退** | LLM 自行判断 | 代码精确控制 |
| 并行 Subagent | `task` tool 并发上限 3 | `Send()` 无限制 |
| 每个阶段用**不同 tools/prompt** | 不行，共享同一套 | 可以，每个 node 绑定不同配置 |
| **开发速度** | 快（只写 prompt + tools） | 慢（需要设计 state、节点、边） |

**推荐策略**：先用隐式 graph 做 MVP（把阶段控制放在 prompt 中），如果发现 LLM 经常跳步或不遵循流程，再升级到显式 StateGraph。这也是 Claude Code、Cursor Agent、Devin 等主流 coding agent 的做法。

---

## 6. 结论

| 维度 | 纯 DeerFlow (隐式 graph) | 纯 LangGraph (显式 graph) | LangGraph + DeerFlow 组件 |
|------|------------------------|--------------------------|--------------------------|
| Graph 控制力 | 低（prompt 软控制） | 高（代码硬控制） | 高 |
| 开发速度 | **最快**（只写 prompt + tools） | 慢（要造轮子） | 中 |
| Memory 灵活度 | 低 | 高 | 高 |
| 基础设施成熟度 | 高 | 低 | 高 |
| 维护成本 | 低（不改 DeerFlow） | 中 | 中 |
| 阶段硬保证 | 无 | 有 | 有 |

### 推荐策略：渐进式

**Phase 0（MVP）**：直接用 DeerFlow 的 `create_agent` 隐式 graph。把三阶段流水线的逻辑全部放在 system prompt 和 tools 里，利用 DeerFlow 现成的 sandbox、subagent、model factory。Memory 层新写一个自定义的 `MemoryStorage` 实现。这是最快出 MVP 的方式。

**Phase 1（如果需要）**：如果发现 LLM 不遵循阶段流程（跳步、遗漏验证），则将 subagent 的内部逻辑改为手写 StateGraph，同时继续复用 DeerFlow 的 sandbox/model/config 基础设施。

**Phase 2（如果需要）**：如果需要超过 3 个 subagent 并行，或需要每个阶段绑定不同的 tools/prompt，则升级为完整的 LangGraph 显式 graph + DeerFlow 组件复用方案。

这种渐进式方法避免了一开始过度工程化，同时保留了后续升级的路径。
