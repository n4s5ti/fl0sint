from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, create_model
from pydantic.config import ConfigDict

from ..utils import resolve_type
from .execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
from .graph import GraphService, create_graph_service
from .logger import Logger
from .vault import VaultProtocol


class InvalidEnricherParams(Exception):
    pass


def build_params_model(params_schema: list) -> BaseModel:
    """
    Build a strict Pydantic model from a params_schema.
    Unknown fields will raise a validation error.

    Note: Vault secrets are always optional in the Pydantic model to allow
    for deferred configuration. Required validation happens after vault resolution.
    """
    fields: Dict[str, Any] = {}

    for param in params_schema:
        name = param["name"]
        param_type = param.get("type", "string")
        field_type = bool if param_type == "bool" else str
        required = param.get("required", False)

        # Vault secrets are always optional in Pydantic validation
        # Required validation happens after vault resolution
        if param_type == "vaultSecret":
            default = param.get("default", None)
        else:
            default = ... if required else param.get("default")

        fields[name] = (
            Optional[field_type],
            Field(default=default, description=param.get("description", "")),
        )

    model = create_model("ParamsModel", __config__=ConfigDict(extra="forbid"), **fields)

    return model


class Enricher(ABC):
    """
    Abstract base class for all enrichers.

    ## InputType and OutputType Pattern

    Enrichers only need to define InputType and OutputType as class attributes.
    The base class automatically handles schema generation:

    ```python
    from typing import List
    from flowsint_types import Domain
    from flowsint_types import Ip

    class MyEnricher(Enricher):
        # Define types as class attributes (base types, not lists)
        InputType = Domain
        OutputType = Ip

        @classmethod
        def name(cls):
            return "my_enricher"

        @classmethod
        def category(cls):
            return "Domain"

        @classmethod
        def key(cls):
            return "domain"

        # preprocess receives a list and returns a list of validated InputType instances
        def preprocess(self, data: List) -> List[InputType]:
            # Generic implementation handles validation automatically
            return super().preprocess(data)

        # scan receives a list of InputType and returns a list of OutputType
        async def scan(self, data: List[InputType]) -> List[OutputType]:
            results: List[OutputType] = []
            # ... implementation
            return results

    # Make types available at module level for easy access
    InputType = MyEnricher.InputType
    OutputType = MyEnricher.OutputType
    ```

    The base class automatically provides:
    - Generic preprocess() that validates inputs using InputType
    - input_schema() method using InputType
    - output_schema() method using OutputType
    - Error handling for missing type definitions
    - Consistent schema generation across all enrichers

    Subclasses can override input_schema() or output_schema() if needed for special cases.
    """

    # Abstract type aliases that must be defined in subclasses for runtime use
    InputType = NotImplemented
    OutputType = NotImplemented

    def __init__(
        self,
        sketch_id: Optional[str] = None,
        scan_id: Optional[str] = None,
        params_schema: Optional[List[Dict[str, Any]]] = None,
        vault: Optional[VaultProtocol] = None,
        params: Optional[Dict[str, Any]] = None,
        graph_service: Optional[GraphService] = None,
    ):
        self.scan_id = scan_id or "default"
        self.sketch_id = sketch_id or "system"
        self.vault = vault
        self.params_schema = params_schema or []
        self.ParamsModel = build_params_model(self.params_schema)
        self.params: Dict[str, Any] = params or {}

        # Initialize graph service (uses singleton connection by default)
        if graph_service:
            self._graph_service = graph_service
        else:
            self._graph_service = create_graph_service(
                sketch_id=self.sketch_id,
                enable_batching=True,
            )

        # Params is filled synchronously by the constructor. This params is generally constructed of
        # vaultSecret references, not the key directly. The idea is that the real key values are resolved after calling
        # async_init(), right before the execution.

    async def async_init(self):
        self.ParamsModel = build_params_model(self.params_schema)

        # Always resolve parameters, even if self.params is empty
        # This allows vault secrets to be fetched by name from params_schema
        resolved_params = self.resolve_params()

        # Strict validation after resolution
        try:
            validated = self.ParamsModel(**resolved_params)
            self.params = validated.model_dump()
        except ValidationError as e:
            raise InvalidEnricherParams(
                f"Enricher '{self.name()}' received invalid parameters: {e}"
            )

    def resolve_params(self) -> Dict[str, Any]:
        resolved = {}

        for param in self.params_schema:
            param_name = param["name"]
            param_type = param.get("type", "string")

            if param_type == "vaultSecret":
                # For vault secrets, try to get from vault by name or ID
                secret = None
                if self.vault is not None:
                    # First, check if user provided a specific vault ID in params
                    if param_name in self.params and self.params[param_name]:
                        secret = self.vault.get_secret(self.params[param_name])
                    # Otherwise, try to get the secret by the param name itself
                    if secret is None:
                        secret = self.vault.get_secret(param_name)

                    if secret is not None:
                        resolved[param_name] = secret
                    elif param.get("required", False):
                        raise Exception(
                            f"Required vault secret '{param_name}' is missing. Please go to the Vault settings and create a '{param_name}' key."
                        )

                # If no vault or no secret found, use default if available
                if param_name not in resolved and param.get("default") is not None:
                    resolved[param_name] = param["default"]
            else:
                # For non-vault params, use the provided value or default
                if param_name in self.params and self.params[param_name] is not None:
                    resolved[param_name] = self.params[param_name]
                elif param.get("default") is not None:
                    resolved[param_name] = param["default"]

        return resolved

    @classmethod
    def required_params(self) -> bool:
        return False

    @classmethod
    @abstractmethod
    def name(cls) -> str:
        pass

    @classmethod
    def icon(cls) -> str | None:
        return None

    @classmethod
    @abstractmethod
    def category(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def key(cls) -> str:
        """Primary key on which the enricher operates (e.g. domain, IP, etc.)"""
        pass

    @classmethod
    def documentation(cls) -> str:
        """
        Return formatted markdown documentation for this enricher.
        Override this method to provide custom documentation.
        Falls back to cleaned docstring if not overridden.
        """
        import inspect

        return inspect.cleandoc(cls.__doc__ or "No documentation available.")

    @classmethod
    def input_schema(cls) -> Dict[str, Any]:
        """
        Generate input schema from InputType class attribute.
        Subclasses don't need to override this unless they have special requirements.
        """
        return cls.generate_input_schema()

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        """Can be overridden in subclasses to declare required parameters"""
        return []

    @classmethod
    def output_schema(cls) -> Dict[str, Any]:
        """
        Generate output schema from OutputType class attribute.
        Subclasses don't need to override this unless they have special requirements.
        """
        return cls.generate_output_schema()

    @classmethod
    def generate_input_schema(cls) -> Dict[str, Any]:
        """
        Helper method to generate input schema from InputType class attribute.

        Raises:
            NotImplementedError: If InputType is not defined in the subclass
        """
        if cls.InputType is NotImplemented:
            raise NotImplementedError(f"InputType must be defined in {cls.__name__}")

        adapter = TypeAdapter(cls.InputType)
        schema = adapter.json_schema()

        # Handle different schema structures
        # Check for direct properties first (even if $defs exists for nested types)
        if "properties" in schema and "title" in schema:
            # Direct type definition (e.g., Domain, Ip, Website)
            return {
                "type": schema.get("title", "Any"),
                "properties": [
                    {"name": prop, "type": resolve_type(info, schema)}
                    for prop, info in schema["properties"].items()
                ],
            }
        elif "$defs" in schema and schema["$defs"] and "$ref" in schema:
            # Reference to a type in $defs
            type_name = schema["$ref"].split("/")[-1]
            details = schema["$defs"][type_name]
            return {
                "type": type_name,
                "properties": [
                    {"name": prop, "type": resolve_type(info, schema)}
                    for prop, info in details["properties"].items()
                ],
            }
        else:
            # Fallback for unknown schema structures
            return {
                "type": schema.get("title", "Any"),
                "properties": [{"name": "value", "type": "object"}],
            }

    @classmethod
    def generate_output_schema(cls) -> Dict[str, Any]:
        """
        Helper method to generate output schema from OutputType class attribute.

        Raises:
            NotImplementedError: If OutputType is not defined in the subclass
        """
        if cls.OutputType is NotImplemented:
            raise NotImplementedError(f"OutputType must be defined in {cls.__name__}")

        adapter = TypeAdapter(cls.OutputType)
        schema = adapter.json_schema()

        # Handle different schema structures
        # Check for direct properties first (even if $defs exists for nested types)
        if "properties" in schema and "title" in schema:
            # Direct type definition (e.g., Domain, Ip, Website)
            return {
                "type": schema.get("title", "Any"),
                "properties": [
                    {"name": prop, "type": resolve_type(info, schema)}
                    for prop, info in schema["properties"].items()
                ],
            }
        elif "$defs" in schema and schema["$defs"] and "$ref" in schema:
            # Reference to a type in $defs
            type_name = schema["$ref"].split("/")[-1]
            details = schema["$defs"][type_name]
            return {
                "type": type_name,
                "properties": [
                    {"name": prop, "type": resolve_type(info, schema)}
                    for prop, info in details["properties"].items()
                ],
            }
        else:
            # Fallback for unknown schema structures
            return {
                "type": schema.get("title", "Any"),
                "properties": [{"name": "value", "type": "object"}],
            }

    @abstractmethod
    async def scan(self, values: List[str]) -> List[Dict[str, Any]]:
        pass

    def set_params(self, params: Dict[str, Any]) -> None:
        self.params = params

    def get_params(self) -> Dict[str, Any]:
        return self.params

    def get_secret(self, key_name: str, default: Any = None) -> Any:
        """
        Get a secret value by key name.
        The secret is automatically resolved from the vault during async_init.

        Args:
            key_name: The name of the secret parameter (e.g., "WHOXY_API_KEY")
            default: Default value if secret is not found

        Returns:
            The secret value from the vault, or default if not found
        """
        value = self.params.get(key_name, default)
        # If the value is None, return the default instead (allows fallback to env vars)
        return value if value is not None else default

    def _input_validation_context(self) -> tuple[TypeAdapter | None, str | None]:
        if self.InputType is NotImplemented:
            return None, None

        base_type = self.InputType
        adapter = TypeAdapter(base_type)
        primary_field = None
        if issubclass(base_type, BaseModel):
            for name, field in base_type.model_fields.items():
                if field.json_schema_extra and field.json_schema_extra.get("primary"):
                    primary_field = name
                    break
            if primary_field is None:
                for name, field in base_type.model_fields.items():
                    if field.is_required():
                        primary_field = name
                        break
                if primary_field is None:
                    primary_field = next(iter(base_type.model_fields.keys()))
        return adapter, primary_field

    def _validate_single_input(
        self,
        item: Any,
        *,
        adapter: TypeAdapter | None = None,
        primary_field: str | None = None,
    ) -> Any:
        """Validate one input using the same conversion rules as ``preprocess``."""
        if self.InputType is NotImplemented:
            return item
        if adapter is None:
            adapter, primary_field = self._input_validation_context()
        if isinstance(item, str) and primary_field:
            item = {primary_field: item}
        return adapter.validate_python(item)

    def preprocess(self, values: List) -> List:
        """
        Generic preprocess that validates and converts input using InputType.
        Automatically handles dicts, objects, and strings (using the model's primary field).
        Invalid items are skipped silently.

        Note: InputType should be defined as the base type (e.g., Ip, Domain),
        not as a List (e.g., List[Ip]). The preprocess method expects a list of values
        and returns a list of validated InputType instances.
        """
        if self.InputType is NotImplemented:
            return values

        adapter, primary_field = self._input_validation_context()
        cleaned = []

        for item in values:
            try:
                cleaned.append(
                    self._validate_single_input(
                        item, adapter=adapter, primary_field=primary_field
                    )
                )
            except Exception:
                continue

        if len(cleaned) == 0:
            Logger.warn(
                self.sketch_id,
                {
                    "message": f"No valid input were provided to enricher '{self.name()}'."
                },
            )
            return values
        return cleaned

    @asynccontextmanager
    async def _structured_execution_context(self) -> AsyncIterator[Any]:
        """Provide optional shared resources for structured per-input processing."""
        yield None

    async def _process_single_input_structured(
        self, input_obj: Any, context: Any
    ) -> List[Any]:
        """Process one validated input; subclasses can preserve richer grouping."""
        return await self.scan([input_obj])

    def _build_structured_evidence(
        self, input_obj: Any, input_ref: str
    ) -> tuple[EvidenceEnvelope, ...]:
        """Return retainable evidence captured while processing one input."""
        return ()

    def _classify_structured_exception(
        self, error: Exception
    ) -> RedactedDiagnostic:
        """Classify failures without retaining exception text or request data."""
        if isinstance(error, (InvalidEnricherParams, ValidationError)):
            return RedactedDiagnostic(
                code="validation_error",
                safe_message="Input validation failed.",
                retryable=False,
            )
        if isinstance(error, httpx.TimeoutException):
            return RedactedDiagnostic(
                code="timeout",
                safe_message="The operation timed out.",
                retryable=True,
            )
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
            if status_code == 429:
                return RedactedDiagnostic(
                    code="http_rate_limited",
                    safe_message="The remote service rate limited the request.",
                    retryable=True,
                )
            if 500 <= status_code < 600:
                return RedactedDiagnostic(
                    code="http_server_error",
                    safe_message="The remote service failed.",
                    retryable=True,
                )
            if 400 <= status_code < 500:
                return RedactedDiagnostic(
                    code="http_client_error",
                    safe_message="The remote service rejected the request.",
                    retryable=False,
                )
            return RedactedDiagnostic(
                code="http_error",
                safe_message="The remote service returned an HTTP error.",
                retryable=False,
            )
        return RedactedDiagnostic(
            code="unexpected_error",
            safe_message="Unexpected processing failure.",
            retryable=False,
        )

    def _classify_structured_success(
        self,
        outputs: tuple[Any, ...],
        evidence: tuple[EvidenceEnvelope, ...],
    ) -> tuple[OutcomeStatus, RedactedDiagnostic | None]:
        return OutcomeStatus.SUCCESS, None

    async def _execute_structured_input(
        self,
        original_input: Any,
        context: Any,
        *,
        adapter: TypeAdapter | None = None,
        primary_field: str | None = None,
    ) -> InputOutcome:
        input_ref = canonical_input_hash(original_input)
        try:
            input_obj = self._validate_single_input(
                original_input, adapter=adapter, primary_field=primary_field
            )
        except Exception as error:
            return InputOutcome(
                input_ref=input_ref,
                status=OutcomeStatus.FAILURE,
                diagnostic=self._classify_structured_exception(error),
            )

        outputs: tuple[Any, ...] = ()
        diagnostic: RedactedDiagnostic | None = None
        status = OutcomeStatus.SUCCESS
        try:
            processed = await self._process_single_input_structured(input_obj, context)
            outputs = tuple(self.postprocess(processed, [input_obj]))
        except Exception as error:
            status = OutcomeStatus.FAILURE
            diagnostic = self._classify_structured_exception(error)

        try:
            evidence = self._build_structured_evidence(input_obj, input_ref)
        except Exception as error:
            evidence = ()
            status = OutcomeStatus.FAILURE
            diagnostic = self._classify_structured_exception(error)

        if status is OutcomeStatus.SUCCESS:
            status, diagnostic = self._classify_structured_success(outputs, evidence)
        if status is not OutcomeStatus.SUCCESS:
            outputs = ()


        return InputOutcome(
            input_ref=input_ref,
            status=status,
            outputs=outputs,
            diagnostic=diagnostic,
            evidence=evidence,
        )

    async def execute_structured(
        self, values: List[Any]
    ) -> StructuredExecutionResult:
        """Execute independently per original input without flattening outputs."""
        outcomes: list[InputOutcome] = []
        if self.name() != "enricher_orchestrator":
            Logger.info(self.sketch_id, {"message": f"Enricher {self.name()} started."})

        try:
            await self.async_init()
        except Exception as error:
            diagnostic = self._classify_structured_exception(error)
            outcomes = [
                InputOutcome(
                    input_ref=canonical_input_hash(value),
                    status=OutcomeStatus.FAILURE,
                    diagnostic=diagnostic,
                )
                for value in values
            ]
        else:
            try:
                adapter, primary_field = self._input_validation_context()
                async with self._structured_execution_context() as context:
                    for value in values:
                        outcomes.append(
                            await self._execute_structured_input(
                                value,
                                context,
                                adapter=adapter,
                                primary_field=primary_field,
                            )
                        )
            except Exception as error:
                diagnostic = self._classify_structured_exception(error)
                for value in values[len(outcomes) :]:
                    outcomes.append(
                        InputOutcome(
                            input_ref=canonical_input_hash(value),
                            status=OutcomeStatus.FAILURE,
                            diagnostic=diagnostic,
                        )
                    )
        finally:
            try:
                self._graph_service.flush()
            except Exception:
                Logger.error(
                    self.sketch_id,
                    {"message": f"Enricher {self.name()} graph flush failed."},
                )

        if self.name() != "enricher_orchestrator":
            Logger.completed(
                self.sketch_id, {"message": f"Enricher {self.name()} finished."}
            )
        return StructuredExecutionResult(
            enricher_name=self.name(),
            outcomes=tuple(outcomes),
        )

    def postprocess(
        self, results: List[Dict[str, Any]], input_data: List[str] = None
    ) -> List[Dict[str, Any]]:
        return results

    async def execute(self, values: List[Any]) -> List[Dict[str, Any]]:
        if self.name() != "enricher_orchestrator":
            Logger.info(self.sketch_id, {"message": f"Enricher {self.name()} started."})
        try:
            await self.async_init()
            preprocessed = self.preprocess(values)
            results = await self.scan(preprocessed)
            processed = self.postprocess(results, preprocessed)

            # Flush any pending batch operations
            self._graph_service.flush()

            if self.name() != "enricher_orchestrator":
                Logger.completed(
                    self.sketch_id, {"message": f"Enricher {self.name()} finished."}
                )

            return processed

        except Exception:
            if self.name() != "enricher_orchestrator":
                Logger.error(
                    self.sketch_id,
                    {"message": f"Enricher {self.name()} errored."},
                )
            return []

    def create_node(self, node_obj) -> None:
        """
        Create a single Neo4j node.

        The following properties are automatically added to every node:
        - type: Lowercase version of node_type
        - sketch_id: Current sketch ID from enricher context
        - label: Automatically computed by FlowsintType, or defaults to key_value if not provided
        - created_at: ISO 8601 UTC timestamp (only on creation, not updates)

        Use Pydantic object directly:
            ```python
            self.create_node(ip)
            ```

        Args:
            node_obj: Either a Pydantic object or node label string
            **properties: Additional node properties or overrides
        """
        self._graph_service.create_node_from_flowsint_type(node_obj=node_obj)

    def create_relationship(
        self,
        from_obj,
        to_obj,
        rel_label="IS_RELATED_TO",
    ) -> None:
        """
        Create a relationship between two nodes.

        Best Practice - Use Pydantic objects directly:
            ```python
            self.create_relationship(individual, domain, "HAS_DOMAIN")
            self.create_relationship(email, breach, "FOUND_IN_BREACH")
            ```

        Args:
            from_obj: Either a Pydantic object (source) or source node label
            to_obj: Either a Pydantic object (target) or source node key property
            rel_label: Either relationship type (Pydantic) or source node key value
        """
        self._graph_service.create_relationship(
            from_obj=from_obj, to_obj=to_obj, rel_label=rel_label
        )

    def log_graph_message(self, message: str) -> None:
        """
        Log a graph operation message.

        Args:
            message: Message to log
        """
        self._graph_service.log_graph_message(message)

    @property
    def graph_service(self) -> GraphService:
        """
        Get the graph service instance.

        Returns:
            GraphService instance for advanced operations
        """
        return self._graph_service
