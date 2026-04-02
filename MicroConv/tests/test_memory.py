"""Tests for the memory layer dataclasses."""

from microconv.memory import (
    AtomicActionSet,
    ClassDependencyGraph,
    ClassEdge,
    ClassNode,
    EditList,
    FileAction,
    InterfaceEndpoint,
    ServiceInfo,
    ServiceInterfaces,
    ServiceTopology,
)


class TestServiceTopology:
    def test_roundtrip(self):
        topo = ServiceTopology(
            services={
                "order-service": ServiceInfo(
                    name="order-service",
                    description="Orders",
                    classes=["com.example.order.OrderService"],
                    depends_on=["user-service"],
                    used_interfaces={"user-service": ["getUserById"]},
                ),
                "user-service": ServiceInfo(name="user-service", description="Users", classes=["com.example.user.UserService"]),
            },
            shared_entities=["com.example.shared.BaseEntity"],
        )
        d = topo.to_dict()
        restored = ServiceTopology.from_dict(d)
        assert restored.services["order-service"].name == "order-service"
        assert restored.services["order-service"].depends_on == ["user-service"]
        assert restored.shared_entities == ["com.example.shared.BaseEntity"]

    def test_get_dependents(self):
        topo = ServiceTopology(
            services={
                "a": ServiceInfo(name="a", depends_on=["b"], used_interfaces={"b": ["getX"]}),
                "b": ServiceInfo(name="b"),
                "c": ServiceInfo(name="c", depends_on=["b"], used_interfaces={"b": ["getY"]}),
            }
        )
        deps = topo.get_dependents("b")
        assert "a" in deps
        assert deps["a"] == ["getX"]
        assert "c" in deps
        assert deps["c"] == ["getY"]


class TestServiceInterfaces:
    def test_roundtrip(self):
        si = ServiceInterfaces(
            interfaces={
                "order-service": [
                    InterfaceEndpoint(name="getOrder", type="REST", method="GET", path="/api/orders/{id}"),
                ]
            }
        )
        d = si.to_dict()
        restored = ServiceInterfaces.from_dict(d)
        eps = restored.get_interfaces_for("order-service")
        assert len(eps) == 1
        assert eps[0].name == "getOrder"
        assert eps[0].method == "GET"

    def test_update_from_corrections(self):
        si = ServiceInterfaces(
            interfaces={"svc": [InterfaceEndpoint(name="old", method="GET")]}
        )
        si.update_from_corrections("svc", {"exposed": [{"name": "new", "type": "REST", "method": "POST", "path": "/new"}]})
        assert si.interfaces["svc"][0].name == "new"
        assert si.interfaces["svc"][0].method == "POST"


class TestEditList:
    def test_append_and_query(self):
        el = EditList()
        el.append(AtomicActionSet(
            transaction_id="tx1",
            service_name="order-service",
            target="create pom",
            actions=[FileAction(file="pom.xml", action_type="create", description="init")],
        ))
        el.append(AtomicActionSet(transaction_id="tx2", service_name="user-service", target="create pom"))

        assert len(el.entries) == 2
        assert len(el.get_by_service("order-service")) == 1
        assert len(el.get_by_service("user-service")) == 1

    def test_roundtrip(self):
        el = EditList()
        el.append(AtomicActionSet(
            transaction_id="tx1",
            service_name="svc",
            target="t",
            actions=[FileAction(file="a.java", action_type="create")],
        ))
        data = el.to_list()
        restored = EditList.from_list(data)
        assert len(restored.entries) == 1
        assert restored.entries[0].actions[0].file == "a.java"


class TestClassDependencyGraph:
    def test_roundtrip(self):
        g = ClassDependencyGraph(
            nodes={
                "OrderService": ClassNode(fqcn="com.example.order.OrderService", type="service", package="com.example.order"),
                "UserClient": ClassNode(fqcn="com.example.order.client.UserClient", type="client", package="com.example.order.client", note="calls user-service"),
            },
            edges=[ClassEdge(source="OrderService", target="UserClient", type="injection")],
        )
        d = g.to_dict()
        restored = ClassDependencyGraph.from_dict(d)
        assert "OrderService" in restored.nodes
        assert len(restored.edges) == 1
        assert restored.edges[0].type == "injection"

    def test_get_external_clients(self):
        g = ClassDependencyGraph(
            nodes={
                "Svc": ClassNode(fqcn="Svc", type="service"),
                "Client": ClassNode(fqcn="Client", type="client"),
            }
        )
        clients = g.get_external_clients()
        assert len(clients) == 1
        assert clients[0].fqcn == "Client"

    def test_get_dependencies_of(self):
        g = ClassDependencyGraph(
            edges=[
                ClassEdge(source="A", target="B"),
                ClassEdge(source="A", target="C"),
                ClassEdge(source="B", target="C"),
            ]
        )
        deps = g.get_dependencies_of("A")
        assert len(deps) == 2
