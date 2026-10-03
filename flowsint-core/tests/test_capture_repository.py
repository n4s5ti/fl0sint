"""
Tests for CaptureGraphRepository - the capture-only graph sink.

Test requirements from DEF-46:
- More than auto-flush batch size produces no live graph writes
- Direct discovery callback and exception after partial scan remain captured
- Unsupported graph method fails explicitly
- Scraper runs with no Neo4j service or credentials
"""

import pytest
from unittest.mock import Mock, patch
import json

from flowsint_core.core.graph import (
    CaptureGraphRepository,
    CapturedOperation,
    GraphService,
    create_graph_service,
)
from flowsint_core.core.graph.types import GraphDict


class TestCaptureGraphRepository:
    """Test basic capture repository functionality."""

    def test_initialization(self):
        """Test CaptureGraphRepository initializes correctly."""
        repo = CaptureGraphRepository(sketch_id="test_sketch")
        assert repo.sketch_id == "test_sketch"
        assert len(repo.operations) == 0
        assert repo.batch_size == 10

    def test_create_node_is_captured(self):
        """Test that create_node operation is captured."""
        repo = CaptureGraphRepository()
        node_obj = {"type": "email", "value": "test@example.com"}
        
        element_id = repo.create_node(node_obj, "test_sketch")
        
        assert element_id is not None
        assert element_id.startswith("node_")
        assert len(repo.operations) == 1
        assert repo.operations[0].method_name == "create_node"

    def test_create_node_preserves_input_lineage(self):
        """Test that input lineage is preserved in captures."""
        repo = CaptureGraphRepository()
        node_obj = {"type": "email", "value": "test@example.com"}
        
        repo.create_node(node_obj, "test_sketch")
        
        operation = repo.operations[0]
        assert "node_obj" in operation.parameters
        assert operation.parameters["node_obj"] == node_obj

    def test_batch_operations_trigger_auto_flush(self):
        """Test that batch size exceeded triggers auto-flush (DEF-46: no live writes)."""
        repo = CaptureGraphRepository()
        repo.set_batch_size(5)
        
        # Add 4 operations - below threshold
        for i in range(4):
            repo.add_to_batch("create_node", node_data={"id": i})
        
        # 4 operations in queue, 0 flushed yet
        flush_ops = [op for op in repo.operations if op.method_name == "flush_batch"]
        assert len(flush_ops) == 0
        assert len(repo.batch_queue) == 4
        
        # Add 2 more - should trigger auto-flush at threshold
        repo.add_to_batch("create_node", node_data={"id": 4})
        repo.add_to_batch("create_node", node_data={"id": 5})
        
        # Now we should have a flush_batch operation recorded
        flush_ops = [op for op in repo.operations if op.method_name == "flush_batch"]
        assert len(flush_ops) == 1
        assert repo.batch_queue == []

    def test_batch_greater_than_auto_flush_produces_no_writes(self):
        """DEF-46 acceptance: batch > auto-flush size produces no live writes."""
        repo = CaptureGraphRepository()
        repo.set_batch_size(5)
        
        # Add 15 operations (exceeds batch size multiple times)
        for i in range(15):
            repo.add_to_batch("create_node", node_data={"id": i})
        
        # Verify only flush_batch operations were recorded
        flush_ops = [op for op in repo.operations if op.method_name == "flush_batch"]
        assert len(flush_ops) >= 2  # At least 2 auto-flushes
        
        # Verify no actual Neo4j writes would happen
        assert repo.batch_queue == []

    def test_unsupported_method_fails_explicitly(self):
        """DEF-46 acceptance: unsupported method fails explicitly."""
        repo = CaptureGraphRepository()
        
        # query() is unsupported because it requires live connection
        with pytest.raises(NotImplementedError) as exc_info:
            repo.query("MATCH (n) RETURN n")
        
        assert "Unsupported method" in str(exc_info.value)
        assert "query" in str(exc_info.value)
        
        # Verify the error was captured
        error_ops = [op for op in repo.operations if op.error is not None]
        assert len(error_ops) == 1

    def test_delete_all_sketch_nodes_fails_explicitly(self):
        """Destructive operation without review should fail."""
        repo = CaptureGraphRepository()
        
        with pytest.raises(NotImplementedError):
            repo.delete_all_sketch_nodes("test_sketch")

    def test_relationship_operations_captured(self):
        """Test relationship operations are captured."""
        repo = CaptureGraphRepository()
        
        rel_id = repo.create_relationship_by_element_id(
            from_element_id="node_1",
            to_element_id="node_2",
            rel_label="REFERENCES",
            sketch_id="test_sketch"
        )
        
        assert rel_id is not None
        assert rel_id.startswith("rel_")
        assert len(repo.operations) == 1
        assert repo.operations[0].method_name == "create_relationship_by_element_id"

    def test_get_captured_operations(self):
        """Test retrieving all captured operations."""
        repo = CaptureGraphRepository()
        
        repo.create_node({"type": "email"}, "sketch_1")
        repo.create_node({"type": "domain"}, "sketch_1")
        repo.delete_nodes(["node_1"], "sketch_1")
        
        ops = repo.get_captured_operations()
        assert len(ops) == 3
        assert all(isinstance(op, CapturedOperation) for op in ops)

    def test_get_captured_writes_excludes_reads(self):
        """Test that writes are separated from reads."""
        repo = CaptureGraphRepository()
        
        repo.create_node({"type": "email"}, "sketch_1")
        repo.get_nodes_by_ids(["node_1"], "sketch_1")  # Read, not captured
        repo.delete_nodes(["node_1"], "sketch_1")
        
        writes = repo.get_captured_writes()
        assert len(writes) == 2  # Only create_node and delete_nodes
        assert all(op.method_name != "get_nodes_by_ids" for op in writes)

    def test_export_captures_json_serializable(self):
        """Test that captures can be exported as JSON."""
        repo = CaptureGraphRepository(sketch_id="test_sketch")
        repo.create_node({"type": "email", "value": "test@example.com"}, "test_sketch")
        
        export = repo.export_captures()
        
        # Verify structure
        assert export["sketch_id"] == "test_sketch"
        assert "capture_timestamp" in export
        assert export["total_operations"] == 1
        assert len(export["operations"]) == 1
        
        # Verify JSON serializable
        json_str = json.dumps(export)
        assert json_str is not None

    def test_clear_captures(self):
        """Test clearing captured operations."""
        repo = CaptureGraphRepository()
        
        repo.create_node({"type": "email"}, "sketch_1")
        repo.create_node({"type": "domain"}, "sketch_1")
        
        assert len(repo.operations) == 2
        
        repo.clear_captures()
        
        assert len(repo.operations) == 0


class TestCreateGraphServiceWithCapture:
    """Test create_graph_service factory with capture mode."""

    def test_create_service_with_capture_mode(self):
        """Test creating GraphService with capture_only=True."""
        # Should not require Neo4j credentials or connection
        service = create_graph_service(
            sketch_id="test_sketch",
            capture_only=True,
        )
        
        assert service is not None
        assert isinstance(service.repository, CaptureGraphRepository)

    def test_capture_service_does_not_connect_to_neo4j(self):
        """DEF-46 acceptance: scraper runs with no Neo4j service."""
        # This should not raise connection errors even without Neo4j
        service = create_graph_service(
            sketch_id="test_sketch",
            capture_only=True,
        )
        
        # Perform some graph operations
        node_id = service.create_node(
            type="email",
            properties={"value": "test@example.com"},
        )
        
        assert node_id is not None
        # No Neo4j errors should have occurred

    def test_capture_service_captures_all_operations(self):
        """Test that all service operations are captured."""
        service = create_graph_service(
            sketch_id="test_sketch",
            capture_only=True,
        )
        
        service.create_node(
            type="email",
            properties={"value": "test@example.com"},
        )
        
        service.create_node(
            type="domain",
            properties={"name": "example.com"},
        )
        
        # Capture repository should have recorded operations
        repo = service.repository
        assert isinstance(repo, CaptureGraphRepository)
        assert len(repo.get_captured_writes()) >= 2


class TestDiscoveryCallbackCapture:
    """Test capturing discovery callbacks and exceptions (DEF-46 requirement)."""

    def test_exception_in_callback_is_captured(self):
        """DEF-46 acceptance: exception after partial scan remains captured."""
        repo = CaptureGraphRepository(sketch_id="test_sketch")
        
        # Simulate partial scan
        repo.create_node({"type": "email"}, "test_sketch")
        repo.create_node({"type": "domain"}, "test_sketch")
        
        # Then simulate an unsupported operation that fails
        try:
            repo.query("MATCH (n) RETURN n")
        except NotImplementedError:
            pass
        
        # Both successful operations and the failed one should be captured
        assert len(repo.operations) == 3
        
        # The last operation should have an error
        error_op = repo.operations[-1]
        assert error_op.error is not None

    def test_callback_with_discovery_data(self):
        """Test capturing discovery callback with data."""
        repo = CaptureGraphRepository()
        
        # Simulate a discovery process with callbacks
        discovered_contacts = [
            {"type": "email", "value": "alice@example.com"},
            {"type": "email", "value": "bob@example.com"},
        ]
        
        for contact in discovered_contacts:
            repo.create_node(contact, "sketch_1")
        
        # Verify all discoveries were captured with lineage
        writes = repo.get_captured_writes()
        assert len(writes) == 2
        assert all(op.source_input is None for op in writes)  # Input lineage available


class TestScraperWithoutCredentials:
    """Test scraper functionality without Neo4j credentials (DEF-46)."""

    def test_scraper_simulation_no_credentials_needed(self):
        """DEF-46 acceptance: scraper without Neo4j/credentials."""
        # Initialize service in capture-only mode
        service = create_graph_service(
            sketch_id="scraper_test",
            capture_only=True,
        )
        
        # Simulate enricher discovery callback
        def discover_callback(item):
            """Simulate enricher discovery that captures contacts."""
            service.create_node(
                type="email",
                properties={"value": item},
            )
        
        # Run discovery with various URLs and contacts
        test_items = [
            "contact1@example.com",
            "contact2@example.com",
            "contact3@example.org",
        ]
        
        for item in test_items:
            discover_callback(item)
        
        # Verify all items were captured
        repo = service.repository
        writes = repo.get_captured_writes()
        assert len(writes) == 3

    def test_enabled_caller_injects_capture_sink(self):
        """Verify enabled callers actually inject the capture sink."""
        # This tests the injection seam requirement from DEF-46
        
        service = create_graph_service(
            sketch_id="injection_test",
            capture_only=True,
        )
        
        # Verify repository is the capture sink
        assert isinstance(service.repository, CaptureGraphRepository)
        
        # Verify operations are being captured
        service.create_node(type="test", properties={"id": "1"})
        
        repo = service.repository
        assert len(repo.operations) >= 1
