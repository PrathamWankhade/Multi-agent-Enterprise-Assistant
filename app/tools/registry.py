"""Tool registry: describes callable tools and dispatches invocations.

Every tool is declared with a JSON-schema-style parameter list, a risk level
and a synchronous handler. The Execution Agent picks a tool, the registry
validates arguments and dispatches.

Risk levels:
- ``read``     : no side effects, always safe to run.
- ``write``    : mutates state (tickets, files) -> human approval required.
- ``external`` : calls out of the system -> never auto-approved.
"""

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolParameter:
    name: str
    type: str  # "string" | "integer" | "number" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[Any]] = None

    def to_schema(self) -> Dict[str, Any]:
        schema: Dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.enum:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)
    risk: str = "read"  # read | write | external
    handler: Optional[Callable[..., Any]] = None

    @property
    def requires_approval(self) -> bool:
        # `workflow` tools manage their own approval lifecycle (e.g. ITSM
        # tickets start in pending_approval), so they skip the generic gate.
        return self.risk in {"write", "external"}

    def to_openai_schema(self) -> Dict[str, Any]:
        """Function-calling style schema (usable with OpenAI/Azure tool calls)."""
        props = {p.name: p.to_schema() for p in self.parameters}
        required = [p.name for p in self.parameters if p.required]
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }

    def to_prompt_block(self) -> str:
        """Compact rendering for prompts sent to local models."""
        lines = [f"- {self.name}: {self.description} (risk={self.risk})"]
        if self.parameters:
            for p in self.parameters:
                req = "required" if p.required else "optional"
                enum = f" one of {p.enum}" if p.enum else ""
                default = f" default={p.default!r}" if p.default is not None else ""
                lines.append(
                    f"    {p.name} ({p.type}, {req}){enum}{default}: {p.description}"
                )
        return "\n".join(lines)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.handler:
            raise ValueError(f"Tool '{spec.name}' has no handler")
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list(self) -> List[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def prompt_block(self) -> str:
        return "\n".join(t.to_prompt_block() for t in self.list())

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.to_openai_schema() for t in self.list()]

    # ------------------------------------------------------------------
    # Argument validation + dispatch
    # ------------------------------------------------------------------

    def validate_args(
        self, spec: ToolSpec, args: Dict[str, Any]
    ) -> tuple[Dict[str, Any], List[str]]:
        """Coerce/check arguments. Returns (clean_args, errors)."""
        clean: Dict[str, Any] = {}
        errors: List[str] = []
        known = {p.name for p in spec.parameters}

        for key in args:
            if key not in known:
                logger.debug("Ignoring unknown arg %s for tool %s", key, spec.name)

        for p in spec.parameters:
            if p.name not in args or args[p.name] is None:
                if p.required and p.default is None:
                    errors.append(f"missing required parameter '{p.name}'")
                    continue
                if p.default is not None:
                    clean[p.name] = p.default
                    continue
                continue

            value = args[p.name]
            try:
                if p.type == "integer":
                    value = int(value)
                elif p.type == "number":
                    value = float(value)
                elif p.type == "boolean":
                    if isinstance(value, str):
                        value = value.strip().lower() in {"true", "1", "yes"}
                    else:
                        value = bool(value)
                elif p.type == "string":
                    value = str(value)
                elif p.type == "array" and not isinstance(value, list):
                    errors.append(f"'{p.name}' must be a list")
                    continue
                elif p.type == "object" and not isinstance(value, dict):
                    errors.append(f"'{p.name}' must be an object")
                    continue
            except (TypeError, ValueError):
                errors.append(f"'{p.name}' has invalid type (expected {p.type})")
                continue

            if p.enum and value not in p.enum:
                errors.append(f"'{p.name}' must be one of {p.enum}")
                continue

            clean[p.name] = value

        return clean, errors

    async def invoke(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and run a tool. Always returns a structured result."""
        spec = self.get(name)
        if spec is None:
            return {
                "status": "error",
                "error": f"Unknown tool: {name}",
                "available": [t.name for t in self.list()],
            }

        clean, errors = self.validate_args(spec, args or {})
        if errors:
            return {
                "status": "error",
                "tool": name,
                "error": "Invalid arguments: " + "; ".join(errors),
            }

        try:
            result = spec.handler(**clean)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict):
                status = "error" if result.get("error") else "ok"
                return {**result, "status": status, "tool": name, "risk": spec.risk}
            return {"status": "ok", "tool": name, "risk": spec.risk, "data": result}
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Tool %s failed", name)
            return {"status": "error", "tool": name, "error": str(exc)}


_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _REGISTRY
