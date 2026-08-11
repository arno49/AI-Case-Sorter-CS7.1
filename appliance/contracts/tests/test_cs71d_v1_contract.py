import json
import unittest
from pathlib import Path


CONTRACT_PATH = Path(__file__).parents[1] / "cs71d-v1.openapi.json"

FROZEN_OPERATIONS = {
    "/v1/health/live": {"get"},
    "/v1/health/ready": {"get"},
    "/v1/snapshot": {"get"},
    "/v1/events": {"get"},
    "/v1/session/connect": {"post"},
    "/v1/session/recover": {"post"},
    "/v1/machine/stop": {"post"},
    "/v1/operations/home": {"post"},
    "/v1/operations/sort": {"post"},
    "/v1/operations/feed": {"post"},
    "/v1/operations": {"get"},
    "/v1/operations/{operation_id}": {"get"},
    "/v1/configuration": {"get", "patch"},
}

FROZEN_RESPONSES = {
    ("/v1/health/live", "get"): {"200", "401", "500"},
    ("/v1/health/ready", "get"): {"200", "401", "500"},
    ("/v1/snapshot", "get"): {"200", "401", "500", "503"},
    ("/v1/events", "get"): {"200", "400", "401", "500", "503"},
    ("/v1/session/connect", "post"): {
        "202",
        "400",
        "401",
        "403",
        "408",
        "409",
        "429",
        "500",
        "503",
    },
    ("/v1/session/recover", "post"): {
        "202",
        "400",
        "401",
        "403",
        "408",
        "409",
        "429",
        "500",
        "503",
    },
    ("/v1/machine/stop", "post"): {
        "202",
        "400",
        "401",
        "403",
        "408",
        "409",
        "429",
        "500",
        "503",
    },
    ("/v1/operations/home", "post"): {
        "202",
        "400",
        "401",
        "403",
        "408",
        "409",
        "429",
        "500",
        "503",
    },
    ("/v1/operations/sort", "post"): {
        "202",
        "400",
        "401",
        "403",
        "408",
        "409",
        "429",
        "500",
        "503",
    },
    ("/v1/operations/feed", "post"): {
        "202",
        "400",
        "401",
        "403",
        "408",
        "409",
        "429",
        "500",
        "503",
    },
    ("/v1/operations", "get"): {"200", "400", "401", "500", "503"},
    ("/v1/operations/{operation_id}", "get"): {
        "200",
        "400",
        "401",
        "404",
        "500",
        "503",
    },
    ("/v1/configuration", "get"): {"200", "401", "403", "500", "503"},
    ("/v1/configuration", "patch"): {
        "202",
        "400",
        "401",
        "403",
        "408",
        "409",
        "500",
        "503",
    },
}

FROZEN_SUCCESS_SCHEMAS = {
    ("/v1/health/live", "get", "200"): "Liveness",
    ("/v1/health/ready", "get", "200"): "Readiness",
    ("/v1/snapshot", "get", "200"): "MachineSnapshot",
    ("/v1/session/connect", "post", "202"): "OperationAccepted",
    ("/v1/session/recover", "post", "202"): "OperationAccepted",
    ("/v1/machine/stop", "post", "202"): "OperationAccepted",
    ("/v1/operations/home", "post", "202"): "OperationAccepted",
    ("/v1/operations/sort", "post", "202"): "OperationAccepted",
    ("/v1/operations/feed", "post", "202"): "OperationAccepted",
    ("/v1/operations", "get", "200"): "OperationPage",
    ("/v1/operations/{operation_id}", "get", "200"): "Operation",
    ("/v1/configuration", "get", "200"): "Configuration",
    ("/v1/configuration", "patch", "202"): "OperationAccepted",
}

FROZEN_REQUIRED_FIELDS = {
    "Actor": {"user_id", "role"},
    "OperationAccepted": {
        "api_version",
        "operation_id",
        "state",
        "generation",
        "accepted_at",
        "status_url",
    },
    "Operation": {
        "api_version",
        "operation_id",
        "type",
        "state",
        "actor",
        "created_at",
        "deadline_at",
        "generation",
        "trusted_terminal",
    },
    "MachineSnapshot": {
        "api_version",
        "generation",
        "connection_state",
        "fault_state",
        "ready",
        "firmware",
        "machine",
        "faults",
        "observed_at",
    },
    "DaemonEvent": {
        "api_version",
        "event_id",
        "occurred_at",
        "generation",
        "type",
        "data",
    },
    "Error": {"api_version", "code", "message", "request_id"},
}

FROZEN_PROPERTIES = {
    "Actor": {"user_id", "role"},
    "OperationAccepted": {
        "api_version",
        "operation_id",
        "state",
        "generation",
        "accepted_at",
        "status_url",
    },
    "Operation": {
        "api_version",
        "operation_id",
        "type",
        "state",
        "actor",
        "created_at",
        "deadline_at",
        "generation",
        "trusted_terminal",
        "started_at",
        "terminal_at",
        "outcome",
        "error",
    },
    "MachineSnapshot": {
        "api_version",
        "generation",
        "connection_state",
        "fault_state",
        "ready",
        "readiness_reason",
        "active_operation",
        "firmware",
        "machine",
        "faults",
        "observed_at",
    },
    "DaemonEvent": {
        "api_version",
        "event_id",
        "occurred_at",
        "generation",
        "type",
        "operation_id",
        "data",
    },
    "Error": {
        "api_version",
        "code",
        "message",
        "request_id",
        "generation",
        "details",
    },
}

FROZEN_ENUMS = {
    "ApiVersion": ["v1"],
    "ConnectionState": [
        "DISCONNECTED",
        "CONNECTING",
        "VERIFYING_V1",
        "ACTIVATING_V2",
        "READY",
        "RECOVERING",
        "UNCERTAIN",
    ],
    "FaultState": ["CLEAR", "ACTIVE", "LATCHED", "UNCERTAIN"],
    "OperationState": [
        "QUEUED",
        "ACCEPTED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "UNCERTAIN",
    ],
    "OperationType": [
        "CONNECT",
        "RECOVER",
        "STOP",
        "HOME",
        "SORT",
        "FEED",
        "CONFIGURE",
    ],
    "OperationOutcome": [
        "COMPLETED",
        "STOPPED",
        "FAILED",
        "CANCELLED",
        "UNCERTAIN",
    ],
}

FROZEN_PROPERTY_SIGNATURES = {
    ("OperationAccepted", "operation_id"): {"type": "string", "format": "uuid"},
    ("OperationAccepted", "generation"): {
        "$ref": "#/components/schemas/Generation"
    },
    ("Operation", "operation_id"): {"type": "string", "format": "uuid"},
    ("Operation", "state"): {"$ref": "#/components/schemas/OperationState"},
    ("Operation", "generation"): {"$ref": "#/components/schemas/Generation"},
    ("DaemonEvent", "event_id"): {"type": "integer", "minimum": 1},
    ("DaemonEvent", "generation"): {"$ref": "#/components/schemas/Generation"},
    ("Error", "request_id"): {"type": "string", "format": "uuid"},
}

STATE_CHANGING_OPERATIONS = {
    ("/v1/session/connect", "post"),
    ("/v1/session/recover", "post"),
    ("/v1/machine/stop", "post"),
    ("/v1/operations/home", "post"),
    ("/v1/operations/sort", "post"),
    ("/v1/operations/feed", "post"),
    ("/v1/configuration", "patch"),
}

REQUIRED_MUTATION_HEADERS = {
    "Idempotency-Key",
    "If-Match-Generation",
    "X-Deadline-Ms",
}


class Cs71dV1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_is_openapi_31_and_unix_socket_only(self):
        self.assertEqual(self.contract["openapi"], "3.1.0")
        self.assertEqual(self.contract["x-transport"], "unix-domain-socket")
        self.assertEqual(
            self.contract["x-unix-socket-path"], "/run/cs71/cs71d.sock"
        )
        self.assertFalse(self.contract["x-browser-addressable"])
        self.assertNotIn("servers", self.contract)

    def test_all_local_references_resolve(self):
        for reference in self._references(self.contract):
            self.assertTrue(reference.startswith("#/"), reference)
            value = self.contract
            for token in reference[2:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                self.assertIn(token, value, reference)
                value = value[token]

    def test_additive_optional_changes_preserve_frozen_v1_baseline(self):
        for path, methods in FROZEN_OPERATIONS.items():
            self.assertIn(path, self.contract["paths"])
            self.assertTrue(methods.issubset(self.contract["paths"][path]))

        schemas = self.contract["components"]["schemas"]
        for schema_name, required_fields in FROZEN_REQUIRED_FIELDS.items():
            self.assertIn(schema_name, schemas)
            self.assertEqual(required_fields, set(schemas[schema_name]["required"]))
            self.assertTrue(
                required_fields.issubset(set(schemas[schema_name]["properties"])),
                schema_name,
            )

        for schema_name, properties in FROZEN_PROPERTIES.items():
            self.assertTrue(
                properties.issubset(schemas[schema_name]["properties"]), schema_name
            )

        for schema_name, values in FROZEN_ENUMS.items():
            self.assertEqual(values, schemas[schema_name]["enum"], schema_name)

        for (schema_name, property_name), signature in (
            FROZEN_PROPERTY_SIGNATURES.items()
        ):
            actual = schemas[schema_name]["properties"][property_name]
            self.assertTrue(signature.items() <= actual.items())

    def test_frozen_responses_and_success_schemas_remain_available(self):
        for (path, method), statuses in FROZEN_RESPONSES.items():
            actual = self.contract["paths"][path][method]["responses"]
            self.assertTrue(statuses.issubset(actual), f"{method.upper()} {path}")

        for (path, method, status), schema_name in FROZEN_SUCCESS_SCHEMAS.items():
            response = self._resolve(
                self.contract["paths"][path][method]["responses"][status]
            )
            media_type = "application/json"
            schema = response["content"][media_type]["schema"]
            self.assertEqual(
                schema["$ref"], f"#/components/schemas/{schema_name}", path
            )

    def test_state_changes_require_identity_generation_and_deadline(self):
        for path, method in STATE_CHANGING_OPERATIONS:
            operation = self.contract["paths"][path][method]
            parameters = [
                self._resolve(parameter) for parameter in operation["parameters"]
            ]
            headers = {
                parameter["name"]
                for parameter in parameters
                if parameter["in"] == "header" and parameter["required"]
            }
            self.assertEqual(headers, REQUIRED_MUTATION_HEADERS, path)
            self.assertIn("requestBody", operation, path)
            self.assertTrue(self._resolve(operation["requestBody"])["required"], path)

    def test_every_operation_requires_local_bff_service_authentication(self):
        expected = [{"BffServiceAuth": []}]
        self.assertEqual(self.contract["security"], expected)
        for path, path_item in self.contract["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "patch", "put", "delete"}:
                    continue
                self.assertEqual(
                    operation.get("security", self.contract["security"]),
                    expected,
                    f"{method.upper()} {path}",
                )

    def test_mutation_header_contract_is_frozen(self):
        parameters = self.contract["components"]["parameters"]
        self.assertEqual(
            parameters["IfMatchGeneration"]["schema"],
            {"type": "string", "pattern": "^(0|[1-9][0-9]*)$"},
        )
        self.assertEqual(
            parameters["StopIfMatchGeneration"]["schema"],
            {"type": "string", "pattern": "^(\\*|0|[1-9][0-9]*)$"},
        )
        self.assertEqual(
            parameters["DeadlineMs"]["schema"],
            {"type": "integer", "minimum": 1, "maximum": 120000},
        )
        self.assertEqual(
            parameters["IdempotencyKey"]["schema"],
            {
                "type": "string",
                "minLength": 16,
                "maxLength": 128,
                "pattern": "^[A-Za-z0-9._~-]+$",
            },
        )

    def test_priority_stop_alone_accepts_wildcard_generation(self):
        for path, method in STATE_CHANGING_OPERATIONS:
            operation = self.contract["paths"][path][method]
            generation_reference = next(
                parameter
                for parameter in operation["parameters"]
                if self._resolve(parameter)["name"] == "If-Match-Generation"
            )
            if path == "/v1/machine/stop":
                self.assertEqual(
                    generation_reference["$ref"],
                    "#/components/parameters/StopIfMatchGeneration",
                )
            else:
                self.assertEqual(
                    generation_reference["$ref"],
                    "#/components/parameters/IfMatchGeneration",
                )

    def test_sse_uses_daemon_event_cursor_and_bounded_reconciliation(self):
        operation = self.contract["paths"]["/v1/events"]["get"]
        parameters = [self._resolve(item) for item in operation["parameters"]]
        self.assertEqual(parameters[0]["name"], "Last-Event-ID")
        event_schema = self.contract["components"]["schemas"]["DaemonEvent"]
        self.assertIn("event_id", event_schema["properties"])
        event_types = self._resolve(event_schema["properties"]["type"])[
            "x-known-values"
        ]
        self.assertIn("snapshot.required", event_types)
        self.assertNotIn("request_id", event_schema["properties"])

    def test_daemon_and_protocol_identifiers_are_not_conflated(self):
        schemas = self.contract["components"]["schemas"]
        self.assertEqual(
            schemas["Operation"]["properties"]["operation_id"]["format"], "uuid"
        )
        self.assertEqual(
            schemas["DaemonEvent"]["properties"]["event_id"]["type"], "integer"
        )
        self.assertEqual(schemas["Generation"]["type"], "integer")
        for schema_name in ("OperationAccepted", "Operation", "DaemonEvent"):
            self.assertNotIn("request_id", schemas[schema_name]["properties"])

    def test_error_codes_cover_documented_mapping(self):
        codes = set(self.contract["components"]["schemas"]["ErrorCode"]["enum"])
        self.assertTrue(
            {
                "VALIDATION_FAILED",
                "UNAUTHENTICATED",
                "FORBIDDEN",
                "STALE_GENERATION",
                "IDEMPOTENCY_CONFLICT",
                "NOT_READY",
                "UNCERTAIN",
                "QUEUE_FULL",
                "DEADLINE_INVALID",
                "DEADLINE_EXPIRED",
                "JOURNAL_UNAVAILABLE",
                "SERVICE_UNAVAILABLE",
                "INTERNAL_ERROR",
            }.issubset(codes)
        )

    def test_http_error_responses_narrow_error_codes(self):
        expected = {
            "ValidationFailed": {"VALIDATION_FAILED", "DEADLINE_INVALID"},
            "Unauthenticated": {"UNAUTHENTICATED"},
            "Forbidden": {"FORBIDDEN"},
            "NotFound": {"RESOURCE_NOT_FOUND"},
            "DeadlineExpired": {"DEADLINE_EXPIRED"},
            "Conflict": {
                "STALE_GENERATION",
                "IDEMPOTENCY_CONFLICT",
                "NOT_READY",
                "UNCERTAIN",
            },
            "QueueFull": {"QUEUE_FULL"},
            "InternalError": {"INTERNAL_ERROR"},
            "ServiceUnavailable": {
                "JOURNAL_UNAVAILABLE",
                "SERVICE_UNAVAILABLE",
            },
        }
        for response_name, expected_codes in expected.items():
            response = self.contract["components"]["responses"][response_name]
            schema = response["content"]["application/problem+json"]["schema"]
            narrowed = self._resolve(schema)["allOf"][1]["properties"]["code"]
            actual_codes = set(
                narrowed.get("enum", [narrowed.get("const")])
            )
            self.assertEqual(actual_codes, expected_codes, response_name)

    def test_terminal_operations_require_terminal_evidence(self):
        operation = self.contract["components"]["schemas"]["Operation"]
        terminal_rule, success_rule = operation["allOf"]
        self.assertEqual(
            set(terminal_rule["then"]["required"]), {"terminal_at", "outcome"}
        )
        self.assertTrue(
            success_rule["then"]["properties"]["trusted_terminal"]["const"]
        )

    def _resolve(self, value):
        if "$ref" not in value:
            return value
        resolved = self.contract
        for token in value["$ref"][2:].split("/"):
            resolved = resolved[token.replace("~1", "/").replace("~0", "~")]
        return resolved

    @staticmethod
    def _references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                yield value["$ref"]
            for child in value.values():
                yield from Cs71dV1ContractTests._references(child)
        elif isinstance(value, list):
            for child in value:
                yield from Cs71dV1ContractTests._references(child)


if __name__ == "__main__":
    unittest.main()
